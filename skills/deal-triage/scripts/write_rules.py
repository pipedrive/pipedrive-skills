"""Shared write rules for the writing skills (ADR-019): whitelist, stale guard, dedupe key.

Three deterministic checks that every Pipedrive write passes through, so the rules live once
instead of as prose in three SKILL.md files:

  validate(skill, tool, params)   parameter whitelist per skill and tool; returns violations
  stale_guard(...)                fresh-read check immediately before a write (from
                                  deal-triage's write_preflight.py, generalised)
  dedupe_key(kind, **parts)       the key that marks a meeting or evidence event as logged

Standard library only. This module never fetches and never writes; it only says whether a
proposed write may be sent and with exactly which parameters.

    python3 scripts/write_rules.py validate --skill deal-triage --tool updateDeal --params p.json
    python3 scripts/write_rules.py guard --proposal p.json --record-response deal.json \
        --activities-response acts.json --today 2026-09-29
"""

import argparse
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile

ACTIVITY_FIELDS = {"type", "subject", "due_date", "due_time", "duration", "done", "note"}

# The one deal-triage whitelist (ADR-038). Its source is the target table in
# specs/wave1-skill-deal-triage.md §13.1 C-DT-M16; tests/test_triage_whitelist.py parses that table and fails on any
# difference. pdwrite.WHITELIST derives from this structure. "forbidden" lists the fields named in the table; any
# other parameter is refused as well. "groups": updateActivity takes id plus the fields of exactly one group.
# "reviewed_append": allowed only through pdwrite's append_preparation action. "current_user": allowed only when
# equal to the current user.
TRIAGE = {
    "addActivity": {"link": ("deal_id", "lead_id"), "required": {"type", "subject", "due_date"},
                    "allowed": {"deal_id", "lead_id", "type", "subject", "due_date", "due_time", "note", "participants", "user_id"},
                    "current_user": {"user_id"},
                    "forbidden": {"owner_id", "outcome", "priority", "project_id", "public_description", "location", "busy",
                                  "attendees", "org_id", "duration", "done"}},
    "addNote": {"link": ("deal_id", "lead_id"), "required": {"content"}, "allowed": {"deal_id", "lead_id", "content"},
                "forbidden": {"user_id", "add_time", "project_id", "task_id"}},
    "updateActivity": {"required": {"id"}, "allowed": {"id", "due_date", "due_time", "done", "participants", "note"},
                       "groups": ({"due_date", "due_time"}, {"done"}, {"participants"}, {"note"}), "reviewed_append": {"note"},
                       "forbidden": {"owner_id", "subject", "type", "outcome", "priority", "project_id", "public_description",
                                     "location", "busy", "attendees"}},
    "updateDeal": {"required": {"id"}, "exactly_one": {"status", "stage_id", "expected_close_date", "value", "label_ids"},
                   "allowed": {"id", "status", "stage_id", "expected_close_date", "value", "label_ids"},
                   "values": {"status": {"won"}}, "forbidden": set()},
}

WHITELIST = {
    "deal-triage": TRIAGE,
    "meeting-follow-up": {
        "addNote": {"link": ("deal_id", "lead_id", "person_id", "org_id"), "required": {"content"},
                    "allowed": {"deal_id", "lead_id", "person_id", "org_id", "content"}},
        "updateActivity": {"required": {"id"}, "allowed": {"id", "done", "note"}},
        # outcome is never written until a decision (S13); owner_id is never set: no cross-owner writes (ADR-039 d.3).
        "addActivity": {"link": ("deal_id", "lead_id"), "required": {"type", "subject", "due_date"},
                        "allowed": {"deal_id", "lead_id", "org_id", "participants", "attendees"} | ACTIVITY_FIELDS,
                        "forbidden": {"outcome", "owner_id"}},
    },
    # sales-outreach is read-only on purpose (ADR-020): an empty entry, so validate() names it and refuses every tool.
    # deal-progression (spec-only, never built) had an entry here until 0.7.21 (review 3.8): a skill that does not exist
    # has no whitelist; its rules stay in specs/wave2-skill-deal-progression.md for the day it is built.
    "sales-outreach": {},
}


