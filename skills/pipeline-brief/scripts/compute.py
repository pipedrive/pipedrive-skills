#!/usr/bin/env python3
"""Deterministic arithmetic for the Pipedrive sales skills.

Standard library only. No network. Reads the raw MCP responses the skill saved to a run
file and the team's config, and prints one JSON document: per-record flags, priority,
at-risk labels, clusters, aggregates, the coverage line and the footer.

    compute.py --mode brief --pass 1 --config CONFIG --input RUN [--today YYYY-MM-DD]
    compute.py --mode brief --pass 2 --config CONFIG --input RUN [--today YYYY-MM-DD]

Run file shape (written by the skill, one entry per tool call):
    {"today": "YYYY-MM-DD", "window_start": "YYYY-MM-DD",
     "calls": [{"tool": "getDeals", "params": {...}, "response": {...}}, ...]}

Pass 1 ranks on free fields and returns `top_n_last_activity_ids` for the skill to
resolve with one getActivities(ids=[...]) call. Pass 2 re-ranks with real last-touch
dates and joins notes. Tool calls and tokens are counted from the run file, never
guessed.
"""

import argparse
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # scripts/ holds the synced pdcore copy
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from account_context import (deal_field_definitions, load_stage_context, metadata, named_pipeline_labels, priority_fields_line,
                             priority_gaps, priority_values, with_context_stages)
from activity_evidence import completed_activity_evidence, DATE_EXPLANATION
from run_state import checkpoint
from pipedrive_links import configured_domain, link_markdown
from final_response import validate_reply  # noqa: E402
from record_presentation import record_refs  # noqa: E402
from pdcore import (md_text,   # noqa: E402  — shared with deal-triage and forecast; source: shared/pdcore.py
    WRITE_TOOLS,
    save_expected_reply,
    framed,
    closing_heading,
    local_today,
    week_phrase,
    week_summary,
    week_window,
    days_ago,
    plural,
    BUYING_SIGNALS,
    MISMATCH_PATTERNS,
    SYSTEM_NOTE_PATTERNS,
    APP_ORIGIN_MARKERS,
    DEFAULT_WEIGHTS,
    TOKEN_CHARS,
    parse_dt,
    to_date,
    days_between,
    iso,
    load_json,
    load_response_file,
    deal_status,
    rows,
    collect,
    stamp_result,
    strip_html,
    clean_notes,
    owner_map,
    owner_name,
    person_name,
    stage_context,
    stage_order_notices,
    GETNOTES_UNAVAILABLE_LINE,
    deal_value,
    is_app_origin,
    in_scope,
    snoozed_until,
    money,
    deal_ref,
)

import context as account_context  # noqa: E402
from rep_language import next_action, rep_signal, rep_text  # noqa: E402

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


LAUNCH_LANGUAGES = {
    "en-US", "en-GB", "en-AU", "de", "fr", "it", "nl", "es", "es-419", "pt-BR",
    "pl", "uk", "cs", "et", "lv", "fi", "sv", "no", "ru", "ja", "zh-TW", "ko",
    "id", "tr", "th",
}
ENGLISH_BUYING_SIGNAL_LANGUAGES = {"en-US", "en-GB", "en-AU"}


def canonical_language(language):
    """Normalize only declared launch locales; legacy `en` uses the maintained US pack."""
    if not isinstance(language, str):
        return None
    raw = language.strip()
    if raw == "en":
        return "en-US"
    for candidate in LAUNCH_LANGUAGES:
        if candidate.lower() == raw.lower():
            return candidate
    return None


def classify_buying_signal(text, language):
    """Classify only against maintained phrases; absence of a pack is never negative evidence."""
    resolved = canonical_language(language)
    if resolved not in ENGLISH_BUYING_SIGNAL_LANGUAGES:
        return {"status": "unavailable", "phrase": None, "language": resolved}
    normalized = text.lower() if isinstance(text, str) else ""
    for phrase in BUYING_SIGNALS:
        if re.search(r"\b" + re.escape(phrase) + r"\b", normalized):
            return {"status": "verified", "phrase": phrase, "language": resolved}
    return {"status": "verified", "phrase": None, "language": resolved}


def identity_source(context):
    """Select a versioned account identity only when context has been accepted."""
    if isinstance(context, dict) and context.get("available") and (context.get("user") or {}).get("id") is not None:
        return "account_context"
    return "confirmed_note"


def effective_timezone(context_value, config_value):
    """Prefer a valid account timezone, retaining the local verified setting as fallback."""
    for candidate in (config_value, context_value, "UTC"):
        if not isinstance(candidate, str) or not candidate:
            continue
        try:
            ZoneInfo(candidate)
            return candidate
        except ZoneInfoNotFoundError:
            continue
    return "UTC"


