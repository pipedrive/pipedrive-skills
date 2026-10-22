"""Turn plugin-hook captures in runs/captured/ into ordinary retained calls of one run (ADR-030).

The plugin hook saves every Pipedrive read to runs/captured/<tool_use_id>.json (Claude Code, Cowork). A run that
carries started_at takes the usable captures observed since then; each becomes a response_file call under
runs/responses/, exactly like a model-saved response, so reuse, archive and the write guard read it unchanged.
A run without started_at takes nothing. Where the hook does not run (claude.ai chat) there are no captures and the
model keeps saving responses itself.

Since 0.7.14 (decision 2A, ADR-030 note) absorb also hands over the next page: it saves the parameters of the next read
of the run's unfinished page chain to runs/next-page-params.json and prints them, so the chat copies them instead of
retyping a cursor it remembered (0.7.13 run (c): a retyped cursor, one wasted call, the run record edited by hand to
hide it). The capture hook flags a page whose cursor or offset is not the saved one; absorb keeps such a page out of
the run (excluded_calls) and says so. `exclude` records any other call to leave out, so the run record is never edited
by hand.
"""

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import uuid

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

from read_plan import observation_time, streams
from response_capture import retain_response

# Setup discovery keeps its own run (account-context-guide.md); report runs never take its context reads.
CONTEXT_TOOLS = frozenset({"getAccountContext", "get_account_context"})
REPORT_TOOLS = frozenset({
    "getActivities", "getActivity", "getDeal", "getDeals", "getDealProducts", "getDealsProducts", "getLead",
    "getLeadConversionStatus", "getLeads", "getNote", "getNotes", "getOrganization", "getOrganizations", "getPerson",
    "getPersons", "getPersonProducts", "getProduct", "getProductDeals", "getProductFiles", "getProductImage", "getProducts",
    "getProductVariations", "getStage", "getStages", "searchDeals", "searchLeads", "searchOrganization", "searchPersons",
    "searchProducts"})
RETENTION_DAYS = 7


def timestamp(now=None):
    """UTC with milliseconds, the hook's format: same-second reads still order correctly."""
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def stamp(run, now=None):
    """Give a run with no reads yet its start time; an existing start is never moved."""
    if not run.get("started_at") and not run.get("calls"):
        run["started_at"] = timestamp(now)
    return run


