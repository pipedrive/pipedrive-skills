"""Deterministic Forecast calculation primitives and command-line entry point."""

import argparse
from collections import defaultdict
from datetime import date
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from account_context import load_stage_context, metadata, named_pipeline_labels, scope_label, with_context_stages
from activity_evidence import completed_activity_evidence, DATE_EXPLANATION
from run_state import checkpoint
from pipedrive_links import configured_domain, link_markdown, markdown_deal
from final_response import validate_reply
from record_presentation import record_refs
from dashboard_charts import money, PROBABILITY_MODE_WORDS
from pdcore import md_text, STAGE_ORDER_LINE, collect, period_bounds, deal_status, load_response_file, order_unknown_pipelines, save_expected_reply, framed, stage_context, stamp_result  # noqa: E402

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _probability(value):
    """Normalize CRM percentages or stored decimal assumptions to a decimal probability."""
    if type(value) not in (int, float):
        return None
    value = float(value)
    if value < 0:
        return None
    return value / 100.0 if value > 1 else value


def _stage_probabilities_vary(stages):
    values = {_probability(stage.get("deal_probability")) for stage in stages.values()}
    values.discard(None)
    return len(values) > 1


def resolve_probability(deal, stages, ladder):
    """Resolve one deal's base probability using the approved strict precedence."""
    deal_probability = _probability(deal.get("probability"))
    if deal_probability is not None:
        return {"source": "deal", "base_probability": deal_probability}

    stage = stages.get(deal.get("stage_id")) or stages.get(str(deal.get("stage_id"))) or {}
    stage_probability = _probability(stage.get("deal_probability"))
    if _stage_probabilities_vary(stages) and stage_probability is not None:
        return {"source": "stage", "base_probability": stage_probability}

    if isinstance(ladder, dict):
        ladder_probability = _probability(ladder.get(deal.get("stage_id"), ladder.get(str(deal.get("stage_id")))) )
        if ladder_probability is not None:
            return {"source": "ladder", "base_probability": ladder_probability}

    return {"source": "unweighted", "base_probability": 1.0}


def apply_haircuts(deal, base_probability, signals, haircuts):
    """Apply the single most cautious configured warning adjustment, without compounding."""
    candidates = []
    for rule in ("silent", "no_next", "slipped"):
        multiplier = (haircuts or {}).get(rule)
        if signals.get(rule) and type(multiplier) in (int, float):
            candidates.append((base_probability * float(multiplier), rule))
    if not candidates:
        return {"adjusted_probability": base_probability, "rules": []}
    lowest = min(value for value, _ in candidates)
    return {
        "adjusted_probability": lowest,
        "rules": [rule for value, rule in candidates if value == lowest],
    }


def _signals(deal, today):
    close_date = deal.get("expected_close_date")
    return {
        "silent": bool(deal.get("silent")),
        "no_next": deal.get("next_activity_id") is None and "next_activity_id" in deal,
        "slipped": isinstance(close_date, str) and close_date < today,
    }


def _target_for(targets, period, currency, lens):
    key = "|".join((period, currency, lens["type"], str(lens["id"])))
    value = (targets or {}).get(key)
    return float(value) if type(value) in (int, float) else None


def _coverage_band(coverage, bands):
    if coverage is None:
        return None
    healthy = float((bands or {}).get("healthy", 3.0))
    at_risk = float((bands or {}).get("at_risk", 2.0))
    if coverage >= healthy:
        return "healthy"
    if coverage < at_risk:
        return "critical"
    return "at_risk"


def _stage_order_notices(order_unknown, stages, config, lens):
    """One line per pipeline in the lens whose stage order Pipedrive did not supply (ADR-040). The line names the
    pipeline as the account context or saved settings do; "First stage → Last stage" only when no name exists."""
    lens_pipeline = (str(lens["id"]) if lens.get("type") == "pipeline" else
                     str(lens["id"]).partition(":")[2] if lens.get("type") == "owner_pipeline" else None)
    scope = config.get("scope") or {}
    excluded = {str(pid) for pid in scope.get("exclude_pipeline_ids") or []}
    in_scope = {str(pid) for pid in scope.get("pipeline_ids") or []}
    unknown = order_unknown - {None}
    labels = named_pipeline_labels(stage_context({key: stage for key, stage in stages.items() if stage.get("pipeline_id") in unknown}, []), config)["pipeline_labels"]
    return [STAGE_ORDER_LINE.format(labels[pid]) for pid in sorted(unknown, key=str)
            if lens_pipeline in (None, str(pid)) and str(pid) not in excluded and (not in_scope or str(pid) in in_scope)]