def overdue_date(value, timezone):
    """Return the local account date of a timestamp; malformed values remain unavailable."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return to_date(value)
    if parsed.tzinfo is None:
        return parsed.date()
    try:
        return parsed.astimezone(ZoneInfo(effective_timezone(timezone, None))).date()
    except ZoneInfoNotFoundError:
        return parsed.date()


def effective_owner_scope(request, role, current_user_id):
    """Resolve overdue scope without widening an ambiguous rep request to the whole team."""
    text = request.lower() if isinstance(request, str) else ""
    named = re.search(r"\b(?:owner(?:[_ ]?id)?|user)\s*(?:#|:)?\s*(\d+)\b", text)
    if named:
        return {"kind": "named_owner", "owner_ids": [int(named.group(1))]}
    if any(term in text for term in (" team", "all owners", "everyone", "whole team")) or role == "manager":
        return {"kind": "team", "owner_ids": []}
    owner_ids = [int(current_user_id)] if current_user_id is not None else []
    return {"kind": "self", "owner_ids": owner_ids}


def display_date(value, date_format):
    """Format a visible date while retaining ISO values in rule evidence and diagnostics."""
    parsed = to_date(value) if not isinstance(value, date) else value
    if parsed is None:
        return None
    formats = {
        "DD/MM/YYYY": "%d/%m/%Y",
        "MM/DD/YYYY": "%m/%d/%Y",
        "YYYY-MM-DD": "%Y-%m-%d",
    }
    return parsed.strftime(formats.get(date_format, "%Y-%m-%d"))


# ----------------------------------------------------------------------------- deals


def analyse_deals(tables, stages, ctx, config, today, window_start, kept_notes, pass_no, unavailable_rules=None,
                  buying_signal_language="en", timezone="UTC"):
    unavailable_rules = set(unavailable_rules or [])
    silence_days = config.get("silence_days")
    flag_no_next = bool(config.get("flag_no_next_activity"))
    slack = config.get("close_slip_slack_days")
    ssd = config.get("stage_stale_days") or {}
    use_rot = ssd.get("use_pipedrive_rotting", True)
    single_from = config.get("single_thread_from_stage_id")
    check_mismatch = bool(config.get("flag_notes_stage_mismatch"))
    value_floor = config.get("value_floor")
    weights = dict(DEFAULT_WEIGHTS, **(config.get("ranking") or {}))
    motion = config.get("sales_motion") or "engagement_led"

    acts = tables["activities"]
    evidence_state = contact_evidence_state(list(acts.values()), today, config.get("contact_evidence"))
    done_by_deal = defaultdict(list)
    undone_by_deal = defaultdict(list)
    for a in acts.values():
        if a.get("deal_id") is None and a.get("lead_id") is None:
            continue  # calendar noise
        (done_by_deal if a.get("done") else undone_by_deal)[a.get("deal_id")].append(a)

    notes_by_deal = defaultdict(list)
    for n in kept_notes:
        if n.get("deal_id") is not None:
            notes_by_deal[n["deal_id"]].append(n)

    deals = []
    excluded = Counter()
    for d in tables["open_deals"].values():
        silence_days = d.get('_silence_days_override', config.get('silence_days')) if config.get('_workflow_v2') else config.get('silence_days')
        if not in_scope(d, config):
            excluded["scope"] += 1
            continue
        if d.get("is_archived"):
            excluded["archived"] += 1
            continue
        until = snoozed_until(config, d["id"], today)
        if until:
            excluded["snoozed"] += 1
            continue
        stage = stages.get(d.get("stage_id")) or {}
        pid = d.get("pipeline_id") or stage.get("pipeline_id")
        order_known = pid not in ctx["order_unknown"]
        order_nr = stage.get("order_nr") or 0
        max_order = ctx["max_order"].get(pid, 1)
        depth = ((order_nr / max_order) if max_order else 0.0) if order_known else None
        value = deal_value(d)
        add_date = to_date(d.get("add_time"))
        update_date = to_date(d.get("update_time"))

        activity_dates = completed_activity_evidence(done_by_deal.get(d["id"], []), today.isoformat())
        la = acts.get(d.get("last_activity_id")) if d.get("last_activity_id") is not None else None
        if not activity_dates["last_completed_activity_date"] and la:
            activity_dates = completed_activity_evidence([la], today.isoformat())
        last_done = to_date(activity_dates["last_completed_activity_date"])
        source = "done activity" if last_done else None
        inbound = to_date(d.get("last_incoming_mail_time"))
        candidates = [x for x in (last_done, inbound) if x]
        if candidates:
            last_touch = max(candidates)
            if inbound and last_touch == inbound:
                source = "inbound email"
        else:
            last_touch = update_date
            source = "update_time (fallback)"
        days_silent = days_between(today, last_touch)

        moved = to_date(d.get("stage_change_time"))
        never_moved = moved is None
        days_in_stage = days_between(today, moved or add_date)

        ecd = to_date(d.get("expected_close_date"))
        slipped_days = days_between(today, ecd) if ecd and ecd < today else 0

        has_next = d.get("next_activity_id") is not None
        activities_count = d.get("activities_count")
        if activities_count is None:
            activities_count = len(done_by_deal.get(d["id"], [])) + len(undone_by_deal.get(d["id"], []))
        undone_open = [a for a in undone_by_deal.get(d["id"], [])]
        overdue = [
            a for a in undone_open
            if overdue_date(a.get("due_date"), timezone) and overdue_date(a["due_date"], timezone) < today
        ]

        flags = []
        if ("R1" not in unavailable_rules and evidence_state != "unverified" and silence_days is not None
                and days_silent is not None and days_silent >= silence_days):
            flags.append({"id": "R1", "flag": "SILENT", "days": days_silent, "since": iso(last_touch),
                          "source": source, "threshold": silence_days})
        if flag_no_next and "R2" not in unavailable_rules and "next_activity_id" in d and not has_next:
            flags.append({"id": "R2", "flag": "NO_NEXT"})
        stale_threshold = None
        if use_rot and stage.get("is_deal_rot_enabled") and stage.get("days_to_rotten"):
            stale_threshold = stage["days_to_rotten"]
        elif (ssd.get("by_stage_id") or {}).get(str(d.get("stage_id"))) is not None:
            stale_threshold = ssd["by_stage_id"][str(d.get("stage_id"))]
        elif ssd.get("default") is not None:
            stale_threshold = ssd["default"]
        if stale_threshold is not None and days_in_stage is not None and days_in_stage >= stale_threshold:
            flags.append({"id": "R3", "flag": "STAGE_STALE", "days": days_in_stage, "threshold": stale_threshold,
                          "since": iso(moved or add_date), "never_moved": never_moved})
        if slack is not None and ecd and slipped_days > slack:
            flags.append({"id": "R4", "flag": "CLOSE_SLIPPED", "days": slipped_days, "expected_close_date": iso(ecd)})
        if order_known and single_from is not None and type(d.get("participants_count")) is int and 0 <= d["participants_count"] <= 1:
            from_stage = stages.get(single_from) or {}
            if from_stage.get("pipeline_id") == pid and order_nr >= (from_stage.get("order_nr") or 0):
                flags.append({"id": "R5", "flag": "SINGLE_THREAD", "participants": d.get("participants_count") or 0})
        mismatch_note = None
        if check_mismatch and "R7" not in unavailable_rules:
            for n in notes_by_deal.get(d["id"], []):
                low = n["text"].lower()
                low = re.sub(r"lost reason:.*", "", low)
                if any(re.search(p, low) for p in MISMATCH_PATTERNS):
                    mismatch_note = n
                    break
            if mismatch_note:
                flags.append({"id": "R7", "flag": "NOTES_MISMATCH", "note_date": mismatch_note["add_time"],
                              "excerpt": mismatch_note["text"][:120]})

        buying = None
        buying_evidence = classify_buying_signal("", buying_signal_language)
        if config.get("buying_signal_evidence", True) and "R7" not in unavailable_rules:
            for n in notes_by_deal.get(d["id"], []):
                low = n["text"].lower()
                low = re.sub(r"lost reason:.*", "", low)
                signal = classify_buying_signal(low, buying_signal_language)
                buying_evidence = signal
                if signal["status"] == "unavailable":
                    break
                if signal["phrase"]:
                    buying = {"phrase": signal["phrase"], "note_date": n["add_time"],
                              "language": signal["language"]}
                if buying:
                    break

        # probability
        p = None
        if d.get("probability") is not None:
            p = float(d["probability"]) / 100.0
        elif ctx["stage_probabilities_vary"] and stage.get("deal_probability") is not None:
            p = float(stage["deal_probability"]) / 100.0

        deals.append({
            "id": d["id"], "title": d.get("title"), "value": value, "currency": d.get("currency"),
            "owner_id": d.get("owner_id"), "person_id": d.get("person_id"), "org_id": d.get("org_id"),
            "pipeline_id": pid, "stage_id": d.get("stage_id"), "stage": stage.get("name"),
            "stage_depth": None if depth is None else round(depth, 3),
            "is_first_stage": order_known and ctx["first_stage"].get(pid) == d.get("stage_id"),
            "add_time": d.get("add_time"), "add_date": iso(add_date), "update_date": iso(update_date),
            "expected_close_date": iso(ecd), "days_silent": days_silent, "last_touch": iso(last_touch),
            "last_touch_source": source, **activity_dates, "contact_evidence": evidence_state,
            "days_in_stage": days_in_stage, "never_moved": never_moved,
            "stale_threshold": stale_threshold,
            "has_next": has_next, "next_activity_known": "next_activity_id" in d,
            "next_activity_id": d.get("next_activity_id"),
            "undone_activities_count": d.get("undone_activities_count"),
            "activities_count": activities_count,
            "undone_open": len(undone_open), "overdue": len(overdue),
            "notes_count": d.get("notes_count"), "participants_count": d.get("participants_count"),
            "p": p, "flags": flags, "at_risk": any(f["id"] in ("R1", "R2") for f in flags),
            "buying_signal": buying, "buying_signal_evidence": {
                "status": buying_evidence["status"], "language": buying_evidence["language"],
            }, "app_origin": is_app_origin(d),
            "origin": d.get("origin"), "channel_id": d.get("channel_id"),
            "below_floor": value_floor is not None and value < value_floor,
            "last_activity_id": d.get("last_activity_id"),
            "closing_this_week": bool(ecd and today <= ecd <= week_window(today)[1]),
            "priority_fields": priority_values(d, config.get("priority_fields"), config.get("_deal_field_definitions") or deal_field_definitions(config), timezone),
        })

    priority_gaps(deals, config.get("priority_fields"))

    # clusters and shells
    clusters = []
    groups = defaultdict(list)
    for dl in deals:
        if (dl["activities_count"] or 0) == 0 and dl.get("add_time"):
            dt = parse_dt(dl["add_time"])
            key = (dt.strftime("%Y-%m-%d %H:%M") if dt else dl["add_time"], dl["stage_id"])
            groups[key].append(dl)
    clustered = set()
    for (minute, stage_id), members in groups.items():
        if len(members) >= 3:
            clusters.append({"kind": "import_cluster", "created": minute, "stage": members[0]["stage"],
                             "count": len(members), "value": round(sum(m["value"] for m in members), 2),
                             "deal_ids": [m["id"] for m in members]})
            clustered.update(m["id"] for m in members)
    shells = [dl for dl in deals if dl["app_origin"] and dl["person_id"] is None and dl["org_id"] is None
              and (dl["notes_count"] or 0) == 0 and (dl["activities_count"] or 0) == 0 and dl["id"] not in clustered]
    if shells:
        by_channel = defaultdict(list)
        for s in shells:
            by_channel[s.get("channel_id") or s.get("origin") or "integration"].append(s)
        for ch, members in by_channel.items():
            clusters.append({"kind": "integration_shell", "channel": ch, "count": len(members),
                             "value": round(sum(m["value"] for m in members), 2), "deal_ids": [m["id"] for m in members]})
            clustered.update(m["id"] for m in members)
    for dl in deals:
        dl["in_cluster"] = dl["id"] in clustered

    # ranking
    rankable = [dl for dl in deals if not dl["in_cluster"] and not dl["below_floor"]]
    max_value = max((dl["value"] for dl in rankable), default=0.0)
    p_available = any(dl["p"] is not None for dl in rankable)
    for dl in rankable:
        v_term = math.sqrt(dl["value"] / max_value) if max_value > 0 and dl["value"] > 0 else 0.0
        neglect = min((dl["days_silent"] or 0) / 90.0, 1.0)
        score = weights["value"] * v_term + weights["depth"] * (dl["stage_depth"] or 0.0) + weights["neglect"] * neglect
        if dl["p"] is not None:
            score += weights["probability"] * dl["p"]
        dl["priority"] = round(score, 4)
        dl["call_first"] = False
        if motion in ("speed_to_lead", "hybrid") and dl["is_first_stage"] and (dl["activities_count"] or 0) == 0:
            dl["call_first"] = True
    call_first = sorted([dl for dl in rankable if dl.get("call_first")], key=lambda x: x.get("add_time") or "", reverse=True)
    ranked = sorted([dl for dl in rankable if not dl.get("call_first")],
                    key=lambda x: (-(x["priority"] or 0), -x["value"]))
    return {
        "deals": deals, "ranked": ranked, "call_first_deals": call_first, "clusters": clusters,
        "excluded": dict(excluded), "weights": weights, "p_available": p_available,
        "max_value": max_value, "motion": motion, "pass": pass_no,
        "buying_signal_evidence": {
            "status": classify_buying_signal("", buying_signal_language)["status"],
            "language": classify_buying_signal("", buying_signal_language)["language"],
        },
    }


# ----------------------------------------------------------------------------- leads

def analyse_leads(tables, config, today):
    scope = config.get("scope") or {}
    motion = config.get("sales_motion") or "engagement_led"
    include = scope.get("include_leads")
    if include is None:
        include = motion in ("speed_to_lead", "hybrid")
    if not include:
        return {"leads": [], "untouched": [], "included": False}
    oids = scope.get("owner_ids") or []
    leads = []
    for l in tables["leads"].values():
        if l.get("is_archived"):
            continue
        if oids and l.get("owner_id") not in oids:
            continue
        val = l.get("value") or {}
        add_date = to_date(l.get("add_time"))
        untouched = l.get("next_activity_id") is None and not l.get("was_seen")
        leads.append({
            "id": l["id"], "title": l.get("title"), "owner_id": l.get("owner_id"),
            "value": float(val.get("amount") or 0) if isinstance(val, dict) else 0.0,
            "currency": val.get("currency") if isinstance(val, dict) else None,
            "add_time": l.get("add_time"), "add_date": iso(add_date),
            "age_days": days_between(today, add_date), "was_seen": bool(l.get("was_seen")),
            "has_next": l.get("next_activity_id") is not None, "untouched": untouched,
            "person_id": l.get("person_id"), "organization_id": l.get("organization_id"),
            "source_deal_id": l.get("source_deal_id"), "cc_email": l.get("cc_email"),
        })
    leads.sort(key=lambda x: x.get("add_time") or "", reverse=True)  # newest first
    return {"leads": leads, "untouched": [l for l in leads if l["untouched"]], "included": True}


# ----------------------------------------------------------------------------- aggregates

def calibration_suggestions(deals, weights, complete):
    """Measure whether value or stage-staleness settings merit user review.

    This only reports an observation. It never changes ranking weights or thresholds.
    """
    rankable = [deal for deal in deals if not deal.get("in_cluster") and not deal.get("below_floor")
                and deal.get("priority") is not None]
    currencies = {deal.get("currency") for deal in rankable}
    value_result = {"eligible": len(rankable), "complete": complete, "same_currency": len(currencies) == 1,
                    "top_five_unchanged": False, "within_one_rank_share": 0.0, "suggest_review": False}
    if complete and len(rankable) >= 30 and len(currencies) == 1:
        current = sorted(rankable, key=lambda deal: (-(deal["priority"] or 0), -deal.get("value", 0), deal["id"]))
        without_value = sorted(
            rankable,
            key=lambda deal: (
                -(weights["depth"] * (deal.get("stage_depth") or 0)
                  + weights["neglect"] * min((deal.get("days_silent") or 0) / 90.0, 1.0)
                  + (weights["probability"] * deal["p"] if deal.get("p") is not None else 0)),
                deal["id"],
            ),
        )
        old_positions = {deal["id"]: index for index, deal in enumerate(current)}
        new_positions = {deal["id"]: index for index, deal in enumerate(without_value)}
        unchanged = [deal["id"] for deal in current[:5]] == [deal["id"] for deal in without_value[:5]]
        within_one = sum(abs(old_positions[deal_id] - position) <= 1 for deal_id, position in new_positions.items())
        value_result.update({
            "top_five_unchanged": unchanged,
            "within_one_rank_share": round(within_one / len(rankable), 3),
            "suggest_review": unchanged and within_one / len(rankable) >= 0.9,
        })

    by_stage = defaultdict(list)
    for deal in deals:
        if deal.get("stage_id") is not None and deal.get("days_in_stage") is not None and deal.get("stale_threshold") is not None:
            by_stage[deal["stage_id"]].append(deal)
    stage_result = []
    for stage_id, members in sorted(by_stage.items(), key=lambda item: str(item[0])):
        flagged = sum(any(flag["id"] == "R3" for flag in deal.get("flags", [])) for deal in members)
        stage_result.append({
            "stage_id": stage_id, "stage": members[0].get("stage"), "threshold": members[0].get("stale_threshold"),
            "eligible": len(members), "flagged": flagged,
            "suggest_review": complete and len(members) >= 10 and flagged / len(members) >= 0.7,
        })
    return {"value": value_result, "stage": stage_result}

def value_by_currency(items):
    """Each currency is summed on its own; one total across currencies would add pounds to euros."""
    totals = defaultdict(float)
    for item in items:
        totals[item.get("currency") or "?"] += item.get("value") or 0
    return {currency: round(total, 2) for currency, total in totals.items()}

def money_split(totals):
    return " · ".join(money(value, currency) for currency, value in totals.items() if value)

def aggregates(deal_result, lead_result, tables, stages, owners, today, window_start, timezone="UTC", overdue_scope=None):
    deals = deal_result["deals"]
    by_stage = defaultdict(lambda: {"count": 0, "value": defaultdict(float)})
    per_currency = defaultdict(float)
    for dl in deals:
        by_stage[dl["stage"] or "?"]["count"] += 1
        by_stage[dl["stage"] or "?"]["value"][dl["currency"] or "?"] += dl["value"]
        per_currency[dl["currency"] or "?"] += dl["value"]
    by_stage_out = {k: {"count": v["count"], "value": {c: round(x, 2) for c, x in v["value"].items()}} for k, v in by_stage.items()}

    won = [d for d in tables["won_deals"].values() if to_date(d.get("won_time")) and to_date(d["won_time"]) >= window_start]
    lost = [d for d in tables["lost_deals"].values() if to_date(d.get("lost_time")) and to_date(d["lost_time"]) >= window_start]
    created = [dl for dl in deals if to_date(dl["add_date"]) and to_date(dl["add_date"]) >= window_start]
    entered = []
    for dl in deals:
        sct = to_date(tables["open_deals"][dl["id"]].get("stage_change_time"))
        if sct and sct >= window_start:
            entered.append(dl)
    won_items = [{"id": d["id"], "title": d.get("title"), "value": deal_value(d), "currency": d.get("currency"), "won_time": iso(to_date(d.get("won_time")))} for d in won]
    lost_items = [{"id": d["id"], "title": d.get("title"), "value": deal_value(d), "currency": d.get("currency"), "lost_time": iso(to_date(d.get("lost_time"))), "lost_reason": d.get("lost_reason")} for d in lost]
    movement = {
        "won": {"count": len(won), "value_by_currency": value_by_currency(won_items), "items": won_items},
        "lost": {"count": len(lost), "value_by_currency": value_by_currency(lost_items), "items": lost_items},
        "created": {"count": len(created), "ids": [dl["id"] for dl in created]},
        "entered_stage": {"count": len(entered), "items": [{"id": dl["id"], "title": dl["title"], "stage": dl["stage"]} for dl in entered]},
    }
    closing = [dl for dl in deals if dl["closing_this_week"]]
    closing_items = [{"id": dl["id"], "title": dl["title"], "value": dl["value"], "currency": dl["currency"], "expected_close_date": dl["expected_close_date"], "at_risk": dl["at_risk"]} for dl in closing]
    closing_out = {"count": len(closing), "value_by_currency": value_by_currency(closing_items), "week": week_summary(today),
                   "at_risk": [{"id": dl["id"], "title": dl["title"]} for dl in closing if dl["at_risk"]],
                   "items": closing_items,
                   "first_stage_review": [{"id": dl["id"], "title": dl["title"], "stage": dl["stage"],
                                           "expected_close_date": dl["expected_close_date"]}
                                          for dl in closing if dl["is_first_stage"]]}

    open_ids = {dl["id"] for dl in deals}
    overdue_by_owner = Counter()
    overdue_items = []
    for a in tables["activities"].values():
        if a.get("done") or a.get("deal_id") is None or a["deal_id"] not in open_ids:
            continue
        if overdue_scope and overdue_scope["kind"] in {"self", "named_owner"}:
            if not overdue_scope["owner_ids"] or a.get("owner_id") not in overdue_scope["owner_ids"]:
                continue
        dd = overdue_date(a.get("due_date"), timezone)
        if dd and dd < today:
            owner = owner_name(owners, a.get("owner_id")) or f"Owner {a.get('owner_id')}"
            overdue_by_owner[owner] += 1
            deal = tables["open_deals"].get(a["deal_id"], {})
            overdue_items.append({
                "id": a.get("id"), "subject": a.get("subject") or "Untitled activity",
                "due_date": iso(dd), "days_overdue": days_between(today, dd), "owner": owner,
                "deal_id": a.get("deal_id"), "deal_title": deal.get("title"),
                "next_action": "Review the activity, then complete, reschedule, or contact the buyer.",
            })
    overdue_items.sort(key=lambda item: (item["due_date"] or "", str(item["id"])))

    values = sorted((dl["value"] for dl in deals), reverse=True)
    total_value = sum(values)
    single_currency = len({dl["currency"] for dl in deals}) <= 1
    concentration = {
        "top1_share": round(values[0] / total_value, 3) if single_currency and total_value and values else None,
        "top2_share": round(sum(values[:2]) / total_value, 3) if single_currency and total_value and len(values) >= 2 else None,
        "all_zero": bool(deals) and total_value == 0,
    }
    return {
        "open_count": len(deals), "open_value_by_currency": {c: round(v, 2) for c, v in per_currency.items()},
        "by_stage": by_stage_out, "movement": movement, "closing_this_week": closing_out,
        "overdue_activities": {"total": len(overdue_items), "by_owner": dict(overdue_by_owner),
                               "items": overdue_items, "scope": "linked to fetched open deals"},
        "value_concentration": concentration, "leads_count": len(lead_result["leads"]),
        "untouched_leads": len(lead_result["untouched"]),
    }


# ----------------------------------------------------------------------------- output


def flag_text(f):
    return rep_text(f)


def render_overdue_answer(result, show=10):
    """Render the on-request overdue view from the bounded data already collected."""
    overdue = (result.get("aggregates") or {}).get("overdue_activities") or {}
    total = overdue.get("total", 0)
    owners = ", ".join(f"{owner}: {count}" for owner, count in overdue.get("by_owner", {}).items()) or "none"
    scope = result.get("overdue_scope") or {}
    kind = scope.get("kind")
    scope_text = kind if kind in {"self", "named_owner", "team"} else overdue.get("scope", "unavailable")
    lines = [f"Overdue work: {plural(total, 'linked activity', 'linked activities')} among fetched open deals.",
             f"Owners: {owners}.", f"Scope: {scope_text}." ]
    for item in overdue.get("items", [])[:show]:
        related = item.get("deal_title") or f"Deal #{item.get('deal_id')}"
        lines.append(
            f"{md_text(item['subject'])} — due {item['due_date']} ({plural(item['days_overdue'], 'day')} overdue) · "
            f"{md_text(item['owner'])} · {md_text(related)}. {item['next_action']}"
        )
    return "\n".join(lines)


def rule_evidence_line(ledger):
    """Summarise rule coverage without converting missing evidence into a pass."""
    parts = []
    for rule in sorted(ledger):
        counts = ledger[rule]
        eligible = counts["eligible"]
        unavailable = counts["unavailable"]
        if unavailable:
            parts.append(f"{rule} unavailable {unavailable}/{eligible}")
        else:
            parts.append(
                f"{rule} {counts['evaluated']}/{eligible} evaluated "
                f"({counts['verified']} verified, {counts['proxy']} proxy)"
            )
    return "Rule evidence: " + (" · ".join(parts) if parts else "no rules enabled")


def priority_recommendation(deal_result):
    """Lead a healthy-evidence brief with one action, without exposing score internals."""
    candidates = [deal for deal in deal_result["ranked"] if deal["flags"]] or deal_result["ranked"]
    if not candidates:
        return "Priority recommendation: review the newest open work and confirm the next customer step."
    deal = candidates[0]
    reason = flag_text(deal["flags"][0]) if deal["flags"] else "review the stage and next step"
    return f"Priority recommendation: {md_text(deal['title'])} — {reason}."


def render_visible_brief(presentation):
    """Render the rep-facing brief while retaining diagnostics in the saved result."""
    visibility = presentation["visibility"]
    lines = []
    if visibility.get("partial_coverage"):
        lines.extend([visibility["partial_coverage"], visibility.get("not_fetched")])
    lines.extend(presentation["body"])
    return "\n".join(line for line in lines if line)


def render_rep_brief(deal_result, lead_result, aggs, config, ctx, visibility, today, window_start,
                     partial, evidence_unavailable, show, locale, optional_limits, owners=None):
    """Put the scan boundary and a rep's next actions before calculation details."""
    fetched = aggs["open_count"]
    deal_word = "deal" if fetched == 1 else "deals"
    answer = (f"I checked {fetched} open {deal_word}; more remain unchecked."
              if partial else f"I checked all {fetched} open {deal_word} in the selected scope.")
    lines = [answer]
    if partial:
        lines.append("Partial coverage: the numbers and recommendations below cover only the deals checked.")
    scope = config.get("scope") or {}
    labels = ctx["pipeline_labels"]
    selected = list(scope.get("pipeline_ids") or labels.keys())
    excluded = set(scope.get("exclude_pipeline_ids") or [])
    included = [pid for pid in selected if pid not in excluded]
    all_named = included + [pid for pid in labels if pid not in included]
    counts = Counter(labels.get(pid, str(pid)) for pid in all_named)
    def label(pid):
        name = labels.get(pid, 'Pipeline name unavailable')
        stages = (ctx.get('pipeline_stage_names') or {}).get(pid) or []
        return f"{name} ({stages[0]} → {stages[-1]})" if counts[name] > 1 and stages else name
    effective = ("Selected saved filter" if scope.get("filter_id") is not None
                 else ", ".join(label(pid) for pid in included) or "No pipelines selected")
    excluded_names = ("governed by saved filter" if scope.get("filter_id") is not None
                      else ", ".join(label(pid) for pid in labels if pid not in included) or "none")
    known_owners = {**(config.get('owners') or {}), **(owners or {})}
    owners = [known_owners.get(str(identifier)) or {} for identifier in scope.get('owner_ids') or []]
    owner_names = ', '.join(person_name((owner if isinstance(owner, str) else owner.get('name'))) or 'Owner name unavailable' for owner in owners) or 'Team deals'
    lines.extend([f"Pipeline brief — {display_date(today, (locale or {}).get('date_format'))}",
                  f"Whose deals: {owner_names}",
                  f"Scope: {effective}", f"Excluded pipelines: {excluded_names}"])
    value_str = " · ".join(money(v, c) for c, v in aggs["open_value_by_currency"].items()) or "no value set"
    lines.append(f"{'Open among fetched records' if partial else 'Open'}: {fetched} {deal_word}, {value_str}")
    lines.extend(ctx.get("stage_order_notices", []))
    lines.extend(ctx.get("unavailable_notices", []))
    if lead_result.get("included") and deal_result["motion"] in ("speed_to_lead", "hybrid"):
        untouched = lead_result.get("untouched") or []
        if untouched:
            lines.extend(["", f"New leads to call ({len(untouched)} total):"])
            for lead in untouched[:3]:
                lines.append(f"- {md_text(lead['title'])}: make a first contact call; created {days_ago(lead['age_days'])} with no activity scheduled.")
    if evidence_unavailable:
        lines.append("Customer contact or next-step evidence was unavailable. Check those records before prioritising them.")
    else:
        candidates = [deal for deal in deal_result["ranked"] if deal["flags"]] or deal_result["ranked"]
        lines.extend(["", "What to do next:"])
        if candidates:
            lines.append("Ordered by your saved priority settings, not by days since the last recorded activity alone.")
        for index, deal in enumerate(candidates[:min(show, 5)], 1):
            signals = [rep_signal(flag) for flag in deal["flags"]]
            if signals:
                action = next_action(deal).rstrip(".")
                reason = "; ".join(signal["headline"].lower() for signal in signals[:2])
                detail = ("Check activity history before treating this update as buyer silence."
                          if signals[0].get("source") == "update_time (fallback)" else
                          "CRM activity dates do not establish buyer contact." if signals[0].get("audit_id") == "R1" else "")
                status = "is at risk" if deal.get("at_risk") else "needs attention"
                lines.append(f"{index}. {deal_ref(deal, config)} {status}. "
                             f"Deal stage: {deal['stage']}. {reason.capitalize()}. {detail} {action}.")
            else:
                lines.append(f"{index}. {deal_ref(deal, config)} — No configured risk was found. {next_action(deal)}")
            if deal.get("priority_fields"):
                lines.append("   " + priority_fields_line(deal["priority_fields"]))
        if not candidates:
            lines.append("No named deal needs an action from the checked records.")
    if not partial:
        movement = aggs["movement"]
        closing = aggs["closing_this_week"]
        lines.extend(["", "What changed and what closes soon:",
                      f"{movement['won']['count']} won; {movement['lost']['count']} lost; "
                      f"{movement['entered_stage']['count']} moved stage since {iso(window_start)}.",
                      f"{plural(closing['count'], 'open deal')} {'is' if closing['count'] == 1 else 'are'} due to close {week_phrase(closing)}; "
                      f"{len(closing['at_risk'])} {'has' if len(closing['at_risk']) == 1 else 'have'} a flagged risk."])
        for item in closing.get("items", [])[:3]:
            title = item.get("title") or f"Deal #{item.get('id')}"
            lines.append(f"- {closing_heading(closing)}: {title} — {item.get('expected_close_date') or 'close date unavailable'}, "
                         f"{money(item.get('value'), item.get('currency'))}.")
        for status in ("won", "lost", "created"):
            for item in movement.get(status, {}).get("items", [])[:2]:
                title = item.get("title") or f"Deal #{item.get('id')}"
                lines.append(f"- {status.capitalize()}: {title}.")
        if closing.get("first_stage_review"):
            early = len(closing['first_stage_review'])
            lines.append(f"Close dates to check: {plural(early, 'early-stage deal')} {'is' if early == 1 else 'are'} due {week_phrase(closing)}.")
    # Keep per-deal reasons above; run-level audit details remain in the saved JSON.
    if deal_result.get("buying_signal_evidence", {}).get("status") == "unavailable":
        language = deal_result["buying_signal_evidence"].get("language") or "unknown language"
        lines.append(f"Buying-signal evidence: unavailable for {language}; notes were not assessed.")
    if optional_limits:
        lines.append("Optional enrichment unavailable: " + "; ".join(optional_limit_detail(item) for item in optional_limits))
    if partial:
        lines.append(visibility["not_fetched"])
    return "\n".join(lines)