def prune(folder, now=None, days=RETENTION_DAYS):
    """Delete captures observed more than `days` ago (file time when the record has none). Captures hold CRM data."""
    folder, cutoff = Path(folder), (now or datetime.now(timezone.utc)) - timedelta(days=days)
    if not folder.is_dir():
        return 0
    removed = 0
    for path in folder.glob("*.json"):
        try:
            observed = observation_time(json.loads(path.read_text(encoding="utf-8")).get("observed_at"))
        except (OSError, ValueError, AttributeError):
            observed = None
        try:
            if observed is None:
                observed = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
            if observed < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def start(run_path, today=None, now=None):
    """Create or stamp the run file before its first read, and prune old captures beside it."""
    run_path = Path(run_path)
    run = json.loads(run_path.read_text(encoding="utf-8")) if run_path.exists() else {}
    if today and "today" not in run:
        run["today"] = today
    run.setdefault("calls", [])
    stamp(run, now)
    run_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = run_path.with_suffix(run_path.suffix + ".tmp")
    temporary.write_text(json.dumps(run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(run_path)
    prune(run_path.parent / "captured", now)
    return run


def _plain(value):
    """Numbers as text, so a hand copy's {"id": "42"} and the hook's {"id": 42} are the same read."""
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return value


plain = _plain


def read_key(tool, params):
    """One read's identity, shared with the capture hook's copy: bare tool name and parameters, numbers as text."""
    return json.dumps(_plain([str(tool).split("__")[-1], params or {}]), sort_keys=True, ensure_ascii=False)


PENDING = "pending-reads.json"


def issue(run_path, reads, session=None, now=None):
    """List the reads `plan` just issued, beside the run, so the capture hook answers them with a receipt (ADR-032).
    Earlier claims are cleared: each new list starts its own one-receipt-per-read count."""
    folder = Path(run_path).parent
    marker = {"id": uuid.uuid4().hex, "run": Path(run_path).name, "session_id": session, "issued_at": timestamp(now),
              "reads": [{"tool": read["tool"], "key": read_key(read["tool"], read["params"])} for read in reads]}
    folder.mkdir(parents=True, exist_ok=True)
    temporary = folder / (PENDING + ".tmp")
    temporary.write_text(json.dumps(marker, ensure_ascii=False), encoding="utf-8")
    temporary.replace(folder / PENDING)
    for claim in (folder / "receipts").glob("*"):
        if not claim.name.startswith(marker["id"]):
            claim.unlink(missing_ok=True)
    return marker


def confirmed_profile(settings_path, skill):
    """A version-2 profile this skill can load. Its run needs no names from the context response, so that read can
    be a receipt (ADR-032 amendment); setup without one keeps the full response, because its questions use the names."""
    try:
        from sales_settings import load_effective_profile
        settings = json.loads(Path(settings_path).read_text(encoding="utf-8"))
        return settings.get("schema_version") == 2 and bool(load_effective_profile(settings, skill))
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return False


def withdraw(run_path):
    """No list, no receipts: every response stays visible."""
    (Path(run_path).parent / PENDING).unlink(missing_ok=True)


def _key(tool, params, response):
    return json.dumps(_plain([str(tool).split("__")[-1], params or {}, response]), sort_keys=True, ensure_ascii=False)


def _saved_response(call, directory):
    if "response" in call:
        return call["response"]
    try:
        folder = Path(directory).resolve()
        source = (folder / call["response_file"]).resolve()
        if not source.is_relative_to(folder):
            return None
        return json.loads(source.read_text(encoding="utf-8"))
    except (KeyError, OSError, TypeError, ValueError):
        return None


def _set_session(run_path, session):
    run = json.loads(run_path.read_text(encoding="utf-8"))
    run["capture_session_id"] = session
    temporary = run_path.with_suffix(run_path.suffix + ".tmp")
    temporary.write_text(json.dumps(run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(run_path)


def _move_last_into(run_path, slot):
    run = json.loads(run_path.read_text(encoding="utf-8"))
    run["calls"][slot] = run["calls"].pop()
    temporary = run_path.with_suffix(run_path.suffix + ".tmp")
    temporary.write_text(json.dumps(run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(run_path)


def retain_failure(run_path, tool, params, error, observed_at=None, tool_use_id=None, pointer_path=None):
    """Append a read that returned no usable body, so read_plan.streams marks it failed and the report names it."""
    run_path = Path(run_path).resolve()
    run = json.loads(run_path.read_text(encoding="utf-8"))
    call = {"tool": tool, "params": params, "response": None, "error": error, "capture_kind": "hook_pointer_unreadable"}
    if observed_at:
        call["observed_at"] = observed_at
    if tool_use_id:
        call["tool_use_id"] = tool_use_id
    if pointer_path:
        call["pointer_path"] = pointer_path
    run.setdefault("calls", []).append(call)
    temporary = run_path.with_suffix(run_path.suffix + ".tmp")
    temporary.write_text(json.dumps(run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(run_path)
    return call


NEXT_PAGE = "next-page-params.json"
MISMATCH_REASON = "its cursor or offset was not the saved next page (retyped?)"


def _write_run(run_path, run):
    temporary = run_path.with_suffix(run_path.suffix + ".tmp")
    temporary.write_text(json.dumps(run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(run_path)


def next_page(run_path):
    """The parameters of the next page of the run's most recent unfinished read, saved to runs/next-page-params.json
    (removed when there is none). Returns the record or None."""
    run_path = Path(run_path).resolve()
    run = json.loads(run_path.read_text(encoding="utf-8"))
    # A failed page (a pointer the hook could not read, an error) is retried with the same cursor: the continuation comes
    # from the good pages alone.
    good = [call for call in run.get("calls") or [] if not call.get("error") and call.get("response") is not None
            or call.get("response_file")]
    states = streams({**run, "calls": good}, run_path.parent)
    record = None
    for call in reversed(good):
        tool = str(call.get("tool") or "").split("__")[-1]
        params = call.get("params") or {}
        state = next((entry for entry in states.values()
                      if entry["tool"] == tool and {k: v for k, v in entry["params"].items() if k not in ("cursor", "start")}
                      == {k: v for k, v in params.items() if k not in ("cursor", "start")}), None)
        if state is None or state.get("failed") or state.get("invalid_continuation"):
            continue
        if state["next_cursor"] is None and state["next_start"] is None:
            break
        following = {k: v for k, v in params.items() if k not in ("cursor", "start")}
        if state["next_cursor"] is not None:
            following["cursor"] = state["next_cursor"]
        else:
            following["start"] = state["next_start"]
        record = {"tool": tool, "params": following, "after_tool_use_id": call.get("tool_use_id"), "pages_so_far": state["pages"],
                  "written_at": timestamp()}
        break
    path = run_path.parent / NEXT_PAGE
    if record is None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return None
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return record


def next_page_lines(record, run_path):
    if record is None:
        return ["no further page: the run's last read has no continuation; make no read with a cursor you typed yourself"]
    return [f"next page: call {record['tool']} with exactly these parameters, copied from the next line "
            f"(saved in {Path(run_path).parent / NEXT_PAGE}); never a cursor from memory or from a response file:",
            json.dumps(record["params"], ensure_ascii=False, sort_keys=True)]


def exclude(run_path, tool_use_id, reason):
    """Move one retained call out of the run into excluded_calls, with the reason; the scripts read calls only."""
    run_path = Path(run_path).resolve()
    run = json.loads(run_path.read_text(encoding="utf-8"))
    calls = run.get("calls") or []
    index = next((i for i, call in enumerate(calls) if call.get("tool_use_id") == tool_use_id), None)
    if index is None:
        return None
    call = calls.pop(index)
    call.update({"excluded_reason": reason, "excluded_at": timestamp()})
    run.setdefault("excluded_calls", []).append(call)
    _write_run(run_path, run)
    return call


def absorb(run_path, since=None, tools=None, match=None, excluded=None):
    """Retain usable captures observed at or after `since` (default: the run's started_at). Returns how many.

    `tools` defaults to the report reads; `match`, when given, must accept the capture record. A capture replaces a hand-saved call with the same tool and parameters in
    place: the capture holds the exact response, a hand copy may have been trimmed (Cowork, 8 Oct; PR #77).
    """
    run_path = Path(run_path)
    run = json.loads(run_path.read_text(encoding="utf-8"))
    after = observation_time(since if since is not None else run.get("started_at"))
    folder = run_path.parent / "captured"
    if after is None or not folder.is_dir():
        return 0
    allowed = REPORT_TOOLS if tools is None else (REPORT_TOOLS | CONTEXT_TOOLS) & set(tools)
    calls = run.get("calls") or []
    ids = {call.get("tool_use_id") for call in calls if call.get("tool_use_id")}
    seen = {_key(call.get("tool"), call.get("params"), _saved_response(call, run_path.parent)) for call in calls}
    hand = {_key(call.get("tool"), call.get("params"), None): index for index, call in enumerate(calls)
            if not str(call.get("capture_kind") or "").startswith("hook") and not call.get("reused_from")}
    usable = []
    for path in folder.glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        observed = observation_time(record.get("observed_at")) if isinstance(record, dict) else None
        # An oversized result whose host file the hook could not read (0.7.12) is a failed read the run must name.
        unreadable = isinstance(record, dict) and record.get("pointer_unreadable") is True
        if (observed is None or observed < after or record.get("tool") not in allowed
                or (not isinstance(record.get("response"), dict) and not unreadable) or not record.get("tool_use_id")
                or (match is not None and not match(record))):
            continue
        usable.append((observed, record))
    # A run keeps to the Claude session of its first capture: another session in the same folder must not feed it.
    session = run.get("capture_session_id")
    taken = 0
    for _, record in sorted(usable, key=lambda item: (item[0], item[1]["tool_use_id"])):
        key = _key(record["tool"], record.get("params"), record["response"])
        if record["tool_use_id"] in ids or key in seen:
            continue
        if session and record.get("session_id") != session:
            continue
        if not session and record.get("session_id"):
            session = record["session_id"]
            _set_session(run_path, session)
        if record.get("pointer_unreadable") is True:
            retain_failure(run_path, record["tool"], record.get("params") or {},
                           f"result too large for the chat; the host's copy could not be read ({record.get('pointer_error')})",
                           observed_at=record["observed_at"], tool_use_id=record["tool_use_id"], pointer_path=record.get("pointer_path"))
        else:
            raw = json.dumps(record["response"], ensure_ascii=False).encode("utf-8")
            retain_response(run_path, raw, record["tool"], record.get("params") or {}, observed_at=record["observed_at"],
                            tool_use_id=record["tool_use_id"], capture_kind=record.get("capture") or "hook", source_sha256=record.get("sha256"))
        slot = hand.pop(_key(record["tool"], record.get("params"), None), None)
        if slot is not None:
            _move_last_into(run_path, slot)
        if record.get("page_mismatch"):
            # The hook saw a cursor or offset that was not the saved next page (0.7.14): the page stays out of the run.
            moved = exclude(run_path, record["tool_use_id"], MISMATCH_REASON)
            if moved is not None and excluded is not None:
                excluded.append(moved)
        ids.add(record["tool_use_id"])
        seen.add(key)
        taken += 1
    return taken


def report(count):
    if count:
        return f"captured automatically: {count} response{'s' if count != 1 else ''} saved by the plugin; do not copy them"
    return "captured automatically: 0 responses; save any response this run still lists as missing"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    begin = commands.add_parser("start", help="create or stamp the run file before its first read")
    begin.add_argument("--run", type=Path, required=True)
    begin.add_argument("--today")
    begin.add_argument("--settings", type=Path, help="setup run: the saved settings, to check for a confirmed profile")
    begin.add_argument("--skill", choices=("pipeline-brief", "deal-triage", "forecast"))
    take = commands.add_parser("absorb", help="retain this run's hook captures")
    take.add_argument("--run", type=Path, required=True)
    take.add_argument("--since")
    take.add_argument("--context", action="store_true",
                      help="setup discovery run: take only account-context captures and print the saved response path")
    leave = commands.add_parser("exclude", help="record a retained call to leave out of the run; never edit the run file by hand")
    leave.add_argument("--run", type=Path, required=True)
    leave.add_argument("--tool-use-id", required=True)
    leave.add_argument("--reason", required=True)
    args = parser.parse_args(argv)
    if args.command == "exclude":
        moved = exclude(args.run, args.tool_use_id, args.reason)
        if moved is None:
            print(f"no retained call {args.tool_use_id} in {args.run}")
            return 1
        print(f"excluded: {moved['tool']} call {args.tool_use_id} ({args.reason}); the run keeps {len(json.loads(args.run.read_text(encoding='utf-8')).get('calls') or [])} calls")
        for line in next_page_lines(next_page(args.run), args.run):
            print(line)
        return 0
    if args.command == "start":
        run = start(args.run, args.today)
        print(f"run started {run['started_at']}: {args.run}")
        if args.settings and args.skill and confirmed_profile(args.settings, args.skill):
            issue(args.run, [{"tool": tool, "params": {}} for tool in sorted(CONTEXT_TOOLS)])
            print("confirmed profile: the account-context read returns a receipt; the scripts read the saved copy")
    elif args.context:
        print(report(absorb(args.run, args.since, tools=CONTEXT_TOOLS)))
        run = json.loads(args.run.read_text(encoding="utf-8"))
        for call in run.get("calls") or []:
            if str(call.get("tool")).split("__")[-1] in CONTEXT_TOOLS and call.get("response_file"):
                print(f"context response: {args.run.parent / call['response_file']}")
    else:
        left_out = []
        print(report(absorb(args.run, args.since, excluded=left_out)))
        for call in left_out:
            print(f"excluded: {call['tool']} call {call.get('tool_use_id')}, {MISMATCH_REASON}; it is not in the run")
        for line in next_page_lines(next_page(args.run), args.run):
            print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
