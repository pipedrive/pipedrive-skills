"""Normalize observed Pipedrive account discovery and save shared metadata safely."""

import argparse
from copy import deepcopy
from datetime import datetime
import json
import re
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

from display_time import display_timestamp
from pdcore import load_response_file, money, person_name, stage_context
from pipedrive_links import configured_domain, normalize_domain

FIELD_CAP = 500  # the context tool returns at most this many custom fields per record type, with no paging
PRIORITY_FIELD_CAP = 15  # getDeals takes at most 15 custom_fields keys
# Deal fields whose getDeals value is a record id, not a name: a card would print "Organization: 4" (0.7.2 live run).
ID_ONLY_TYPES = {"user", "org", "people", "activity", "stage"}
ID_ONLY_KEYS = {"id", "pipeline_id", "owner_id", "creator_user_id", "user_id", "org_id", "person_id", "stage_id",
                "next_activity_id", "last_activity_id"}
FIELD_RECORD_TYPES = {"deal": "deals", "deals": "deals", "person": "persons", "persons": "persons",
                      "organization": "organizations", "organizations": "organizations",
                      "activity": "activities", "activities": "activities",
                      "product": "products", "products": "products"}


def display_name(name):
    """A pipeline or stage name as the rep reads it: outer spaces and one closing full stop dropped, so setup shows
    `Delivery (Kick-off → Handover)` for stages saved as " Kick-off" and "Handover." (0.7.2 live run)."""
    name = name.strip()
    return name[:-1].rstrip() if name.endswith(".") and not name.endswith("..") and len(name) > 1 else name


def normalize(payload):
    if not isinstance(payload, dict) or payload.get("success") is False or payload.get("error"):
        return {"available": False}
    if isinstance(payload.get("data"), dict):
        payload = payload["data"]
    if payload.get("version") not in (None, 1, "1"):
        return {"available": False}
    user = payload.get("current_user")
    if not isinstance(user, dict) or type(user.get("id")) is not int or user["id"] <= 0:
        return {"available": False}
    pipelines = payload.get("pipelines")
    users = payload.get("company_users")
    if not isinstance(pipelines, list) or not isinstance(users, list):
        return {"available": False}
    names = {}
    stages = []
    stage_ids = set()
    for pipeline in pipelines:
        if (not isinstance(pipeline, dict) or type(pipeline.get("id")) is not int or
                pipeline["id"] <= 0 or pipeline["id"] in names or not isinstance(pipeline.get("name"), str) or
                not pipeline["name"].strip() or not isinstance(pipeline.get("stages"), list)):
            return {"available": False}
        names[pipeline["id"]] = display_name(pipeline["name"])
        for stage in pipeline["stages"]:
            if (not isinstance(stage, dict) or type(stage.get("id")) is not int or stage["id"] <= 0 or
                    stage["id"] in stage_ids or not isinstance(stage.get("name"), str) or not stage["name"].strip()):
                return {"available": False}
            stage_ids.add(stage["id"])
            # Pipedrive's own order_nr only; list position is not board order (ADR-040).
            own = {key: value for key, value in stage.items() if key != "order_nr" or type(value) is int}
            stages.append({**own, "name": display_name(stage["name"]), "pipeline_id": pipeline["id"]})
    timezone = user.get("timezone_name")
    try:
        timezone = ZoneInfo(timezone).key if isinstance(timezone, str) else None
    except (ValueError, ZoneInfoNotFoundError):
        timezone = None
    domain = None
    if user.get("company_domain"):
        try:
            domain = normalize_domain(user["company_domain"])
        except ValueError:
            pass
    fields = payload.get("custom_fields") or {}
    field_counts = {}
    for key, definitions in (fields.items() if isinstance(fields, dict) else ()):
        record_type = FIELD_RECORD_TYPES.get(str(key).lower())
        if record_type and isinstance(definitions, list):
            field_counts[record_type] = {"count": len(definitions), "may_be_incomplete": len(definitions) >= FIELD_CAP}
    shown = [{**deepcopy(pipeline), "name": names[pipeline["id"]],
              "stages": [{**deepcopy(stage), "name": display_name(stage["name"])} for stage in pipeline["stages"]]}
             for pipeline in pipelines]
    return {"available": True, "user": dict(user), "pipelines": shown, "stages": stages,
            "users": [dict(item) for item in users if isinstance(item, dict) and type(item.get("id")) is int],
            "timezone": timezone, "currency": user.get("default_currency"), "locale": user.get("locale"),
            "domain": domain, "custom_fields": deepcopy(fields) if isinstance(fields, dict) else {}, "field_counts": field_counts}