def partial_recommendations(deal_result, config, show):
    """Keep D1 recommendations inside the fetched boundary and attached to their evidence."""
    candidates = [deal for deal in deal_result["ranked"] if deal["flags"]] or deal_result["ranked"]
    lines = ["Recommendations among fetched records:"]
    for index, deal in enumerate(candidates[:min(show, 5)], 1):
        evidence = " · ".join(flag_text(flag) for flag in deal["flags"])
        if not evidence:
            evidence = "No current rule signal; review the fetched record's stage and next step"
        lines.append(f"{index}. {md_text(deal['title'])} ({deal_ref(deal, config)}) — Evidence: {evidence}")
    if len(lines) == 1:
        lines.append("No named fetched records are available for a recommendation.")
    return lines


def optional_limit_detail(item):
    """Render an optional-limit recovery message without rewriting server guidance."""
    classification = item.get("classification")
    rule_names = {"R1": "recent CRM touch", "R2": "next activity", "R3": "time in stage",
                  "R4": "passed close date", "R5": "contact coverage", "R7": "note and stage consistency"}
    affected = ", ".join(rule_names.get(rule, rule) for rule in item["rules"])
    if not classification:
        return f"{item['tool']} hit a quota or limit ({affected} unavailable)"
    detail = (f"{item['tool']} hit {classification['kind'].replace('_', ' ')} "
              f"({affected} unavailable)")
    if classification.get("guidance"):
        detail += f". Server guidance: {classification['guidance']}"
    return detail


