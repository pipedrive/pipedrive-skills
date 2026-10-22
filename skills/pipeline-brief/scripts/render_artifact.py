"""Render a standalone HTML dashboard from an already computed pipeline-brief result."""

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import date
from html import escape

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from rep_language import next_action, rep_signal  # noqa: E402
from artifact_delivery import delivery_options  # noqa: E402


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dashboard_charts import CSS as CHART_CSS, SCRIPT as CHART_SCRIPT, brief_charts, risk_charts, forecast_charts, ordered_stage_keys, stage_charts
from pdcore import closing_heading, money, person_name, plural  # noqa: E402

LINK_CSS = '.attention-card a,.detail-list a{color:var(--pipedrive-deep);text-decoration:underline;text-underline-offset:2px}.attention-card a:focus-visible,.detail-list a:focus-visible{outline:3px solid var(--amber);outline-offset:3px}'

def render(result, capabilities):
    """Backward-compatible alias for safe dashboard delivery selection."""
    return delivery_options(result, capabilities)


def _display_date(value):
    try:
        return date.fromisoformat(str(value)).strftime("%-d %b")
    except ValueError:
        return str(value or "Close date not set")


def _value_split(totals):
    return " · ".join(money(value, currency) for currency, value in totals.items()) or "No value recorded"


def _next_activity_summary(deal):
    if deal.get("has_next") is True or deal.get("next_activity_id"):
        return "linked in Pipedrive; details not checked"
    count = deal.get("undone_activities_count")
    if (deal.get("next_activity_known") is True and deal.get("next_activity_id") is None
            and type(count) is int and count == 0):
        return "none linked"
    return "not verified"


def _deal_link(result, deal):
    from pipedrive_links import html_deal
    return html_deal(result, deal)


def build_presentation(result):
    """Project one computed brief into facts shared by the local visual renderer."""
    aggregates = result.get("aggregates") or {}
    actions = []
    for deal in (result.get("ranked") or [])[:5]:
        actions.append({
            "id": deal.get("id"), "title": deal.get("title") or "Untitled deal",
            "value": deal.get("value") or 0, "currency": deal.get("currency") or "",
            "stage": deal.get("stage") or "Unknown stage", "at_risk": bool(deal.get("at_risk")),
            "signals": [rep_signal(flag) for flag in deal.get("flags", [])],
            "next_step": next_action(deal),
            "last_touch": deal.get("last_touch"), "expected_close_date": deal.get("expected_close_date"),
            "next_activity": _next_activity_summary(deal),
        })
    return {
        "today": result.get("today") or "today", "window_start": result.get("window_start") or "Not set",
        "open_count": aggregates.get("open_count", 0),
        "open_value_by_currency": aggregates.get("open_value_by_currency") or {}, "actions": actions,
        "scope": result.get("scope") or {}, "movement": aggregates.get("movement") or {},
        "closing_this_week": aggregates.get("closing_this_week") or {}, "by_stage": aggregates.get("by_stage") or {},
        "coverage": result.get("coverage_line") or "Coverage unavailable",
        "partial": result.get("partial_line"),
    }


def default_output_path(result, directory="."):
    """Return a discoverable artifact location outside internal state directories."""
    date = (result.get("today") or "latest").replace("/", "-")
    return os.path.join(directory, f"pipeline-brief-{date}.html")


def native_artifact_payload(result):
    """Return canonical standalone HTML for a validated host-native artifact surface."""
    return {
        "mime_type": "text/html",
        "filename": os.path.basename(default_output_path(result)),
        "html": render_html(result),
    }


