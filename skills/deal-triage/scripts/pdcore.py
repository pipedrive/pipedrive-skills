"""pdcore — shared Pipedrive MCP parsing for the sales skills.

SOURCE OF TRUTH: shared/pdcore.py at the repo root. Every skill's scripts/ folder carries a
byte-identical copy (tools/sync-shared.py), because a hub zip must be self-contained. Edit the
root copy, run the sync, and tests/test_shared_sync.py fails if a copy drifts.

Standard library only. No network. Contents: date parsing for the three formats the MCP
returns, run-file loading (inline `response` or a saved `response_file`), the per-tool table
builder with call and token counting, note hygiene and owner-name hydration, stage context,
scope/snooze helpers, and small output formatters.
"""

import calendar
import html
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from copy import deepcopy
from zoneinfo import ZoneInfo


# ----------------------------------------------------------------------------- constants

# Every connector tool named add*, update*, delete* or convert* (S4; tests/fixtures/connector-tool-names-2026-10-10.json).
WRITE_TOOLS = {"addActivity", "addNote", "updateActivity", "updateDeal", "updateLead",
               "addDeal", "addLead", "addPerson", "addOrganization", "addProduct",
               "updatePerson", "updateOrganization", "updateNote", "updateProduct",
               "addDealProduct", "updateDealProduct", "convertLeadToDeal",
               "addProject", "updateProject", "updateMailThreadDetails",
               "addProductVariation", "updateProductVariation"}

# Every connector read tool (the live server, 20 Oct 2026). One list: hooks/hooks.json's capture matcher and the capture hook's
# own pattern are asserted equal to it (0.7.20, review 3.5 and 3.6).
READ_TOOLS = {"getAccountContext", "get_account_context", "getActivities", "getActivity", "getDeal", "getDeals",
              "getDealProducts", "getDealsProducts", "getLead", "getLeadConversionStatus", "getLeads", "getNote", "getNotes",
              "getOrganization", "getOrganizations", "getPerson", "getPersons", "getPersonProducts", "getProduct",
              "getProductDeals", "getProductFiles", "getProductImage", "getProducts", "getProductVariations",
              "searchDeals", "searchLeads", "searchOrganization", "searchPersons", "searchProducts",
              "getStage", "getStages"}  # READ_TOOLS classifies reads for the capture hook; nothing here calls a stage tool

BUYING_SIGNALS = [
    "budget confirmed", "budget approved", "budget signed off", "quote accepted",
    "accepted the quote", "accepted the proposal", "contract signed", "ready to sign",
    "willing to buy", "wants to sign", "go-ahead", "green light", "po raised",
    "purchase order", "legal review", "sent to legal", "procurement",
    "looped in procurement", "start date", "kick-off date", "verbal yes",
    "verbal agreement", "chose us", "selected us", "signed",
]

MISMATCH_PATTERNS = [
    r"\bcontract signed\b", r"\bsigned (the )?(contract|order|agreement)\b",
    r"\b(deal|they|we) (is |are |have )?(now )?won\b", r"\bclosed[- ]won\b",
    r"\bmarked? (as )?won\b", r"\bclosed[- ]lost\b", r"\b(deal|we) lost\b",
    r"\bcancell?ed\b", r"\bwent with (a |the )?(competitor|another)\b",
]

SYSTEM_NOTE_PATTERNS = [
    r"^this deal was converted", r"^deal created from lead", r"^lead converted to deal",
    r"^this lead was converted", r"^merged with", r"^\[sample\]",
]

APP_ORIGIN_MARKERS = ("marketplace", "api", "integration", "zapier", "make", "claude",
                      "chatgpt", "openai", "assistant")

DEFAULT_WEIGHTS = {"value": 0.35, "probability": 0.25, "depth": 0.20, "neglect": 0.20}

TOKEN_CHARS = 4