def build_output(run, tables, config, ctx, deal_result, lead_result, aggs, owners, ledger, calibration, today, window_start, show,
                 window_provided=False, optional_limits=None, locale=None):
    n_records = aggs["open_count"] + aggs["leads_count"]
    style = (config.get("output") or {}).get("style") or "auto"
    if tables["open_cursor_unfollowed"]:
        mode = "narrow"
    elif style == "short" or (style == "auto" and n_records < 30):
        mode = "recall"
    else:
        mode = "triage"

    fetched = len(tables["open_deals"])
    coverage = (f"Coverage: fetched {fetched} open deals; more records remain"
                if tables["open_cursor_unfollowed"] else f"Coverage: fetched {fetched}/{fetched} open deals")
    parts = [coverage, f"{tables['tool_calls']} tool calls",
             f"window since {iso(window_start)}", f"~{tables['tokens']:,} tokens read"]
    if tables["stages_source"] == "account_context":
        parts.append("stages from account context")
    if tables["failed_calls"]:
        parts.append("failed after retry: " + ", ".join(tables["failed_calls"]))
    coverage_line = " · ".join(parts)
    partial_line = None
    if tables["open_cursor_unfollowed"]:
        oldest_ids = set()
        newest_ids = set()
        for call in run.get("calls", []):
            params = call.get("params") or {}
            if ((call.get("tool") or "").split("__")[-1] != "getDeals"
                    or deal_status(params.get("status", "open")) != "open"):
                continue
            response = call.get("response")
            if response is None and call.get("response_file"):
                response = load_response_file(call["response_file"])
            if not isinstance(response, dict) or response.get("success") is False or "error" in response:
                continue
            ids = {deal["id"] for deal in rows(response) if isinstance(deal, dict) and deal.get("id") is not None}
            (newest_ids if params.get("sort_direction") == "desc" else oldest_ids).update(ids)
        newest_page_fetched = bool(newest_ids - oldest_ids)
        unread = ("open deals between the oldest- and newest-updated pages"
                  if newest_page_fetched else "the most recently updated open deals")
        partial_line = (f"Not fetched: {unread}. Totals and recommendations cover checked deals only; "
                        "'what moved' and 'closing this week' may be incomplete. "
                        "Use a saved Pipedrive filter to focus the next brief on a smaller group of deals.")

    defaults = []
    if config.get("ranking") in (None, {}) or config.get("ranking") == DEFAULT_WEIGHTS:
        defaults.append("ranking weights")
    if (config.get("output") or {}).get("style", "auto") == "auto":
        defaults.append("output shape by record count")
    if not window_provided and not checkpoint(config, "pipeline-brief"):
        defaults.append("window 7 days")
    p_line = None
    if not deal_result["p_available"]:
        p_line = "probability not used — stage probabilities are not configured and no deal has its own"
    elif not ctx["stage_probabilities_vary"]:
        p_line = "probability: deal-level only (stage probabilities do not vary)"
    ranking_configuration = "default" if config.get("ranking") in (None, {}) or config.get("ranking") == DEFAULT_WEIGHTS else "custom"
    footer = (f"Footer: motion {deal_result['motion']} · ranking configuration: {ranking_configuration}"
              + (f" · {p_line}" if p_line else "")
              + " · defaults in force: " + (", ".join(defaults) if defaults else "none"))

    excluded_pipelines = []
    scope = config.get("scope") or {}
    show_pipeline_context = len(scope.get("pipeline_ids") or ctx["pipeline_labels"]) > 1
    for pid, label in ctx["pipeline_labels"].items():
        if (scope.get("pipeline_ids") and pid not in scope["pipeline_ids"]) or pid in (scope.get("exclude_pipeline_ids") or []):
            excluded_pipelines.append(label)

    has_unavailable_rule = any(counts["unavailable"] for counts in ledger.values())
    engagement_evidence_unavailable = any(
        (ledger.get(rule) or {}).get("unavailable", 0)
        for rule in ("R1", "R2")
    )
    visibility = {
        "rule_evidence": rule_evidence_line(ledger),
        "partial_coverage": ("Partial coverage: recommendations and totals are limited to fetched records."
                             if tables["open_cursor_unfollowed"] else None),
        "not_fetched": partial_line,
        "fetch_policy": (render_budget_status(diagnostics_for(run, config.get("fetch")))
                         if tables["open_cursor_unfollowed"] else None),
        "coverage": coverage_line,
        "footer": footer,
    }
    md = []
    if not tables["open_cursor_unfollowed"] and not has_unavailable_rule:
        md.append(priority_recommendation(deal_result))
    scope_labels = [ctx["pipeline_labels"].get(pid, str(pid)) for pid in (scope.get("pipeline_ids") or ctx["pipeline_labels"].keys())]
    effective_scope = f"Saved filter {scope['filter_id']}" if scope.get("filter_id") is not None else ", ".join(scope_labels)
    value_str = " · ".join(money(v, c) for c, v in aggs["open_value_by_currency"].items()) or "no value set"
    visible_date = display_date(today, (locale or {}).get("date_format"))
    md.append(f"Pipeline brief — {visible_date}")
    md.append(f"Scope: {effective_scope}")
    md.append(f"Excluded pipelines: {', '.join(excluded_pipelines) or 'none'}" if not scope.get("filter_id") else "Excluded pipelines: governed by saved filter")
    md.append(f"Motion: {deal_result['motion']}")
    open_label = "Open among fetched records" if tables["open_cursor_unfollowed"] else "Open"
    md.append(f"{open_label}: {plural(aggs['open_count'], 'deal')}, {value_str}" + (f" · {aggs['leads_count']} leads" if lead_result['included'] else ""))
    md.append(f"Window: since {iso(window_start)}")
    md.extend(ctx.get("stage_order_notices", []))
    md.extend(ctx.get("unavailable_notices", []))
    if deal_result["buying_signal_evidence"]["status"] == "unavailable":
        language = deal_result["buying_signal_evidence"]["language"] or "unknown language"
        md.append(f"Buying-signal evidence: unavailable for {language} — no maintained phrase registry.")
    if optional_limits:
        details = "; ".join(optional_limit_detail(item) for item in optional_limits)
        md.append(f"Optional enrichment unavailable: {details}. Core deal results remain available.")
    if aggs["value_concentration"]["all_zero"]:
        md.append("Deal values are all 0 — the value field is unfilled and cannot drive the ranking.")
    elif aggs["value_concentration"]["top2_share"] and aggs["value_concentration"]["top2_share"] >= 0.5:
        md.append(f"Two deals hold {aggs['value_concentration']['top2_share']:.0%} of the open value — read the ranking with that in mind.")

    if tables["open_cursor_unfollowed"]:
        md.append("")
        md.extend(partial_recommendations(deal_result, config, show))
    else:
        if lead_result["included"] and deal_result["motion"] in ("speed_to_lead", "hybrid"):
            cf = lead_result["untouched"] + deal_result["call_first_deals"]
            if cf:
                md.append("")
                md.append(f"New and untouched — call first ({len(cf)} total)")
                for i, item in enumerate(cf[:show], 1):
                    if "untouched" in item:
                        md.append(f"{i}. {md_text(item['title'])} — {money(item['value'], item['currency'])} — lead created "
                                  f"{days_ago(item['age_days'])}, never opened, nothing scheduled")
                    else:
                        md.append(f"{i}. {md_text(item['title'])} — {money(item['value'], item['currency'])}, {item['stage']} — created "
                                  f"{item['add_date']}, no activity yet")
        mv = aggs["movement"]
        md.append("")
        md.append(f"What moved since {iso(window_start)}")
        lost_names = ", ".join(f"{md_text(x['title'])}" + (f" (reason: {md_text(x['lost_reason'])})" if x.get("lost_reason") else "") for x in mv["lost"]["items"][:3])
        won_split = money_split(mv["won"]["value_by_currency"])
        won_summary = f"{mv['won']['count']} won" + (f" ({won_split})" if won_split else "")
        md.append(f"• {won_summary} · {mv['lost']['count']} lost"
                  + (f" ({lost_names})" if lost_names else "") + f" · {mv['entered_stage']['count']} entered a new stage · {mv['created']['count']} created")
        cw = aggs["closing_this_week"]
        md.append("")
        closing_split = money_split(cw["value_by_currency"])
        closing_summary = f"{closing_heading(cw)}: {plural(cw['count'], 'deal')}" + (f", {closing_split}" if closing_split else "")
        md.append(closing_summary + (f" — {len(cw['at_risk'])} at risk ({', '.join(md_text(x['title']) for x in cw['at_risk'])})" if cw["at_risk"] else ""))
        if cw["first_stage_review"]:
            examples = ", ".join(f"{md_text(item['title'])} ({item['stage']}, {item['expected_close_date']})"
                                 for item in cw["first_stage_review"][:3])
            review_count = len(cw["first_stage_review"])
            md.append(f"Close dates to check: {plural(review_count, 'first-stage deal')} {'closes' if review_count == 1 else 'close'} {week_phrase(cw)} — {examples}.")
        ranked = deal_result["ranked"]
        if engagement_evidence_unavailable:
            md.append("")
            md.append("Engagement evidence is unavailable; no priority recommendation or ranked actions are shown.")
        elif mode == "triage":
            md.append("")
            md.append("Needs attention (ranked)")
            shown = [dl for dl in ranked if dl["flags"]][:show] or ranked[:show]
            for i, dl in enumerate(shown, 1):
                bits = [flag_text(f) for f in dl["flags"]]
                stage_context = (f"Pipeline: {ctx['pipeline_labels'].get(dl['pipeline_id'], dl['pipeline_id'])} · Stage: {dl['stage']}"
                                 if show_pipeline_context else dl["stage"])
                md.append(f"{i}. {md_text(dl['title'])} ({deal_ref(dl, config)}) — {money(dl['value'], dl['currency'])}")
                md.append(f"   {stage_context}")
                line = "   Action: " + ("AT RISK · " if dl["at_risk"] else "")
                line += " · ".join(bits) if bits else "No current rule signal; review the stage and next step"
                if dl["buying_signal"]:
                    line += f" ▲ buying signal: \"{dl['buying_signal']['phrase']}\" (note {dl['buying_signal']['note_date']})"
                md.append(line)
        else:
            md.append("")
            md.append("Recall mode (small pipeline — prose, no ranking):")
            flagged = [dl for dl in ranked if dl["flags"]]
            for dl in flagged[:show]:
                stage_context = (f"Pipeline: {ctx['pipeline_labels'].get(dl['pipeline_id'], dl['pipeline_id'])} · Stage: {dl['stage']}"
                                 if show_pipeline_context else dl["stage"])
                md.append(f"• {md_text(dl['title'])} ({deal_ref(dl, config)}, {money(dl['value'], dl['currency'])}, {stage_context}): " + "; ".join(flag_text(f) for f in dl["flags"]) + (" — AT RISK" if dl["at_risk"] else ""))
            if not flagged:
                md.append("• Nothing is flagged under the current thresholds. Walk the never-empty ladder (references/output-format.md).")
        if deal_result["clusters"]:
            md.append("")
            md.append("Clusters")
            for c in deal_result["clusters"]:
                if c["kind"] == "import_cluster":
                    md.append(f"• {plural(c['count'], 'deal')} created {c['created']}, all in {c['stage']}, no activity — looks like an import; not ranked individually")
                else:
                    md.append(f"• {plural(c['count'], 'record')} created by {c['channel']} with no contact, notes or activity — shells; link or clear")
        suggestions = []
        if calibration["value"]["suggest_review"]:
            suggestions.append("Value has little effect on this complete, same-currency ranking. Review the value weight before changing it.")
        for stage in calibration["stage"]:
            if stage["suggest_review"]:
                suggestions.append(
                    f"{stage['stage']}: {stage['flagged']}/{stage['eligible']} eligible deals are stage-stale "
                    f"at the current {stage['threshold']}-day threshold. Review a stage override or disabling R3."
                )
        if suggestions:
            md.append("")
            md.append("Calibration to review")
            md.extend(f"• {suggestion}" for suggestion in suggestions)
    presentation = {"visibility": visibility, "body": md}

    rep_markdown = render_rep_brief(
        deal_result, lead_result, aggs, config, ctx, visibility, today, window_start,
        tables["open_cursor_unfollowed"], engagement_evidence_unavailable, show, locale, optional_limits, owners,
    )
    return {
        "mode": mode, "n_records": n_records, "coverage_line": coverage_line, "partial_line": partial_line,
        "footer": footer, "excluded_pipelines": excluded_pipelines, "presentation": presentation,
        "audit": {"rule_evidence": visibility["rule_evidence"], "footer": footer},
        "markdown": rep_markdown,
        "follow_up_question": ("Would you like me to continue toward a full scan in bounded batches, or narrow the next brief to a pipeline or saved filter? This scan did not return an exact remaining deal count."
                               if tables['open_cursor_unfollowed'] else
                               "Which of these next steps will you own today?" if not engagement_evidence_unavailable else None),
    }


