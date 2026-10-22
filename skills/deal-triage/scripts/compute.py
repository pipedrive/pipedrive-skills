#!/usr/bin/env python3
"""Deterministic arithmetic for deal triage.

Standard library only. No network. Reads the raw MCP responses the skill saved to a run
file and the team's config, and prints one JSON document: per-record flags, priority,
at-risk labels, clusters, aggregates, the coverage line and the footer.

    compute.py --mode triage --pass 1 --config CONFIG --input RUN [--today YYYY-MM-DD]
    compute.py --mode triage --pass 2 --config CONFIG --input RUN [--today YYYY-MM-DD]

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
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # scripts/ holds the synced pdcore copy
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from account_context import load_stage_context, metadata, named_pipeline_labels, with_context_stages
from triage_execution import action_states, read_back_confirms
from activity_evidence import completed_activity_evidence, DATE_EXPLANATION
from run_state import checkpoint
from pipedrive_links import configured_domain, deal_url, link_markdown, saved_deals
from final_response import validate_reply  # noqa: E402
from dashboard_charts import ordered_stage_keys  # noqa: E402
from pdcore import (md_text,   # noqa: E402  — shared with deal-triage and forecast; source: shared/pdcore.py
    WRITE_TOOLS,
    save_expected_reply,
    framed,
    closing_heading,
    plural,
    week_summary,
    week_window,
    crm_quote,
    days_ago,
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
    stage_context,
    stage_order_notices,
    GETNOTES_UNAVAILABLE_LINE,
    deal_value,
    is_app_origin,
    in_scope,
    snoozed_until,
    money,
    deal_ref,
    EMAIL_HANDOFF,
)

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ----------------------------------------------------------------------------- deals


def activity_scan_complete(run, deal):
    """Whether the saved open-activity pages can establish absence for this deal."""
    owner_id = deal.get("owner_id")
    pages = {}
    for call in run.get("calls", []):
        if (call.get("tool") or "").split("__")[-1] != "getActivities":
            continue
        params = call.get("params") or {}
        if params.get("ids") or params.get("deal_id") or params.get("done") is True:
            continue
        if set(params) - {"owner_id", "done", "limit", "cursor", "sort_by", "sort_direction"}:
            continue
        if params.get("owner_id") is not None and params["owner_id"] != owner_id:
            continue
        response = call.get("response")
        if response is None and call.get("response_file"):
            try:
                response = load_response_file(call["response_file"])
            except (OSError, ValueError):
                continue
        if (not isinstance(response, dict) or response.get("success") is False
                or "error" in response or not isinstance(response.get("data"), list)):
            continue
        extra = response.get("additional_data")
        if not isinstance(extra, dict):
            continue
        if all(isinstance(item, dict) and type(item.get("id")) is int
               and type(item.get("done")) is bool for item in response["data"]):
            cursor = params.get("cursor")
            if cursor in pages:
                return False
            pages[cursor] = extra.get("next_cursor")
    cursor = None
    visited = set()
    while cursor in pages and cursor not in visited:
        visited.add(cursor)
        cursor = pages[cursor]
        if cursor is None:
            return True
    return False


def deal_signal_state(deal, activities, activity_complete):
    """Resolve what is known before deriving no-next or single-contact findings."""
    open_activities = [a for a in activities if a.get("done") is False and a.get("deal_id") == deal.get("id")
                       and type(a.get("id")) is int and a["id"] > 0]
    linked = deal.get("next_activity_id")
    person = deal.get("person_id")
    person_is_linked = type(person) is int and person > 0
    if open_activities or type(linked) is int and linked > 0:
        next_state = {"state": "confirmed", "evidence": "An open customer task is linked to this deal."}
    elif "next_activity_id" in deal and linked is None and type(deal.get("undone_activities_count")) is int and deal["undone_activities_count"] == 0:
        next_state = {"state": "absent", "evidence": "Pipedrive reports no next activity and zero unfinished activities on this deal."}
    elif activity_complete and "next_activity_id" in deal and linked is None:
        next_state = {"state": "absent", "evidence": "No open linked activity was found in the complete activity read."}
    else:
        next_state = {"state": "unknown", "evidence": "The open activity check is incomplete."}
    count = deal.get("participants_count")
    if type(count) is int and count >= 0:
        if count == 0 and person_is_linked:
            contacts = {"state": "unknown", "evidence": "At least one linked contact exists; the contact count conflicts."}
        else:
            contacts = {"state": "confirmed", "count": count, "evidence": f"{count} CRM-linked contacts recorded."}
    elif person_is_linked:
        contacts = {"state": "unknown", "evidence": "At least one linked contact exists; the total is unknown."}
    else:
        contacts = {"state": "unknown", "evidence": "The number of linked contacts is unknown."}
    done = [a for a in activities if a.get("done") is True and a.get("deal_id") == deal.get("id")]
    buyer_activity = [a for a in done if not any(term in str(a.get("subject") or "").lower()
                                                   for term in ("newsletter", "bulk", "campaign"))]
    if buyer_activity or deal.get("last_incoming_mail_time"):
        touch = {"state": "confirmed", "evidence": "A buyer activity or inbound email is recorded."}
    elif done:
        touch = {"state": "unknown", "evidence": "Only bulk outreach is recorded; buyer response is unverified."}
    else:
        touch = {"state": "unknown", "evidence": "Buyer contact quality has not been checked."}
    return {"next_activity": next_state, "contacts": contacts, "last_customer_touch": touch}


def analyse_deals(tables, stages, ctx, config, today, window_start, kept_notes, pass_no, activity_run=None):
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

        activity_complete = activity_scan_complete(activity_run or {}, d)
        signal_state = deal_signal_state(d, list(acts.values()), activity_complete)
        has_next = signal_state["next_activity"]["state"] == "confirmed"
        activities_count = d.get("activities_count")
        if activities_count is None:
            activities_count = len(done_by_deal.get(d["id"], [])) + len(undone_by_deal.get(d["id"], []))
        undone_open = [a for a in undone_by_deal.get(d["id"], [])]
        overdue = [a for a in undone_open if to_date(a.get("due_date")) and to_date(a["due_date"]) < today]

        flags = []
        if silence_days is not None and days_silent is not None and days_silent >= silence_days:
            if signal_state["last_customer_touch"]["evidence"] != "Only bulk outreach is recorded; buyer response is unverified.":
                flags.append({"id": "R1", "flag": "SILENT", "days": days_silent, "since": iso(last_touch),
                              "source": source, "threshold": silence_days})
        if flag_no_next and signal_state["next_activity"]["state"] == "absent":
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
        contacts = signal_state["contacts"]
        if order_known and single_from is not None and contacts["state"] == "confirmed" and contacts["count"] <= 1:
            from_stage = stages.get(single_from) or {}
            if from_stage.get("pipeline_id") == pid and order_nr >= (from_stage.get("order_nr") or 0):
                flags.append({"id": "R5", "flag": "SINGLE_THREAD", "participants": contacts["count"]})
        mismatch_note = None
        if check_mismatch:
            for n in notes_by_deal.get(d["id"], []):
                low = n["text"].lower()
                low = re.sub(r"lost reason:.*", "", low)
                hit = next((p for p in MISMATCH_PATTERNS if re.search(p, low)), None)
                if hit:
                    mismatch_note = n
                    break
            if mismatch_note:
                flags.append({"id": "R7", "flag": "NOTES_MISMATCH", "note_date": mismatch_note["add_time"],
                              "excerpt": mismatch_note["text"][:120], "status": "won" if hit in WON_NOTE_PATTERNS else "lost"})

        buying = None
        if config.get("buying_signal_evidence", True):
            for n in notes_by_deal.get(d["id"], []):
                low = n["text"].lower()
                low = re.sub(r"lost reason:.*", "", low)
                for phrase in BUYING_SIGNALS:
                    if re.search(r"\b" + re.escape(phrase) + r"\b", low):
                        buying = {"phrase": phrase, "note_date": n["add_time"]}
                        break
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
            "stage_order_known": order_known,
            "add_time": d.get("add_time"), "add_date": iso(add_date), "update_date": iso(update_date),
            "expected_close_date": iso(ecd), "days_silent": days_silent, "last_touch": iso(last_touch),
            "last_touch_source": source, **activity_dates, "days_in_stage": days_in_stage, "never_moved": never_moved,
            "has_next": has_next, "signal_state": signal_state, "activities_count": activities_count,
            "undone_open": len(undone_open), "overdue": len(overdue),
            "notes_count": d.get("notes_count"), "participants_count": d.get("participants_count"),
            "p": p, "flags": flags, "at_risk": any(f["id"] in ("R1", "R2") for f in flags),
            "buying_signal": buying, "app_origin": is_app_origin(d),
            "origin": d.get("origin"), "channel_id": d.get("channel_id"),
            "below_floor": value_floor is not None and value < value_floor,
            "last_activity_id": d.get("last_activity_id"),
            "closing_this_week": bool(ecd and today <= ecd <= week_window(today)[1]),
        })

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

def value_by_currency(items):
    """Each currency is summed on its own; one total across currencies would add pounds to euros."""
    totals = defaultdict(float)
    for item in items:
        totals[item.get("currency") or "?"] += item.get("value") or 0
    return {currency: round(total, 2) for currency, total in totals.items()}

def money_split(totals):
    return " · ".join(money(value, currency) for currency, value in totals.items() if value)


def aggregates(deal_result, lead_result, tables, stages, owners, today, window_start):
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
                   "items": closing_items}

    open_ids = {dl["id"] for dl in deals}
    overdue_by_owner = Counter()
    overdue_total = 0
    for a in tables["activities"].values():
        if a.get("done") or a.get("deal_id") is None or a["deal_id"] not in open_ids:
            continue
        dd = to_date(a.get("due_date"))
        if dd and dd < today:
            overdue_total += 1
            overdue_by_owner[owner_name(owners, a.get("owner_id")) or f"Owner {a.get('owner_id')}"] += 1

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
        "overdue_activities": {"total": overdue_total, "by_owner": dict(overdue_by_owner)},
        "value_concentration": concentration, "leads_count": len(lead_result["leads"]),
        "untouched_leads": len(lead_result["untouched"]),
    }


# ----------------------------------------------------------------------------- output


def flag_text(f):
    if f["id"] == "R1":
        return f"R1 silent {f['days']} days (last touch {f['since']}, {f['source']})"
    if f["id"] == "R2":
        return "R2 no next activity"
    if f["id"] == "R3":
        nm = ", never moved" if f.get("never_moved") else ""
        return f"R3 {f['days']} days in stage (threshold {f['threshold']}{nm})"
    if f["id"] == "R4":
        return f"R4 close date {f['expected_close_date']} slipped {f['days']} days"
    if f["id"] == "R5":
        return f"R5 single-threaded ({f['participants']} contact)"
    if f["id"] == "R7":
        return f"R7 note {f['note_date']} says \"{f['excerpt']}\" — stage unchanged"
    if f["id"] == "R8":
        return f"R8 {f['count']} overdue (oldest due {f['oldest_due']})"
    if f["id"] == "R9":
        return "R9 no deal value"
    if f["id"] == "R10":
        return f"R10 probability {f['probability']:.0%} but close date {f['expected_close_date']}"
    if f["id"] == "R11":
        return "R11 unlinked: " + ", ".join(m.replace("_id", "") for m in f["missing"])
    if f["id"] == "R6" or f["id"].startswith("F-"):
        return f"{f['id']} matches your \"{f['flag']}\" filter ({f['meaning']})"
    return f["id"]


def rep_flag_text(flag):
    """Describe a verified finding without exposing internal rule names."""
    rule = flag["id"]
    if rule == "R1":
        return f"Last CRM touch proxy: {days_ago(flag['days'])}"
    if rule == "R2":
        return "No next customer activity is scheduled"
    if rule == "R3":
        return f"This deal has stayed in its stage for {flag['days']} days"
    if rule == "R4":
        return f"The expected close date passed {days_ago(flag['days'])}"
    if rule == "R5":
        count = flag["participants"]
        return f"Only {count} {'contact is' if count == 1 else 'contacts are'} linked in Pipedrive"
    if rule == "R7":
        return "A recent note may not match the deal's stage"
    if rule == "R8":
        count = flag["count"]
        return f"{count} {'activity is' if count == 1 else 'activities are'} overdue"
    if rule == "R9":
        return "No deal value is recorded"
    if rule == "R10":
        return "The close date is far away despite a high probability"
    if rule == "R11":
        return "A company or contact link is missing"
    if rule == "R6" or rule.startswith("F-"):
        return f"Your saved filter flagged: {flag['meaning']}"
    return "A configured finding needs review"


def render_triage_markdown(result, shown, proposals, leads, config):
    """Build a short decision queue, with the complete risk record in a labelled audit."""
    by_deal = {proposal.get("deal_id"): proposal for proposal in proposals if proposal.get("deal_id") is not None}
    unavailable = {rule for rule, state in (result.get("rule_execution") or {}).items()
                   if isinstance(state, dict) and state.get("unavailable")}
    if unavailable:
        lines = [f"I checked {len(result['deals'])} open deals; {len(result['ranked'])} have verified findings, but some evidence is incomplete."]
        if "R1" in unavailable:
            lines.append("I could not verify recent customer contact for every deal.")
        if "R2" in unavailable:
            lines.append("I could not verify whether every deal has a next activity.")
    else:
        lines = [f"I checked {len(result['deals'])} open deals; {len(result['ranked'])} need attention."]
    if result.get("partial_line"):
        lines.append("More open deals remain unchecked; this queue covers only fetched records.")
    work_counts = {"Act now": len(leads), "Review existing tasks": 0, "Check missing evidence": 0}
    for deal in shown:
        kind = deal["decision"]["kind"]
        if kind == "proposed_activity":
            work_counts["Act now"] += 1
        elif kind == "existing_task_review" or deal.get("existing_next_step"):
            work_counts["Review existing tasks"] += 1
        else:
            work_counts["Check missing evidence"] += 1
    lines.append("Work to review: " + "; ".join(f"{count} {label.lower()}" for label, count in work_counts.items()) + ".")
    lines.extend(["", "Start with these decisions:"])
    for deal in shown[:3]:
        first_risk = rep_flag_text(deal["flags"][0]) if deal["flags"] else "Review the saved deal evidence"
        lines.append(f"- {md_text(deal['title'])}: {first_risk}. {md_text(deal['decision']['text'])}")
    if not shown:
        if unavailable:
            lines.append("- Check the missing CRM evidence and rerun triage before treating these deals as clear.")
        else:
            lines.append("- No checked deal has a verified finding. Review the scope if that seems wrong.")
    lines.extend(["", "Complete action queue:"])
    for number, deal in enumerate(shown, 1):
        risks = "; ".join(rep_flag_text(flag) for flag in deal["flags"])
        lines.append(f"{number}. {md_text(deal['title'])} ({deal_ref(deal, config)}) — {deal['severity']} risk. {md_text(deal['decision']['text'])}")
        lines.append(f"   Why: {risks}.")
        if deal.get("evidence"):
            lines.append(f"   CRM evidence: {deal['evidence'][0]}")
        if deal["decision"]["kind"] == "proposed_activity":
            proposal = by_deal.get(deal["id"], {})
            if proposal.get("action") == "updateActivity":
                lines.append(f"   To reschedule the existing activity '{md_text(crm_quote(deal['overdue_activity'].get('subject') or proposal['subject']))}', confirm item {number} in chat. [accept {number}] [edit {number}] [skip {number}]")
            else:
                if proposal.get("person_id") is not None:
                    lines.append(f"   Contact: {proposal.get('person_name') or 'the linked person (name unavailable)'} will be included in this new activity.")
                else:
                    lines.append("   Contact: no linked person was recorded; this proposal creates an activity without a contact.")
                lines.append(f"   To create this activity, confirm item {number} in chat. [accept {number}] [edit {number}] [skip {number}]")
            if by_deal.get(deal["id"], {}).get("needs_second_confirmation"):
                lines.append("   Any separate change to the deal's status, close date or value needs its own confirmation.")
        if deal["signal_state"]["contacts"]["state"] == "unknown":
            lines.append(f"   Contact count: {deal['signal_state']['contacts']['evidence']}")
        proposal = by_deal.get(deal["id"], {})
        if deal["decision"]["kind"] == "proposed_activity" and proposal.get("action") == "addActivity" and str(proposal.get("type")).lower() == "email":
            lines.append("   " + EMAIL_HANDOFF.format(number))
    if len(result["ranked"]) > len(shown):
        lines.append(f"Items {len(shown) + 1} to {len(result['ranked'])} are saved too and can be accepted by number.")
    shown_ids = {deal["id"] for deal in shown}
    other_filter_matches = [deal for deal in result["ranked"] if deal["id"] not in shown_ids
                            and any(flag["id"] == "R6" or flag["id"].startswith("F-")
                                    for flag in deal["flags"])]
    if other_filter_matches:
        lines.extend(["", "Other saved-filter findings (outside the action queue):"])
        for deal in other_filter_matches:
            meanings = "; ".join(rep_flag_text(flag) for flag in deal["flags"]
                                 if flag["id"] == "R6" or flag["id"].startswith("F-"))
            step = deal.get("existing_next_step")
            next_step = (f" Existing task: {md_text(step['subject'], 'Activity')}, due {step['due_date']}."
                         if step else " Review this deal and agree the next step.")
            proposal = by_deal.get(deal["id"])
            commands = (f" Item {proposal['item']}: [accept {proposal['item']}] [edit {proposal['item']}] [skip {proposal['item']}]"
                        if proposal else "")
            lines.append(f"- {md_text(deal['title'])} ({deal_ref(deal, config)}): {meanings}.{next_step}{commands}")
    if leads:
        lines.extend(["", "Leads to contact:"])
        lead_names = {lead["id"]: lead.get("title") or f"Lead #{lead['id']}" for lead in result.get("leads") or []}
        for proposal in leads:
            lead_name = lead_names.get(proposal["lead_id"]) or f"Lead #{proposal['lead_id']}"
            lines.append(f"{proposal['item']}. {md_text(lead_name)} — {md_text(proposal['subject'])} by {proposal['due_date']}. [accept {proposal['item']}] [edit {proposal['item']}] [skip {proposal['item']}]")
    lines.extend(["", result["coverage_line"]])
    for line in result.get("disclosures") or []:
        if line != result["coverage_line"]:
            lines.append(line)
    return "\n".join(lines)


def narrow_stage_order(by_stage, deals, ctx):
    """Stage names in pipeline order, the order the dashboard stage list and chart use."""
    pipelines = {str(pid): {"stages": (ctx.get("pipeline_stage_names") or {}).get(pid) or []} for pid in ctx["pipeline_labels"]}
    pipeline_of = {}
    for dl in deals:
        pipeline_of.setdefault(dl["stage"] or "?", str(dl["pipeline_id"]))
    return [stage for _, stage in ordered_stage_keys([(pipeline_of.get(stage, "None"), stage) for stage in by_stage], pipelines)]

def build_output(run, tables, config, ctx, deal_result, lead_result, aggs, owners, today, window_start, budget, show, window_provided=False):
    n_records = aggs["open_count"] + aggs["leads_count"]
    style = (config.get("output") or {}).get("style") or "auto"
    if tables["open_cursor_unfollowed"]:
        mode = "narrow"
    elif style == "short" or (style == "auto" and n_records < 30):
        mode = "recall"
    else:
        mode = "triage"

    fetched = len(tables["open_deals"])
    total = f"{fetched}+" if tables["open_cursor_unfollowed"] else str(fetched)
    parts = [f"Coverage: fetched {fetched}/{total} open deals", f"{tables['tool_calls']} tool calls",
             f"window since {iso(window_start)}", f"~{tables['tokens']:,} tokens read"]
    if tables["stages_source"] == "account_context":
        parts.append("stages from account context")
    if tables["failed_calls"]:
        parts.append("failed after retry: " + ", ".join(tables["failed_calls"]))
    coverage_line = " · ".join(parts)
    partial_line = None
    if tables["open_cursor_unfollowed"]:
        partial_line = ("Not fetched: the most recently updated open deals beyond the token budget — "
                        "'what moved' and 'closing this week' may be incomplete. Widen with a saved filter, "
                        "the plugin copy on Claude Code, or the snapshot tool when available.")

    w = deal_result["weights"]
    defaults = []
    if config.get("ranking") in (None, {}) or config.get("ranking") == DEFAULT_WEIGHTS:
        defaults.append("ranking weights")
    if (config.get("output") or {}).get("style", "auto") == "auto":
        defaults.append("output shape by record count")
    if not window_provided and not checkpoint(config, "deal-triage"):
        defaults.append("window 7 days")
    if config.get("budget_tokens") is None:
        defaults.append(f"fetch budget {budget:,} tokens")
    p_line = None
    if not deal_result["p_available"]:
        p_line = "probability not used — stage probabilities are not configured and no deal has its own"
    elif not ctx["stage_probabilities_vary"]:
        p_line = "probability: deal-level only (stage probabilities do not vary)"
    footer = (f"Footer: motion {deal_result['motion']} · weights value {w['value']:.2f} / probability {w['probability']:.2f} / "
              f"depth {w['depth']:.2f} / neglect {w['neglect']:.2f}"
              + (f" · {p_line}" if p_line else "")
              + " · defaults in force: " + (", ".join(defaults) if defaults else "none"))

    excluded_pipelines = []
    scope = config.get("scope") or {}
    for pid, label in ctx["pipeline_labels"].items():
        if (scope.get("pipeline_ids") and pid not in scope["pipeline_ids"]) or pid in (scope.get("exclude_pipeline_ids") or []):
            excluded_pipelines.append(label)

    md = []
    scope_labels = [ctx["pipeline_labels"].get(pid, str(pid)) for pid in (scope.get("pipeline_ids") or ctx["pipeline_labels"].keys())]
    value_str = " · ".join(money(v, c) for c, v in aggs["open_value_by_currency"].items()) or "no value set"
    md.append(f"Pipeline brief — {today.strftime('%a %d %b')} · scope: {', '.join(scope_labels)} · "
              f"excluded: {', '.join(excluded_pipelines) or 'none'} · motion: {deal_result['motion']} · "
              f"{plural(aggs['open_count'], 'open deal')}, {value_str}"
              + (f" · {aggs['leads_count']} leads" if lead_result['included'] else "")
              + f" · window since {iso(window_start)}")
    md.extend(ctx.get("stage_order_notices", []))
    md.extend(ctx.get("unavailable_notices", []))
    if aggs["value_concentration"]["all_zero"]:
        md.append("Deal values are all 0 — the value field is unfilled and cannot drive the ranking.")
    elif aggs["value_concentration"]["top2_share"] and aggs["value_concentration"]["top2_share"] >= 0.5:
        md.append(f"Two deals hold {aggs['value_concentration']['top2_share']:.0%} of the open value — read the ranking with that in mind.")

    if mode == "narrow":
        md.append("Too many open deals to brief reliably in one pass. Shape by stage:")
        for st in narrow_stage_order(aggs["by_stage"], deal_result["deals"], ctx):
            v = aggs["by_stage"][st]
            md.append(f"• {st}: {plural(v['count'], 'deal')}, " + ", ".join(money(x, c) for c, x in v["value"].items()))
        md.append("Which slice? a stage · an owner · a close window · a value floor · a saved Pipedrive filter (give me its number)")
    else:
        if lead_result["included"] and deal_result["motion"] in ("speed_to_lead", "hybrid"):
            cf = lead_result["untouched"] + deal_result["call_first_deals"]
            if cf:
                md.append("")
                md.append("New and untouched — call first")
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
        md.append(f"• {mv['won']['count']} won" + (f" ({won_split})" if won_split else "") + f" · {mv['lost']['count']} lost"
                  + (f" ({lost_names})" if lost_names else "") + f" · {mv['entered_stage']['count']} entered a new stage · {mv['created']['count']} created")
        cw = aggs["closing_this_week"]
        md.append("")
        closing_split = money_split(cw["value_by_currency"])
        md.append(f"{closing_heading(cw)}: {plural(cw['count'], 'deal')}" + (f", {closing_split}" if closing_split else "") + (f" — {len(cw['at_risk'])} at risk ({', '.join(md_text(x['title']) for x in cw['at_risk'])})" if cw["at_risk"] else ""))
        ranked = deal_result["ranked"]
        if mode == "triage":
            md.append("")
            md.append("Needs attention (ranked)")
            shown = [dl for dl in ranked if dl["flags"]][:show] or ranked[:show]
            for i, dl in enumerate(shown, 1):
                bits = [flag_text(f) for f in dl["flags"]]
                line = f"{i}. {md_text(dl['title'])} ({deal_ref(dl, config)}) — {money(dl['value'], dl['currency'])}, {dl['stage']} — "
                line += ("AT RISK · " if dl["at_risk"] else "") + (" · ".join(bits) if bits else f"priority {dl['priority']:.2f}, no flags")
                if dl["buying_signal"]:
                    line += f" ▲ buying signal: \"{dl['buying_signal']['phrase']}\" (note {dl['buying_signal']['note_date']})"
                md.append(line)
        else:
            md.append("")
            md.append("Recall mode (small pipeline — prose, no ranking):")
            flagged = [dl for dl in ranked if dl["flags"]]
            for dl in flagged[:show]:
                md.append(f"• {md_text(dl['title'])} ({deal_ref(dl, config)}, {money(dl['value'], dl['currency'])}, {dl['stage']}): " + "; ".join(flag_text(f) for f in dl["flags"]) + (" — AT RISK" if dl["at_risk"] else ""))
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
        ov = aggs["overdue_activities"]
        if ov["total"]:
            md.append("")
            md.append(f"Overdue activities: {ov['total']} (" + " · ".join(f"{k} {v}" for k, v in ov["by_owner"].items()) + ")")
    md.append("")
    md.append(coverage_line)
    if partial_line:
        md.append(partial_line)
    md.append(footer)

    return {
        "mode": mode, "n_records": n_records, "coverage_line": coverage_line, "partial_line": partial_line,
        "footer": footer, "excluded_pipelines": excluded_pipelines, "markdown": "\n".join(md),
    }


# ----------------------------------------------------------------------------- main

def apply_stages_input(args, config, run):
    """--stages: stages and pipeline names from a saved getAccountContext response when the run has no stage read."""
    if not getattr(args, "stages", None):
        return config, run
    source = load_stage_context(args.stages)
    return metadata(config, source), with_context_stages(run, source)


def run_brief(args, run_override=None):
    config = load_json(args.config)
    if config.get('schema_version', 1) != 1:
        raise ValueError('Use workflow.py with the confirmed profile instead of legacy compute')
    run = run_override if run_override is not None else load_json(args.input)
    config, run = apply_stages_input(args, config, run)
    unavailable_tools = set(getattr(args, "unavailable", None) or [])
    if "getNotes" in unavailable_tools:
        config = {**config, "flag_notes_stage_mismatch": False, "buying_signal_evidence": False}
    # budget: CLI flag > config.budget_tokens > 60,000 (a presentation default, printed in the footer)
    if args.budget is None:
        args.budget = int(config.get("budget_tokens") or 60000)
    today = to_date(args.today) or to_date(run.get("today")) or date.today()
    window_start = to_date(run.get("window_start")) or to_date(checkpoint(config, "deal-triage")) or (today - timedelta(days=7))

    tables = collect(run)
    stages = tables["stages"]
    scope_pids = (config.get("scope") or {}).get("pipeline_ids") or []
    ctx = named_pipeline_labels(stage_context(stages, scope_pids), config)
    ctx["stage_order_notices"] = stage_order_notices(ctx, scope_pids, (config.get("scope") or {}).get("exclude_pipeline_ids") or [])
    ctx["unavailable_notices"] = [GETNOTES_UNAVAILABLE_LINE] if "getNotes" in unavailable_tools else []
    kept_notes = clean_notes(tables["notes"]) if tables["notes"] else []
    owners = owner_map(kept_notes, config)
    deal_result = analyse_deals(tables, stages, ctx, config, today, window_start, kept_notes, args.pass_no, run)
    lead_result = analyse_leads(tables, config, today)
    aggs = aggregates(deal_result, lead_result, tables, stages, owners, today, window_start)

    open_n = len(tables["open_deals"])
    per_deal_chars = (tables["open_deal_chars"] / open_n) if open_n else None
    # Two per-deal figures. `per_deal_tokens` is one deal row in the open-deals response
    # (comparable to the spec's "~600 tokens per deal"). `per_deal_tokens_all_in` divides
    # everything the run read — stages, won/lost, activities, leads, notes — by the open
    # deals, and is what the budget headroom is based on.
    per_deal_all_in = (tables["tokens"] / open_n) if open_n else None
    window_provided = to_date(run.get("window_start")) is not None
    result = {
        "mode": "brief", "pass": args.pass_no, "today": iso(today), "window_start": iso(window_start),
        "tool_calls": tables["tool_calls"], "tools_used": dict(tables["tools_used"]),
        "write_calls": tables["write_calls"], "failed_calls": tables["failed_calls"],
        "token_estimate": tables["tokens"],
        "per_deal_chars": round(per_deal_chars) if per_deal_chars else None,
        "per_deal_tokens": round(per_deal_chars / TOKEN_CHARS) if per_deal_chars else None,
        "per_deal_tokens_all_in": round(per_deal_all_in) if per_deal_all_in else None,
        "budget_tokens": args.budget,
        "display_timezone": config.get("timezone"),
        "budget_remaining_deals": (int((args.budget - tables["tokens"]) / per_deal_all_in) if per_deal_all_in else None),
        "open_cursor_unfollowed": tables["open_cursor_unfollowed"],
        "stage_probabilities_vary": ctx["stage_probabilities_vary"],
        "pipelines": {str(pid): {"label": ctx["pipeline_labels"][pid], "stages": ctx["pipeline_stage_names"][pid]} for pid in ctx["pipeline_labels"]},
        "stage_order_notices": ctx["stage_order_notices"],
        "unavailable_notices": ctx["unavailable_notices"],
        "owners": owners,
        "excluded": deal_result["excluded"],
        "clusters": deal_result["clusters"],
        "aggregates": aggs,
        "leads": lead_result["leads"],
        "untouched_leads": lead_result["untouched"],
        "call_first_deals": [dl["id"] for dl in deal_result["call_first_deals"]],
        "ranked": deal_result["ranked"],
        "deals": deal_result["deals"],
    }
    if args.pass_no == 1:
        top = [dl for dl in deal_result["ranked"] if dl.get("last_activity_id") is not None][: args.top_n]
        result["top_n_last_activity_ids"] = [str(dl["last_activity_id"]) for dl in top]
        result["next_step"] = ("call getActivities(ids=top_n_last_activity_ids) once per 100 ids, then run --pass 2"
                               if result["top_n_last_activity_ids"] else "no last_activity_id to resolve; run --pass 2")
    out = build_output(run, tables, config, ctx, deal_result, lead_result, aggs, owners, today, window_start, args.budget, args.show, window_provided)
    result.update(out)
    return result


SEVERITY = {"Low": 1, "Medium": 2, "High": 3}
# Which flag drives the proposal when a deal carries several: status/silence first, data-hygiene last.
# MISMATCH_PATTERNS (pdcore) that mean the deal was won; the rest (closed-lost, lost, cancelled, went with a competitor) mean lost.
WON_NOTE_PATTERNS = MISMATCH_PATTERNS[:5]
LEAD_FLAG_ORDER = ["R7", "R1", "R2", "R4", "R3", "R5", "R6", "R10", "R8", "R9", "R11"]
ACTIVITY_BY_RULE = {
    "R1": ("call", "Reconnect with the buyer and agree a next step"),
    "R2": ("call", "Agree and schedule the next step"),
    "R3": ("meeting", "Meet the stakeholder blocking stage progress"),
    "R4": ("call", "Agree a realistic close milestone"),
    "R5": ("meeting", "Bring a second stakeholder into the deal"),
    "R6": ("task", "Complete the missing qualification"),
    "R7": ("task", "Verify the deal status against the note"),
    "R8": ("task", "Reschedule the overdue Activity"),
    "R9": ("task", "Establish the deal value"),
    "R10": ("call", "Agree an earlier milestone or mutual close plan"),
    "R11": ("task", "Link the missing organisation or contact"),
}


def lead_flag_rank(flag_id):
    if flag_id.startswith("F-"):
        return LEAD_FLAG_ORDER.index("R6") + 0.5
    return LEAD_FLAG_ORDER.index(flag_id) if flag_id in LEAD_FLAG_ORDER else len(LEAD_FLAG_ORDER)


def next_business_due(today, days):
    due = today + timedelta(days=days)
    while due.weekday() >= 5:
        due += timedelta(days=1)
    return due


def deal_evidence(deal_id, activities, kept_notes, limit=3):
    """The last completed activities and kept notes on one deal, newest first — the only
    material the diagnosis may draw on."""
    done = sorted((a for a in activities.values() if a.get("deal_id") == deal_id and a.get("done") and a.get("due_date")),
                  key=lambda a: a["due_date"], reverse=True)[:limit]
    notes = sorted((n for n in kept_notes if n.get("deal_id") == deal_id), key=lambda n: n["add_time"], reverse=True)[:limit]
    lines = [f"{a['due_date']} {a.get('type') or 'activity'} \"{md_text((a.get('subject') or '').strip(), '')}\""
             + (f" — {md_text(strip_html(a['note'])[:80], '')}" if a.get("note") else "") for a in done]
    lines += [f"{n['add_time'][:10]} note: \"{md_text(n['text'][:100], '')}\"" for n in notes
              if not n["text"].lstrip().lower().startswith("deal summary")]
    return lines


def write_accounting(run):
    """Writes actually made in this run, from the saved responses: a failed write is never
    counted as written, and created Activity ids come from the responses, not the model. A successful write with
    no matching saved read-back is uncertain, not written (C-DT-M11)."""
    written, failed, created, uncertain = [], [], [], []
    for index, call in enumerate(run.get("calls", [])):
        tool = (call.get("tool") or "").split("__")[-1]
        if tool not in WRITE_TOOLS:
            continue
        resp = call.get("response")
        if resp is None and call.get("response_file"):
            resp = load_response_file(call["response_file"])
        ok = isinstance(resp, dict) and resp.get("success") is not False and "error" not in resp
        if ok and not read_back_confirms(run["calls"], index):
            uncertain.append(tool)
            continue
        (written if ok else failed).append(tool)
        if ok and tool == "addActivity" and isinstance(resp.get("data"), dict) and resp["data"].get("id") is not None:
            created.append(resp["data"]["id"])
    return {"written": written, "failed": failed, "uncertain": uncertain, "created_activity_ids": created}


def build_triage_presentation(result, shown):
    """Project an already-computed triage result for a renderer without new analysis."""
    proposals = {proposal.get("deal_id"): proposal for proposal in result.get("proposals") or []
                 if proposal.get("deal_id") is not None}
    risk_counts = Counter(deal.get("severity") for deal in shown if deal.get("severity"))
    owner_counts = Counter()
    items = []
    owners = result.get("owners") or {}
    for number, deal in enumerate(shown, 1):
        owner_id = deal.get("owner_id")
        owner = owners.get(str(owner_id)) if owner_id is not None else None
        owner_label = (owner or {}).get("name") or (f"Owner {owner_id}" if owner_id is not None else "Unassigned")
        owner_counts[owner_label] += 1
        evidence = deal.get("evidence") or []
        items.append({
            "item": number,
            "deal_id": deal.get("id"),
            "owner_id": owner_id,
            "severity": deal.get("severity"),
            "flags": deal.get("flags") or [],
            "primary_evidence": evidence[0] if evidence else None,
            "proposal": proposals.get(deal.get("id")),
            "existing_next_step": deal.get("existing_next_step"),
            "decision": deal.get("decision"),
            "signal_state": deal.get("signal_state"),
        })
    leads = {lead["id"]: lead for lead in result.get("leads") or []}
    lead_items = []
    for proposal in result.get("proposals") or []:
        if "lead_id" not in proposal:
            continue
        lead = leads[proposal["lead_id"]]
        lead_items.append({"item": proposal["item"], "lead_id": lead["id"],
                           "owner_id": lead.get("owner_id"), "proposal": proposal})
    owner_risks = {}
    for item in items:
        owner = owners.get(str(item["owner_id"])) or {}
        label = owner.get("name") or (f"Owner {item['owner_id']}" if item["owner_id"] is not None else "Unassigned")
        counts = owner_risks.setdefault(label, {"High": 0, "Medium": 0, "Low": 0})
        counts[item["severity"]] += 1
    for item in items + lead_items:
        proposal = item["proposal"]
        item["rule_wording"] = [rep_flag_text(flag) for flag in item.get("flags", [])]
        item["evidence"] = (next((deal.get("evidence") or [] for deal in shown
                                 if deal["id"] == item.get("deal_id")), [])
                            if "deal_id" in item else [proposal["why"]])
        if proposal:
            owner_id = item.get("owner_id")
            owner = owners.get(str(owner_id)) or {}
            label = owner.get("name") or (f"Owner {owner_id}" if owner_id is not None else "Unassigned")
            item["internal_task"] = {
                "type": "task", "subject": f"Internal coaching: review triage item {item['item']}",
                "note": f"Internal only. {label}: review {proposal['subject']} and agree the next step. Evidence: {proposal['why']}",
                "owner_id": owner_id, "due_date": proposal["due_date"],
                **({"deal_id": item["deal_id"]} if "deal_id" in item else {"lead_id": item["lead_id"]}),
            }
        else:
            item["internal_task"] = None
    return {
        "risk_counts": dict(risk_counts),
        "owner_counts": dict(owner_counts),
        "owner_risks": owner_risks,
        "proposal_count": len(result.get("proposals") or []),
        "covered_count": sum(bool(item["existing_next_step"]) for item in items),
        "escalation_count": sum(bool(p.get("needs_second_confirmation")) for p in result.get("proposals") or []),
        "lead_items": lead_items,
        "items": items,
        "supporting_findings": {
            "existing_next_steps": [item for item in items if item["existing_next_step"]],
            "clusters": result.get("clusters") or [],
            "partial_coverage": result.get("partial_line"),
        },
        "disclosures": result.get("disclosures") or [],
    }


def core_rule_execution(run, config):
    """State whether the dashboard's two core engagement rules have complete evidence."""
    r1_enabled = config.get("silence_days") is not None
    r2_enabled = bool(config.get("flag_no_next_activity"))
    successful_open_deals = False
    failed_open_deals = False
    successful_activities = False
    failed_activities = False
    for call in run.get("calls", []):
        tool = (call.get("tool") or "").split("__")[-1]
        params = call.get("params") or {}
        response = call.get("response")
        if response is None and call.get("response_file"):
            try:
                response = load_response_file(call["response_file"])
            except (OSError, ValueError, json.JSONDecodeError):
                response = None
        failed = isinstance(response, dict) and (
            response.get("success") is False or "error" in response
        )
        readable = (isinstance(response, dict) and isinstance(response.get("data"), list)
                    and all(isinstance(row, dict) and type(row.get("id")) is int and row["id"] > 0
                            for row in response["data"]))
        unavailable = failed or not readable
        if tool == "getActivities":
            if readable and r1_enabled:
                unavailable = unavailable or any(
                    "deal_id" not in row or type(row.get("done")) is not bool
                    or (row.get("deal_id") is not None and
                        (type(row["deal_id"]) is not int or row["deal_id"] <= 0))
                    or (row["done"] and to_date(row.get("due_date")) is None)
                    for row in response["data"])
            successful_activities = successful_activities or (readable and not failed)
            failed_activities = failed_activities or unavailable
        if tool == "getDeals" and (params.get("filter_id") is None or config.get('_validated_cohort')) and deal_status(params.get("status", "open")) == "open":
            pagination_readable = (isinstance(response, dict) and (
                "additional_data" not in response or isinstance(response.get("additional_data"), dict)
            ))
            successful_open_deals = successful_open_deals or (readable and not failed)
            failed_open_deals = failed_open_deals or unavailable or not pagination_readable or (readable and r2_enabled and any(
                "next_activity_id" not in row or (row["next_activity_id"] is not None
                and (type(row["next_activity_id"]) is not int or row["next_activity_id"] <= 0))
                for row in response["data"]))
            if readable and r1_enabled:
                failed_activities = failed_activities or any(
                    "last_activity_id" not in row or "last_incoming_mail_time" not in row
                    or to_date(row.get("update_time")) is None
                    or (row.get("last_activity_id") is not None and
                        (type(row["last_activity_id"]) is not int or row["last_activity_id"] <= 0))
                    or (row.get("last_incoming_mail_time") is not None
                        and to_date(row["last_incoming_mail_time"]) is None)
                    for row in response["data"])
    return {
        "R1": {"unavailable": int(r1_enabled and (failed_activities or not successful_activities
                                                   or failed_open_deals or not successful_open_deals))},
        "R2": {"unavailable": int(r2_enabled and (failed_open_deals or not successful_open_deals))},
    }