# The parameter names the Pipedrive MCP's write tools accept: the plugin's own copy (0.7.17, decision 2A, ADR-044 note),
# taken from the connector's tool list as the eval suite captured it (evals/mocks/pipedrive/_tools.json;
# tests/test_release_0_7_17_phase1.py fails on any difference). pdwrite.prepare checks a write against this list, never
# against a tools.json the chat wrote: in the 0.7.16 release run (b) the chat's file lacked updateActivity and the chat
# added the entry by hand before the preflight.
WRITE_TOOL_PARAMETERS = {
    "addActivity": ["attendees", "busy", "deal_id", "done", "due_date", "due_time", "duration", "lead_id", "location", "note", "org_id", "outcome", "owner_id", "participants", "priority", "project_id", "public_description", "subject", "type"],
    "addNote": ["add_time", "content", "deal_id", "lead_id", "org_id", "person_id", "pinned_to_deal_flag", "pinned_to_lead_flag", "pinned_to_organization_flag", "pinned_to_person_flag", "pinned_to_project_flag", "pinned_to_task_flag", "project_id", "task_id", "user_id"],
    "updateActivity": ["attendees", "busy", "deal_id", "done", "due_date", "due_time", "duration", "id", "lead_id", "location", "note", "org_id", "outcome", "owner_id", "participants", "priority", "project_id", "public_description", "subject", "type"],
    "updateDeal": ["archive_time", "close_time", "currency", "custom_fields", "expected_close_date", "id", "is_archived", "is_deleted", "label_ids", "lost_reason", "lost_time", "org_id", "owner_id", "person_id", "pipeline_id", "probability", "stage_id", "status", "title", "value", "visible_to", "won_time"],
}


def validate(skill, tool, params, current_user_id=None, reviewed_append=False):
    """Return a list of violations; an empty list means the write may be proposed.

    current_user_id and reviewed_append only matter for rules that name them: a "current_user" parameter passes
    only when it equals current_user_id, and a "reviewed_append" parameter only when reviewed_append is true.
    """
    tools = WHITELIST.get(skill)
    if tools is None:
        return [f"{skill} has no write whitelist; it may not call {tool}"]
    rule = tools.get(tool)
    if rule is None:
        return [f"{tool} is not a write tool {skill} may call"]
    params = params or {}
    out = []
    for key in sorted(set(params) - rule["allowed"]):
        out.append(f"{tool}: parameter '{key}' is {'forbidden' if key in rule.get('forbidden', ()) else 'not allowed'} for {skill}")
    for key in sorted(rule.get("current_user", set()) & set(params)):
        if current_user_id is None or params[key] != current_user_id:
            out.append(f"{tool}: '{key}' must be the current user; cross-owner writes are refused for {skill}")
    for key in sorted(rule.get("reviewed_append", set()) & set(params)):
        if not reviewed_append:
            out.append(f"{tool}: '{key}' may only be written by the reviewed append action for {skill}")
    groups = rule.get("groups")
    if groups:
        changed = (set(params) & rule["allowed"]) - {"id"}
        if not any(changed and changed <= group for group in groups):
            shown = " | ".join("{" + ", ".join(sorted(group)) + "}" for group in groups)
            out.append(f"{tool} takes id plus the fields of exactly one group: {shown}")
    for key in sorted(rule.get("required", set()) - set(params)):
        out.append(f"{tool}: required parameter '{key}' is missing")
    link = rule.get("link")
    if link and not any(params.get(k) is not None for k in link):
        out.append(f"{tool} needs one of {', '.join(link)}; an unlinked record is invisible in Pipedrive")
    exactly = rule.get("exactly_one")
    if exactly:
        present = sorted(k for k in exactly if k in params)
        if len(present) != 1:
            out.append(f"{tool} takes id plus exactly one of {', '.join(sorted(exactly))}; got {present or 'none'}")
    for key, value in rule.get("fixed", {}).items():
        if key in params and params[key] != value:
            out.append(f"{tool}: '{key}' must be {value!r} for {skill}")
    for key, values in rule.get("values", {}).items():
        if key in params and params[key] not in values:
            out.append(f"{tool}: '{key}' must be {' or '.join(sorted(map(repr, values)))} for {skill}")
    if tool == "updateDeal" and "lost_reason" in rule["allowed"]:
        if params.get("status") == "lost" and not params.get("lost_reason"):
            out.append("updateDeal: status 'lost' needs a lost_reason from the user or the account's own reasons")
        if "lost_reason" in params and params.get("status") != "lost":
            out.append("updateDeal: lost_reason is only allowed together with status 'lost'")
    return out