# ----------------------------------------------------------------------------- main

FETCH_DEFAULTS = {"max_response_bytes": 102_400, "reserved_context_tokens": 15_000}
MAX_THROTTLE_RETRY_SECONDS = 60


def classify_limit(response):
    """Classify only a structured 429 limit response without guessing its cause.

    The MCP has not confirmed this response contract.  This deliberately accepts the
    provisional body fields only when a caller has already established that it can
    safely act on a short wait.  All other 429s are safe-stop responses.
    """
    unknown = {"kind": "unknown", "retry_after_seconds": None, "guidance": None}
    if not isinstance(response, dict) or response.get("status") != 429:
        return unknown
    body = response.get("body")
    if not isinstance(body, dict):
        return unknown
    guidance = body.get("guidance") if isinstance(body.get("guidance"), str) else None
    if body.get("allowance_exhausted") is True:
        return {"kind": "allowance_exhausted", "retry_after_seconds": None, "guidance": guidance}
    retry_after = body.get("retry_after_seconds")
    if (body.get("retry_after_seconds_authoritative") is True
            and isinstance(retry_after, int) and not isinstance(retry_after, bool)
            and 0 < retry_after <= MAX_THROTTLE_RETRY_SECONDS):
        return {"kind": "throttle", "retry_after_seconds": retry_after, "guidance": guidance}
    return {"kind": "unknown", "retry_after_seconds": None, "guidance": guidance}