def collection_safe_triage_run(run):
    """Keep malformed core rows out of shared collection; availability reads the raw run."""
    calls = []
    for call in run.get("calls", []):
        tool = (call.get("tool") or "").split("__")[-1]
        params = call.get("params") or {}
        is_core = tool == "getActivities" or (
            tool == "getDeals" and params.get("filter_id") is None
            and deal_status(params.get("status", "open")) == "open"
        )
        if not is_core:
            calls.append(call)
            continue
        response = call.get("response")
        if response is None and call.get("response_file"):
            try:
                response = load_response_file(call["response_file"])
            except (OSError, ValueError, json.JSONDecodeError):
                response = None
        valid_rows = (response.get("data") if isinstance(response, dict)
                      and isinstance(response.get("data"), list) else None)
        if valid_rows is not None:
            valid_rows = [row for row in valid_rows if isinstance(row, dict)
                          and isinstance(row.get("id"), (str, int))]
            if tool == "getActivities":
                valid_rows = [row for row in valid_rows if type(row.get("done")) is bool
                              and (row.get("deal_id") is None or type(row["deal_id"]) is int)
                              and (not row["done"] or to_date(row.get("due_date")) is not None)]
            else:
                cleaned = []
                for row in valid_rows:
                    safe_row = dict(row)
                    if row.get("last_activity_id") is not None and type(row["last_activity_id"]) is not int:
                        safe_row["last_activity_id"] = None
                    if row.get("next_activity_id") is not None and type(row["next_activity_id"]) is not int:
                        safe_row.pop("next_activity_id", None)
                    cleaned.append(safe_row)
                valid_rows = cleaned
        if valid_rows is not None and valid_rows == response["data"]:
            calls.append(call)
            continue
        safe = dict(call)
        safe.pop("response_file", None)
        safe_response = dict(response) if isinstance(response, dict) else {}
        safe_response["data"] = valid_rows or []
        safe["response"] = safe_response
        calls.append(safe)
    return dict(run, calls=calls)


