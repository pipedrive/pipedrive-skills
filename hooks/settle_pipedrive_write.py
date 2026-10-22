"""PostToolUse hook: a Pipedrive write that succeeded consumes its prepared action (0.7.21, review 2.4).

The PreToolUse guard (guard_pipedrive_write.py) lets a prepared write through twice in a turn: the call and the one retry
the skills allow after a failed call. Before 0.7.21 nothing told the retry from a repeat of a write that had already
succeeded, so a second identical call created a duplicate record. This hook runs after every write tool call the guard
matches, reads the connector's answer, and when it reports a created or updated record (`success` true with a record
id, as write_rules.record_state reads it) marks the ledger entry `consumed` with the time, the call id and the record id;
the guard then denies any further call on that entry as `already_written`. A failed or unreadable answer leaves the entry
alone, so the retry stays. It never blocks anything, writes only where the runs folder exists, logs one `settle` line to
runs/write-hook.log, and always exits 0. Standard library only; the write rules come from the same shipped copy the
guard loads.
"""

from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import tempfile

# hooks/guard_pipedrive_write.WRITE_TOOLS; tests/test_release_0_7_21_hook_whitelist.py fails when the two differ.
WRITE_TOOLS = ("addActivity", "addDeal", "addDealProduct", "addLead", "addNote", "addOrganization", "addPerson",
               "addProduct", "addProductVariation", "addProject", "convertLeadToDeal", "updateActivity",
               "updateDeal", "updateDealProduct", "updateLead", "updateMailThreadDetails", "updateNote",
               "updateOrganization", "updatePerson", "updateProduct", "updateProductVariation", "updateProject")
TOOL = re.compile(r"mcp__(?P<server>.+)__(?P<tool>" + "|".join(WRITE_TOOLS) + r")")
PREPARED, LOG = "prepared-actions.json", "write-hook.log"
RULES_PATHS = ("skills/deal-triage/scripts/write_rules.py", "shared/write_rules.py")


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


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


def _plain(value):
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return value


def _write_json(path, data):
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(data, out, indent=1, ensure_ascii=False)
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


def _turn(runs):
    try:
        value = json.loads((runs / "turn.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value.get("turn") if isinstance(value, dict) else None


def _this_turn(entry, turn):
    return turn is None or entry.get("turn") is None or entry.get("turn") == turn


def record_state(response):
    """write_rules.record_state through the shipped copy; uncertain (nothing marked) when the module cannot be loaded."""
    root = Path(__file__).resolve().parents[1]
    for relative in RULES_PATHS:
        path = root / relative
        if not path.is_file():
            continue
        try:
            spec = importlib.util.spec_from_file_location("pipedrive_sales_write_rules", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module.record_state(response)
        except Exception:  # noqa: BLE001 — without the rules nothing is marked, and the guard's two-call rule stands
            continue
    return {"status": "uncertain"}


def settle(runs, tool, params, call_id, turn, status):
    """Mark the entry this call used as consumed when the write succeeded; (item, record id) or (None, None)."""
    path = runs / PREPARED
    if not path.exists():
        return None, None
    entries = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(entries, list):
        return None, None
    wanted = _plain(params)
    candidates = [entry for entry in entries if isinstance(entry, dict) and entry.get("tool") == tool and _this_turn(entry, turn)]
    by_call = [entry for entry in candidates if call_id and any(use.get("tool_use_id") == call_id for use in entry.get("uses") or [] if isinstance(use, dict))]
    by_params = [entry for entry in candidates if entry.get("uses") and _plain(entry.get("params") or {}) == wanted]
    entry = next(iter(by_call or by_params), None)
    if entry is None:
        return None, None
    if status.get("status") == "written" and not isinstance(entry.get("consumed"), dict):
        entry["consumed"] = {"at": _now(), "tool_use_id": call_id or None, "id": status.get("id")}
        _write_json(path, entries)
    return entry.get("item"), status.get("id")


def main():
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    if not isinstance(event, dict):
        return 0
    match = TOOL.fullmatch(str(event.get("tool_name") or ""))
    if not match:
        return 0
    runs = Path(os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or os.getcwd()) / ".pipedrive-sales" / "runs"
    if not runs.is_dir():
        return 0
    tool, server = match.group("tool"), match.group("server")
    params = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
    call_id = re.sub(r"[^A-Za-z0-9_-]", "", str(event.get("tool_use_id") or ""))
    turn = _turn(runs)
    status = record_state(_parse(_text(event.get("tool_response"))))
    item, record_id, error = None, None, None
    try:
        item, record_id = settle(runs, tool, params, call_id, turn, status)
    except Exception as failure:  # noqa: BLE001 — an entry that stays unconsumed keeps the guard's two-call rule
        error = f"{type(failure).__name__}: {failure}"
    line = {"at": _now(), "turn": turn, "tool": tool, "server": server, "decision": "settle", "why": "settle",
            "status": status.get("status"), "item": item, "record_id": record_id, "tool_use_id": call_id or None, "error": error}
    try:
        with (runs / LOG).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # a settle failure must never fail the write call
        sys.exit(0)