# A record named by its number ("Deal 12", "Activity #9001", "(record id: 7)"); HC-04 keeps these out of every reply.
# An optional separator never sits between two whitespace runs (`(?:\s*[:#])?\s*`, 0.7.20 review 2.6): "Deal id" followed by a
# long run of spaces and no digit used to backtrack quadratically.
VISIBLE_RECORD_ID_PATTERN = (
    r'\b(?:Deal|Lead|Activity|Person|Organization|Org|User|Owner|Pipeline|Stage|Note|Filter|Project|Product|Team)'
    r'\s*(?:#|(?:record\s+)?id(?:\s*[:#])?)\s*\d+(?!\d|[.,]\d)'
    r'|\b(?:Deal|Lead|Activity|Person|Organization|Org|User|Owner|Pipeline|Stage|Note|Filter|Project|Product|Team)\s+\d+(?!\d|[.,]\d)\b'
    r'|\(\s*(?:record\s+)?id(?:\s*[:#])?\s*\d+\s*\)'
    # "record 24 (Acme Corp, £17,700)": the shape 0.7.19 check (i) used for a deal number with no type word before it
    # (the optional separator sits inside one group, the 0.7.20 review 2.6 shape, so a long run of spaces stays linear).
    r'|\brecord\s+(?:(?:#|id(?:\s*[:#])?|number)\s*)?\d+(?!\d|[.,]\d)'
    # "#78 (Acme Corp, £30,190)": a bare hash number, the shape 0.7.19 check (i-b) used.
    r'|(?<![\w#&])#\d+(?!\d|[.,]\d)'
    # A field line that names a record by number ("Organization: 4 · Contact person: 16", 0.7.2 live run).
    r'|\b(?:Organization|Org|Contact person|Person|Owner|Creator|User|Stage|Next activity|Last activity)\s*:\s*\d+(?!\d|[.,]\d)\b')


def visible_record_ids(text, record_names=()):
    """The record identifiers found in text, matched case-insensitively; an empty list means none. A reply record's own
    name is not an identifier, linked or not: "Deal 2" passes when a record is named "Deal 2" (0.7.4 decision 2A). A
    number that names no reply record, such as a bare "deal 7", still counts."""
    for name in sorted({str(item) for item in record_names if item and item != 'Name unavailable'}, key=len, reverse=True):
        text = re.sub(r'(?<!\w)' + re.escape(name) + r'(?!\w)', ' ', text, flags=re.I)
    return re.findall(VISIBLE_RECORD_ID_PATTERN, text, re.I)


# The plugin's own source sentences inside a CRM note: meeting-follow-up's "logged by" line and the older call-details line.
PLUGIN_SOURCE_SENTENCE = re.compile(r'\bSource:\s.*\b(?:logged by meeting-follow-up|call details logged on activity)\b', re.I)


def crm_quote(text, link=None):
    """CRM wording (an activity subject or a note) ready to quote in a reply. Sentences that name a record by number or are
    plugin source lines are dropped, and the "(Activity created)" marker is removed. ``link`` then links record names. An empty
    string means nothing readable remains, so the caller leaves the quote out. The reply checks stay strict: this cleans
    quoted CRM text only, never the reply's own wording."""
    text = re.sub(r'\s*\(Activity created\)', '', str(text or ''))
    kept = [part.strip() for part in re.split(r'(?<=[.!?])\s+|\n+', text)
            if part.strip() and not visible_record_ids(part) and not PLUGIN_SOURCE_SENTENCE.search(part)]
    cleaned = ' '.join(kept)
    return link(cleaned) if link and cleaned else cleaned

# ----------------------------------------------------------------------------- dates

def parse_dt(value):
    """Accepts YYYY-MM-DD, 'YYYY-MM-DD HH:MM:SS', RFC3339 with or without fraction/Z."""
    if not value or not isinstance(value, str):
        return None
    v = value.strip().replace("T", " ").rstrip("Z")
    v = re.sub(r"\.\d+$", "", v)
    v = re.sub(r"[+-]\d\d:\d\d$", "", v)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(v, fmt)
        except ValueError:
            continue
    return None

def to_date(value):
    dt = parse_dt(value)
    return dt.date() if dt else None

MONTH_ABBREVIATIONS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

def local_today(zone_name, now=None):
    """The calendar date in the account timezone at `now` (UTC clock by default)."""
    return (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(zone_name)).date()

def week_window(today):
    """Monday to Sunday of the calendar week that contains `today`. The one meaning of "this week"."""
    start = today - timedelta(days=today.weekday())
    return start, start + timedelta(days=6)

def week_summary(today):
    """The week window as the scripts print it: ISO bounds plus a label such as '12–18 Oct' or '28 Sep – 4 Oct'."""
    start, end = week_window(today)
    if start.year != end.year:
        label = f"{start.day} {MONTH_ABBREVIATIONS[start.month - 1]} {start.year} – {end.day} {MONTH_ABBREVIATIONS[end.month - 1]} {end.year}"
    elif start.month != end.month:
        label = f"{start.day} {MONTH_ABBREVIATIONS[start.month - 1]} – {end.day} {MONTH_ABBREVIATIONS[end.month - 1]}"
    else:
        label = f"{start.day}–{end.day} {MONTH_ABBREVIATIONS[end.month - 1]}"
    return {"start": start.isoformat(), "end": end.isoformat(), "label": label}

def closing_heading(closing):
    """'Closing this week (12–18 Oct)' when the aggregate carries its window; the bare heading for older saved runs."""
    week = (closing or {}).get("week") or {}
    return f"Closing this week ({week['label']})" if week.get("label") else "Closing this week"