def run_triage(args):
    config = load_json(args.config)
    run = load_json(args.input)
    config, run = apply_stages_input(args, config, run)
    collection_run = collection_safe_triage_run(run)
    rules = config.get("filter_rules") or []
    by_filter = {str(rule["filter_id"]): rule for rule in rules if rule.get("filter_id") is not None}
    filter_matches = defaultdict(set)
    base_calls = []
    for call in collection_run.get("calls", []):
        params = call.get("params") or {}
        fid = str(params.get("filter_id")) if params.get("filter_id") is not None else None
        if call.get("tool", "").split("__")[-1] == "getDeals" and fid in by_filter:
            response = call.get("response")
            if response is None and call.get("response_file"):
                response = load_response_file(call["response_file"])
            filter_matches[fid].update(d["id"] for d in rows(response) if d.get("status", "open") == "open")
        else:
            base_calls.append(call)

    base_run = dict(collection_run, calls=base_calls)
    result = run_brief(args, base_run)
    brief_ranked = result["ranked"]  # priority order over every in-scope deal, before triage re-ranks the flagged ones
    selected_ids = set(getattr(args, "deal_ids", None) or [])
    missing_selected_ids = set()
    if selected_ids:
        missing_selected_ids = selected_ids - {deal["id"] for deal in result["deals"]}
        result["deals"] = [deal for deal in result["deals"] if deal["id"] in selected_ids]
        brief_ranked = [deal for deal in brief_ranked if deal["id"] in selected_ids]
        result["leads"] = []
        result["untouched_leads"] = []
    tables = collect(base_run)
    all_calls = collect(collection_run)
    kept_notes = clean_notes(all_calls["notes"]) if all_calls["notes"] else []
    today = to_date(result["today"])
    rule_execution = core_rule_execution(run, config)
    if config.get("flag_no_next_activity"):
        unknown_next = sum(deal.get("signal_state", {}).get("next_activity", {}).get("state") == "unknown"
                           for deal in result["deals"])
        rule_execution["R2"]["unavailable"] = max(rule_execution["R2"]["unavailable"], unknown_next)
    open_ids = {d["id"] for d in result["deals"]}
    undone = defaultdict(list)
    for activity in tables["activities"].values():
        did = activity.get("deal_id")
        if did in open_ids and not activity.get("done"):
            undone[did].append(activity)

    for deal in result["deals"]:
        did = deal["id"]
        flags = deal["flags"]
        if rule_execution["R1"]["unavailable"]:
            flags[:] = [flag for flag in flags if flag["id"] != "R1"]
        for index, rule in enumerate(rules):
            fid = str(rule.get("filter_id"))
            if did in filter_matches.get(fid, set()):
                flags.append({"id": f"F-{index + 1}" if config.get('_workflow_v2') else "R6" if index == 0 else f"F-{index}", "flag": rule.get("label") or "FILTER_RULE",
                              "meaning": rule.get("meaning") or "matches saved filter", "filter_id": rule["filter_id"],
                              "severity": rule.get("severity") or "Medium"})
        overdue = [a for a in undone[did] if to_date(a.get("due_date")) and to_date(a["due_date"]) < today]
        if overdue:
            flags.append({"id": "R8", "flag": "OVERDUE", "count": len(overdue),
                          "oldest_due": min(a["due_date"] for a in overdue)})
            deal["overdue_activity"] = min(overdue, key=lambda a: a["due_date"])
            deal["overdue_activity_id"] = deal["overdue_activity"]["id"]
        if deal["stage_order_known"] and deal["value"] <= 0 and not deal["is_first_stage"]:
            flags.append({"id": "R9", "flag": "NO_VALUE"})
        high = config.get("high_probability_threshold")
        far = config.get("far_close_days")
        close = to_date(deal.get("expected_close_date"))
        if high is not None and far is not None and deal.get("p") is not None and close:
            threshold = float(high) / 100 if float(high) > 1 else float(high)
            if deal["p"] >= threshold and close > today + timedelta(days=int(far)):
                flags.append({"id": "R10", "flag": "FAR_CLOSE", "expected_close_date": iso(close),
                              "probability": deal["p"]})
        if deal["stage_order_known"] and not deal["is_first_stage"] and (deal.get("org_id") is None or deal.get("person_id") is None):
            flags.append({"id": "R11", "flag": "UNLINKED", "missing": [k for k in ("org_id", "person_id") if deal.get(k) is None]})

        active_ids = {f["id"] for f in flags}
        level = "High" if active_ids & {"R1", "R7"} or {"R1", "R2"} <= active_ids else "Medium"
        if not flags or active_ids <= {"R8", "R9", "R11"}:
            level = "Low"
        for flag in flags:
            configured = flag.get("severity")
            if configured in SEVERITY and SEVERITY[configured] > SEVERITY[level]:
                level = configured
        deal["severity"] = level
        deal["existing_next_step"] = next(({"id": a["id"], "due_date": a.get("due_date"), "subject": a.get("subject")}
                                             for a in sorted(undone[did], key=lambda x: x.get("due_date") or "")
                                             if to_date(a.get("due_date")) and today <= to_date(a["due_date"]) <= today + timedelta(days=3)), None)
        deal["proposable"] = bool(flags) and deal["existing_next_step"] is None

    ranked = sorted((d for d in result["deals"] if d["flags"] and (selected_ids or (not d["in_cluster"] and not d["below_floor"]))),
                    key=lambda d: (-SEVERITY[d["severity"]], -(d.get("priority") or 0), -d["value"]))
    result["ranked"] = ranked
    result["report_skill"] = "deal-triage"
    result["mode"] = "triage"
    result["tool_calls"] = all_calls["tool_calls"]
    result["tools_used"] = dict(all_calls["tools_used"])
    result["write_calls"] = all_calls["write_calls"]
    result["rule_execution"] = rule_execution
    result["token_estimate"] = all_calls["tokens"]
    result["filter_matches"] = {fid: sorted(ids & open_ids) for fid, ids in filter_matches.items()}
    if args.pass_no == 1:
        # Resolve last-touch dates for the brief's top-N by priority, not only the deals already
        # flagged: before the lookup, R1 runs on the update_time fallback and can miss.
        result["top_n_last_activity_ids"] = [str(d["last_activity_id"]) for d in brief_ranked
                                              if d.get("last_activity_id") is not None][:args.top_n]

    shown = ranked[:min(args.show, 10)]
    proposals = []
    # Every ranked deal gets its decision and, where no task is due, its proposal (0.7.15, decision 2A, ADR-044 note): the
    # item number is the deal's place in the ranked queue, so a "show more" page can print its own items and an accept by
    # number works for any page. Before 0.7.15 only the first page (shown) was proposed, so a day where the top five all
    # had a task due proposed nothing (0.7.14 release run (b)).
    for number, deal in enumerate(ranked, 1):
        evidence = "; ".join(flag_text(f) for f in deal["flags"])
        evidence_lines = deal_evidence(deal["id"], all_calls["activities"], kept_notes)
        deal["evidence"] = evidence_lines
        # A note saying the deal was won or lost is about its status, not its next activity: an open task does not hide
        # the status question (0.7.10 HC-06: the booked evaluation meeting skipped the "contract signed" deal).
        if deal["existing_next_step"] and not any(flag["id"] == "R7" for flag in deal["flags"]):
            step = deal["existing_next_step"]
            deal["decision"] = {
                "kind": "existing_task_review",
                "text": f"Work the existing task '{step['subject'] or 'Activity'}' by {step['due_date']}; confirm the buyer's next step.",
                "due_date": step["due_date"],
            }
            continue
        # An overdue task (R8) leads over the other flags so the proposal reuses that task; a status note (R7) leads over
        # it, as LEAD_FLAG_ORDER says (0.7.20, review 3.2: a deal with both lost its status question).
        lead_flag = (next((flag for flag in deal["flags"] if flag["id"] == "R7"), None)
                     or next((flag for flag in deal["flags"] if flag["id"] == "R8"), None)
                     or min(deal["flags"], key=lambda f: lead_flag_rank(f["id"])))
        activity_type, subject = ACTIVITY_BY_RULE.get(lead_flag["id"], ("task", "Review the saved-filter finding"))
        if lead_flag["id"] == "R8" and crm_quote(deal["overdue_activity"].get("subject")):
            subject = f"Review and reschedule the existing task '{crm_quote(deal['overdue_activity']['subject'])}'"
        if lead_flag["id"] == "R6" or lead_flag["id"].startswith("F-"):
            rule = next((r for r in rules if str(r.get("filter_id")) == str(lead_flag["filter_id"])), {})
            activity_type = rule.get("activity_type") or activity_type
            subject = rule.get("proposed_activity") or (subject if lead_flag["id"] == "R6" else f"Act on the \"{rule.get('label')}\" finding")
        due = next_business_due(today, 1 if deal["severity"] == "High" else 3)
        proposal = {"item": number, "deal_id": deal["id"], "type": activity_type,
                    "subject": subject, "due_date": iso(due), "why": evidence, "lead_flag": lead_flag["id"],
                    "action": "updateActivity" if lead_flag["id"] == "R8" else "addActivity",
                    "needs_second_confirmation": lead_flag["id"] in ("R7", "R4", "R9")}
        if lead_flag["id"] == "R7":
            # The status the note points to; the accept turn asks "mark <deal> as WON/LOST" (write_confirmation.py --turn).
            proposal["status_change"] = lead_flag["status"]
        elif lead_flag["id"] in ("R4", "R9"):
            # The accept turn names the field and what is recorded; compute has no new date or value to propose, so the
            # confirmed write stays the review activity (decision B, 17 Oct).
            proposal["field_check"] = ({"field": "expected_close_date", "recorded": lead_flag.get("expected_close_date")}
                                       if lead_flag["id"] == "R4" else {"field": "value", "recorded": None})
        if proposal["action"] == "updateActivity":
            proposal["activity_id"] = deal["overdue_activity_id"]
        else:
            person = deal.get("person_id")
            proposal["person_id"] = person.get("value", person.get("id")) if isinstance(person, dict) else person
            proposal["person_name"] = person.get("name") if isinstance(person, dict) else deal.get("person_name")
        proposals.append(proposal)
        deal["decision"] = {"kind": "proposed_activity",
                            "text": f"{subject} by {iso(due)}.", "due_date": iso(due)}

    # leads going cold (speed_to_lead / hybrid): untouched leads older than the silence threshold
    silence_days = config.get("silence_days")
    cold = [l for l in result.get("untouched_leads", [])
            if silence_days is not None and l.get("age_days") is not None and l["age_days"] >= silence_days]
    if cold:
        for lead in cold[:5]:
            number = len(ranked) + sum("lead_id" in p for p in proposals) + 1
            due = next_business_due(today, 1)
            proposal = {"item": number, "lead_id": lead["id"], "type": "call", "subject": "First contact call",
                        "due_date": iso(due), "why": f"lead created {lead['add_date']}, {lead['age_days']} days untouched",
                        "lead_flag": "LEAD_COLD", "action": "addActivity", "needs_second_confirmation": False}
            proposals.append(proposal)

    acc = write_accounting(run)
    accepted = run.get("accepted_items") or []
    skipped = run.get("skipped_items") or []
    result["proposals"] = proposals
    result["action_states"] = action_states(result, run)
    result["created_activity_ids"] = acc["created_activity_ids"]
    result["failed_writes"] = acc["failed"]
    result["run_log_line"] = (f"Proposed {len(proposals)} · accepted {len(accepted)} · skipped {len(skipped)} · "
                              f"written {len(acc['written'])} · {all_calls['tool_calls']} tool calls"
                              + (f" · created activities: {len(acc['created_activity_ids'])}" if acc["created_activity_ids"] else "")
                              + (f" · failed writes: {', '.join(acc['failed'])}" if acc["failed"] else "")
                              + (f" · uncertain writes: {', '.join(acc['uncertain'])}" if acc["uncertain"] else ""))
    result["coverage_line"] = (f"Coverage: fetched {len(tables['open_deals'])}/{str(len(tables['open_deals'])) + ('+' if tables['open_cursor_unfollowed'] else '')} open deals · {all_calls['tool_calls']} tool calls"
                               + (" · stages from account context" if tables["stages_source"] == "account_context" else ""))
    disclosures = [result["coverage_line"], *result["stage_order_notices"], *result["unavailable_notices"]]
    if selected_ids:
        disclosures.append("Selected deal review: " + ", ".join(f"#{deal_id}" for deal_id in sorted(selected_ids))
                           + ". The saved value floor does not exclude explicitly requested deals.")
        if missing_selected_ids:
            disclosures.append("Requested deals not found in the fetched scope: "
                               + ", ".join(f"#{deal_id}" for deal_id in sorted(missing_selected_ids))
                               + ". No action was proposed for those deals.")
    for rule, state in result["rule_execution"].items():
        if state["unavailable"]:
            limitation = f"{rule} evidence unavailable: required CRM fields or responses are missing or malformed. Dashboard unavailable; treat affected findings as unverified."
            disclosures.append(limitation)
    if tables["open_cursor_unfollowed"]:
        result["partial_line"] = "Partial coverage: the most recently updated open deals were not fetched; narrow the scope before acting on an incomplete list."
        disclosures.append(result["partial_line"])
    if not (acc["written"] or acc["uncertain"]):
        nothing_written_line = "Nothing has been written yet. Name which items to accept — a bare \"yes\" writes nothing."
        disclosures.append(nothing_written_line)
    if config.get("writes_allowed") is False:
        # Only a config that says so (0.7.15, decision 1A, ADR-038 note): a version-2 profile has no writes setting, its
        # writes are allowed per item by the accept and the write guard, and an absent key printed this line on every
        # Cowork quick-setup triage while the writes went through (0.7.13 run (iii), 0.7.14 release run (b)).
        writes_off_line = "Writes are off in config (writes_allowed): proposals only; nothing will be written this run."
        disclosures.append(writes_off_line)
    disclosures.append(result["run_log_line"])
    result["disclosures"] = disclosures
    lead_proposals = [proposal for proposal in proposals if proposal.get("lead_id") is not None]
    result.setdefault("disclosures", []).insert(-1, DATE_EXPLANATION)
    result["output_domain"] = configured_domain(config)
    result["markdown"] = link_markdown(render_triage_markdown(result, shown, proposals, lead_proposals, config), result)
    result["audit"] = {"rule_ids": {str(deal["id"]): [flag["id"] for flag in deal["flags"]]
                                      for deal in ranked}, "run_log": result["run_log_line"]}
    result["triage_stats_delta"] = {"proposed": len(proposals), "accepted": len(accepted),
                                    "written": len(acc["written"]), "skipped": len(skipped)}
    result["presentation"] = build_triage_presentation(result, ranked)
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["brief", "triage", "forecast"], required=True)
    ap.add_argument("--pass", dest="pass_no", type=int, choices=[1, 2], default=2)
    ap.add_argument("--config", required=True)
    ap.add_argument("--input", required=True)
    ap.add_argument("--today", default=None, help="YYYY-MM-DD; defaults to the run file's today, then the system date")
    ap.add_argument("--budget", type=int, default=None, help="token budget for the fetch; default config.budget_tokens, else 60000")
    ap.add_argument("--top-n", dest="top_n", type=int, default=30)
    ap.add_argument("--show", type=int, default=10)
    ap.add_argument("--deal-ids", type=lambda value: [int(part) for part in value.split(",")],
                    help="comma-separated deal IDs for an explicit selected-deal review")
    ap.add_argument("--markdown", action="store_true", help="print the Markdown block instead of JSON")
    ap.add_argument("--full", action="store_true", help="print every record (deals, ranked, leads) in the JSON; default is a compact summary")
    ap.add_argument("--stages", help="saved getAccountContext response; its stages stand in when the run has no stage read")
    ap.add_argument("--unavailable", action="append", choices=["getNotes"],
                    help="a tool that is not in this workspace after one tool search; skips the rules it feeds and prints its notice")
    ap.add_argument("--output", help="write the complete result (what --full prints) to this path; stdout keeps the Markdown or the compact form")
    args = ap.parse_args(argv)
    if args.mode == "forecast":
        print(json.dumps({"error": "--mode forecast is not implemented in deal-triage"}))
        return 2
    result = run_triage(args) if args.mode == "triage" else run_brief(args)
    stamp_result(result, result["tool_calls"], result["token_estimate"], SKILL_DIR, load_json(args.config))
    if args.output:
        # The saved result is written by the script (0.7.15, ADR-044 note): DT-04 on the 0.7.15 candidate ran --markdown --full,
        # got the Markdown alone, and the shell redirect it tried next was denied by the eval sandbox.
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(result, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
        os.replace(temporary, path)
        print(f"saved {path}", file=sys.stderr)
    if args.markdown:
        return print_checked_markdown(result, args.input)
    elif args.full:
        print(json.dumps(result, indent=1, ensure_ascii=False, default=str))
    else:
        print(json.dumps(compact(result), indent=1, ensure_ascii=False, default=str))
    return 0


def link_refs(result):
    """The record links the version-1 Markdown may carry, for the reply check's link-host rule: one reference per saved deal
    with its account URL and no name. Without a name the check enforces no name-to-link rule (that is the version-2
    contract, where every mention is rendered as a link); with the URL it allows the configured account host only."""
    domain = result.get("output_domain")
    refs = []
    for deal_id in saved_deals(result):
        url = deal_url(domain, deal_id)
        if url:
            refs.append({"type": "deal", "id": deal_id, "url": url})
    return refs


def print_checked_markdown(result, near=None):
    """Print the version-1 report only when the reply checks pass (0.7.21, review 3.1; the brief's pattern since 0.7.9): no
    visible record ids, every link on a record host, the text exact. A failed check prints the checks to stderr and nothing
    to stdout, so the chat cannot paste a block the checks refused; the printed text is saved for the end-of-reply hook."""
    text = result["markdown"]
    checked = validate_reply(text, {"markdown": text, "record_refs": link_refs(result)})
    if not checked["passed"]:
        print(json.dumps(checked, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    save_expected_reply(text, "reply", near)
    print(framed(text))
    return 0


COMPACT_KEYS = ("report_skill", "mode", "pass", "today", "window_start", "skill", "version", "calls", "estimated_tokens", "tool_calls", "tools_used", "write_calls", "failed_calls",
                "token_estimate", "per_deal_tokens", "per_deal_tokens_all_in", "budget_tokens", "budget_remaining_deals",
                "open_cursor_unfollowed", "stage_probabilities_vary", "pipelines", "stage_order_notices", "owners", "excluded",
                "excluded_pipelines", "n_records", "top_n_last_activity_ids", "next_step", "coverage_line",
                "partial_line", "footer", "rule_execution")


def compact(result):
    """Everything the skill needs between passes, without the per-record tables (which can run to 100 KB
    and get parked in a file by the client). The Markdown block is included on pass 2."""
    out = {k: result[k] for k in COMPACT_KEYS if k in result}
    out["counts"] = {"deals_in_scope": len(result.get("deals", [])), "ranked": len(result.get("ranked", [])),
                     "call_first_deals": len(result.get("call_first_deals", [])), "leads": len(result.get("leads", [])),
                     "untouched_leads": len(result.get("untouched_leads", [])), "clusters": len(result.get("clusters", []))}
    out["ranked_top10"] = [{"id": d["id"], "title": d["title"], "priority": d.get("priority"), "at_risk": d["at_risk"],
                            "flags": [f["id"] for f in d["flags"]]} for d in result.get("ranked", [])[:10]]
    if result.get("mode") == "triage":
        for key in ("proposals", "filter_matches", "created_activity_ids", "failed_writes", "run_log_line", "triage_stats_delta", "presentation", "action_states"):
            if key in result:
                out[key] = result[key]
        out["ranked_top10"] = [dict(row, severity=d.get("severity"), existing_next_step=d.get("existing_next_step"))
                               for row, d in zip(out["ranked_top10"], result.get("ranked", [])[:10])]
    if result.get("pass") == 2:
        out["markdown"] = result.get("markdown")
    return out


if __name__ == "__main__":
    sys.exit(main())