def should_retry_limit(classification, client_capabilities):
    """Allow one wait-and-retry only for a short, classified throttle response."""
    return bool(
        isinstance(classification, dict)
        and classification.get("kind") == "throttle"
        and isinstance(classification.get("retry_after_seconds"), int)
        and not isinstance(classification.get("retry_after_seconds"), bool)
        and 0 < classification["retry_after_seconds"] <= MAX_THROTTLE_RETRY_SECONDS
        and isinstance(client_capabilities, dict)
        and client_capabilities.get("wait") is True
    )


def _pipedrive_allowance(response):
    """Return an allowance only when the MCP explicitly includes one."""
    if not isinstance(response, dict):
        return None
    return response.get("pipedrive_allowance")


def run_diagnostics(run, fetch_config=None):
    """Preserve observed run cost and limits without inventing server allowances."""
    calls = []
    pages = Counter()
    limits = []
    total_elapsed = 0
    response_bytes = 0
    pipedrive_allowance = None
    for index, call in enumerate(run.get("calls", []), 1):
        tool = (call.get("tool") or "").split("__")[-1]
        params = call.get("params") or {}
        response = call.get("response")
        if response is None and call.get("response_file"):
            response = load_response_file(call["response_file"])
        elapsed = call.get("elapsed_ms")
        if isinstance(elapsed, (int, float)):
            total_elapsed += elapsed
        call_response_bytes = len(json.dumps(response, ensure_ascii=False, default=str).encode("utf-8")) if response is not None else 0
        response_bytes = max(response_bytes, call_response_bytes)
        if pipedrive_allowance is None:
            pipedrive_allowance = _pipedrive_allowance(response)
        page = int(params.get("start", 0) / max(int(params.get("limit", 100) or 100), 1)) + 1
        pages[tool] = max(pages[tool], page)
        error = (response or {}).get("error") if isinstance(response, dict) else None
        if error and any(term in str(error).lower() for term in ("limit", "quota", "429")):
            limits.append(tool)
        calls.append({"index": index, "tool": tool, "page": page, "elapsed_ms": elapsed,
                      "response_bytes": call_response_bytes, "status": "failed" if error else "ok", "error": error})
    settings = dict(FETCH_DEFAULTS, **(fetch_config or {}))
    return {
        "calls": calls,
        "pages": dict(pages),
        "total_elapsed_ms": total_elapsed,
        "limits": limits,
        "response_bytes": response_bytes,
        "reserved_context_tokens": int(settings["reserved_context_tokens"]),
        "pipedrive_allowance": pipedrive_allowance,
    }


def diagnostics_for(run, fetch_config=None):
    """Return measured response data without estimating a Pipedrive account allowance."""
    return run_diagnostics(run, fetch_config)


def optional_enrichment_limits(run):
    """Map optional-limit responses to unavailable evidence without retrying them."""
    dependent_rules = {"getActivities": ["R1"], "getNotes": ["R7"]}
    limited = []
    seen = set()
    for call in run.get("calls", []):
        tool = (call.get("tool") or "").split("__")[-1]
        if tool not in dependent_rules or tool in seen:
            continue
        response = call.get("response")
        if response is None and call.get("response_file"):
            response = load_response_file(call["response_file"])
        error = (response or {}).get("error") if isinstance(response, dict) else None
        classification = classify_limit(response)
        structured_429 = isinstance(response, dict) and response.get("status") == 429
        is_legacy_limit = error and any(term in str(error).lower() for term in ("limit", "quota", "429"))
        if classification["kind"] != "unknown" or structured_429 or is_legacy_limit:
            if classification["guidance"] is None and isinstance(error, str):
                classification = dict(classification, guidance=error)
            item = {"tool": tool, "rules": dependent_rules[tool]}
            if structured_429:
                item["classification"] = classification
            limited.append(item)
            seen.add(tool)
    return limited