def week_phrase(closing):
    """'this week (12–18 Oct)' for sentences; plain 'this week' for older saved runs."""
    week = (closing or {}).get("week") or {}
    return f"this week ({week['label']})" if week.get("label") else "this week"

def days_between(later, earlier):
    if later is None or earlier is None:
        return None
    return (later - earlier).days

def iso(d):
    return d.isoformat() if d else None

# ----------------------------------------------------------------------------- input

def load_json(path):
    from pathlib import Path
    with open(path, encoding="utf-8") as fh:
        result = json.load(fh)
    if isinstance(result, dict):
        for call in result.get('calls') or []:
            if call.get('response_file') and not Path(call['response_file']).is_absolute():
                call['response_file'] = str(Path(path).resolve().parent / call['response_file'])
    return result


def deal_status(value="open"):
    """Normalize the connector's scalar or single-item status array."""
    if isinstance(value, list):
        value = value[0] if len(value) == 1 else None
    return value.lower() if isinstance(value, str) else None

def load_response_file(path):
    """A tool result the client saved to a file instead of showing it. The file holds the raw
    tool output, sometimes with a line or two of client preamble before the JSON."""
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return {"success": False, "error": f"response_file not readable: {path}"}
    try:
        return json.loads(text)
    except ValueError:
        start = text.find("{")
        if start < 0:
            return {"success": False, "error": f"response_file has no JSON: {path}"}
        try:
            return json.JSONDecoder().raw_decode(text[start:])[0]
        except ValueError:
            return {"success": False, "error": f"response_file JSON unreadable: {path}"}

def rows(response):
    if isinstance(response, dict):
        data = response.get("data")
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return [data]
    if isinstance(response, list):
        return response
    return []

MCP_ERROR_TEXT = re.compile(r"^\s*MCP error -?\d+")
API_ERROR_TEXT = re.compile(r"^\s*Pipedrive API \S+ error\b", re.I)


def call_failed(resp):
    """True when a tool result is one of the three error formats (S5): a response object with success false or an
    `error` key; the text `MCP error -32602 …` or `Pipedrive API <status> error: {json}`; or the mail service's
    envelope with a non-2xx `statusCode`. A mail response with `data: null` and no error is an empty success."""
    if isinstance(resp, str):
        return bool(MCP_ERROR_TEXT.match(resp) or API_ERROR_TEXT.match(resp))
    if not isinstance(resp, dict):
        return False
    code = resp.get("statusCode")
    return (resp.get("success") is False or "error" in resp
            or (type(code) is int and not 200 <= code < 300))


def run_cost(run):
    """(real reads, estimated tokens) of a saved run, counted as collect counts them but without building tables,
    so a malformed row cannot stop the count (S8). Context stages are a computed copy, not a read."""
    calls = run.get("calls", [])
    tokens = 0
    for call in calls:
        resp = call.get("response")
        if resp is None and call.get("response_file"):
            resp = load_response_file(call["response_file"])
        tokens += len(json.dumps(resp, ensure_ascii=False)) / TOKEN_CHARS if resp is not None else 0
    return len(calls), int(round(tokens))


def add_stage(out, stage):
    if isinstance(stage.get("name"), str):
        stage["name"] = stage["name"].strip()  # UI-entered names arrive with stray spaces/dots
    out["stages"][stage["id"]] = stage