def unwrap_capture(value):
    """Return the raw response of a hook capture file; any other value comes back unchanged (ADR-030).

    hooks/capture_pipedrive_response.py saves a read as {tool, server, params, response, capture: "hook", ...}. A capture whose
    response did not parse holds None there, which the fresh-read rules below refuse like any other failed read.
    """
    if isinstance(value, dict) and value.get("capture") == "hook" and isinstance(value.get("tool"), str) and "response" in value:
        return value["response"]
    return value


def _fresh_record(response, expected_id, kind):
    if not isinstance(response, dict) or response.get("success") is not True or not isinstance(response.get("data"), dict):
        raise ValueError(f"Fresh {kind} read must be a successful response object")
    record = response["data"]
    if record.get("id") != expected_id or record.get("is_deleted") is True or record.get("is_archived") is True:
        raise ValueError(f"{kind.capitalize()} changed or could not be verified")
    return record


def _complete_activities(response):
    activities = response.get("data") if isinstance(response, dict) else None
    pagination = response.get("additional_data") or {} if isinstance(response, dict) else {}
    if (not isinstance(response, dict) or response.get("success") is not True or not isinstance(activities, list)
            or not isinstance(pagination, dict) or pagination.get("next_cursor")
            or not isinstance(pagination.get("pagination") or {}, dict)
            or (pagination.get("pagination") or {}).get("more_items_in_collection")):
        raise ValueError("Activity read is failed or incomplete; finish the fresh read before writing")
    return activities


def _guard_deal_update(proposal, record_response):
    deal = _fresh_record(record_response, proposal.get("deal_id", (proposal.get("params") or {}).get("id")), "deal")
    if deal.get("status") != "open":
        raise ValueError(f"Deal changed since it was read: status is now {deal.get('status')!r}; nothing written")
    for key, shown in (proposal.get("expected") or {}).items():
        if deal.get(key) != shown:
            raise ValueError(f"Deal changed since it was read: {key} is now {deal.get(key)!r}, was {shown!r}; nothing written")
    return {"tool": "updateDeal", "params": dict(proposal["params"])}