def _matches_lens(deal, lens):
    lens_type = lens.get("type")
    lens_id = str(lens.get("id"))
    if lens_type == "team":
        return True
    if lens_type == "owner":
        return str(deal.get("owner_id")) == lens_id
    if lens_type == "pipeline":
        return str(deal.get("pipeline_id")) == lens_id
    if lens_type == "owner_pipeline":
        owner_id, separator, pipeline_id = lens_id.partition(":")
        return bool(separator) and str(deal.get("owner_id")) == owner_id and str(deal.get("pipeline_id")) == pipeline_id
    return False


def _excluded_summary(excluded, in_forecast, period):
    """Open deals the period left out, counted per currency, so the report never says it covers deals it dropped."""
    by_currency = {}
    for reason, table in excluded.items():
        for currency, total in table.items():
            by_currency.setdefault(currency, {})[reason] = {"count": total["count"], "value": total["value"]}
    count = sum(total["count"] for table in excluded.values() for total in table.values())
    totals = {}
    for currency, reasons in by_currency.items():
        totals[currency] = sum(item["value"] for item in reasons.values())
    summary = {"count": count, "in_forecast_count": in_forecast, "by_currency": by_currency,
               "value_by_currency": totals, "period": period}
    summary["line"] = excluded_line(summary)
    return summary


def excluded_line(summary):
    """The one mandatory line that names the open deals this forecast leaves out."""
    count = summary.get("count") or 0
    if not count:
        return ""
    value = ", ".join(f"{money(amount, currency)}" for currency, amount in sorted(summary["value_by_currency"].items()))
    if count == 1:
        return f"1 open deal ({value}) has a close date outside {summary['period']} or none, so it is not in this forecast."
    return f"{count} open deals ({value}) have close dates outside {summary['period']} or none, so they are not in this forecast."