def _render_html(result):
    """Return a private, self-contained Pipedrive-styled dashboard."""
    if result.get("data_unavailable"):
        message = escape(result.get("markdown") or "Required pipeline data is unavailable.")
        return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width, initial-scale=1">'
                '<title>Pipeline brief unavailable</title></head><body><main>'
                '<h1>Pipeline brief unavailable</h1><pre style="white-space:pre-wrap">'
                + message + '</pre></main></body></html>')
    presentation = build_presentation(result)
    scope = presentation["scope"]
    pipelines = result.get("pipelines") or {}
    included_ids = scope.get("pipeline_ids") or list(pipelines)
    excluded_ids = set(str(item) for item in scope.get("exclude_pipeline_ids") or [])
    included_ids = [item for item in included_ids if str(item) not in excluded_ids]
    labels = {str(pid): str((details or {}).get("label") or "Pipeline name unavailable")
              for pid, details in pipelines.items()}
    def pipeline_name(pid):
        name = labels.get(str(pid), 'Pipeline name unavailable')
        stages = (pipelines.get(str(pid)) or {}).get('stages') or []
        if list(labels.values()).count(name) > 1:
            return f"{name} ({stages[0]} → {stages[-1]})" if stages else name + ' · shared pipeline name'
        return name
    scope_line = ('Selected saved filter' if scope.get('filter_id') is not None else
                  ', '.join(pipeline_name(pid) for pid in included_ids) or 'No pipelines selected')
    excluded_line = ('governed by selected filter' if scope.get('filter_id') is not None else
                     ', '.join(pipeline_name(pid) for pid in pipelines if str(pid) not in set(map(str, included_ids))) or 'none')
    owners = [(result.get('owners') or {}).get(str(identifier)) or {} for identifier in scope.get('owner_ids') or []]
    owner_names = ', '.join(person_name((owner if isinstance(owner, str) else owner.get('name'))) or 'Owner name unavailable' for owner in owners) or 'Team deals'
    totals = " · ".join(money(value, currency) for currency, value in presentation["open_value_by_currency"].items()) or "No value recorded"
    risk_count = sum(1 for deal in presentation["actions"] if deal["at_risk"])
    movement = presentation["movement"]
    closing = presentation["closing_this_week"]
    closing_title = closing_heading(closing)
    closing_title_small = closing_title.upper()
    action_rows = "".join(
        "<article class=\"attention-card\"><span class=\"rank\">{rank}</span><div class=\"deal\">"
        "<h3>{title}</h3><p>Deal stage: {stage}</p><div class=\"tags\">{tags}</div><p class=\"next-step\">{next_step}</p>"
        "<details><summary>Why this needs attention</summary><p>{reason}</p><p>Next activity: {next_activity}</p>"
        "<details><summary>Calculation details</summary><p>{audit} · Last touch: {last_touch} · Expected close: {close}</p></details></details>"
        "</div><strong class=\"value\">{value}</strong></article>".format(
            rank=index, title=_deal_link(result, deal), stage=escape(str(deal["stage"])),
            tags="".join(f"<span class=\"tag {'risk' if deal['at_risk'] else ''}\">{escape(signal['headline'])}</span>" for signal in deal["signals"]) or "<span class=\"tag\">Review</span>",
            next_step=escape("Next step: " + deal['next_step']),
            reason=escape(" ".join(signal["detail"] for signal in deal["signals"]) or "Review the deal's current evidence."),
            audit=escape(" · ".join(f"{signal['headline']}: {signal['detail']}"
                                     + (f" Source: {signal['source']}." if signal.get("source") else "")
                                     for signal in deal["signals"]) or "No rule signal"),
            id=escape(str(deal["id"])), last_touch=escape(str(deal["last_touch"] or "unknown")),
            close=escape(str(deal["expected_close_date"] or "not set")),
            next_activity=escape(deal["next_activity"]),
            value=escape(money(deal["value"], deal["currency"])),
        ) for index, deal in enumerate(presentation["actions"], 1)
    ) or "<p class=\"empty\">No named recommendations are available.</p>"
    max_stage_count = max((item.get("count", 0) for item in presentation["by_stage"].values()), default=1)
    stage_rows = []
    deals_by_stage = {}
    for deal in result.get("deals") or []:
        deals_by_stage.setdefault(deal.get("stage") or "?", []).append(deal)
    stage_pipeline = {}
    for deal in result.get("deals") or []:
        stage_pipeline.setdefault(deal.get("stage") or "?", str(deal.get("pipeline_id")))
    stage_keys = ordered_stage_keys([(stage_pipeline.get(stage, "None"), stage) for stage in presentation["by_stage"]], result.get("pipelines") or {})
    for _, stage in stage_keys:
        item = presentation["by_stage"][stage]
        count = item.get("count", 0)
        stage_value = " · ".join(
            money(value, currency) for currency, value in (item.get("value") or {}).items()
        ) or "No value recorded"
        members = deals_by_stage.get(stage) or []
        deal_list = "".join(
            "<li><strong>{title}</strong> · {value} · Close date: {close}</li>".format(
                title=_deal_link(result, deal),
                id=escape(str(deal.get("id") or "?")),
                value=escape(money(deal.get("value"), deal.get("currency") or "")),
                close=escape(_display_date(deal.get("expected_close_date"))),
            ) for deal in sorted(members, key=lambda deal: str(deal.get("title") or ""))
        )
        drilldown = (f'<details class="stage-deals"><summary>Show {plural(len(members), "deal")} in {escape(str(stage))}</summary>'
                     f'<ul class="detail-list">{deal_list}</ul></details>') if members else ""
        stage_rows.append(
            "<div class=\"stage-row\"><span>{stage}</span><div class=\"bar\" role=\"img\" aria-label=\"{label}\" tabindex=\"0\" data-tooltip=\"{label}\">"
            "<i style=\"width:{width}%\"></i></div><b>{count} {noun} with total value {value}</b></div>{drilldown}".format(
                stage=escape(str(stage)), label=escape(f"{stage}: {count} open deals, {stage_value}"),
                width=round(count * 100 / max_stage_count), count=escape(str(count)),
                noun="deal" if count == 1 else "deals", value=escape(stage_value), drilldown=drilldown,
            )
        )
    stage_rows = "".join(stage_rows) or "<p class=\"empty\">No stage distribution is available.</p>"
    partial = presentation["partial"]
    notice = (f"<section class=\"notice\">{escape(partial)}</section>" if partial else "")
    unresolved = (result.get("activity_coverage") or {}).get("deal_update_fallback", 0)
    if unresolved:
        notice += f'<aside class="notice">Completed activity dates were not resolved for {unresolved} checked deals; recency falls back to deal updates. This does not prove buyer contact or that these deals have no completed activities.</aside>'
    context = "Partial scan: figures below cover checked records only" if partial else "Completed pipeline scan"
    first_action = presentation["actions"][0] if presentation["actions"] else None
    if first_action:
        next_step = first_action['next_step']
        lead = f"Start with {_deal_link(result, first_action)}: {escape(next_step)}"
    else:
        lead = "No named recommendations are available."
    checked = f"I checked {presentation['open_count']} open deals"
    headline = f"{escape(checked)}; more remain. {lead}" if partial else f"{escape(checked)}. {lead}"
    if partial:
        closing_count = won_count = "—"
        closing_value = won_value = "Not fully checked"
        lower_metrics = ""
    else:
        closing_count = closing.get("count", 0)
        closing_items = closing.get("items") or []
        closing_value = _value_split(closing.get("value_by_currency") or {})
        won_count = (movement.get("won") or {}).get("count", 0)
        won_value = _value_split((movement.get("won") or {}).get("value_by_currency") or {})
        closing_names = "".join(
            "<li><strong>{title}</strong> · {date} · {value}</li>".format(
                title=_deal_link(result, item),
                date=escape(_display_date(item.get("expected_close_date"))),
                value=escape(money(item.get("value"), item.get("currency") or "")),
            ) for item in closing_items
        )
        movement_names = []
        for label in ("won", "lost", "created"):
            for item in (movement.get(label) or {}).get("items", [])[:2]:
                movement_names.append("<li><strong>{status}:</strong> {title}</li>".format(
                    status=label.capitalize(),
                    title=_deal_link(result, item),
                ))
        lower_metrics = ("<section class=\"lower-grid\" aria-label=\"Supporting pipeline metrics\">"
                         f"<article class=\"panel\"><h2>{closing_title}</h2><p>{plural(closing_count, 'deal')} · {escape(closing_value)}</p>"
                         f"<ul class=\"detail-list\">{closing_names}</ul></article>"
                         f"<article class=\"panel\"><h2>What moved</h2><p>{won_count} won · "
                         f"{(movement.get('lost') or {}).get('count', 0)} lost · {(movement.get('created') or {}).get('count', 0)} created</p>"
                         f"<ul class=\"detail-list\">{''.join(movement_names)}</ul></article></section>")
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pipeline brief — {today}</title><style>
:root{{--pipedrive-green:#017737;--pipedrive-deep:#014722;--pipedrive-mint:#D7F9D3;--ink:#182b22;--muted:#5d7066;--line:#d9e5dd;--amber:#a96800;--red:#bd3d38;--paper:#f7faf8}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font-family:ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;line-height:1.45}}main{{width:min(1040px,calc(100% - 32px));margin:48px auto 64px}}.masthead{{display:flex;justify-content:space-between;gap:24px;padding:32px 36px;background:var(--pipedrive-deep);color:white;border-radius:18px 18px 4px 4px}}.eyebrow{{margin:0 0 8px;color:#7ee0a8;font-size:.78rem;font-weight:700;letter-spacing:.08em}}h1{{margin:0;font-size:clamp(2.35rem,6vw,4.6rem);letter-spacing:-.065em;line-height:.9}}h2{{margin:42px 0 15px;font-size:1.35rem;letter-spacing:-.035em}}h3{{margin:0;font-size:1rem;letter-spacing:-.02em}}.meta{{margin:4px 0;text-align:right;color:#c7d6cd;font-size:.9rem}}.context{{display:flex;flex-wrap:wrap;gap:8px;padding:14px 20px;background:white;border:1px solid var(--line);border-top:0;border-radius:0 0 12px 12px;color:var(--muted);font-size:.86rem}}.context b{{color:var(--ink)}}.notice{{margin-top:22px;padding:14px 18px;background:#fff7df;border-left:4px solid var(--amber);border-radius:4px;color:#614200}}.metric-grid{{display:grid;grid-template-columns:repeat(4,1fr);margin-top:22px;background:white;border:1px solid var(--line);border-radius:12px;overflow:hidden}}.metric{{padding:20px;border-right:1px solid var(--line)}}.metric:last-child{{border:0}}.metric small{{display:block;color:var(--muted);font-size:.72rem;font-weight:750;letter-spacing:.06em}}.metric strong{{display:block;margin-top:3px;font-size:2rem;letter-spacing:-.06em;color:var(--pipedrive-green)}}.metric.danger strong{{color:var(--red)}}.metric.warn strong{{color:var(--amber)}}.metric span{{font-size:.8rem;color:var(--muted)}}.section-label{{display:flex;justify-content:space-between;align-items:baseline;gap:16px}}.section-label p{{color:var(--muted);font-size:.82rem}}.attention-list{{display:grid;gap:9px}}.attention-card{{display:grid;grid-template-columns:45px 1fr auto;gap:12px;align-items:start;padding:17px 20px;background:white;border:1px solid var(--line);border-left:4px solid var(--red);border-radius:8px}}.rank{{font-size:1.55rem;font-weight:750;color:#a9b8b0;line-height:1}}.deal>p{{margin:3px 0 10px;color:var(--muted);font-size:.86rem}}.value{{font-size:1rem;white-space:nowrap}}.tags{{display:flex;flex-wrap:wrap;gap:6px}}.tag{{padding:3px 6px;border-radius:4px;background:var(--pipedrive-mint);color:var(--pipedrive-green);font-size:.7rem;font-weight:700}}.tag.risk{{background:#fbe8e6;color:#a2332e}}details{{margin-top:10px;color:var(--muted);font-size:.78rem}}summary{{cursor:pointer;color:var(--pipedrive-green);font-weight:650}}details p{{margin:7px 0 0}}.lower-grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}.panel{{min-height:160px;padding:20px;background:white;border:1px solid var(--line);border-radius:12px}}.panel h2{{margin:0 0 14px;font-size:1.1rem}}.panel p{{margin:0;color:var(--muted)}}.movement-number{{font-size:2rem;color:var(--pipedrive-green);font-weight:750;letter-spacing:-.06em}}.stage-panel{{padding:20px;background:white;border:1px solid var(--line);border-radius:12px}}.stage-row{{display:grid;grid-template-columns:140px 1fr 250px;gap:12px;align-items:center;margin:10px 0;font-size:.83rem}}.stage-row b{{font-size:.78rem;text-align:right}}.bar{{height:8px;background:#edf2ee;border-radius:99px;overflow:hidden}}.bar i{{display:block;height:100%;background:var(--pipedrive-green);border-radius:inherit}}footer{{margin-top:28px;padding-top:16px;border-top:1px solid var(--line);color:var(--muted);font-size:.78rem}}footer p{{margin:5px 0}}.empty{{color:var(--muted)}}@media(max-width:720px){{main{{width:min(100% - 20px,1040px);margin-top:12px}}.masthead{{padding:28px 22px;display:block}}.meta{{text-align:left}}.metric-grid{{grid-template-columns:1fr 1fr}}.metric:nth-child(2){{border-right:0}}.metric:nth-child(-n+2){{border-bottom:1px solid var(--line)}}.attention-card{{grid-template-columns:30px 1fr}}.value{{grid-column:2}}.lower-grid{{grid-template-columns:1fr}}.stage-row{{grid-template-columns:105px 1fr}}.stage-row b{{grid-column:2;text-align:left}}}}@media print{{body{{background:white}}main{{width:100%;margin:0}}.masthead{{border-radius:0}}}}
summary:focus-visible{{outline:3px solid var(--amber);outline-offset:3px;border-radius:2px}}@media(max-width:480px){{.stage-row{{grid-template-columns:1fr}}.stage-row b{{grid-column:1}}}}@media(max-width:320px){{.metric-grid{{grid-template-columns:1fr}}.metric{{border-right:0;border-bottom:1px solid var(--line)}}.metric:last-child{{border-bottom:0}}}}@media print{{.notice,details,footer{{break-inside:avoid}}details>*{{display:block}}}}
.lead-answer{{margin:24px 0 0;font-size:clamp(1.15rem,2.5vw,1.55rem);font-weight:650;line-height:1.35;color:var(--pipedrive-deep)}}.detail-list{{margin:12px 0 0;padding-left:20px;color:var(--ink)}}.detail-list:empty{{display:none}}.detail-list li{{margin:6px 0}}.stage-deals{{margin:4px 0 14px;padding:0 0 10px 152px;border-bottom:1px solid var(--line)}}.stage-deals summary{{font-size:.85rem}}.audit{{margin-top:26px;padding:14px 18px;border:1px solid var(--line);border-radius:10px;background:white}}.audit summary{{font-size:.92rem}}@media(max-width:720px){{.stage-deals{{padding-left:0}}}}
:root{{--pd-color-green:#017737;--pd-color-green-dark:#014722;--pd-color-green-light:#D7F9D3;--pd-color-yellow:#FFDE00;--pd-color-purple:#6861F2;--pd-color-purple-dark:#3B3A97;--pd-color-purple-light:#CFD0F9;--pipedrive-green:var(--pd-color-green);--pipedrive-deep:var(--pd-color-green-dark);--pipedrive-mint:var(--pd-color-green-light);--pd-font-family:ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;--pd-font-title-xxl-font:700 25px/1.2 var(--pd-font-family);--pd-font-title-xl-font:700 21px/1.3 var(--pd-font-family);--pd-font-title-l-font:700 16px/1.35 var(--pd-font-family);--pd-font-title-m-font:700 14px/1.4 var(--pd-font-family);--pd-font-body-l-font:400 16px/1.5 var(--pd-font-family);--pd-font-body-l-strong-font:700 16px/1.5 var(--pd-font-family);--pd-font-body-font:400 14px/1.5 var(--pd-font-family);--pd-font-body-strong-font:700 14px/1.5 var(--pd-font-family);--pd-font-body-s-font:400 12px/1.45 var(--pd-font-family);--pd-font-body-s-strong-font:700 12px/1.45 var(--pd-font-family);--pd-font-link-font:600 14px/1.5 var(--pd-font-family);--pd-font-link-s-font:600 12px/1.45 var(--pd-font-family);--pd-font-link-l-font:600 16px/1.5 var(--pd-font-family);--pd-font-link-alt-font:400 14px/1.5 var(--pd-font-family);--pd-font-link-alt-s-font:400 12px/1.45 var(--pd-font-family);--pd-font-button-font:700 14px/1.4 var(--pd-font-family);--pd-font-button-s-font:700 12px/1.4 var(--pd-font-family);--pd-font-badge-font:700 10px/1.3 var(--pd-font-family);--pd-font-caption-font:400 13px/1.4 var(--pd-font-family);--pd-font-caption-s-font:400 11px/1.4 var(--pd-font-family);--pd-font-monospace-font:400 14px/1.5 ui-monospace,SFMono-Regular,monospace}}
body{{font:var(--pd-font-body-font)}}h1{{font:var(--pd-font-title-xxl-font);letter-spacing:normal}}h2{{font:var(--pd-font-title-xl-font);letter-spacing:normal}}h3,.panel h2{{font:var(--pd-font-title-l-font);letter-spacing:normal}}.lead-answer{{font:var(--pd-font-body-l-strong-font)}}.metric strong,.movement-number{{font:var(--pd-font-title-xxl-font)}}.eyebrow,.metric small{{font:var(--pd-font-caption-s-font);font-weight:700}}.meta,.context,.section-label p,footer{{font:var(--pd-font-caption-font)}}.tag{{font:var(--pd-font-badge-font)}}details,.stage-row,.deal>p{{font:var(--pd-font-body-s-font)}}summary{{font:var(--pd-font-link-s-font)}}
</style></head><body><main aria-label="Pipeline brief dashboard">
<header class="masthead"><div><p class="eyebrow">PIPEDRIVE · SALES PIPELINE</p><h1>Pipeline brief</h1></div><div><p class="meta">{today}</p><p class="meta">Window since {window_start}</p></div></header>
<section class="context" aria-label="Brief context"><span><b>Scope</b> {scope}</span><span><b>Whose deals</b> {owners}</span><span><b>Excluded pipelines</b> {excluded}</span><span><b>Snapshot</b> {context}</span></section>{notice}
<p class="lead-answer">{headline}</p>
<section class="metric-grid" aria-label="Pipeline summary"><div class="metric"><small>CHECKED OPEN DEALS</small><strong>{open_count}</strong><span>{totals}</span></div><div class="metric danger"><small>AT RISK</small><strong>{risk_count}</strong><span>among top actions</span></div><div class="metric warn"><small>{closing_title_small}</small><strong>{closing_count}</strong><span>{closing_value}</span></div><div class="metric"><small>WON IN WINDOW</small><strong>{won_count}</strong><span>{won_value}</span></div></section>
<div class="section-label"><h2 id="needs-attention">Needs attention</h2><p>Top actions from this scan</p></div><p>Ordered by your saved priority settings, not by days since the last recorded activity alone.</p>
<section class="attention-list" aria-labelledby="needs-attention">{actions}</section>
{lower_metrics}
{charts}
<h2 id="open-by-stage">Open by stage</h2><section class="stage-panel" aria-labelledby="open-by-stage">{stage_charts}{stages}</section>
<footer><p>Dashboard rendered from the saved Pipedrive scan. No Pipedrive records were changed.</p></footer>
</main></body></html>""".format(
        today=escape(str(presentation["today"])), window_start=escape(str(presentation["window_start"])),
        scope=escape(scope_line), owners=escape(owner_names), excluded=escape(excluded_line), context=escape(context), notice=notice,
        headline=headline,
        open_count=escape(str(presentation["open_count"])), totals=escape(totals),
        risk_count=risk_count, closing_count=escape(str(closing_count)), closing_value=escape(closing_value), closing_title_small=escape(closing_title_small),
        won_count=escape(str(won_count)), won_value=escape(won_value), lower_metrics=lower_metrics,
        charts=brief_charts(result), actions=action_rows, stages=stage_rows, coverage=escape(presentation["coverage"]),
        stage_charts=stage_charts(result),
    )


def render_html(result, *args, **kwargs):
    if result.get('report_schema_version') == 2:
        from report_contract import render_html as render_profile
        return render_profile(result)
    html = _render_html(result, *args, **kwargs)
    if result.get("data_unavailable"):
        return html
    return html.replace("</style>", CHART_CSS + LINK_CSS + "</style>", 1).replace("</main>", "</main>" + CHART_SCRIPT, 1)


def write_html(result, path):
    """Write one artifact from an already computed result and return its path."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as output:
        output.write(render_html(result))
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Full JSON result from compute.py")
    parser.add_argument("--output", help="HTML file to create (defaults to a visible file in the current directory)")
    parser.add_argument("--sha256", action="store_true", help="print the canonical HTML SHA-256 without creating a file")
    args = parser.parse_args(argv)
    with open(args.input, encoding="utf-8") as source:
        result = json.load(source)
    if args.sha256:
        print(hashlib.sha256(render_html(result).encode("utf-8")).hexdigest())
        return 0
    write_html(result, args.output or default_output_path(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