def deal_field_definitions(context):
    fields = (context or {}).get("custom_fields") or {}
    for key, definitions in (fields.items() if isinstance(fields, dict) else ()):
        if FIELD_RECORD_TYPES.get(str(key).lower()) == "deals" and isinstance(definitions, list):
            return [definition for definition in definitions if isinstance(definition, dict)]
    return []


def _field_key(definition):
    return definition.get("field_code") or definition.get("code") or definition.get("key")


def _field_label(definition):
    label = definition.get("name") or definition.get("field_name") or definition.get("label")
    return label.strip() if isinstance(label, str) and label.strip() else None


def names_a_value(key, definition=None):
    """False for a field that holds only a record id (Organization, Contact person, Owner, Stage): never offered or shown."""
    field_type = (definition or {}).get("field_type") or (definition or {}).get("type")
    return key not in ID_ONLY_KEYS and field_type not in ID_ONLY_TYPES


def deal_field_choices(context):
    """The account's own deal fields that carry a name, as {key, label}, in context order (C-PB-R1).
    A field the context does not list is never offered."""
    choices, seen = [], set()
    for definition in deal_field_definitions(context):
        key, label = _field_key(definition), _field_label(definition)
        if isinstance(key, str) and key and label and key not in seen and names_a_value(key, definition):
            seen.add(key)
            choices.append({"key": key, "label": label})
    return choices


def priority_field_options(context):
    """Option labels for the 'which fields do you look at' setup question: field names from the context, at most 15."""
    return [choice["label"] for choice in deal_field_choices(context)[:PRIORITY_FIELD_CAP]]


def resolve_priority_fields(context, names):
    """Turn the field names a rep chose into [{key, label}] (at most 15). A name the context does not list, or
    one that two fields share, is an error: the question never invents or guesses a field."""
    choices = deal_field_choices(context)
    resolved = []
    for name in names or []:
        matches = [choice for choice in choices if choice["label"].casefold() == str(name).strip().casefold()]
        if len(matches) != 1:
            raise ValueError("Choose a deal field name that appears exactly once in the account context")
        if matches[0] not in resolved:
            resolved.append(matches[0])
    if len(resolved) > PRIORITY_FIELD_CAP:
        raise ValueError(f"Choose at most {PRIORITY_FIELD_CAP} fields")
    return resolved


def _value_kind(key, definition):
    """How a deal field prints: money for the deal value and monetary fields, a percentage for the deal probability, a local
    date, or a UTC timestamp shown in the display timezone. Pipedrive sends system timestamps as `*_time` keys and dates as
    `*_date` or type `date`."""
    field_type = (definition or {}).get("field_type") or (definition or {}).get("type")
    if field_type == "monetary" or key == "value":
        return "money"
    if key == "probability":
        return "percent"
    if field_type == "date" or str(key).endswith("_date"):
        return "date"
    if str(key).endswith("_time"):
        return "timestamp"
    return None