def render_markdown(result):
    """Render only facts already calculated by Forecast; no arithmetic lives in prose."""
    if result.get("data_unavailable"):
        return "Forecast unavailable: " + result.get("reason", "required evidence is unavailable")
    lines = [f"# Are we on track? — {result['period']}"]
    if result.get("scope_label"):
        lines.append("Scope: " + result["scope_label"])
    lines.extend(result.get("stage_order_notices") or [])
    unweighted = result.get("mode") == "unweighted" and any(
        section.get("deals") or section.get("open_value")
        for section in result.get("currency_sections", {}).values())
    revenue_label = "Value including won revenue" if unweighted else "Likely revenue this period"
    open_label = "Open value after risk adjustments" if unweighted else "Likely revenue from open deals"
    for currency, section in sorted(result.get("currency_sections", {}).items()):
        lines.extend((
            f"## {currency}",
            f"Won to date: {money(section['won_to_date'], currency)}",
            f"{revenue_label}: {money(section['adjusted_forecast'], currency)}",
        ))
        if unweighted:
            lines.append(f"Open deal value: {money(section['open_value'], currency)}")
            lines.append("Usable probabilities are missing. This is open deal value, not a likelihood estimate; any accepted risk adjustments are shown separately.")
        elif result.get("mode") in {"deal", "stage", "mixed"}:
            lines.append("Based on the chances recorded in Pipedrive, with any accepted local assumptions and risk adjustments shown below.")
        if section.get("adjusted_open") is not None:
            lines.append(f"{open_label}: {money(section['adjusted_open'], currency)}")
        comparison = result.get("target_comparisons", {}).get(currency, {})
        if comparison.get("state") == "available":
            lines.append(f"Target: {money(comparison['target'], currency)}")
            difference = comparison["projected_difference"]
            if unweighted and section["won_to_date"] >= comparison["target"]:
                lines.append("On track: Yes — won revenue has already met the target.")
            elif unweighted:
                lines.append("On track: Cannot estimate likelihood without usable probabilities.")
                label = "Value below target" if difference > 0 else "Value above target" if difference < 0 else "Value matches target"
                lines.append(f"{label}: {money(abs(difference), currency)}")
            elif difference > 0:
                lines.append("On track: No — likely revenue is below the target.")
                lines.append(f"Projected shortfall: {money(difference, currency)}")
            elif difference < 0:
                lines.append("On track: Yes — likely revenue is above the target.")
                lines.append(f"Projected surplus: +{money(-difference, currency)}")
            else:
                lines.append("On track: Yes — likely revenue meets the target.")
                lines.append("Likely revenue meets the target exactly.")
            lines.append(f"Still to win against target: {money(comparison['remaining_to_target'], currency)}")
        else:
            lines.append("Target: not configured")
            lines.append("On track: Cannot tell without a target.")
        if result.get("mode") != "unweighted":
            lines.append(f"Weighted forecast: {money(section['weighted_forecast'], currency)}")
        lines.append(f"If every included open deal closes: {money(section['best_case'], currency)}")
        commit = comparison.get("manager_commit")
        if commit is not None:
            lines.append(f"Manager commit (entered judgment): {money(commit, currency)}")
            if comparison.get("commit_above_best_case"):
                lines.append("Current in-scope CRM deals cannot support this Manager commit.")
        if result.get("adjustment_status") in {"not_configured", "disabled"}:
            state = "not configured" if result["adjustment_status"] == "not_configured" else "turned off"
            lines.append(f"Risk adjustments are {state}. Stale activity, missing follow-ups and passed close dates have not reduced this amount.")
        activity = (result.get("evidence_status") or {}).get("activity") or {}
        if not activity.get("available", True):
            lines.append("Activity evidence is incomplete; this amount may be optimistic.")
        lines.append("")
    for currency, subtotal in sorted((result.get("slipped_subtotal") or {}).items()):
        count = subtotal["count"]
        noun = "deal" if count == 1 else "deals"
        handling = result.get("slipped_handling", "include")
        placement = "included in the headline" if handling == "include" else "shown separately from the headline"
        lines.append(
            f"Slipped from earlier periods: {count} {noun}, {money(subtotal['value'], currency)} open value; "
            f"{money(subtotal['adjusted_forecast'], currency)} at current probabilities and risk adjustments, {placement}."
        )
    if (result.get("excluded_open_deals") or {}).get("line"):
        lines.append(result["excluded_open_deals"]["line"])
    risks = [
        deal
        for section in result.get("currency_sections", {}).values()
        for deal in section.get("deals", [])
        if deal.get("haircut_impact", 0) > 0
    ]
    if risks:
        lines.append("## What could change this forecast")
        reasons_by_rule = {"silent": "no recent completed CRM activity was recorded",
                           "no_next": "no next customer activity is scheduled",
                           "slipped": "the expected close date has passed"}
        for currency, section in sorted(result.get("currency_sections", {}).items()):
            currency_risks = sorted((deal for deal in section.get("deals", [])
                                     if deal.get("haircut_impact", 0) > 0),
                                    key=lambda deal: -deal["haircut_impact"])
            for deal in currency_risks[:5]:
                title = markdown_deal(result, deal)
                reasons = "; ".join(reasons_by_rule.get(rule, rule.replace("_", " "))
                                    for rule in deal["haircut_rules"])
                lines.append(f"- {title}: forecast reduced because {reasons}; impact {money(deal['haircut_impact'], currency)}.")
    concentration = result.get("concentration_risks") or []
    if concentration:
        lines.append("## Revenue concentrated in a few deals")
        for deal in sorted(concentration, key=lambda item: -item["share"])[:5]:
            title = markdown_deal(result, deal)
            contribution_label = f"included {deal['currency']} value" if unweighted else f"likely {deal['currency']} revenue"
            lines.append(
                f"- {title}: {deal['share']:.0%} of {contribution_label} depends on this deal closing."
            )
    if risks or concentration:
        lines.append("Which recorded assumption would you validate first? For deal-level next steps, run Deal Triage on the named deals.")
    for currency, section in sorted(result.get("currency_sections", {}).items()):
        adjusted = [deal for deal in section.get("deals", []) if deal.get("haircut_impact", 0) > 0]
        if len(adjusted) > 5:
            lines.append(f"Showing the 5 largest adjustments for {currency}; {len(adjusted) - 5} other adjusted deals are retained in the saved report.")
        silent = sum("silent" in deal.get("haircut_rules", []) for deal in section.get("deals", []))
        total = len(section.get("deals", []))
        if total and silent == total:
            lines.append(f"The inactivity adjustment affects {silent} of {total} open deals in {currency}. Review whether the saved {result.get('silence_days', 7)}-day threshold suits your sales motion before changing it; settings have not been changed.")
    activity = (result.get("evidence_status") or {}).get("activity") or {}
    if not activity.get("available", True):
        count = activity.get("affected_deals", 0)
        noun = "deal" if count == 1 else "deals"
        lines.append(f"Activity evidence incomplete for {count} {noun}; available risk adjustments were applied and the amount may be optimistic.")
    lines.append("## Assumptions")
    lines.append("Probability source: " + ("Unweighted" if result.get("mode") == "unweighted" else PROBABILITY_MODE_WORDS.get(result.get("mode"), "unavailable")) + ".")
    if result.get("adjustment_settings"):
        names = {"silent": "inactive activity", "no_next": "no next activity", "slipped": "passed close date"}
        lines.append("Active risk multipliers: " + ", ".join(f"{names.get(key, key)} {value:g}" for key, value in sorted(result["adjustment_settings"].items())) + "; overlapping rules use the smallest multiplier.")
    lines.append("Probabilities use the available deal, CRM-stage, local-ladder, or unweighted source shown above.")
    if result.get("stages_source") == "account_context":
        lines.append("Stage names and probabilities come from the account context.")
    lines.append("Limits: this forecast reflects Pipedrive data and cannot predict individual closes or stage velocity.")
    return link_markdown("\n".join(lines), result)


