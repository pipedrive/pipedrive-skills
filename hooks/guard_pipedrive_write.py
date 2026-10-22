"""PreToolUse hook: a Pipedrive write goes through only when a skill script prepared it (ADR-045, 0.7.13).

A preflight (pdwrite.py, write_preflight.py, write_rules.py guard) prints a write's exact parameters and saves them to
.pipedrive-sales/runs/prepared-actions.json. This hook matches the connector call against those entries: the same tool
and the same parameters (numbers as text), in the same turn → the call goes through; an entry serves two calls (the
write and the one retry the skills allow after a server error) and the third is denied; anything else is denied
and recorded in runs/stopped-writes.json, so write_confirmation.py --turn / --ask and compute.py confirm report it in
the script's voice. Where no .pipedrive-sales folder exists the hook does nothing: a chat with no skill run keeps the
connector as it is. Any failure to read the ledger is a deny, because an unguarded write is what this hook exists to
stop (the capture hook's "always allow" rule is for reads). Standard library only; always exits 0.
Since 0.7.21 (review 2.1, 2.4, 2.5) the matching entry is checked again here, against the plugin's own write rules
(skills/deal-triage/scripts/write_rules.py, the shipped copy of shared/write_rules.py): the entry must name a
whitelisted skill, its parameters must pass that skill's whitelist (a user_id must equal the user the capture hook
recorded from getAccountContext in runs/current-user.json) and the call's parameters must be ones the connector's tool
takes. An entry that fails is denied as `whitelist`; rules that cannot be loaded deny every write (`rules_unavailable`).
An entry the settle hook marked consumed (a write that succeeded) is denied as `already_written`, so the retry applies
only after a failed call. No secret signs the ledger: a key a script could read, the model could read; the re-check
limits a forged entry to what a preflight could have printed, nothing more.
"""

from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import tempfile

# shared/pdcore.WRITE_TOOLS; tests/test_release_0_7_13_write_hook.py fails when the two sets differ.
WRITE_TOOLS = ("addActivity", "addDeal", "addDealProduct", "addLead", "addNote", "addOrganization", "addPerson",
               "addProduct", "addProductVariation", "addProject", "convertLeadToDeal", "updateActivity",
               "updateDeal", "updateDealProduct", "updateLead", "updateMailThreadDetails", "updateNote",
               "updateOrganization", "updatePerson", "updateProduct", "updateProductVariation", "updateProject")
SKILL_TOOLS = {"addActivity", "updateActivity", "addNote", "updateDeal"}
TOOL = re.compile(r"mcp__(?P<server>.+)__(?P<tool>" + "|".join(WRITE_TOOLS) + r")")
PREPARED, STOPPED, LOG = "prepared-actions.json", "stopped-writes.json", "write-hook.log"
RECORD_KEYS = ("id", "deal_id", "lead_id", "person_id", "org_id")
MAX_USES = 2  # shared/write_rules.MAX_USES
# The write rules, as the plugin ships them (the deal-triage copy) or as the repo holds them (tests run the hook in place).
RULES_PATHS = ("skills/deal-triage/scripts/write_rules.py", "shared/write_rules.py")

NEXT_STEP = {
    "deal-triage": ("Run python3 <skill folder>/scripts/write_confirmation.py --ask <n> for the item the rep named "
                    "(or --turn after an accept) and send its output as the whole reply."),
    "meeting-follow-up": ("Run python3 <skill folder>/scripts/compute.py confirm with the accepted items and send its "
                          "output as the whole reply."),
}
REASON = {
    "no_skill": "WRITE-GUARD: no skill writes {tool}; the call was stopped. Do not retry it. Tell the rep this change is "
                "not something the skill does.",
    "differs": "WRITE-GUARD: the parameters differ from what the preflight printed for item {item}; the call was stopped. "
               "Do not change them: send exactly the printed params, or run the preflight again.",
    "not_prepared": "WRITE-GUARD: this write was not prepared by a skill script and was stopped. Do not retry it and do "
                    "not write any other way. {next_step}",
    "exhausted": "WRITE-GUARD: this write for item {item} was already sent twice this turn; the call was stopped. Do not send "
                 "it again: check the record in Pipedrive. {next_step}",
    "unreadable": "WRITE-GUARD: the prepared actions could not be read, so the call was stopped. Do not retry it. {next_step}",
    "whitelist": "WRITE-GUARD: the prepared write for item {item} is outside the skill's whitelist ({detail}); the call was "
                 "stopped. Do not retry it and do not change the parameters. {next_step}",
    "rules_unavailable": "WRITE-GUARD: the plugin's write rules could not be loaded, so the call was stopped. Do not retry it. "
                         "{next_step}",
    "already_written": "WRITE-GUARD: the write for item {item} was already written this turn as record {detail}; the call was "
                       "stopped. Do not send it again: check the record in Pipedrive. {next_step}",
}

_RULES = []