def stale_guard(proposal, record_response, activity_response, today, execution=None, closed_in_batch=(), meeting_steps=False):
    """Re-check a confirmed proposal against fresh reads; return the exact write, or raise ValueError.

    Activity proposals (addActivity, updateActivity) keep write_preflight.py's rules: the linked
    deal must still be open (or the lead unarchived), the activity read must be complete, the due
    date must not have rolled into the past, and no equivalent open activity may already cover
    the next step. updateDeal proposals compare every field in proposal["expected"] with the fresh
    deal and abort on any difference.

    closed_in_batch holds the activity ids this batch has already marked done with a written
    updateActivity. A next-step proposal whose closes_activity_id is in that list ignores that
    one open activity; a refused or skipped closing item leaves it in the snapshot as open.

    meeting_steps: the rep named these next steps in a meeting, so only an equivalent open activity
    (same subject and type) refuses one. Another open activity due soon does not: that rule keeps
    deal-triage from proposing a follow-up the rep already planned.
    """
    if (execution or {}).get("status") == "written":
        raise ValueError("Item is already written; do not create another activity")
    if isinstance(proposal.get("params"), dict) and proposal.get("tool") and "action" not in proposal:
        # A saved-result item: {n, tool, params, …}. Unwrap so skills can pass items[n] as is.
        params = proposal["params"]
        proposal = {k: v for k, v in proposal.items() if k not in ("params", "violations", "label", "n")} | params
        proposal["action"] = proposal.pop("tool")
        if proposal["action"] == "updateActivity":
            proposal.setdefault("activity_id", params.get("id"))
            proposal.setdefault("due_date", params.get("due_date"))
    action = proposal.get("action", "addActivity")
    if action == "updateDeal":
        return _guard_deal_update(proposal, record_response)
    if action == "addNote":
        link_key = next((k for k in ("deal_id", "lead_id", "person_id", "org_id") if proposal.get(k) is not None), None)
        if link_key is None:
            raise ValueError("Note has no deal_id, lead_id, person_id or org_id; an unlinked note is invisible")
        record = _fresh_record(record_response, proposal[link_key], link_key[:-3])
        if link_key == "deal_id" and record.get("status") != "open":
            raise ValueError("Deal changed or could not be verified as open")
        return {"tool": "addNote", "params": {link_key: proposal[link_key], "content": proposal.get("content") or ""}}
    if proposal.get("deal_id") is not None:
        link_key, link_id = "deal_id", proposal["deal_id"]
        deal = _fresh_record(record_response, link_id, "deal")
        if deal.get("status") != "open":
            raise ValueError("Deal changed or could not be verified as open")
    elif proposal.get("lead_id") is not None:
        link_key, link_id = "lead_id", proposal["lead_id"]
        _fresh_record(record_response, link_id, "lead")
    else:
        raise ValueError("Proposal has neither deal_id nor lead_id; an unlinked activity is invisible")
    activities = _complete_activities(activity_response)
    today = date.fromisoformat(today)
    due = date.fromisoformat(proposal["due_date"]) if proposal.get("due_date") else None
    if due and due < today and proposal.get("done") is not True:
        raise ValueError("Proposed due date is now in the past; revise and reconfirm")
    matching = []
    for activity in activities:
        if not isinstance(activity, dict) or activity.get(link_key) != link_id:
            continue
        if type(activity.get("done")) is not bool:
            raise ValueError("An activity completion state is unknown")
        if action == "updateActivity" and activity.get("id") == proposal.get("activity_id"):
            matching.append(activity)
            continue
        if activity["done"]:
            continue
        if action == "addActivity" and proposal.get("closes_activity_id") is not None and activity.get("id") == proposal["closes_activity_id"] \
                and activity.get("id") in closed_in_batch:
            continue
        if action == "addActivity" and proposal.get("done") is not True:
            if activity.get("subject") == proposal.get("subject") and activity.get("type") == proposal.get("type"):
                raise ValueError("An equivalent open activity already exists")
            if meeting_steps:
                continue
            try:
                existing_due = date.fromisoformat(str(activity.get("due_date"))[:10])
            except ValueError:
                raise ValueError("An existing open activity has an unknown date")
            if existing_due <= today + timedelta(days=3):
                raise ValueError("An existing open activity already covers the immediate next step")
    if action == "updateActivity":
        if proposal.get("original_due_date") is not None and matching and matching[0].get("due_date") != proposal["original_due_date"]:
            raise ValueError("Target activity due date changed; revise and reconfirm")
        if len(matching) != 1 or matching[0].get("done") is not False:
            raise ValueError("Target activity changed or is not open on this record")
        params = {"id": proposal["activity_id"]}
        if proposal.get("due_date") is not None:
            params["due_date"] = proposal["due_date"]
        for key in ("due_time", "done"):
            if proposal.get(key) is not None:
                params[key] = proposal[key]
        if proposal.get("note"):
            params["note"] = proposal["note"]
        return {"tool": "updateActivity", "params": params}
    params = {link_key: link_id} | {key: proposal[key] for key in ("type", "subject", "due_date")}
    params |= {key: proposal[key] for key in ("org_id", "due_time", "duration", "done", "participants", "attendees")
               if proposal.get(key) is not None}
    note = proposal.get("why") or proposal.get("note")
    if note:
        params["note"] = note  # an empty note field is rejected by the live API; omit it instead
    return {"tool": "addActivity", "params": params}