def collect(run):
    """Split the saved calls into typed tables, merged by id, and count cost."""
    calls = run.get("calls", [])
    out = {
        "stages": {}, "open_deals": {}, "won_deals": {}, "lost_deals": {},
        "activities": {}, "leads": {}, "notes": {}, "persons": {}, "organizations": {},
        "tool_calls": len(calls), "tokens": 0, "open_deal_chars": 0,
        "open_pages": 0, "open_cursor_unfollowed": False, "write_calls": [],
        "failed_calls": [], "tools_used": Counter(), "stages_source": None,
    }
    open_cursors = {}
    for stage in deepcopy(run.get("context_stages") or []):
        add_stage(out, stage)
        out["stages_source"] = "account_context"
    for call in calls:
        tool = (call.get("tool") or "").split("__")[-1]
        params = call.get("params") or {}
        resp = call.get("response")
        if resp is None and call.get("response_file"):
            resp = load_response_file(call["response_file"])
        chars = len(json.dumps(resp, ensure_ascii=False)) if resp is not None else 0
        out["tools_used"][tool] += 1
        out["tokens"] += chars / TOKEN_CHARS
        if tool in WRITE_TOOLS:
            out["write_calls"].append(tool)
        failed_response = call_failed(resp) or bool(call.get("error"))  # a tool error kept as text with response null
        if failed_response:
            out["failed_calls"].append(tool)
            continue
        if tool in ("getStages", "getStage"):  # a run saved before 0.7.0 only: nothing issues this read now (ADR-036)
            for s in deepcopy(rows(resp)):
                add_stage(out, s)
        elif tool == "getDeals":
            status = deal_status(params.get("status", "open"))
            table = {"open": "open_deals", "won": "won_deals", "lost": "lost_deals"}.get(status)
            requested_statuses = params.get("status")
            combined_closed_statuses = (isinstance(requested_statuses, list) and
                                        len(requested_statuses) > 1 and
                                        all(deal_status(item) in {"won", "lost"}
                                            for item in requested_statuses))
            if table is None and not combined_closed_statuses:
                continue
            for d in deepcopy(rows(resp)):
                actual = deal_status(d.get("status") or status)
                tgt = {"open": "open_deals", "won": "won_deals", "lost": "lost_deals"}.get(actual, table)
                if tgt is not None:
                    out[tgt][d["id"]] = d
            if status == "open" and params.get("sort_direction", "asc") != "desc":
                out["open_pages"] += 1
                out["open_deal_chars"] += chars
                if isinstance(resp, dict) and not failed_response:
                    additional_data = resp.get("additional_data")
                    selector = json.dumps({key: value for key, value in params.items() if key not in ('cursor', 'limit')}, sort_keys=True)
                    open_cursors[selector] = (additional_data.get("next_cursor")
                                              if isinstance(additional_data, dict) else None)
        elif tool in ("getActivities", "getActivity"):
            for a in deepcopy(rows(resp)):
                out["activities"][a["id"]] = a
        elif tool == "getLeads":
            for l in deepcopy(rows(resp)):
                out["leads"][l["id"]] = l
        elif tool == "getNotes":
            for n in deepcopy(rows(resp)):
                out["notes"][n["id"]] = n
        elif tool == 'getDeal':
            for d in deepcopy(rows(resp)):
                table = {'open': 'open_deals', 'won': 'won_deals', 'lost': 'lost_deals'}.get(d.get('status'))
                if table:
                    out[table][d['id']] = d
        elif tool in ('getPerson', 'getPersons', 'getOrganization', 'getOrganizations'):
            table = 'persons' if tool in ('getPerson', 'getPersons') else 'organizations'
            for record in deepcopy(rows(resp)):
                out[table][record['id']] = record
    out["open_cursor_unfollowed"] = any(open_cursors.values())
    out["tokens"] = int(round(out["tokens"]))
    return out

def stamp_result(result, calls, tokens, folder, config=None):
    """S8/S9: every saved result carries skill, version, calls (real reads, as collect counts them) and
    estimated_tokens. With the effective setting `tester_footer: true` its markdown also ends with the tester line."""
    import version
    result.update(version.stamp(calls, tokens, folder))
    if (config or {}).get("tester_footer") is True and isinstance(result.get("markdown"), str):
        result["markdown"] += "\n" + version.tester_line(result)
    return result

# ----------------------------------------------------------------------------- notes

def strip_html(text):
    text = re.sub(r"<br\s*/?>", "\n", text or "", flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"[ \t]+", " ", text).strip()

def clean_notes(notes):
    """Strip HTML, drop system / mention-only / image-only / near-duplicate notes."""
    kept = []
    seen = []  # (deal_id, text, datetime)
    for n in sorted(notes.values(), key=lambda x: parse_dt(x.get("add_time")) or datetime.min):
        raw = n.get("content") or ""
        if re.fullmatch(r"\s*<img[^>]*>\s*", raw, flags=re.I):
            continue
        text = strip_html(raw)
        if not text:
            continue
        low = text.lower()
        if any(re.match(p, low) for p in SYSTEM_NOTE_PATTERNS):
            continue
        if re.fullmatch(r"(@\w+[\s,]*)+", text):
            continue
        when = parse_dt(n.get("add_time"))
        dup = False
        for (did, t2, w2) in seen:
            if did == n.get("deal_id") and t2 == low and when and w2 and abs((when - w2).total_seconds()) <= 600:
                dup = True
                break
        if dup:
            continue
        seen.append((n.get("deal_id"), low, when))
        kept.append({
            "id": n["id"], "deal_id": n.get("deal_id"), "lead_id": n.get("lead_id"),
            "user_id": n.get("user_id"), "user": n.get("user") or {},
            "add_time": iso(to_date(n.get("add_time"))), "text": text,
        })
    return kept

# A Pipedrive user whose name field holds an address (an invited user who never set a name) shows this instead (0.7.4 decision 1A).
NAME_NOT_SET = "Name not set in Pipedrive"
ADDRESS_SHAPED = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def person_name(name):
    """A person's name as a report shows it. An address in a name field is not a name, so it shows NAME_NOT_SET."""
    if isinstance(name, str) and ADDRESS_SHAPED.match(name.strip()):
        return NAME_NOT_SET
    return name