def rules():
    """The write_rules module, loaded once from the first RULES_PATHS entry that exists; None when none loads."""
    if not _RULES:
        root = Path(__file__).resolve().parents[1]
        loaded = None
        for relative in RULES_PATHS:
            path = root / relative
            if not path.is_file():
                continue
            try:
                spec = importlib.util.spec_from_file_location("pipedrive_sales_write_rules", path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
            except Exception:  # noqa: BLE001 — a broken module is the same as a missing one: no rules, no write
                continue
            if all(hasattr(module, name) for name in ("WHITELIST", "WRITE_TOOL_PARAMETERS", "validate", "recorded_current_user")):
                loaded = module
                break
        _RULES.append(loaded)
    return _RULES[0]


def violation(module, entry, tool, params, current_user):
    """Why the saved entry may not be written, or None: the skill, the skill's whitelist, the connector's own parameters."""
    skill = entry.get("skill")
    if not isinstance(skill, str) or skill not in module.WHITELIST:
        return f"the entry names no whitelisted skill ({skill!r})"
    found = module.validate(skill, tool, entry.get("params") or {}, current_user_id=current_user)
    if found:
        return found[0]
    extra = sorted(str(key) for key in set(params) - set(module.WRITE_TOOL_PARAMETERS.get(tool, ())))
    if extra:
        return f"{tool}: parameter '{extra[0]}' is not one the connector's {tool} takes"
    return None


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _plain(value):
    """Numbers as text, as run_capture and the capture hook compare reads: {"id": "42"} and {"id": 42} are the same."""
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


def _load_list(path):
    if not path.exists():
        return []
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise ValueError(f"{path.name} is not a list")
    return value


def _turn(runs):
    path = runs / "turn.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except (OSError, ValueError):
        return None
    return value.get("turn") if isinstance(value, dict) else None


def _this_turn(entry, turn):
    """An entry prepared in another turn does not serve this one; with no turn recorded on either side it does."""
    return turn is None or entry.get("turn") is None or entry.get("turn") == turn


def decide(runs, tool, params, call_id, turn):
    """(decision, why, item, detail). Records the use on the matching prepared action on allow."""
    if tool not in SKILL_TOOLS:
        return "deny", "no_skill", None, None
    module = rules()
    if module is None:
        return "deny", "rules_unavailable", None, None
    path = runs / PREPARED
    entries = _load_list(path)
    wanted = _plain(params)
    candidates = [entry for entry in entries if isinstance(entry, dict) and entry.get("tool") == tool and _this_turn(entry, turn)]
    exhausted = None
    for entry in candidates:
        if _plain(entry.get("params") or {}) != wanted:
            continue
        consumed = entry.get("consumed")
        if isinstance(consumed, dict):
            return "deny", "already_written", entry.get("item"), str(consumed.get("id") if consumed.get("id") is not None else "?")
        uses = entry.setdefault("uses", [])
        if len(uses) >= MAX_USES:
            exhausted = entry
            continue
        detail = violation(module, entry, tool, params, module.recorded_current_user(runs))
        if detail:
            return "deny", "whitelist", entry.get("item"), detail
        uses.append({"at": _now(), "tool_use_id": call_id or None})
        _write_json(path, entries)
        return "allow", "prepared" if len(uses) == 1 else "retry", entry.get("item"), None
    if exhausted is not None:
        return "deny", "exhausted", exhausted.get("item"), None
    for entry in candidates:
        if len(entry.get("uses") or []) >= MAX_USES or isinstance(entry.get("consumed"), dict):
            continue
        saved = _plain(entry.get("params") or {})
        if any(saved.get(key) is not None and saved.get(key) == wanted.get(key) for key in RECORD_KEYS):
            return "deny", "differs", entry.get("item"), None
    return "deny", "not_prepared", None, None


def next_step(folder):
    results = folder / "results"
    skills = [name for name in ("deal-triage", "meeting-follow-up") if (results / f"{name}-full.json").exists()]
    if not skills:
        return ("Run the skill's confirmation script (write_confirmation.py --ask <n> for deal-triage, compute.py confirm "
                "for meeting-follow-up) and send its output as the whole reply.")
    return " ".join(NEXT_STEP[name] for name in skills)


def main():
    try:
        event = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        event = {}
    if not isinstance(event, dict):
        event = {}
    match = TOOL.fullmatch(str(event.get("tool_name") or ""))
    if not match:
        return 0
    folder = Path(os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or os.getcwd()) / ".pipedrive-sales"
    if not folder.is_dir():
        return 0
    runs = folder / "runs"
    tool, server = match.group("tool"), match.group("server")
    params = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
    call_id = re.sub(r"[^A-Za-z0-9_-]", "", str(event.get("tool_use_id") or ""))
    error, turn = None, _turn(runs)
    try:
        decision, why, item, detail = decide(runs, tool, params, call_id, turn)
    except Exception as failure:  # noqa: BLE001 — whatever went wrong, the write does not go through
        decision, why, item, detail = "deny", "unreadable", None, None
        error = f"{type(failure).__name__}: {failure}"
    line = {"at": _now(), "turn": turn, "tool": tool, "server": server, "decision": decision, "why": why, "item": item,
            "param_keys": sorted(str(key) for key in params.keys()), "tool_use_id": call_id or None, "error": error,
            "violation": detail if why == "whitelist" else None}
    try:
        runs.mkdir(parents=True, exist_ok=True)
        with (runs / LOG).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError:
        pass
    if decision == "allow":
        return 0
    try:
        stopped_path = runs / STOPPED
        stopped = _load_list(stopped_path) if why != "unreadable" else []
        stopped.append({"at": line["at"], "turn": turn, "tool": tool, "param_keys": line["param_keys"], "why": why, "item": item})
        _write_json(stopped_path, stopped)
    except (OSError, ValueError):
        pass
    reason = REASON[why].format(tool=tool, item=item if item is not None else "?", detail=detail, next_step=next_step(folder))
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": reason}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