def _digest(*parts):
    return hashlib.sha1(json.dumps(parts, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:12]


def dedupe_key(kind, **parts):
    """One stable key per logged meeting or evidence event (ADR-019, W1 §5.4, W2 §6.5)."""
    if kind == "pd-activity":
        return f"pd-activity:{parts['activity_id']}"
    if kind == "pasted":
        attendees = sorted(str(a).strip().lower() for a in parts.get("attendees") or [])
        return f"pasted:{_digest(parts.get('date'), attendees, (parts.get('content') or '')[:200])}"
    if kind == "event":
        subject = " ".join(str(parts.get("subject") or "").lower().split())
        return f"event:{_digest(parts.get('deal_id'), str(parts.get('type') or '').lower(), parts.get('date'), subject)}"
    if "source_id" in parts:
        return f"{kind}:{parts['source_id']}"
    raise ValueError(f"unknown dedupe key kind {kind!r}")


def record_state(response):
    """The item state a write's answer shows: failed on an explicit refusal, written with the record id, else uncertain."""
    response = unwrap_capture(response)
    if isinstance(response, dict) and (response.get("success") is False or response.get("error")):
        return {"status": "failed"}
    data = response.get("data") if isinstance(response, dict) else None
    if isinstance(response, dict) and response.get("success") is True and isinstance(data, dict) and data.get("id") is not None:
        return {"status": "written", "id": data["id"]}
    return {"status": "uncertain"}


def _write_json(path, data):
    """Replace a JSON file in one step, keeping its mode; a new file is owner-only (0.7.20, review 2.2: it holds CRM data)."""
    path = Path(path)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(data, out, indent=1, ensure_ascii=False)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


def save_result(result_path, result):
    """Replace a saved result in one step (0.7.12: an edited item's due date and revision)."""
    _write_json(Path(result_path), result)
    return result


def record_action_state(result_path, item, state):
    """Write state under action_states[str(item)] of a saved result, replacing the file in one step."""
    path = Path(result_path)
    result = json.loads(path.read_text(encoding="utf-8"))
    result.setdefault("action_states", {})[str(item)] = state
    _write_json(path, result)
    return state


# ------------------------------------------------------------ turn ledger (0.7.3)
# What each item of the current accept turn said, so the turn's reply can be printed by a script. An entry
# written under an earlier turn id (runs/turn.json) is stale and does not count for this turn.

def _ledger_path(runs_dir):
    return Path(runs_dir) / "confirmations.json"


def _current_turn(runs_dir):
    path = Path(runs_dir) / "turn.json"
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    return value.get("turn") if isinstance(value, dict) else None


def _load_ledger(runs_dir):
    path = _ledger_path(runs_dir)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def record_confirmation(runs_dir, item, status, markdown):
    """Write or replace the ledger entry of one item: its status, the text to show and the turn it belongs to."""
    Path(runs_dir).mkdir(parents=True, exist_ok=True)
    ledger = _load_ledger(runs_dir)
    entry = {"status": status, "markdown": markdown,
             "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "turn": _current_turn(runs_dir)}
    ledger[str(item)] = entry
    _write_json(_ledger_path(runs_dir), ledger)
    return entry


def turn_entries(runs_dir, accepted):
    """The ledger entry of each accepted item number, or None when the item has none for this turn."""
    ledger = _load_ledger(runs_dir)
    turn = _current_turn(runs_dir)
    out = {}
    for item in accepted:
        entry = ledger.get(str(item))
        if entry is not None and turn is not None and entry.get("turn") is not None and entry.get("turn") != turn:
            entry = None
        out[item] = entry
    return out


def clear_confirmations(runs_dir):
    """Delete the ledger when present, with the prepared actions and stopped writes of the same turn (0.7.13)."""
    for path in (_ledger_path(runs_dir), _prepared_path(runs_dir), _stopped_path(runs_dir)):
        if path.exists():
            path.unlink()


# ------------------------------------------------------------ prepared actions (0.7.13, ADR-045)
# A write reaches Pipedrive only when a preflight printed its exact parameters. The preflight saves them here; the plugin's
# PreToolUse hook (hooks/guard_pipedrive_write.py) lets a connector call through only when an unconsumed entry has the same
# tool and the same parameters, and records any other write call in runs/stopped-writes.json so the turn's reply can say so.

PREPARED_FILE = "prepared-actions.json"
STOPPED_FILE = "stopped-writes.json"
STOPPED_LINE = "A write was stopped before it reached Pipedrive: it was not one of this {what}. Nothing was written."
EXHAUSTED_LINE = ("A write for item {item} was sent twice and stopped the third time. Check the record in Pipedrive before "
                  "doing anything else; do not send it again.")
WRITTEN_LINE = ("A write for item {item} was already made this turn and a second send was stopped. Check the record in "
                "Pipedrive; do not send it again.")
MAX_USES = 2  # the call and one retry after a failed call (references/troubleshooting.md), inside the same turn; since
# 0.7.21 (review 2.4) the settle hook marks a written entry consumed, so the retry applies only after a failure
CURRENT_USER_FILE = "current-user.json"


def _prepared_path(runs_dir):
    return Path(runs_dir) / PREPARED_FILE


def _stopped_path(runs_dir):
    return Path(runs_dir) / STOPPED_FILE


def recorded_current_user(runs_dir):
    """The signed-in user's id the capture hook saved from the newest getAccountContext answer (0.7.21, review 2.5);
    None when nothing is recorded. The write hook and the guard command check a user_id against it."""
    try:
        value = json.loads((Path(runs_dir) / CURRENT_USER_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    user = value.get("current_user_id") if isinstance(value, dict) else None
    return user if type(user) is int and user > 0 else None


def _load_list(path):
    if not path.exists():
        return []
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise ValueError(f"{path.name} is not a list")
    return value


def prepared_actions(runs_dir):
    """Every prepared action saved beside the run, consumed or not."""
    return _load_list(_prepared_path(runs_dir))


def record_prepared_action(runs_dir, tool, params, item=None, revision=None, run=None, skill=None):
    """Save the exact write a preflight printed, so the write hook can let that one call through."""
    Path(runs_dir).mkdir(parents=True, exist_ok=True)
    entries = prepared_actions(runs_dir)
    entry = {"tool": str(tool).split("__")[-1], "params": params, "item": item, "revision": revision, "run": run,
             "skill": skill, "prepared_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
             "turn": _current_turn(runs_dir), "uses": []}
    entries.append(entry)
    _write_json(_prepared_path(runs_dir), entries)
    return entry


def stopped_writes(runs_dir):
    """The writes the hook stopped in the current turn (every entry when no turn is recorded)."""
    try:
        entries = _load_list(_stopped_path(runs_dir))
    except (OSError, ValueError):
        return []
    turn = _current_turn(runs_dir)
    return [entry for entry in entries if isinstance(entry, dict)
            and (turn is None or entry.get("turn") is None or entry.get("turn") == turn)]


def stopped_line(runs_dir, what="report's proposals"):
    """The sentence(s) a reply carries when the hook stopped a write this turn, else None: one for writes nothing
    prepared, one per item whose prepared write was sent twice and stopped the third time."""
    stopped = stopped_writes(runs_dir)
    if not stopped:
        return None
    lines = []
    if any(entry.get("why") not in ("exhausted", "already_written") for entry in stopped):
        lines.append(STOPPED_LINE.format(what=what))
    for item in sorted({entry.get("item") for entry in stopped if entry.get("why") == "exhausted"}, key=str):
        lines.append(EXHAUSTED_LINE.format(item=item if item is not None else "?"))
    for item in sorted({entry.get("item") for entry in stopped if entry.get("why") == "already_written"}, key=str):
        lines.append(WRITTEN_LINE.format(item=item if item is not None else "?"))
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    v = sub.add_parser("validate", help="check proposed parameters against the skill's whitelist")
    v.add_argument("--skill", required=True)
    v.add_argument("--tool", required=True)
    v.add_argument("--params", required=True, type=Path, help="JSON file with the parameters as they would be sent")
    r = sub.add_parser("record", help="save a write's answer as the item's state in the saved result")
    r.add_argument("--result", required=True, type=Path, help="the saved result JSON that holds action_states")
    r.add_argument("--item", required=True, type=Path, help="the saved item JSON (its n names the state)")
    r.add_argument("--response", required=True, type=Path, help="the write tool's response, saved unchanged")
    g = sub.add_parser("guard", help="re-check a confirmed proposal against fresh reads and print the exact write")
    g.add_argument("--proposal", required=True, type=Path)
    g.add_argument("--record-response", required=True, type=Path, help="fresh getDeal or getLead response")
    g.add_argument("--activities-response", type=Path, help="fresh complete getActivities response (activity proposals)")
    g.add_argument("--today", required=True)
    g.add_argument("--execution", type=Path, help="optional execution state JSON for this item")
    g.add_argument("--closed-in-batch", type=Path, help="optional JSON list of activity ids marked done by a written item in this batch")
    g.add_argument("--result", type=Path, help="the saved result: a refusal is recorded in its action_states (with --item)")
    g.add_argument("--item", type=int, help="the item number a refusal is recorded for (with --result)")
    g.add_argument("--runs", type=Path, default=Path("./.pipedrive-sales/runs"), help="the runs folder whose confirmation ledger holds a refusal")
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            violations = validate(args.skill, args.tool, json.loads(args.params.read_text()))
            for line in violations:
                print(line)
            return 1 if violations else 0
        if args.command == "record":
            state = record_state(json.loads(args.response.read_text()))
            item = json.loads(args.item.read_text())
            record_action_state(args.result, item["n"], state)
            print(json.dumps(state))
            return 0
        activities = json.loads(args.activities_response.read_text()) if args.activities_response else None
        execution = json.loads(args.execution.read_text()) if args.execution else None
        closed = json.loads(args.closed_in_batch.read_text()) if args.closed_in_batch else []
        proposal, record_response = json.loads(args.proposal.read_text()), json.loads(args.record_response.read_text())
        skill = "meeting-follow-up" if Path(__file__).resolve().parent.parent.name == "meeting-follow-up" else "deal-triage"
        try:
            result = stale_guard(proposal, record_response, activities, args.today, execution, closed_in_batch=closed,
                                 meeting_steps=skill == "meeting-follow-up")
            # The whitelist, before anything is prepared (0.7.21, review 2.1): a proposal the CRM text shaped cannot carry a
            # field the skill may not write, and the hook re-checks the saved entry against the same rules.
            violations = validate(skill, result["tool"], result["params"], current_user_id=recorded_current_user(args.runs))
            if violations:
                raise ValueError("; ".join(violations))
        except ValueError as error:
            if args.result is not None and args.item is not None:
                reason = str(error)
                record_action_state(args.result, args.item, {"status": "refused", "reason": reason})
                record_confirmation(args.runs, args.item, "refused", f"Item {args.item} was not written: {reason[:1].lower() + reason[1:]}.")
            raise
    except (ValueError, OSError, KeyError) as error:
        parser.error(str(error))
    try:
        record_prepared_action(args.runs, result["tool"], result["params"], item=args.item, skill=skill)
    except (OSError, ValueError) as error:
        parser.error(f"the prepared action could not be saved, so the write is not printed: {error}")
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
