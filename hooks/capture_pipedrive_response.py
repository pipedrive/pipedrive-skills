"""PostToolUse hook: save one Pipedrive MCP response to .pipedrive-sales/runs/captured/<tool_use_id>.json.

The bundled scripts read these files instead of a copy the model re-types. For a read the latest `plan` issued
(runs/pending-reads.json), the hook answers with a one-line receipt through updatedToolOutput, so the response
does not sit in Claude's context (ADR-032); every other result reaches the model unchanged. It always exits 0,
so a capture problem never fails the tool call.
A response that does not parse as JSON is kept as raw_text with response null; the scripts ignore it and the
skill falls back to saving that response itself.
Cowork replaces an oversized result with a pointer ("Error: result (N characters) exceeds maximum allowed tokens. Output
has been saved to <path>"). The hook reads that file and saves it as the response (`capture: hook_file`, 0.7.12 phase 0
showed the file is readable from the hook); a pointer it cannot read is kept as raw_text with `pointer_unreadable`, and
the receipt for a planned read says so, so the run records a failed read and the report names it.
Since 0.7.14 a continuation whose cursor or offset is not the one run_capture.py absorb saved (runs/next-page-params.json)
is flagged `page_mismatch`, and absorb keeps that page out of the run (decision 2A, ADR-030 note).
"""

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile

# Pipedrive read tools, under any connector name: a custom connector is named by whoever adds it (8 Oct: "Pipedrive Beta").
# The list equals pdcore.READ_TOOLS and the matcher in hooks.json (tests/test_release_0_7_20_phase2.py).
TOOL = re.compile(r"mcp__(?P<server>.+)__(?P<tool>getAccountContext|get_account_context|getActivities|getActivity|getDeal|getDeals|getDealProducts|getDealsProducts|getLead|getLeadConversionStatus|getLeads|getNote|getNotes|getOrganization|getOrganizations|getPerson|getPersons|getPersonProducts|getProduct|getProductDeals|getProductFiles|getProductImage|getProducts|getProductVariations|getStage|getStages|searchDeals|searchLeads|searchOrganization|searchPersons|searchProducts)")
# Captures hold CRM data: a file older than this is deleted on every capture, run or no run (0.7.20, review 2.2).
RETENTION = timedelta(days=7)


def prune(folder, now=None):
    """Delete captures older than RETENTION by file time; the scripts' own prune reads each record's observed_at."""
    cutoff = (now or datetime.now(timezone.utc)) - RETENTION
    for path in folder.glob("*.json"):
        try:
            if datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) < cutoff:
                path.unlink()
        except OSError:
            continue