def owner_map(kept_notes, config):
    owners = {}
    for n in kept_notes:
        u = n.get("user") or {}
        uid = n.get("user_id")
        if uid is not None and u.get("name") and str(uid) not in owners:
            owners[str(uid)] = {"name": person_name(u.get("name")), "email": u.get("email"), "is_you": bool(u.get("is_you"))}
    for k, v in (config.get("owners") or {}).items():
        if isinstance(v, str):
            owners[str(k)] = {"name": person_name(v), "email": None, "is_you": False, "from_config": True}
        elif isinstance(v, dict):
            owners.setdefault(str(k), {}).update(v)
            if "name" in v:
                owners[str(k)]["name"] = person_name(v["name"])
    return owners

def owner_name(owners, owner_id):
    o = owners.get(str(owner_id)) if owner_id is not None else None
    return o["name"] if o and o.get("name") else None

# ----------------------------------------------------------------------------- fixed lines

# The one wording a client that cannot run the scripts shows (ADR-037); every skill carries it unchanged.
NOT_CODE_CHECKED = ("Not code-checked: this client could not run the skill's scripts, "
                    "so the figures are the model's own reading of the rules.")
# Triage ends an email-type proposal with this line and drafts nothing (ADR-039 d.1, W0 §2.2); {} is the item number.
EMAIL_HANDOFF = 'Say "draft the email for {}" to get the text.'

# ----------------------------------------------------------------------------- stages

STAGE_ORDER_LINE = "Stage order not available for {}; stage-order rules skipped."
# Printed once when the run is told getNotes is not in this workspace (`compute.py --unavailable getNotes`, 0.7.6 decision 4A).
GETNOTES_UNAVAILABLE_LINE = ("getNotes is not available in this workspace; ask your Pipedrive admin to re-add the connector. "
                             "The notes-mismatch rule R7 and buying signals were skipped.")
PERIOD_ERROR = "Period '{}' not understood; use YYYY-MM, YYYY-Qn or YYYY-MM-DD (for example 2026-10, 2026-Q4 or 2026-10-15)."