def ladder_offer(result):
    """The stage-ladder offer SKILL.md §2 requires on an unweighted result, or "" when it does not apply.

    A pipeline without Pipedrive stage order gets no offer (ADR-040), nor does a result with no open deals or no data.
    """
    sections = result.get("currency_sections") or {}
    if (result.get("data_unavailable") or result.get("mode") != "unweighted" or result.get("stage_order_notices")
            or not any(section.get("deals") for section in sections.values())):
        return ""
    stages = list(dict.fromkeys(item["stage"] for section in sections.values() for item in section.get("stage_breakdown") or []))
    shown = ", ".join(stages[:8]) + (f" and {len(stages) - 8} more" if len(stages) > 8 else "")
    return (f"Stage ladder: the CRM stage probabilities do not weight these deals. A local, editable ladder for {shown} stays on this machine, "
            "is never written to Pipedrive and never overrides a CRM value. Use these, edit them, or leave it unweighted.")


def compute_forecast(data, config, period, today, lens=None, include_breakdowns=True):
    """Calculate reconciled per-currency Forecast figures from already-read CRM rows."""
    forecast = (config or {}).get("forecast") or {}
    lens = lens or {"type": "team", "id": "all"}
    unavailable = list(data.get("core_unavailable") or [])
    if unavailable:
        result = {
            "period": period, "today": today, "lens": lens, "data_unavailable": True,
            "reason": "Required forecast evidence unavailable: " + ", ".join(unavailable),
            "currency_sections": {}, "target_comparisons": {}, "slipped_subtotal": {},
            "evidence_status": {"core": {"unavailable": unavailable}},
            "reconciliation": {"passed": False},
        }
        result["activity_date_explanation"] = DATE_EXPLANATION
        result["markdown"] = render_markdown(result) + "\n\n" + DATE_EXPLANATION
        return result
    stages = data.get("stages") or {}
    order_unknown = order_unknown_pipelines(stages)
    stage_position = {stage_id: index for index, stage_id in enumerate(stages)}
    open_deals = data.get("open_deals") or []
    won_deals = data.get("won_deals") or []
    lost_deals = data.get("lost_deals") or []
    haircuts = forecast.get("haircuts") or {}
    adjustment_status = ("not_configured" if "haircuts" not in forecast else
                         "disabled" if all(value == 1 for value in haircuts.values()) else "configured")
    ladder = forecast.get("probability_ladder")
    slipped_handling = forecast.get("slipped_handling", "include")
    period_start, period_end = period_bounds(period)
    sections = defaultdict(lambda: {
        "won_to_date": 0.0, "open_value": 0.0, "weighted_forecast": 0.0,
        "adjusted_forecast": 0.0, "best_case": 0.0, "deals": [],
        "closed_won": [], "closed_lost": [],
    })
    for deal in won_deals:
        if not _matches_lens(deal, lens):
            continue
        won_date = str(deal.get("local_won_date") or deal.get("won_time") or "")[:10]
        if not (period_start <= won_date <= min(period_end, today[:10])):
            continue
        currency = deal.get("currency") or "UNSPECIFIED"
        value = float(deal.get("value") or 0)
        sections[currency]["won_to_date"] += value
        sections[currency]["closed_won"].append({
            "id": deal.get("id"), "title": deal.get("title"), "value": value,
            "date": won_date,
        })
    for deal in lost_deals:
        if not _matches_lens(deal, lens):
            continue
        lost_date = str(deal.get("local_lost_date") or deal.get("lost_time") or "")[:10]
        if not (period_start <= lost_date <= min(period_end, today[:10])):
            continue
        currency = deal.get("currency") or "UNSPECIFIED"
        sections[currency]["closed_lost"].append({
            "id": deal.get("id"), "title": deal.get("title"),
            "value": float(deal.get("value") or 0), "date": lost_date,
        })
    modes = set()
    slipped_subtotal = defaultdict(lambda: {"count": 0, "value": 0.0, "adjusted_forecast": 0.0})
    excluded_open = {"outside_period": defaultdict(lambda: {"count": 0, "value": 0.0}),
                     "no_close_date": defaultdict(lambda: {"count": 0, "value": 0.0})}
    open_in_forecast = 0
    for deal in open_deals:
        if not _matches_lens(deal, lens):
            continue
        close_date = deal.get("expected_close_date")
        dated = isinstance(close_date, str) and bool(close_date.strip())
        slipped_from_earlier_period = dated and close_date < period_start and close_date < today[:10]
        in_period = dated and period_start <= close_date <= period_end
        currency = deal.get("currency") or "UNSPECIFIED"
        value = float(deal.get("value") or 0)
        if not slipped_from_earlier_period and not in_period:
            reason = excluded_open["outside_period" if dated else "no_close_date"][currency]
            reason["count"] += 1
            reason["value"] += value
            continue
        open_in_forecast += 1
        probability = resolve_probability(deal, stages, ladder)
        modes.add(probability["source"])
        adjusted = apply_haircuts(deal, probability["base_probability"], _signals(deal, today), haircuts)
        if slipped_from_earlier_period:
            slipped_subtotal[currency]["count"] += 1
            slipped_subtotal[currency]["value"] += value
            slipped_subtotal[currency]["adjusted_forecast"] += value * adjusted["adjusted_probability"]
            if slipped_handling == "separate":
                continue
        section = sections[currency]
        section["open_value"] += value
        section["weighted_forecast"] += value * probability["base_probability"]
        section["adjusted_forecast"] += value * adjusted["adjusted_probability"]
        if probability["base_probability"] > 0:
            section["best_case"] += value
        section["deals"].append({
            "id": deal.get("id"), "title": deal.get("title"), "value": value, "currency": currency,
            "stage": (stages.get(deal.get("stage_id")) or {}).get("name") or "Unknown stage",
            "stage_id": deal.get("stage_id"), "pipeline_id": deal.get("pipeline_id"),
            "stage_order": None if (stages.get(deal.get("stage_id")) or {}).get("pipeline_id") in order_unknown
            else (stages.get(deal.get("stage_id")) or {}).get("order_nr"),
            "expected_close_date": close_date,
            "completed_activity_evidence": deal.get("completed_activity_evidence") or [],
           "last_activity_scheduled_date": deal.get("last_activity_scheduled_date"),
           "last_activity_marked_as_done_time": deal.get("last_activity_marked_as_done_time"),
           "last_completed_activity_date": deal.get("last_completed_activity_date"),
            "last_activity_date_source": deal.get("last_activity_date_source"),
            "days_since_activity": deal.get("days_since_activity"),
            "next_activity_id": deal.get("next_activity_id"),
            "next_activity_known": "next_activity_id" in deal,
            "base_probability": probability["base_probability"], "probability_source": probability["source"],
            "adjusted_probability": adjusted["adjusted_probability"], "haircut_rules": adjusted["rules"],
            "haircut_impact": round(value * (probability["base_probability"] - adjusted["adjusted_probability"]), 2),
        })
    for section in sections.values():
        stage_totals = {}
        for deal in section["deals"]:
            stage = deal["stage"]
            stage_key = (deal["pipeline_id"], deal["stage_id"])
            item = stage_totals.setdefault(stage_key, {"stage": stage, "order": deal["stage_order"],
                                                       "pipeline_id": deal["pipeline_id"],
                                                       "stage_id": deal["stage_id"],
                                                       "count": 0, "value": 0.0, "deal_ids": []})
            item["count"] += 1
            item["value"] += deal["value"]
            item["deal_ids"].append(deal["id"])
        section["stage_breakdown"] = sorted(stage_totals.values(), key=lambda item: (
            item["order"] is None,
            stage_position.get(item["stage_id"], 0) if item["order"] is None else item["order"], item["stage"]))
        review = [deal for deal in section["deals"]
                  if deal["next_activity_known"] and deal["next_activity_id"] is None]
        section["close_date_review"] = {
            "count": len(review),
            "deal_ids": [deal["id"] for deal in sorted(review, key=lambda deal: (deal["expected_close_date"] or "9999", -deal["value"]))],
        }
        section["adjusted_open"] = round(section["adjusted_forecast"], 2)
        for name in ("weighted_forecast", "adjusted_forecast", "best_case"):
            section[name] += section["won_to_date"]
    mode = "unweighted" if modes == {"unweighted"} else "mixed" if len(modes) > 1 else next(iter(modes), "unweighted")
    reconciled = all(
        abs(section["weighted_forecast"] - (section["won_to_date"] + sum(
            deal["value"] * deal["base_probability"] for deal in section["deals"]
        ))) < 1e-9 for section in sections.values()
    )
    comparisons = {}
    for currency, section in sections.items():
        target = _target_for(forecast.get("targets"), period, currency, lens)
        manager_commit = _target_for(forecast.get("manager_commits"), period, currency, lens)
        if target is None:
            comparisons[currency] = {"target": None, "manager_commit": manager_commit, "gap": None,
                                     "remaining_to_target": None, "projected_difference": None,
                                     "commit_above_best_case": bool(manager_commit is not None and manager_commit > section["best_case"]),
                                     "coverage": None, "coverage_band": None, "state": "not_configured"}
        else:
            remaining = max(target - section["won_to_date"], 0.0)
            coverage = section["open_value"] / remaining if remaining else None
            comparisons[currency] = {
                "target": target, "manager_commit": manager_commit, "gap": remaining,
                "remaining_to_target": remaining,
                "projected_difference": round(target - section["adjusted_forecast"], 2),
                "commit_above_best_case": bool(manager_commit is not None and manager_commit > section["best_case"]),
                "coverage": coverage,
                "coverage_band": _coverage_band(coverage, forecast.get("coverage_bands")),
                "state": "available",
            }
    unavailable_activity = (data.get("activity_unavailable") or {}).get("deal_ids") or []
    concentration_threshold = float(forecast.get("concentration_threshold", 0.4))
    concentration_risks = []
    for currency, section in sections.items():
        denominator = section["adjusted_forecast"]
        if denominator <= 0:
            continue
        for deal in section["deals"]:
            share = deal["value"] * deal["adjusted_probability"] / denominator
            if share >= concentration_threshold:
                concentration_risks.append({**deal, "share": share})
    result = {
        "report_skill": "forecast", "period": period, "today": today, "lens": lens, "data_unavailable": False,
        "output_domain": configured_domain(config),
        "scope_label": scope_label(config, lens),
        "display_timezone": config.get("timezone"),
        "mode": mode, "currency_sections": dict(sections),
        "target_comparisons": comparisons, "slipped_subtotal": dict(slipped_subtotal),
        "slipped_handling": slipped_handling,
        "concentration_risks": concentration_risks,
        "adjustment_status": adjustment_status,
        "silence_days": int(config.get("silence_days", 7)) if config.get("silence_days", 7) is not None else None,
        "adjustment_settings": dict(haircuts),
        "evidence_status": {"activity": {"available": not bool(unavailable_activity), "affected_deals": len(unavailable_activity)}},
        "reconciliation": {"passed": reconciled},
        "stage_order_notices": _stage_order_notices(order_unknown, stages, config, lens),
        "stages_source": data.get("stages_source"),
        "excluded_open_deals": _excluded_summary(excluded_open, open_in_forecast, period),
    }
    if include_breakdowns and lens.get("type") == "team":
        owner_ids = sorted({str(deal.get("owner_id")) for deal in open_deals + won_deals if deal.get("owner_id") is not None})
        pipeline_ids = sorted({str(deal.get("pipeline_id")) for deal in open_deals + won_deals if deal.get("pipeline_id") is not None})
        result["breakdowns"] = {
            "owners": {
                owner_id: compute_forecast(data, config, period, today, {"type": "owner", "id": owner_id}, False)
                for owner_id in owner_ids
            },
            "pipelines": {
                pipeline_id: compute_forecast(data, config, period, today, {"type": "pipeline", "id": pipeline_id}, False)
                for pipeline_id in pipeline_ids
            },
        }
    result["activity_date_explanation"] = DATE_EXPLANATION
    result["markdown"] = render_markdown(result) + "\n\n" + DATE_EXPLANATION
    return result