def _text(response):
    if isinstance(response, str):
        return response
    blocks = response.get("content") if isinstance(response, dict) and "content" in response else response
    if isinstance(blocks, list):
        return "".join(b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text")
    return json.dumps(response, ensure_ascii=False)


def _parse(text):
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value if isinstance(value, (dict, list)) else None


PENDING_FOR = timedelta(minutes=15)
POINTER = re.compile(r"exceeds maximum allowed tokens\. Output has been saved to (\S+?)\.?(?:\s|$)")
POINTER_MAX_BYTES = 20 * 1024 * 1024


def follow_pointer(path):
    """The parsed body of the file a pointer names, with the text it was read from; else (None, None, why)."""
    try:
        size = os.stat(path).st_size
        if size > POINTER_MAX_BYTES:
            return None, None, f"file too large ({size} bytes)"
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except (OSError, UnicodeDecodeError) as error:
        return None, None, type(error).__name__
    parsed = _parse(text)
    if parsed is None:
        return None, None, "file is not a JSON body"
    return parsed, text, None


def _plain(value):
    """Numbers as text, matching run_capture: {"id": "42"} and {"id": 42} are the same read."""
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return value


def read_key(tool, params):
    return json.dumps(_plain([str(tool).split("__")[-1], params or {}]), sort_keys=True, ensure_ascii=False)


def _selector(params):
    parts = []
    for key, value in (params or {}).items():
        if key in ("limit", "cursor", "start", "include_fields"):
            continue
        if key == "ids" and isinstance(value, list):
            parts.append(f"{len(value)} ids")
        else:
            parts.append(f"{key} {', '.join(map(str, value)) if isinstance(value, list) else value}")
    return "; ".join(parts) or "no filter"


def receipt(runs, record, session):
    """The receipt for a successful, saved read the latest plan issued in this session, at most once; else None."""
    if os.environ.get("PIPEDRIVE_SALES_RECEIPTS", "").strip().lower() == "off":
        return None
    response = record.get("response")
    context = record["tool"] in ("getAccountContext", "get_account_context")
    # The context tool answers with the account object itself, without a success flag.
    succeeded = isinstance(response, dict) and (response.get("success") is True or
                                                (context and "success" not in response and isinstance(response.get("current_user"), dict)))
    if not succeeded and not record.get("pointer_unreadable"):
        return None
    try:
        marker = json.loads((runs / "pending-reads.json").read_text(encoding="utf-8"))
        issued = datetime.fromisoformat(str(marker["issued_at"]).replace("Z", "+00:00"))
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not timedelta(minutes=-1) <= datetime.now(timezone.utc) - issued <= PENDING_FOR:
        return None
    if marker.get("session_id") and marker["session_id"] != session:
        return None
    key, owner = read_key(record["tool"], record["params"]), re.sub(r"[^A-Za-z0-9]", "", str(marker.get("id") or ""))
    claims = runs / "receipts"
    for index, read in enumerate(marker.get("reads") or []):
        if not owner or not isinstance(read, dict) or read.get("key") != key:
            continue
        try:
            claims.mkdir(parents=True, exist_ok=True)
            os.close(os.open(claims / f"{owner}-{index}", os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        except FileExistsError:
            continue
        if record.get("pointer_unreadable"):
            # The host saved the oversized result to a file the hook could not read: the run records the read as failed
            # when plan absorbs it, and the report names the read as not made. The path is the host's, not CRM data.
            return (f"Pipedrive {record['tool']} ({_selector(record['params'])}): the result was too large for the chat and the plugin "
                    f"could not read the host's copy ({record.get('pointer_error')}) at {record['pointer_path']}. Run plan next: the "
                    "report will say this read was not made. If you can read that file, save it first with response_capture.py "
                    f"--run .pipedrive-sales/runs/<run> --response-file {record['pointer_path']} --tool {record['tool']} --params-file <params.json>.")
        if context:
            sizes = [(len(response.get(key) or []), name) for key, name in (("pipelines", "pipeline"), ("company_users", "user"))]
            counts = ", ".join(f"{size} {name}{'' if size == 1 else 's'}" for size, name in sizes)
            run = re.sub(r"[^A-Za-z0-9._-]", "", str(marker.get("run") or "<setup-run>"))
            return (f"Pipedrive {record['tool']}: account context saved for this run ({counts}). The full response is in "
                    f"runs/captured/{record['tool_use_id']}.json; do not save it yourself. Run run_capture.py absorb --run "
                    f".pipedrive-sales/runs/{run} --context next: it prints the saved file for account_context.py and workflow.py --context.")
        data = response.get("data")
        count = len(data) if isinstance(data, list) else 1 if isinstance(data, dict) else 0
        extra = response.get("additional_data") or {}
        more = bool(extra.get("next_cursor") or (extra.get("pagination") or {}).get("more_items_in_collection"))
        return (f"Pipedrive {record['tool']} ({_selector(record['params'])}): {count} {'row' if count == 1 else 'rows'} saved for this run, "
                f"{'more pages remain' if more else 'no further page'}. The full response is in runs/captured/{record['tool_use_id']}.json "
                "for the brief's scripts; do not save it yourself. Run plan next.")
    return None


def record_current_user(runs, record):
    """Save the signed-in user's id from a getAccountContext answer to runs/current-user.json (0.7.21, review 2.5), owner-only,
    so pdwrite.py and the write guard take the user from Pipedrive's answer and not from an id the chat typed."""
    if record["tool"] not in ("getAccountContext", "get_account_context"):
        return
    response = record.get("response")
    if isinstance(response, dict) and isinstance(response.get("data"), dict):
        response = response["data"]
    user = response.get("current_user") if isinstance(response, dict) else None
    if not isinstance(user, dict) or type(user.get("id")) is not int or user["id"] <= 0:
        return
    saved = {"current_user_id": user["id"], "session_id": record.get("session_id"), "tool_use_id": record["tool_use_id"],
             "observed_at": record["observed_at"], "turn": record.get("turn")}
    try:
        handle, tmp = tempfile.mkstemp(dir=runs, prefix=".current-user.")
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(json.dumps(saved))
        os.chmod(tmp, 0o600)
        os.replace(tmp, runs / "current-user.json")
    except OSError:
        return


def page_mismatch(runs, tool, params):
    """A continuation whose cursor or offset is not the one run_capture.py absorb saved for this tool (0.7.14, decision 2A):
    the chat typed it instead of copying it. A first page (no cursor, start 0) is never a mismatch; so is another tool."""
    try:
        saved = json.loads((runs / "next-page-params.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(saved, dict) or saved.get("tool") != tool:
        return None
    expected = saved.get("params") or {}
    params = params or {}
    if params.get("cursor") is not None and "cursor" in expected and str(params["cursor"]) != str(expected["cursor"]):
        return {"expected_cursor": expected["cursor"], "sent_cursor": params["cursor"]}
    if params.get("start") not in (None, 0) and "start" in expected and str(params["start"]) != str(expected["start"]):
        return {"expected_start": expected["start"], "sent_start": params["start"]}
    return None


def main():
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    match = TOOL.fullmatch(str(event.get("tool_name") or ""))
    call_id = re.sub(r"[^A-Za-z0-9_-]", "", str(event.get("tool_use_id") or ""))
    if not match or not call_id:
        return 0
    text = _text(event.get("tool_response"))
    server = (event.get("mcp_server") or {}).get("name") if isinstance(event.get("mcp_server"), dict) else None
    record = {"tool": match.group("tool"), "server": server or match.group("server"), "params": event.get("tool_input") or {},
              "response": _parse(text),
              "tool_use_id": call_id, "session_id": event.get("session_id"),
              "observed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z", "capture": "hook",
              "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}
    if record["response"] is None:
        record["raw_text"] = text
        pointer = POINTER.search(text)
        if pointer:
            record["pointer_path"] = pointer.group(1)
            parsed, body, why = follow_pointer(pointer.group(1))
            if parsed is not None:
                record.update({"response": parsed, "capture": "hook_file", "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
                               "pointer_bytes": len(body.encode("utf-8"))})
                del record["raw_text"]
            else:
                record.update({"pointer_unreadable": True, "pointer_error": why})
    folder = Path(os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or os.getcwd()) / ".pipedrive-sales" / "runs" / "captured"
    if not folder.parent.is_dir():
        # No skill run has started in this folder (run_capture.py start, or a message naming a skill, creates runs/): a read
        # made outside the skills is not saved anywhere (0.7.20, review 2.2). The write guard follows the same rule.
        return 0
    try:  # the turn hooks/mark_turn.py recorded, so a zip shows which read ran in which turn (0.7.18, decision 3A)
        record["turn"] = json.loads((folder.parent / "turn.json").read_text(encoding="utf-8")).get("turn")
    except (OSError, ValueError, AttributeError):
        record["turn"] = None
    mismatch = page_mismatch(folder.parent, record["tool"], record["params"])
    if mismatch:
        record["page_mismatch"] = mismatch
    try:
        # Owner-only files and folder: the captures are CRM data in the rep's working folder (0.7.20, review 2.2).
        folder.mkdir(mode=0o700, exist_ok=True)
        os.chmod(folder, 0o700)
        handle, tmp = tempfile.mkstemp(dir=folder, prefix=f".{call_id}.")
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(json.dumps(record, ensure_ascii=False))
        os.replace(tmp, folder / f"{call_id}.json")
        prune(folder)
    except OSError:
        return 0
    record_current_user(folder.parent, record)
    text = receipt(folder.parent, record, event.get("session_id"))
    if text:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": text}}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # a capture failure must never fail the Pipedrive call; the skill falls back to saving it
        sys.exit(0)