def choose_page_size(sample_bytes, remaining_context_tokens, fetch_config=None):
    """Choose a safe page size from a one-deal measurement and context reserve."""
    if not sample_bytes or sample_bytes < 1:
        return 0
    settings = dict(FETCH_DEFAULTS, **(fetch_config or {}))
    response_limit = int(settings["max_response_bytes"])
    reserve = int(settings["reserved_context_tokens"])
    # Leave room for response envelopes and larger rows on later pages.
    usable_bytes = max(0, int(response_limit * 0.9) - 4096)
    by_response = min(usable_bytes // sample_bytes, 100)
    if remaining_context_tokens is None:
        return max(0, by_response)
    sample_tokens = sample_bytes / TOKEN_CHARS
    by_context = (int((remaining_context_tokens - reserve) // sample_tokens)
                  if remaining_context_tokens > reserve else 0)
    return max(0, min(by_response, by_context))


def render_budget_status(budget):
    """Render separately named response-size and AI-context budget limits."""
    labels = [
        f"Pipedrive response size: {budget['response_bytes']:,} bytes",
        f"AI context reserve: {budget['reserved_context_tokens']:,} tokens",
    ]
    if budget.get("pipedrive_allowance") is not None:
        labels.append(f"Pipedrive account allowance: {budget['pipedrive_allowance']}")
    return " · ".join(labels)


def _claimed_values(metadata, singular, plural):
    value = metadata.get(singular)
    if value is not None:
        return [value]
    value = metadata.get(plural)
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _provenance_claims(run, call, config=None):
    claims = []
    run_scope = run.get("scope")
    if isinstance(run_scope, dict):
        claims.append(("run scope", dict(run_scope)))
    metadata_scope = (run.get("metadata") or {}).get("scope")
    if isinstance(metadata_scope, dict):
        claims.append(("run metadata scope", dict(metadata_scope)))
    config_scope = (config or {}).get("scope")
    if isinstance(config_scope, dict):
        effective_scope = dict(config_scope)
        if effective_scope.get("filter_id") is not None:
            effective_scope.pop("owner_id", None)
            effective_scope.pop("owner_ids", None)
            effective_scope.pop("pipeline_id", None)
            effective_scope.pop("pipeline_ids", None)
        claims.append(("configured scope", effective_scope))
    for key in ("scope", "request_metadata"):
        metadata = call.get(key)
        if isinstance(metadata, dict):
            claims.append((key, dict(metadata)))
    if any(metadata.get("filter_id") is not None for _, metadata in claims):
        for _, metadata in claims:
            metadata.pop("owner_id", None)
            metadata.pop("owner_ids", None)
            metadata.pop("pipeline_id", None)
            metadata.pop("pipeline_ids", None)
    return claims


def request_owner_scope(run, config):
    """Config with `scope.owner_ids` filled from the owner the open-deals reads actually used.

    A rep who asks for "only my deals" gets owner-filtered reads, but the saved settings may hold no owner. The
    header must name the scope the run used, not the saved one, so a single owner_id shared by every open-deals
    request is adopted when the settings name none. Saved owners, a saved filter and mixed requests stay as they are.
    """
    scope = config.get("scope") or {}
    if scope.get("owner_ids") or scope.get("filter_id") is not None:
        return config
    requested = []
    for call in run.get("calls", []):
        if (call.get("tool") or "").split("__")[-1] != "getDeals":
            continue
        params = call.get("params") or {}
        if deal_status(params.get("status", "open")) != "open":
            continue
        if params.get("filter_id") is not None:
            return config
        requested.append(params.get("owner_id"))
    if not requested or None in requested or len(set(requested)) != 1:
        return config
    return {**config, "scope": {**scope, "owner_ids": [requested[0]]}}


def validate_call_provenance(run, config=None):
    """Report saved scope metadata that is not present in an open-deals request.

    Won/lost calls are account-wide movement reads by contract, so an open pipeline,
    owner, or saved-filter scope must not be projected onto them.
    """
    if (config or {}).get('_workflow_v2') and (config or {}).get('_validated_cohort'):
        return []
    mismatches = []
    for index, call in enumerate(run.get("calls", []), 1):
        if (call.get("tool") or "").split("__")[-1] != "getDeals":
            continue
        params = call.get("params") or {}
        if deal_status(params.get("status", "open")) != "open":
            continue
        for source, metadata in _provenance_claims(run, call, config):
            for field, plural in (("owner_id", "owner_ids"), ("pipeline_id", "pipeline_ids")):
                claimed = _claimed_values(metadata, field, plural)
                if claimed and params.get(field) not in claimed:
                    mismatches.append({
                        "code": "request_provenance_mismatch",
                        "call_index": index,
                        "field": field,
                        "claimed": claimed,
                        "recorded": params.get(field),
                        "metadata_source": source,
                    })
            if metadata.get("filter_id") is not None and params.get("filter_id") != metadata["filter_id"]:
                mismatches.append({
                    "code": "request_provenance_mismatch",
                    "call_index": index,
                    "field": "filter_id",
                    "claimed": metadata["filter_id"],
                    "recorded": params.get("filter_id"),
                    "metadata_source": source,
                })
    return mismatches


def contact_evidence_state(activities, today, policy=None):
    """Return whether completed-activity data is trustworthy enough for R1."""
    today = to_date(today) or today
    policy = policy or {}
    minimum = int(policy.get("min_historical_sample", 5))
    incomplete_threshold = float(policy.get("poor_completion_undone_pct", 0.70))
    sample = [a for a in activities if a.get("deal_id") is not None and to_date(a.get("due_date"))
              and to_date(a["due_date"]) < today]
    if len(sample) < minimum:
        return "insufficient"
    incomplete = sum(1 for a in sample if not a.get("done"))
    return "unverified" if incomplete / len(sample) >= incomplete_threshold else "verified"


def integrity_issue(run, tables, config):
    """Return the first scope or stage inconsistency that makes a brief unsafe."""
    run_id = run.get("run_id")
    scope = config.get("scope") or {}
    if not tables["stages"] and any(
        (call.get("tool") or "").split("__")[-1] == "getStages"
        for call in run.get("calls", [])
    ):
        return {"reason": "stage_data_unavailable"}
    for call in run.get("calls", []):
        if run_id and call.get("run_id") and call["run_id"] != run_id:
            return {"reason": "run_id_mismatch", "expected_run_id": run_id, "observed_run_id": call["run_id"]}
        if (call.get("tool") or "").split("__")[-1] != "getDeals":
            continue
        params = call.get("params") or {}
        if scope.get("filter_id"):
            observed_filter = params.get("filter_id")
            if observed_filter is not None and observed_filter != scope["filter_id"]:
                return {"reason": "filter_scope_mismatch", "expected_filter_id": scope["filter_id"],
                        "observed_filter_id": observed_filter}
        elif params.get("filter_id") is not None:
            return {"reason": "unexpected_filter_scope", "observed_filter_id": params["filter_id"]}
        for key in ("pipeline_id", "owner_id"):
            configured = scope.get(f"{key}s") or []
            if configured and params.get(key) is not None and params[key] not in configured:
                return {"reason": "call_scope_mismatch", "field": key,
                        "expected": configured, "observed": params[key]}

    scoped_deal_fetch = any(
        (call.get("tool") or "").split("__")[-1] == "getDeals"
        and ((call.get("params") or {}).get("pipeline_id") is not None or (call.get("params") or {}).get("filter_id") is not None)
        for call in run.get("calls", [])
    )
    allowed_pipelines = set(scope.get("pipeline_ids") or [])
    excluded_pipelines = set(scope.get("exclude_pipeline_ids") or [])
    for status, deals in (("open", tables["open_deals"]), ("won", tables["won_deals"]), ("lost", tables["lost_deals"])):
        for deal in deals.values():
            stage_id = deal.get("stage_id")
            stage = tables["stages"].get(stage_id)
            if stage is None:
                return {"reason": "missing_stage", "status": status, "deal_id": deal.get("id"), "stage_id": stage_id}
            deal_pipeline = deal.get("pipeline_id")
            stage_pipeline = stage.get("pipeline_id")
            if deal_pipeline is not None and stage_pipeline is not None and deal_pipeline != stage_pipeline:
                return {"reason": "stage_pipeline_mismatch", "status": status, "deal_id": deal.get("id"),
                        "stage_id": stage_id, "deal_pipeline_id": deal_pipeline, "stage_pipeline_id": stage_pipeline}
            effective_pipeline = deal_pipeline if deal_pipeline is not None else stage_pipeline
            if scoped_deal_fetch and not scope.get("filter_id") and allowed_pipelines and effective_pipeline not in allowed_pipelines:
                return {"reason": "pipeline_outside_scope", "status": status, "deal_id": deal.get("id"),
                        "pipeline_id": effective_pipeline}
            if scoped_deal_fetch and not scope.get("filter_id") and effective_pipeline in excluded_pipelines:
                return {"reason": "pipeline_excluded_from_scope", "status": status, "deal_id": deal.get("id"),
                        "pipeline_id": effective_pipeline}
    return None


def rule_execution(deals, config, unavailable_rules=None):
    """Count rule evidence from the records actually evaluated in this run."""
    stale_days = config.get("stage_stale_days") or {}
    enabled = {
        "R1": config.get("silence_days") is not None,
        "R2": bool(config.get("flag_no_next_activity")),
        "R3": bool(stale_days.get("use_pipedrive_rotting", True) or stale_days.get("default") is not None or stale_days.get("by_stage_id")),
        "R4": config.get("close_slip_slack_days") is not None,
        "R5": config.get("single_thread_from_stage_id") is not None,
        "R7": bool(config.get("flag_notes_stage_mismatch")),
    }
    unavailable_rules = set(unavailable_rules or [])
    ledger = {}
    for rule, is_enabled in enabled.items():
        if not is_enabled:
            continue
        eligible = len(deals)
        if rule in unavailable_rules:
            evaluated, verified, proxy = [], [], []
        elif rule == "R1":
            evaluated = [d for d in deals if d.get("last_touch") and d.get("contact_evidence") != "unverified"]
            verified = [d for d in evaluated if d.get("last_touch_source") != "update_time (fallback)"]
            proxy = [d for d in evaluated if d.get("last_touch_source") == "update_time (fallback)"]
        elif rule == "R2":
            evaluated = [d for d in deals if d.get("next_activity_known")]
            verified, proxy = evaluated, []
        elif rule == "R5":
            evaluated = [d for d in deals if type(d.get("participants_count")) is int and d["participants_count"] >= 0]
            verified, proxy = evaluated, []
        else:
            evaluated, verified, proxy = deals, deals, []
        ledger[rule] = {
            "eligible": eligible, "evaluated": len(evaluated), "verified": len(verified),
            "proxy": len(proxy), "flagged": sum(1 for d in deals if any(f["id"] == rule for f in d["flags"])),
            "unavailable": eligible - len(evaluated),
        }
    return ledger


def integrity_stop_result(args, tables, today, window_start, issue):
    detail = ", ".join(f"{key}={value}" for key, value in issue.items() if key != "reason")
    heading = "Request provenance integrity check failed" if issue.get("code") == "request_provenance_mismatch" else "Scope integrity check failed"
    markdown = heading + (f": {detail}" if detail else "")
    if issue.get("reason") == "stage_data_unavailable":
        markdown = "Stage data is unavailable. I stopped before checking or ranking any deals"
    markdown += ". Reconnect Pipedrive or correct the saved scope before running a brief."
    return {
        "report_skill": "pipeline-brief", "mode": "brief", "pass": args.pass_no, "today": iso(today), "window_start": iso(window_start),
        "tool_calls": tables["tool_calls"], "tools_used": dict(tables["tools_used"]),
        "write_calls": tables["write_calls"], "failed_calls": tables["failed_calls"],
        "token_estimate": tables["tokens"], "integrity_stop": issue, "rule_execution": {},
        "aggregates": None, "deals": [], "ranked": [], "leads": [], "untouched_leads": [],
        "call_first_deals": [], "clusters": [], "markdown": markdown, "coverage_line": None,
        "partial_line": None, "footer": None, "top_n_last_activity_ids": [],
        "next_step": "correct scope integrity before fetching more data",
    }


def data_unavailable_issue(run):
    """Required open-deal data must be readable before the brief can calculate anything."""
    readable_open_page = False
    unavailable = None
    for call in run.get("calls", []):
        if (call.get("tool") or "").split("__")[-1] != "getDeals":
            continue
        if deal_status((call.get("params") or {}).get("status", "open")) != "open":
            continue
        response = call.get("response")
        if response is None and call.get("response_file"):
            response = load_response_file(call["response_file"])
        if (isinstance(response, dict) and response.get("success") is not False
                and "error" not in response and isinstance(response.get("data"), list)):
            readable_open_page = True
        elif response is None or (isinstance(response, dict) and (
            response.get("success") is False or "error" in response
        )):
            unavailable = {"reason": "open_deals_unavailable",
                           "error": (response or {}).get("error") if isinstance(response, dict) else None}
    return None if readable_open_page else unavailable


def data_unavailable_result(args, tables, today, window_start, issue):
    message = "Open-deal data is unavailable"
    if issue.get("error"):
        message += f": {issue['error']}"
    message += ". Re-run after the response is available to this compute environment."
    result = integrity_stop_result(args, tables, today, window_start, issue)
    result["integrity_stop"] = None
    result["data_unavailable"] = issue
    result.setdefault("disclosures", []).append(DATE_EXPLANATION)
    result["markdown"] = message
    return result


def run_brief(args):
    config = load_json(args.config)
    if config.get('schema_version', 1) != 1:
        raise ValueError('Use workflow.py with the confirmed profile instead of legacy compute')
    run = load_json(args.input)
    context = account_context.load(run.get("account_context"))
    config = metadata(config, context)
    if getattr(args, "stages", None):
        stage_source = load_stage_context(args.stages)
        run = with_context_stages(run, stage_source)
        config = metadata(config, stage_source)
        config["_deal_field_definitions"] = deal_field_definitions(stage_source)
    config = request_owner_scope(run, config)
    output = config.get("output") or {}
    locale = {
        "timezone": effective_timezone(context.get("timezone") if context["available"] else None, config.get("timezone")),
        "date_format": context.get("date_format") if context["available"] and context.get("date_format")
        else output.get("date_format") or "YYYY-MM-DD",
        "currency": context.get("currency") if context["available"] and context.get("currency")
        else output.get("currency"),
    }
    today = (to_date(args.today) or to_date(run.get("today"))
             or overdue_date(run.get("now") or run.get("timestamp"), locale["timezone"]) or local_today(locale["timezone"]))
    window_start = to_date(run.get("window_start")) or to_date(checkpoint(config, "pipeline-brief")) or (today - timedelta(days=7))

    tables = collect(run)
    provenance = validate_call_provenance(run, config)
    if provenance:
        return integrity_stop_result(args, tables, today, window_start, provenance[0])
    unavailable = data_unavailable_issue(run)
    if unavailable:
        return data_unavailable_result(args, tables, today, window_start, unavailable)
    issue = integrity_issue(run, tables, config)
    if issue:
        return integrity_stop_result(args, tables, today, window_start, issue)
    stages = tables["stages"]
    scope_pids = (config.get("scope") or {}).get("pipeline_ids") or []
    ctx = named_pipeline_labels(stage_context(stages, scope_pids), config)
    ctx["stage_order_notices"] = stage_order_notices(ctx, scope_pids, (config.get("scope") or {}).get("exclude_pipeline_ids") or [],
                                                   notified=config.get("stage_order_notified") or [])
    optional_limits = optional_enrichment_limits(run)
    unavailable_rules = {rule for item in optional_limits for rule in item["rules"]}
    unavailable_tools = set(getattr(args, "unavailable", None) or [])
    if "getNotes" in unavailable_tools:
        unavailable_rules.add("R7")
    ctx["unavailable_notices"] = [GETNOTES_UNAVAILABLE_LINE] if "getNotes" in unavailable_tools else []
    kept_notes = clean_notes(tables["notes"]) if tables["notes"] else []
    owners = owner_map(kept_notes, config)
    overdue_request = getattr(args, "overdue_request", None) or run.get("overdue_request")
    if overdue_request is not None or getattr(args, "mode", None) == "overdue":
        confirmed_owner_ids = (config.get("scope") or {}).get("owner_ids") or []
        current_user_id = (context.get("user") or {}).get("id")
        if current_user_id is None and len(confirmed_owner_ids) == 1:
            current_user_id = confirmed_owner_ids[0]
        overdue_scope = effective_owner_scope(
            overdue_request or "Show overdue activities",
            run.get("role") or config.get("role") or "rep",
            current_user_id,
        )
    else:
        overdue_scope = None
    deal_result = analyse_deals(
        tables, stages, ctx, config, today, window_start, kept_notes, args.pass_no, unavailable_rules,
        output.get("language") or "en", locale["timezone"],
    )
    ledger = rule_execution(deal_result["deals"], config, unavailable_rules)
    lead_result = analyse_leads(tables, config, today)
    aggs = aggregates(deal_result, lead_result, tables, stages, owners, today, window_start, locale["timezone"], overdue_scope)
    calibration = calibration_suggestions(deal_result["deals"], deal_result["weights"], not tables["open_cursor_unfollowed"])

    open_n = len(tables["open_deals"])
    per_deal_chars = (tables["open_deal_chars"] / open_n) if open_n else None
    # Two per-deal figures. `per_deal_tokens` is one deal row in the open-deals response
    # (comparable to the spec's "~600 tokens per deal"). `per_deal_tokens_all_in` divides
    # everything the run read — stages, won/lost, activities, leads, notes — by the open
    # deals, and is what the budget headroom is based on.
    per_deal_all_in = (tables["tokens"] / open_n) if open_n else None
    measured_row_bytes = []
    for call in run.get("calls", []):
        if (call.get("tool") or "").split("__")[-1] != "getDeals":
            continue
        if deal_status((call.get("params") or {}).get("status", "open")) != "open":
            continue
        response = call.get("response")
        if response is None and call.get("response_file"):
            response = load_response_file(call["response_file"])
        if isinstance(response, dict) and isinstance(response.get("data"), list):
            measured_row_bytes.extend(len(json.dumps(row, ensure_ascii=False).encode("utf-8"))
                                      for row in response["data"])
    measured_bytes = max(measured_row_bytes, default=0)
    window_provided = to_date(run.get("window_start")) is not None
    result = {
        "report_skill": "pipeline-brief", "mode": "brief", "pass": args.pass_no, "today": iso(today), "window_start": iso(window_start),
        "tool_calls": tables["tool_calls"], "tools_used": dict(tables["tools_used"]),
        "write_calls": tables["write_calls"], "failed_calls": tables["failed_calls"],
        "token_estimate": tables["tokens"],
        "per_deal_chars": round(per_deal_chars) if per_deal_chars else None,
        "per_deal_tokens": round(per_deal_chars / TOKEN_CHARS) if per_deal_chars else None,
        "per_deal_tokens_all_in": round(per_deal_all_in) if per_deal_all_in else None,
        "measured_row_bytes": measured_bytes,
        "suggested_page_size": choose_page_size(
            measured_bytes,
            getattr(args, "remaining_context_tokens", None),
            config.get("fetch"),
        ),
        "open_cursor_unfollowed": tables["open_cursor_unfollowed"],
        "stage_probabilities_vary": ctx["stage_probabilities_vary"],
        "pipelines": {str(pid): {"label": ctx["pipeline_labels"][pid], "stages": ctx["pipeline_stage_names"][pid]} for pid in ctx["pipeline_labels"]},
        "stage_order_notices": ctx["stage_order_notices"],
        "owners": owners,
        "context": context,
        "identity_source": identity_source(context),
        "locale": locale,
        "buying_signal_evidence": deal_result["buying_signal_evidence"],
        "overdue_scope": overdue_scope,
        "scope": config.get("scope") or {},
        "output_domain": configured_domain(config),
        "excluded": deal_result["excluded"],
        "clusters": deal_result["clusters"],
        "aggregates": aggs,
        "leads": lead_result["leads"],
        "untouched_leads": lead_result["untouched"],
        "call_first_deals": [dl["id"] for dl in deal_result["call_first_deals"]],
        "ranked": deal_result["ranked"],
        "deals": deal_result["deals"],
        "integrity_stop": None,
        "data_unavailable": None,
        "rule_execution": ledger,
        "calibration": calibration,
        "diagnostics": diagnostics_for(run, config.get("fetch")),
        "optional_enrichment_limits": optional_limits,
    }
    if args.pass_no == 1:
        top = [dl for dl in deal_result["ranked"] if dl.get("last_activity_id") is not None][: args.top_n]
        result["top_n_last_activity_ids"] = [str(dl["last_activity_id"]) for dl in top]
        result["next_step"] = ("call getActivities(ids=top_n_last_activity_ids) once per 100 ids, then run --pass 2"
                               if result["top_n_last_activity_ids"] else "no last_activity_id to resolve; run --pass 2")
    out = build_output(
        run, tables, config, ctx, deal_result, lead_result, aggs, owners, ledger, calibration, today, window_start,
        args.show, window_provided, optional_limits, locale,
    )
    result.update(out)
    proxy_count = sum(deal.get("last_touch_source") == "update_time (fallback)" for deal in result["deals"])
    result["activity_coverage"] = {"checked_deals": len(result["deals"]), "deal_update_fallback": proxy_count}
    if proxy_count:
        result["markdown"] += (f"\nActivity evidence: completed activity dates were not resolved for {proxy_count} checked deals; "
                               "those recency assessments use deal updates. This does not establish that the deals have no completed activities.")
    result["markdown"] = link_markdown(result["markdown"], result)
    result["markdown"] += '\n' + result['coverage_line']
    if result.get('follow_up_question'):
        result["markdown"] += '\n' + result['follow_up_question']
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["brief", "overdue", "triage", "forecast"], required=True)
    ap.add_argument("--pass", dest="pass_no", type=int, choices=[1, 2], default=2)
    ap.add_argument("--config", required=True)
    ap.add_argument("--input", required=True)
    ap.add_argument("--today", default=None, help="YYYY-MM-DD; defaults to the run file's today, then the system date")
    ap.add_argument("--top-n", dest="top_n", type=int, default=30)
    ap.add_argument("--show", type=int, default=5, help="maximum named actions in the initial brief")
    ap.add_argument("--markdown", action="store_true", help="print the Markdown block instead of JSON")
    ap.add_argument("--full", action="store_true", help="print every record (deals, ranked, leads) in the JSON; default is a compact summary")
    ap.add_argument("--stages", help="saved getAccountContext response; its stages stand in when the run has no stage read")
    ap.add_argument("--unavailable", action="append", choices=["getNotes"],
                    help="a tool that is not in this workspace after one tool search; skips the rules it feeds and prints its notice")
    ap.add_argument("--output", help="write the complete result (what --full prints) to this path; stdout keeps the Markdown or the compact form")
    args = ap.parse_args(argv)
    if args.mode not in ("brief", "overdue"):
        print(json.dumps({"error": f"--mode {args.mode} is not implemented yet (week {'2' if args.mode == 'triage' else '3'})"}))
        return 2
    result = run_brief(args)
    stamp_result(result, result["tool_calls"], result["token_estimate"], SKILL_DIR, load_json(args.config))
    if args.output:
        # The saved result is written by the script (0.7.16 phase 1, ADR-044 note), as deal-triage's since 0.7.15: the eval
        # sandbox denies a shell redirect, and the chat cannot read the JSON a bare run prints.
        write_output(result, args.output)
    if args.mode == "overdue":
        if result.get("integrity_stop") or result.get("data_unavailable"):
            # A failed scope check or unreadable open-deal data is not "0 overdue" (0.7.20, review 3.3): print the stop
            # message the brief prints and fail, as the Kitchen already does.
            print(result["markdown"])
            return 1
        print(render_overdue_answer(result, args.show))
    elif args.markdown:
        return print_checked_markdown(result, args.input, args.output)
    elif args.full:
        print(json.dumps(result, indent=1, ensure_ascii=False, default=str))
    else:
        print(json.dumps(compact(result), indent=1, ensure_ascii=False, default=str))
    return 0


def write_output(result, output):
    """Write the complete result (what --full prints) to `output` atomically and say so on stderr."""
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(result, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    os.replace(temporary, path)
    print(f"saved {path}", file=sys.stderr)


def print_checked_markdown(result, near=None, output=None):
    """Print the brief's Markdown only when the reply checks pass (PB-38, the FC-14 pattern). A failed check prints nothing
    to stdout, so the model cannot paste a block the checks refused. The printed text is saved for the end-of-reply check
    (hooks/check_last_reply.py), as the version-2 check-reply does (0.7.9). With a saved output, the questions the reply
    leaves are recorded for the card (0.7.19 phase 2, ADR-024 note), as check-reply records them."""
    text = result["markdown"]
    checked = validate_reply(text, {"markdown": text, "record_refs": record_refs(result)})
    if not checked["passed"]:
        print(json.dumps(checked, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    save_expected_reply(text, "reply", near)
    if output:
        import shlex
        from final_response import follow_up_questions
        from pdcore import note_card_due
        skill = str(result.get("report_skill") or "pipeline-brief")
        command = [str(Path(sys.argv[0]).with_name("workflow.py")), "questions", "--skill", skill, "--input", str(output)]
        note_card_due(follow_up_questions(result, {}, {}), " ".join(shlex.quote(part) for part in command), near)
    print(framed(text))
    return 0


COMPACT_KEYS = ("report_skill", "mode", "pass", "today", "window_start", "skill", "version", "calls", "estimated_tokens", "tool_calls", "tools_used", "write_calls", "failed_calls",
                "token_estimate", "per_deal_tokens", "per_deal_tokens_all_in",
                "suggested_page_size",
                "diagnostics",
                "open_cursor_unfollowed", "stage_probabilities_vary", "pipelines", "stage_order_notices", "owners", "excluded",
                "integrity_stop", "data_unavailable",
                "excluded_pipelines", "n_records", "top_n_last_activity_ids", "next_step", "coverage_line",
                "partial_line", "footer")


def compact(result):
    """Everything the skill needs between passes, without the per-record tables (which can run to 100 KB
    and get parked in a file by the client). The Markdown block is included on pass 2."""
    out = {k: result[k] for k in COMPACT_KEYS if k in result}
    out["counts"] = {"deals_in_scope": len(result.get("deals", [])), "ranked": len(result.get("ranked", [])),
                     "call_first_deals": len(result.get("call_first_deals", [])), "leads": len(result.get("leads", [])),
                     "untouched_leads": len(result.get("untouched_leads", [])), "clusters": len(result.get("clusters", []))}
    out["ranked_top10"] = [{"id": d["id"], "title": d["title"], "priority": d.get("priority"), "at_risk": d["at_risk"],
                            "flags": [f["id"] for f in d["flags"]]} for d in result.get("ranked", [])[:10]]
    if result.get("pass") == 2 or result.get("integrity_stop") or result.get("data_unavailable"):
        out["markdown"] = result.get("markdown")
    return out


if __name__ == "__main__":
    sys.exit(main())