def period_bounds(period):
    """First and last day of a forecast period. A period without a year (`Q4`) is refused, never guessed."""
    text = period if isinstance(period, str) else ""
    month = re.fullmatch(r"(\d{4})-(0[1-9]|1[0-2])", text)
    if month:
        year, number = int(month.group(1)), int(month.group(2))
        final_day = calendar.monthrange(year, number)[1]
        return f"{year:04d}-{number:02d}-01", f"{year:04d}-{number:02d}-{final_day:02d}"
    quarter = re.fullmatch(r"(\d{4})-Q([1-4])", text)
    if quarter:
        year, number = int(quarter.group(1)), int(quarter.group(2))
        start_month, end_month = (number - 1) * 3 + 1, number * 3
        final_day = calendar.monthrange(year, end_month)[1]
        return f"{year:04d}-{start_month:02d}-01", f"{year:04d}-{end_month:02d}-{final_day:02d}"
    try:
        day = datetime.strptime(text, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        raise ValueError(PERIOD_ERROR.format(period)) from None
    return day, day


def order_unknown_pipelines(stages):
    """Pipeline ids with a stage that lacks an integer order_nr: Pipedrive did not supply that pipeline's order."""
    known = defaultdict(list)
    for s in stages.values():
        known[s.get("pipeline_id")].append(type(s.get("order_nr")) is int)
    return {pid for pid, flags in known.items() if not all(flags)}


def stage_context(stages, pipeline_ids):
    """Per-pipeline stage facts. A pipeline is order-unknown when any of its stages lacks an integer order_nr
    (ADR-040): it has no first stage and no depth, and its stage names keep the order Pipedrive returned."""
    by_pipeline = defaultdict(list)
    for s in stages.values():
        by_pipeline[s.get("pipeline_id")].append(s)
    order_unknown = order_unknown_pipelines(stages)
    ordered_stages = {pid: list(ss) if pid in order_unknown else sorted(ss, key=lambda s: s["order_nr"])
                      for pid, ss in by_pipeline.items()}
    max_order = {pid: max(s["order_nr"] for s in ss) or 1 for pid, ss in by_pipeline.items() if pid not in order_unknown}
    first_stage = {pid: ss[0]["id"] for pid, ss in ordered_stages.items() if pid not in order_unknown}
    in_scope = [s for s in stages.values() if not pipeline_ids or s.get("pipeline_id") in pipeline_ids]
    probs = {s.get("deal_probability") for s in in_scope if s.get("deal_probability") is not None}
    labels = {pid: ss[0]["name"] + " → " + ss[-1]["name"] if ss else str(pid) for pid, ss in ordered_stages.items()}
    return {
        "by_pipeline": by_pipeline, "max_order": max_order, "first_stage": first_stage, "order_unknown": order_unknown,
        "stage_probabilities_vary": len(probs) > 1, "pipeline_labels": labels,
        "pipeline_stage_names": {pid: [s["name"] for s in ss] for pid, ss in ordered_stages.items()},
    }


def order_unknown_in_scope(ctx, pipeline_ids=(), excluded_ids=()):
    """Ids of the in-scope pipelines whose stage order Pipedrive did not supply."""
    return [pid for pid in ctx["pipeline_labels"]
            if pid is not None and pid in ctx["order_unknown"] and (not pipeline_ids or pid in pipeline_ids) and pid not in excluded_ids]


def stage_order_notices(ctx, pipeline_ids=(), excluded_ids=(), notified=()):
    """One line per in-scope pipeline whose stage order Pipedrive did not supply. `notified` lists the pipelines the
    confirmed setup review already printed (ADR-040 decision 2A): those get no line in a brief."""
    return [STAGE_ORDER_LINE.format(ctx["pipeline_labels"][pid])
            for pid in order_unknown_in_scope(ctx, pipeline_ids, excluded_ids) if pid not in notified]

# ----------------------------------------------------------------------------- deal helpers

def deal_value(d):
    v = d.get("value")
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0

def is_app_origin(d):
    blob = " ".join(str(d.get(k) or "") for k in ("origin", "channel_id")).lower()
    return any(m in blob for m in APP_ORIGIN_MARKERS)

def in_scope(d, config):
    scope = config.get("scope") or {}
    if scope.get("filter_id") and not config.get('_workflow_v2'):
        return True
    pids = scope.get("pipeline_ids") or []
    ex = scope.get("exclude_pipeline_ids") or []
    if pids and d.get("pipeline_id") not in pids:
        return False
    if d.get("pipeline_id") in ex:
        return False
    oids = scope.get("owner_ids") or []
    if oids and d.get("owner_id") not in oids:
        return False
    return True

def snoozed_until(config, deal_id, today):
    sn = (config.get("snoozed") or {}).get(str(deal_id))
    until = to_date(sn) if sn else None
    return until if until and until >= today else None

# ----------------------------------------------------------------------------- formatting

def money(value, currency):
    """The one formatter for every printed amount: symbol for known currencies, grouped digits, no float noise."""
    if value is None:
        return "—"
    symbol = {'GBP':'£','EUR':'€','USD':'$'}.get(currency)
    amount = f'{float(value):,.0f}' if float(value).is_integer() else f'{float(value):,.2f}'
    return f'{symbol}{amount}' if symbol else f'{amount} {currency or ""}'.strip()

def plural(n, word, plural_word=None):
    """'1 day', '2 days', '1 activity', '2 activities': a count with its noun in the right number."""
    return f"{n} {word if n == 1 else (plural_word or word + 's')}"

def days_ago(n):
    """'today', '1 day ago' or 'N days ago': the one wording for a whole number of days back."""
    if n == 0:
        return 'today'
    return plural(n, 'day') + ' ago'

# Characters that open or close Markdown structure inside prose: a backslash, a link's brackets, emphasis and code marks.
# Anyone who can rename a record through a form or an integration writes into the reply, so a title is quoted as literal
# text (0.7.20, review 2.3). Parentheses, quotes, ampersands and angle brackets stay: outside a link destination they are
# text, and the HTML paths escape angle brackets themselves.
MD_SPECIAL = re.compile(r'([\\\[\]*_`])')


def md_text(value, default='Name unavailable'):
    """A CRM string (a title, a name, a subject, a note) as literal Markdown text, on one line."""
    text = str(value) if value not in (None, '') else default
    return MD_SPECIAL.sub(r'\\\1', ' '.join(text.splitlines()))


def md_unescape(text):
    """The CRM string back from its md_text form, so a reply check can compare it with the record's name."""
    return re.sub(r'\\([\\\[\]*_`])', r'\1', str(text or ''))


def deal_ref(dl, config):
    """Use a readable record name and only a verified account route."""
    from pipedrive_links import configured_domain, deal_url
    url = deal_url(configured_domain(config), dl['id'])
    if url:
        return f"[{md_text(dl.get('title'))}]({url})"
    return md_text(dl.get('title')) + ' · Link unavailable'


__all__ = ['WRITE_TOOLS', 'READ_TOOLS', 'md_text', 'md_unescape', 'call_failed', 'run_cost', 'stamp_result', 'BUYING_SIGNALS', 'MISMATCH_PATTERNS', 'SYSTEM_NOTE_PATTERNS', 'APP_ORIGIN_MARKERS', 'DEFAULT_WEIGHTS', 'TOKEN_CHARS', 'parse_dt', 'to_date', 'days_between', 'iso', 'load_json', 'load_response_file', 'rows', 'collect', 'strip_html', 'clean_notes', 'owner_map', 'owner_name', 'person_name', 'NAME_NOT_SET', 'stage_context', 'order_unknown_pipelines', 'stage_order_notices', 'order_unknown_in_scope', 'STAGE_ORDER_LINE', 'GETNOTES_UNAVAILABLE_LINE', 'NOT_CODE_CHECKED', 'EMAIL_HANDOFF', 'deal_value', 'is_app_origin', 'in_scope', 'snoozed_until', 'money', 'deal_ref']


# ---------------------------------------------------------------------------
# Wave-2 helpers (W0 §3.2): attendee matching, last touch, evidence shape, loss categories.

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalise_email(value):
    """Lower-case, trimmed address, or None when the value is not an email."""
    if not value or not isinstance(value, str):
        return None
    v = value.strip().lower()
    return v if _EMAIL_RE.match(v) else None


def email_domain(value):
    v = normalise_email(value)
    return v.rsplit("@", 1)[1] if v else None


def attendee_candidates(activity, company_domains=()):
    """People on a meeting activity, external first, one entry per person or email.

    Reads `attendees` (name, email_address, person_id) and `participants` (person_id) as the
    MCP returns them. `internal` is true when the email domain is one of the user's own
    company domains (M5); with no domains nobody is internal and the skill asks.
    """
    domains = {d.strip().lower() for d in company_domains or () if d}
    out, seen = [], set()
    for a in activity.get("attendees") or []:
        email = normalise_email(a.get("email_address") or a.get("email"))
        key = ("p", a.get("person_id")) if a.get("person_id") is not None else ("e", email or a.get("name"))
        if key in seen:
            continue
        seen.add(key)
        out.append({"name": a.get("name") or None, "email": email, "person_id": a.get("person_id"),
                    "internal": bool(email and email_domain(email) in domains)})
    for p in activity.get("participants") or []:
        pid = p.get("person_id")
        if pid is None or ("p", pid) in seen:
            continue
        seen.add(("p", pid))
        out.append({"name": None, "email": None, "person_id": pid, "internal": False})
    return sorted(out, key=lambda a: a["internal"])


def last_touch(deal, activities, today):
    """One definition of "last contact" for suppression (W3 X3) and silence.

    Candidates: the scheduled date of the latest completed activity on the deal (marked-done
    time as fallback, as activity_evidence.py does) and the deal's incoming and outgoing mail
    timestamps. Returns {date, source, days} or None when nothing touched the deal.
    """
    today_d = to_date(today)
    candidates = []
    for a in activities or []:
        if a.get("deal_id") != deal.get("id") or a.get("done") not in (True, 1):
            continue
        scheduled, recorded = to_date(a.get("due_date")), to_date(a.get("marked_as_done_time"))
        if scheduled:
            candidates.append((scheduled, "completed activity scheduled date"))
        elif recorded:
            candidates.append((recorded, "completed activity marked-done date"))
    for field, label in (("last_incoming_mail_time", "incoming email"), ("last_outgoing_mail_time", "outgoing email")):
        d = to_date(deal.get(field))
        if d:
            candidates.append((d, label))
    if not candidates:
        return None
    d, source = max(candidates, key=lambda c: c[0])
    return {"date": iso(d), "source": source, "days": days_between(today_d, d)}


def evidence_item(kind, source_id, date_value, quote, strength="weak"):
    """The one evidence shape every skill cites with (W2 §5, W3 §5): kind, source_id, date, quote, strength."""
    if strength not in ("strong", "weak"):
        raise ValueError("evidence strength must be 'strong' or 'weak'")
    text = " ".join(str(quote or "").split())
    return {"kind": kind, "source_id": source_id, "date": iso(to_date(date_value)),
            "quote": text[:200], "strength": strength}


def loss_category(lost_reason, mapping):
    """Map Pipedrive's free-text lost_reason to a configured category (O5); None when unmapped."""
    if not lost_reason or not mapping:
        return None
    wanted = " ".join(str(lost_reason).lower().split())
    for reason, category in mapping.items():
        if " ".join(str(reason).lower().split()) == wanted:
            return category
    return None


# ----------------------------------------------------------------------------- expected reply (0.7.8)

# Every checked reply prints the way the end-of-reply check's send-back does: a line saying the rep has not seen it, then
# the text between marker lines. Every send-back so far was followed exactly; a bare page was answered "(shown above)"
# and a bare brief was retyped with one wrong probability (0.7.10 Cowork runs). The saved text is the reply alone.
REPLY_FRAME = ('The rep has not seen this text: they see only your reply, never command output. Your reply must open '
               'with the text between the two marker lines, character for character, without the marker lines and '
               'with nothing before it. Nothing goes after it: a question for the rep goes through the question card, '
               'or the script\'s numbered fallback. A record is named by its name, never by its number, in this reply '
               'and in any message you send before it.')
BEGIN_REPLY, END_REPLY = '-----BEGIN REPLY-----', '-----END REPLY-----'


def framed(text):
    return REPLY_FRAME + '\n' + BEGIN_REPLY + '\n' + str(text).rstrip('\n') + '\n' + END_REPLY


def runs_folder_near(near=None):
    """runs/ under the first .pipedrive-sales at or above `near` (a run file, a results path), else under the working
    folder; None where no such folder exists. The same walk save_expected_reply makes."""
    from pathlib import Path
    start = Path(near).resolve() if near else Path.cwd() / ".pipedrive-sales"
    folder = next((p for p in (start, *start.parents) if p.name == ".pipedrive-sales"), None)
    if folder is None or not folder.is_dir():
        return None
    return folder / "runs"


CARD_DUE = "card-due.json"


def _turn_in(runs):
    try:
        return json.loads((runs / "turn.json").read_text(encoding="utf-8")).get("turn")
    except (OSError, ValueError, AttributeError):
        return None


def note_card_due(questions, command, near=None):
    """Record that this turn's checked reply leaves questions for the rep (runs/card-due.json: turn, question ids, the
    questions command), so the Stop hook can send the turn back once when it ends without the card (0.7.19 phase 2,
    ADR-024 note: release run (a) and phase 0 run 1 ended the turn on the forecast report and the rep typed the request).
    No questions: the record is removed. A `questions` run earlier in the same turn keeps its `asked_at`."""
    import os
    runs = runs_folder_near(near)
    if runs is None:
        return None
    path = runs / CARD_DUE
    if not questions:
        try:
            path.unlink()
        except OSError:
            pass
        return None
    turn = _turn_in(runs)
    record = {"turn": turn, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "questions": [str(q.get("id")) for q in questions if isinstance(q, dict)], "command": command}
    try:
        earlier = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(earlier, dict) and earlier.get("turn") == turn and earlier.get("asked_at"):
            record["asked_at"] = earlier["asked_at"]
    except (OSError, ValueError):
        pass
    try:
        runs.mkdir(exist_ok=True)
        temporary = runs / (CARD_DUE + ".tmp")
        temporary.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        return None
    return path


def note_card_asked(near=None):
    """Mark this turn's card as asked (the `questions` command ran): `asked_at` on runs/card-due.json, created for the turn
    when check-reply has not written it yet."""
    import os
    runs = runs_folder_near(near)
    if runs is None:
        return None
    path = runs / CARD_DUE
    turn = _turn_in(runs)
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(record, dict) or record.get("turn") != turn:
            record = {"turn": turn, "questions": [], "command": None}
    except (OSError, ValueError):
        record = {"turn": turn, "questions": [], "command": None}
    record["asked_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        runs.mkdir(exist_ok=True)
        temporary = runs / (CARD_DUE + ".tmp")
        temporary.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        return None
    return path


def save_expected_reply(text, kind, near=None):
    """Save the text the chat must send as its whole reply, for the end-of-reply hook (hooks/check_last_reply.py).

    The hook compares the chat's last message with this text and sends the chat back once when they differ (0.7.7
    Cowork runs: a retyped brief, show more answered with "(above)", a confirm reply without its footer). The file
    is runs/expected-reply.json under the first .pipedrive-sales folder above `near`, else under the working folder;
    without that folder nothing is saved. The turn is the one hooks/mark_turn.py recorded; None without the hook.
    `command` is this process's own invocation, so a send-back can tell the chat to run it again instead of
    retyping the text (0.7.18, decision 1A: the 0.7.17 release run (b) retry retyped one line wrong)."""
    import os
    import shlex
    import sys
    from pathlib import Path
    start = Path(near).resolve() if near else Path.cwd() / ".pipedrive-sales"
    folder = next((p for p in (start, *start.parents) if p.name == ".pipedrive-sales"), None)
    if folder is None or not folder.is_dir():
        return None
    runs = folder / "runs"
    try:
        turn = json.loads((runs / "turn.json").read_text(encoding="utf-8")).get("turn")
    except (OSError, ValueError, AttributeError):
        turn = None
    record = {"turn": turn, "kind": kind, "text": text, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "command": " ".join(shlex.quote(str(argument)) for argument in sys.argv)}
    try:
        runs.mkdir(exist_ok=True)
        temporary = runs / "expected-reply.json.tmp"
        temporary.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, runs / "expected-reply.json")
    except OSError:
        return None
    return runs / "expected-reply.json"