def _field_text(value, options, kind=None, currency=None, timezone_name=None):
    if isinstance(value, list):
        return ", ".join(text for text in (_field_text(item, options, kind, currency, timezone_name) for item in value) if text)
    if isinstance(value, dict):
        if value.get("value") is not None:
            return money(value["value"], value.get("currency"))
        return ", ".join(str(part) for part in value.values() if isinstance(part, (str, int, float)) and str(part).strip())
    if value is None or str(value).strip() == "":
        return ""
    if kind == "money" and isinstance(value, (int, float)):
        return money(value, currency)
    if kind == "percent" and isinstance(value, (int, float)):
        return f"{value:g}%"
    if kind == "date" and re.match(r"\d{4}-\d{2}-\d{2}$", str(value)):
        return datetime.strptime(str(value), "%Y-%m-%d").strftime("%-d %b %Y")
    # Pipedrive types last_incoming_mail_time and stage_change_time as "date" but sends a full timestamp.
    if kind in ("date", "timestamp") and re.match(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", str(value)):
        text = str(value).strip().replace(" ", "T")
        return display_timestamp(text if re.search(r"(Z|[+-]\d{2}:?\d{2})$", text) else text + "Z", timezone_name)
    return str(options.get(str(value), value))


def priority_values(record, fields, definitions=(), timezone_name=None):
    """What the rep's chosen fields hold on one deal, for its card: [{label, value}] in the chosen order.
    A field the connector returned no value for is left out, so with no chosen fields (or no values) the card is unchanged."""
    options = {}
    types = {_field_key(definition): definition for definition in definitions or () if isinstance(definition, dict)}
    for definition in definitions or ():
        if isinstance(definition, dict) and _field_key(definition):
            options[_field_key(definition)] = {str(option["id"]): option.get("label") for option in definition.get("options") or []
                                               if isinstance(option, dict) and "id" in option and option.get("label")}
    shown = []
    for field in fields or []:
        key = field.get("key") if isinstance(field, dict) else None
        if not key or not field.get("label") or not names_a_value(key, types.get(key)):
            continue
        text = _field_text(record.get(key, (record.get("custom_fields") or {}).get(key)), options.get(key, {}),
                           _value_kind(key, types.get(key)), record.get("currency"), timezone_name)
        if text:
            shown.append({"label": field["label"], "value": text})
    return shown


def priority_gaps(contexts, fields):
    """Give every fetched deal one entry per chosen field, in the chosen order. A field with no value on any deal says
    `not returned by Pipedrive`; a field that some deals have says `not set` on the deals that lack it."""
    labels = [str(field["label"]) for field in fields or []
              if isinstance(field, dict) and field.get("key") and field.get("label") and names_a_value(field["key"])]
    returned = {item["label"] for context in contexts for item in context.get("priority_fields") or [] if item.get("value")}
    for context in contexts:
        shown = {item["label"]: item for item in context.get("priority_fields") or []}
        context["priority_fields"] = [shown.get(label) or {"label": label, "value": None,
                                                           "status": "not set" if label in returned else "not returned by Pipedrive"}
                                      for label in labels]


def priority_fields_line(shown):
    return "Fields you track: " + " · ".join(f"{item['label']}: {item.get('value') or item.get('status')}" for item in shown) if shown else ""


def _field_definitions(context, record_type):
    fields = (context or {}).get("custom_fields") or {}
    for key, definitions in (fields.items() if isinstance(fields, dict) else ()):
        if FIELD_RECORD_TYPES.get(str(key).lower()) == record_type and isinstance(definitions, list):
            return [definition for definition in definitions if isinstance(definition, dict)]
    return []


def _option_entries(definition):
    """The options of one field definition as [{key, label}]. A string option is its own key and label; an option
    object keeps its label and takes its key from `key_string`, `key`, `value` or a string `id`, else the lower-cased label."""
    entries = []
    for option in definition.get("options") or []:
        if isinstance(option, str) and option.strip():
            entries.append({"key": option.strip(), "label": option.strip()})
        elif isinstance(option, dict):
            label = _field_label(option)
            key = next((option[name].strip() for name in ("key_string", "key", "value", "id")
                        if isinstance(option.get(name), str) and option[name].strip()), None)
            key = key or (label.casefold() if label else None)
            if key:
                entries.append({"key": key, "label": label or key})
    return entries


def _named_field_options(context, record_type, code):
    for definition in _field_definitions(context, record_type):
        if _field_key(definition) == code:
            return _option_entries(definition)
    return []


def activity_type_options(context):
    """The activity types the account defines, from the `type` field of the activity definitions, as [{key, label}].
    Empty when the context lists none: the caller then falls back to the types seen in the run plus the standard six."""
    return _named_field_options(context, "activities", "type")


def lost_reason_options(context):
    """The lost reasons the account has configured, from the `lost_reason` field of the deal definitions, as [{key, label}].
    Empty when the account has none configured (the sandbox returns an empty list): the caller then uses the reasons seen on past deals."""
    return _named_field_options(context, "deals", "lost_reason")


TRIAL_DEMO_WORDS = ("trial", "demo", "expir")


def trial_demo_signs(context):
    """Where the account shows trials or demos, as [{source, name}]: a case-insensitive `trial`, `demo` or `expir` in a
    pipeline name, a stage name, a `lost_reason` option or an activity type option. Empty means no sign, and the
    setup question about trials and demos is not asked."""
    if not (context or {}).get("available"):
        return []
    places = [("pipeline name", [(pipeline.get("name"),) for pipeline in context.get("pipelines") or []]),
              ("stage name", [(stage.get("name"),) for stage in context.get("stages") or []]),
              ("lost reason", [(option["label"], option["key"]) for option in lost_reason_options(context)]),
              ("activity type", [(option["label"], option["key"]) for option in activity_type_options(context)])]
    return [{"source": source, "name": names[0]} for source, entries in places for names in entries
            if any(isinstance(name, str) and any(word in name.casefold() for word in TRIAL_DEMO_WORDS) for name in names)]


def user_names(context):
    """Calling user first, then colleagues, as [{id, name, me}]; from `current_user` and `company_users` only."""
    if not (context or {}).get("available"):
        return []
    people = [{"id": context["user"]["id"], "name": context["user"].get("name"), "me": True}]
    people += [{"id": user["id"], "name": user.get("name"), "me": False} for user in context.get("users") or []
               if user["id"] != context["user"]["id"]]
    return [person for person in people if isinstance(person["name"], str) and person["name"].strip()]


OWNER_ME = {"me", "mine", "my", "my deals", "only my deals", "my own", "my own deals", "myself", "own"}
OWNER_TEAM = {"team", "team deals", "all", "all deals", "all owners", "everyone", "anyone"}
PIPELINE_ALL = {"all", "all pipelines", "every pipeline", "any", "any pipeline"}


def _scope_names(scope, *keys):
    names = []
    for key in keys:
        value = scope.get(key)
        for item in value if isinstance(value, list) else [value]:
            if item is not None and item != "":
                names.append(item)
    return names


def _named(name, markers):
    return isinstance(name, str) and name.strip().casefold() in markers


def _pipeline_name_ids(names, context, problems):
    ids = []
    for name in names:
        if not isinstance(name, str) or not name.strip():
            problems.append("A pipeline choice must be a pipeline name")
            continue
        matches = [pipeline["id"] for pipeline in (context or {}).get("pipelines") or []
                   if pipeline["name"].strip().casefold() == name.strip().casefold()]
        if len(matches) != 1:
            problems.append(f"More than one pipeline is named {name.strip()}" if matches else f"No pipeline named {name.strip()} in this account")
        elif matches[0] not in ids:
            ids.append(matches[0])
    return ids


def _owner_name_ids(names, context, problems):
    ids = []
    people = user_names(context)
    for name in names:
        if not isinstance(name, str) or not name.strip():
            problems.append("An owner choice must be me, team or a user name")
            continue
        text = name.strip().casefold()
        if text in OWNER_ME:
            found = [context["user"]["id"]] if (context or {}).get("available") else []
        else:
            found = [person["id"] for person in people if person["name"].strip().casefold() == text]
        if len(found) != 1:
            problems.append(f"More than one user is named {name.strip()}" if found else f"No user named {name.strip()} in this account")
        elif found[0] not in ids:
            ids.append(found[0])
    return ids


def resolve_scope(scope, context):
    """Names in a scope answer (`pipelines`, `exclude_pipelines`, `owner`) become ids from the session account context.
    Returns (scope with `pipeline_ids` / `exclude_pipeline_ids` / `owner_ids` added beside the names, problems).
    Ids already present win; a name that does not resolve is a problem, never a guess."""
    resolved, problems = deepcopy(scope), []
    pipelines = [name for name in _scope_names(scope, "pipelines", "pipeline") if not _named(name, PIPELINE_ALL)]
    if pipelines and not scope.get("pipeline_ids"):
        before = len(problems)
        ids = _pipeline_name_ids(pipelines, context, problems)
        if ids and len(problems) == before:
            resolved["pipeline_ids"] = ids
    excluded = _scope_names(scope, "exclude_pipelines")
    if excluded and not scope.get("exclude_pipeline_ids"):
        before = len(problems)
        ids = _pipeline_name_ids(excluded, context, problems)
        if ids and len(problems) == before:
            resolved["exclude_pipeline_ids"] = ids
    owners = [name for name in _scope_names(scope, "owner", "owners") if not _named(name, OWNER_TEAM)]
    if owners and not scope.get("owner_ids"):
        before = len(problems)
        ids = _owner_name_ids(owners, context, problems)
        if ids and len(problems) == before:
            resolved["owner_ids"] = ids
    return resolved, list(dict.fromkeys(problems))


def scope_gap(scope):
    """Why a scope that names pipelines or an owner cannot filter a read, or None when every named choice has its ids.
    A planner that sees a gap must not plan an unfiltered read."""
    parts = []
    pipelines = [str(name).strip() for name in _scope_names(scope, "pipelines", "pipeline") if not _named(name, PIPELINE_ALL)]
    if pipelines and not scope.get("pipeline_ids"):
        parts.append(f"Scope names {', '.join(pipelines)} but no pipeline id")
    excluded = [str(name).strip() for name in _scope_names(scope, "exclude_pipelines")]
    if excluded and not scope.get("exclude_pipeline_ids"):
        parts.append(f"Scope excludes {', '.join(excluded)} but no excluded pipeline id")
    owners = [str(name).strip() for name in _scope_names(scope, "owner", "owners") if not _named(name, OWNER_TEAM)]
    if owners and not scope.get("owner_ids"):
        parts.append(f"Scope names owner {', '.join(owners)} but no owner id")
    return "; ".join(parts) + "; rerun setup" if parts else None


def _unwrap_hook(payload):
    """The response inside a hook capture ({tool, response, capture: "hook"}); any other payload comes back unchanged."""
    if isinstance(payload, dict) and payload.get("capture") == "hook" and "response" in payload:
        return payload["response"]
    return payload


def load_context(path):
    """Normalized account context from a saved getAccountContext response or hook capture; {"available": False} when the file is unusable."""
    return normalize(_unwrap_hook(load_response_file(path)))


def latest_capture(run_path, cwd=None):
    """The newest usable getAccountContext hook capture: beside the run file (runs/captured/), then under the working
    directory. None when neither folder holds one."""
    folders = [Path(run_path).resolve().parent / "captured"] if run_path else []
    folders.append(Path(cwd or Path.cwd()) / ".pipedrive-sales" / "runs" / "captured")
    for folder in folders:
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if (isinstance(payload, dict) and payload.get("tool") in ("getAccountContext", "get_account_context")
                    and payload.get("capture") == "hook" and normalize(payload.get("response"))["available"]):
                return path
    return None


def load_stage_context(path):
    """Normalized account context from a saved getAccountContext response: the compute scripts' --stages input. The plugin
    hook's capture file (runs/captured/<id>.json: tool, params, response, …) is accepted as it is (0.7.15, ADR-030 note:
    DT-04 on the 0.7.15 candidate unwrapped it by hand)."""
    loaded = load_response_file(path)
    context = normalize(loaded)
    if not context["available"] and isinstance(loaded, dict) and "tool" in loaded and isinstance(loaded.get("response"), dict):
        context = normalize(loaded["response"])
    if not context["available"]:
        raise ValueError(f"--stages {path} is not a usable getAccountContext response")
    return context


def with_context_stages(run, context):
    """Stages from the session's account context (ADR-036: no skill reads a stage tool; getAccountContext lists every
    pipeline's stages). Only the computed copy changes, under a top-level `context_stages` key that `pdcore.collect`
    reads; the run ledger gains no call. A saved run that holds a real stage call from before 0.7.0 keeps its own stages."""
    if any((call.get('tool') or '').split('__')[-1] in ('getStages', 'getStage') for call in run.get('calls') or []):
        return run
    if not (context.get('available') and context.get('stages')):
        return run
    derived = deepcopy(run)
    derived['context_stages'] = deepcopy(context['stages'])
    return derived


def metadata(config, context):
    result = deepcopy(config)
    if not context.get("available"):
        return result
    pipelines = result["pipelines"] = dict(result.get("pipelines") or {})
    for pipeline in context["pipelines"]:
        previous = pipelines.get(str(pipeline["id"]))
        pipelines[str(pipeline["id"])] = {**(previous if isinstance(previous, dict) else {}), "label": pipeline["name"],
                                         "name": pipeline["name"]}
    owners = result["owners"] = dict(result.get("owners") or {})
    for user in context["users"] + [context["user"]]:
        key = str(user["id"])
        owners.setdefault(key, {"name": person_name(user.get("name"))})
    result["authenticated_user"] = {"id": context["user"]["id"], "name": person_name(context["user"].get("name"))}
    if not result.get("timezone") and context.get("timezone"):
        result["timezone"] = context["timezone"]
        result["timezone_source"] = "current_user.timezone_name"
    if not configured_domain(result) and context.get("domain"):
        result["output"] = {**(result.get("output") or {}), "domain": context["domain"]}
    return result


def named_pipeline_labels(ctx, config):
    for pid in ctx["pipeline_labels"]:
        pipeline = (config.get("pipelines") or {}).get(str(pid)) or {}
        name = pipeline if isinstance(pipeline, str) else pipeline.get("name") or pipeline.get("label")
        if name:
            duplicates = sum((item if isinstance(item, str) else item.get("name") or item.get("label")) == name
                             for item in (config.get("pipelines") or {}).values())
            ctx["pipeline_labels"][pid] = str(name) + (' · duplicate name; choose by recorded context' if duplicates > 1 else '')
        elif str(ctx['pipeline_labels'][pid]).startswith('Pipeline #'):
            ctx['pipeline_labels'][pid] = 'Pipeline name unavailable'
    return ctx


def scope_label(config, lens):
    owners = config.get("owners") or {}
    pipelines = config.get("pipelines") or {}
    def owner(identifier):
        return person_name((owners.get(str(identifier)) or {}).get("name")) or 'Owner name unavailable'
    def pipeline(identifier):
        item = pipelines.get(str(identifier)) or {}
        name = item if isinstance(item, str) else item.get("name") or item.get("label")
        duplicates = sum((value if isinstance(value, str) else value.get("name") or value.get("label")) == name for value in pipelines.values()) if name else 0
        return str(name) + (' · duplicate name; choose by recorded context' if duplicates > 1 else '') if name else 'Pipeline name unavailable'
    kind, identifier = lens.get("type"), str(lens.get("id", "all"))
    if kind == "owner":
        return owner(identifier)
    if kind == "pipeline":
        return pipeline(identifier)
    if kind == "owner_pipeline":
        owner_id, pipeline_id = identifier.split(":", 1)
        return owner(owner_id) + " · " + pipeline(pipeline_id)
    selected = (config.get("scope") or {}).get("pipeline_ids") or []
    return "Team" + (" · " + ", ".join(pipeline(pid) for pid in selected) if selected else "")


def pipeline_ranges(context):
    """Each pipeline's name and stage range from the normalized stages, in the order the brief uses (Pipedrive's order_nr
    when supplied). The setup question quotes these labels, so it never shows a raw stage name."""
    stages = {stage["id"]: stage for stage in context.get("stages") or []}
    if not stages:
        return []
    ordered = stage_context(stages, []).get("pipeline_stage_names") or {}
    names = {pipeline["id"]: pipeline["name"] for pipeline in context.get("pipelines") or []}
    found = []
    for pid, stage_names in ordered.items():
        name = names.get(pid, "Pipeline name unavailable")
        found.append({"id": pid, "name": name, "first_stage": stage_names[0], "last_stage": stage_names[-1],
                      "label": f"{name} ({stage_names[0]} → {stage_names[-1]})"})
    return sorted(found, key=lambda item: names.get(item["id"], ""))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Original account-context response JSON")
    parser.add_argument("--config", help="Shared settings path; save discovery metadata while preserving scope/checkpoints")
    parser.add_argument("--run", help="Retained discovery run; emit a read-back receipt for its context and saved metadata")
    parser.add_argument("--deal-fields", action="store_true", help="Print the account's named deal fields: the options for the priority-fields setup question")
    parser.add_argument("--pipeline-ranges", action="store_true", help="Print each pipeline's label with its stage range, for the scope setup question")
    args = parser.parse_args(argv)
    context = normalize(json.loads(Path(args.input).read_text(encoding="utf-8")))
    if args.deal_fields:
        print(json.dumps({"options": priority_field_options(context), "fields": deal_field_choices(context)}, ensure_ascii=False))
        return 0
    if args.pipeline_ranges:
        print(json.dumps({"pipelines": pipeline_ranges(context)}, ensure_ascii=False))
        return 0
    if args.run:
        if not args.config or not context['available']:
            raise ValueError('A discovery receipt needs available context and saved metadata')
        run_path = Path(args.run).resolve()
        run = json.loads(run_path.read_text(encoding='utf-8'))
        calls = run.get('calls') or []
        if len(calls) != 1 or calls[0].get('tool', '').split('__')[-1] not in ('getAccountContext', 'get_account_context') or calls[0].get('params') != {}:
            raise ValueError('Discovery receipt needs the single retained account-context call')
        filename = calls[0].get('response_file')
        retained = (run_path.parent / filename).resolve() if filename else None
        original = json.loads(Path(args.input).read_text(encoding='utf-8'))
        if not retained or json.loads(retained.read_text(encoding='utf-8')) != original:
            raise ValueError('Discovery run must retain the complete observed context')
        workspace = Path.cwd().resolve()
        paths = {'context': retained, 'run': run_path, 'settings': Path(args.config).resolve()}
        if any(not value.is_relative_to(workspace) or 'sealed' in value.parts for value in paths.values()):
            raise ValueError('Discovery receipt files must be inside the working folder')
    if args.config and context["available"]:
        path = Path(args.config)
        config = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        updated = metadata(config, context)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
        if json.loads(path.read_text(encoding="utf-8")) != updated:
            raise ValueError("Account metadata read-back failed")
    if args.run:
        import hashlib
        packet = {'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
        for key, value in paths.items():
            packet[key] = {'path': str(value.relative_to(workspace)), 'data': json.loads(value.read_text(encoding='utf-8'))}
        print('PIPEDRIVE_DISCOVERY_SNAPSHOT_V1 ' + json.dumps(packet, ensure_ascii=False))
    else:
        print(json.dumps(context, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