def run_forecast(run, config, period, today, lens=None, run_directory=None):
    """Adapt a persisted MCP run file to the Forecast calculation without extra reads."""
    if config.get('schema_version', 1) != 1:
        raise ValueError('Use workflow.py with the confirmed profile instead of legacy compute')
    calls = (run or {}).get("calls") or []
    resolved_calls = []
    for call in calls:
        if call.get("response") is None and call.get("response_file"):
            path = Path(call["response_file"])
            if not path.is_absolute() and run_directory is not None:
                path = Path(run_directory) / path
            call = {**call, "response": load_response_file(path)}
        resolved_calls.append(call)
    calls = resolved_calls
    tables = collect({**(run or {}), "calls": calls})
    core_unavailable = []
    stage_calls = [call for call in calls if (call.get("tool") or "").split("__")[-1] in {"getStages", "getStage"}]
    open_calls = [call for call in calls if (call.get("tool") or "").split("__")[-1] == "getDeals" and deal_status((call.get("params") or {}).get("status", "open")) == "open"]
    if not (stage_calls or (run or {}).get("context_stages")) or any(isinstance(call.get("response"), dict) and (call["response"].get("error") or call["response"].get("success") is False) for call in stage_calls):
        core_unavailable.append("stages")
    if not open_calls or any(isinstance(call.get("response"), dict) and (call["response"].get("error") or call["response"].get("success") is False) for call in open_calls):
        core_unavailable.append("open_deals")
    last_open_response = (open_calls[-1].get("response") or {}) if open_calls else {}
    additional = last_open_response.get("additional_data") if isinstance(last_open_response, dict) else None
    open_cursor_unfollowed = bool(tables["open_cursor_unfollowed"] or
                                  (isinstance(additional, dict) and additional.get("next_cursor")))
    if open_cursor_unfollowed:
        core_unavailable.append("open_deals pagination incomplete")
    activity_calls = [call for call in calls if (call.get("tool") or "").split("__")[-1] == "getActivities"]
    activity_failed = any(call.get("error") or call.get("isError") is True or
                          not isinstance(call.get("response"), (dict, list)) or
                          (isinstance(call["response"], dict) and
                           (call["response"].get("error") or call["response"].get("success") is False))
                          for call in activity_calls)
    activity_readable = bool(activity_calls) and not activity_failed
    # An activity page whose cursor was never followed (0.7.16 phase 2 check (ii): one page of 100 of a 90-day window, the
    # chat said so after the reply, the report said nothing). The loaded rows are the newest (sorted by update time,
    # descending), so a deal with a loaded activity keeps its recency; a deal without one is unknown, never silent.
    followed = {(call.get("params") or {}).get("cursor") for call in activity_calls if (call.get("params") or {}).get("cursor")}
    activity_cursor_unfollowed = any(
        isinstance(call.get("response"), dict) and isinstance(call["response"].get("additional_data"), dict)
        and call["response"]["additional_data"].get("next_cursor") and call["response"]["additional_data"]["next_cursor"] not in followed
        for call in activity_calls)
    open_deals = list(tables["open_deals"].values())
    unknown_recency = []
    if activity_readable:
        silence_days = (config or {}).get("silence_days", 7)
        for deal in open_deals:
            own = [activity for activity in tables["activities"].values() if activity.get("deal_id") == deal.get("id")]
            dates = completed_activity_evidence(own, today)
            deal.update(dates)
            threshold = deal.get('_silence_days_override', silence_days) if config.get('_workflow_v2') else silence_days
            deal["silent"] = (threshold is not None and dates["days_since_activity"] is not None and dates["days_since_activity"] >= threshold
                              if config.get('_workflow_v2') else threshold is not None and (dates['days_since_activity'] is None or dates['days_since_activity'] >= threshold))
            if activity_cursor_unfollowed and not own:
                deal["silent"] = False
                unknown_recency.append(deal.get("id"))
    data = {
        "stages": tables["stages"],
        "stages_source": tables["stages_source"],
        "open_deals": open_deals,
        "won_deals": list(tables["won_deals"].values()),
        "lost_deals": list(tables["lost_deals"].values()),
        "core_unavailable": core_unavailable,
        "activity_unavailable": {"deal_ids": list(tables["open_deals"]) if not activity_readable else unknown_recency},
    }
    result = compute_forecast(data, config, period, today, lens)
    result["activity_cursor_unfollowed"] = activity_cursor_unfollowed
    if activity_cursor_unfollowed and activity_readable and not result.get("data_unavailable"):
        line = (f"Partial activity coverage: a completed-activity page still has an unread cursor; recency is unknown for "
                f"{len(unknown_recency)} open {'deal' if len(unknown_recency) == 1 else 'deals'} without a loaded activity, and no inactivity adjustment was applied to them.")
        result["activity_partial_line"] = line
        result["markdown"] = result["markdown"].replace("## Assumptions", line + "\n## Assumptions", 1) if "## Assumptions" in result["markdown"] else result["markdown"] + "\n" + line
    result["tool_calls"] = tables["tool_calls"]
    result["token_estimate"] = tables["tokens"]
    result["write_calls"] = tables["write_calls"]
    result["open_cursor_unfollowed"] = open_cursor_unfollowed
    if open_cursor_unfollowed:
        result["partial_line"] = "Partial coverage: an open-deals page still has an unread cursor. Complete the remaining pages before calculating the forecast or advancing its checkpoint."
        result["markdown"] = result["partial_line"] + "\n\n" + result["markdown"]
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--period", required=True)
    parser.add_argument("--today")
    parser.add_argument("--lens")
    parser.add_argument("--markdown", action="store_true")
    parser.add_argument("--scheduled", action="store_true", help="text-only scheduled run: print no ladder offer")
    parser.add_argument("--stages", help="saved getAccountContext response; its stages stand in when the run has no stage read")
    parser.add_argument("--output", help="write the complete result (what a bare run prints) to this path; stdout keeps the Markdown")
    args = parser.parse_args(argv)
    try:
        period_bounds(args.period)
    except ValueError as error:
        parser.error(str(error))
    with open(args.config, encoding="utf-8") as handle:
        config = json.load(handle)
    with open(args.input, encoding="utf-8") as handle:
        run = json.load(handle)
    if args.stages:
        source = load_stage_context(args.stages)
        run, config = with_context_stages(run, source), metadata(config, source)
    lens = json.loads(args.lens) if args.lens else None
    result = run_forecast(run, config, args.period, args.today or run.get("today"), lens, Path(args.input).parent)
    stamp_result(result, result["tool_calls"], result["token_estimate"], SKILL_DIR, config)
    if args.output:
        # The saved result is written by the script (0.7.16 phase 1, ADR-044 note), as deal-triage's since 0.7.15: the eval
        # sandbox denies a shell redirect, and the chat cannot read the JSON a bare run prints.
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(result, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
        os.replace(temporary, path)
        print(f"saved {path}", file=sys.stderr)
    if args.markdown:
        offer = "" if args.scheduled else ladder_offer(result)
        text = (result.get("markdown") or json.dumps(result, sort_keys=True)) + ("\n\n" + offer if offer else "")
        refs = record_refs({"currency_sections": result.get("currency_sections") or {}, "output_domain": result.get("output_domain")})
        checked = validate_reply(text, {"markdown": text, "record_refs": refs})
        if not checked["passed"]:
            print(json.dumps(checked, ensure_ascii=False, indent=2), file=sys.stderr)
            raise SystemExit(1)
        if args.scheduled:
            print(text)
        else:
            save_expected_reply(text, "reply", args.input)
            if args.output:
                # The questions this reply leaves go through the card in the same turn; the Stop hook sends the turn back
                # once when it ends without one (0.7.19 phase 2, ADR-024 note; check (ii) saved its reply on this path).
                import shlex
                from final_response import follow_up_questions
                from pdcore import note_card_due
                command = [str(Path(sys.argv[0]).with_name("workflow.py")), "questions", "--skill", "forecast", "--input", args.output]
                note_card_due(follow_up_questions(result, {}, {}), " ".join(shlex.quote(part) for part in command), args.input)
            print(framed(text))
    else:
        print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
