"""Render a completed Forecast result as self-contained, read-only canonical HTML."""

import argparse
import hashlib
from html import escape
import json
import os
import sys
from collections import defaultdict


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dashboard_charts import CSS as CHART_CSS, SCRIPT as CHART_SCRIPT, brief_charts, risk_charts, forecast_charts
from pipedrive_links import html_deal
from display_time import display_timestamp
from forecast_components import _money, _chart_row, _currency_charts



def default_output_path(result, directory="."):
    return os.path.join(directory, f"forecast-{result.get('today', 'latest')}.html")


def _at_risk_deals(result):
    deals = {}
    for section in (result.get("currency_sections") or {}).values():
        for item in section.get("deals") or []:
            if item.get("id") is not None and item.get("haircut_rules"):
                deals[str(item["id"])] = item
    for item in result.get("concentration_risks") or []:
        if item.get("id") is not None:
            deals[str(item["id"])] = item
    return deals






def _activity_charts(currency, values, unweighted=False, result=None):
    return forecast_charts(currency, values, unweighted, result=result)


def _stage_detail(currency, values, result=None):
    stages = values.get("stage_breakdown") or []
    if not stages:
        return '<section class="detail-section"><h3>Open deals by stage</h3><p>Stage detail is unavailable in this saved result.</p></section>'
    deals = {str(deal.get("id")): deal for deal in values.get("deals") or []}
    scale = max((item.get("value") or 0 for item in stages), default=0)
    repeated_names = {name for name in (str(item.get("stage") or "Unknown stage") for item in stages)
                      if sum(str(item.get("stage") or "Unknown stage") == name for item in stages) > 1}
    rows = []
    for item in stages:
        stage = str(item.get("stage") or "Unknown stage")
        if stage in repeated_names:
            stage += f" · pipeline {item.get('pipeline_id') or 'unknown'}"
        count = item.get("count") or 0
        rows.append(_chart_row(f"{stage} · {count} {'deal' if count == 1 else 'deals'}",
                               item.get("value"), currency, scale))
        members = [deals[str(identifier)] for identifier in item.get("deal_ids") or []
                   if str(identifier) in deals]
        if members:
            rows.append('<details class="deal-drilldown"><summary>Show {count} {noun} in {stage}</summary><ul>{items}</ul></details>'.format(
                count=count, noun="deal" if count == 1 else "deals", stage=escape(stage), items="".join(
                    '<li>{title} · {value} · Close date: {close}</li>'.format(
                        title=html_deal(result or {}, deal),
                        id=escape(str(deal.get("id"))),
                        value=escape(_money(deal.get("value"), currency)),
                        close=escape(str(deal.get("expected_close_date") or "not set")),
                    ) for deal in members)))
    return '<section class="detail-section"><h3>Open deals by stage</h3><p>Amounts are open deal values, not expected revenue.</p>{rows}</section>'.format(rows="".join(rows))


def _date_review(currency, values, result=None):
    review = values.get("close_date_review") or {}
    count = review.get("count") or 0
    if not count:
        return '<section class="detail-section"><h3>Close dates to review</h3><p>No deals are flagged for this review in the saved result.</p></section>'
    deals = {str(deal.get("id")): deal for deal in values.get("deals") or []}
    selected = [deals[str(identifier)] for identifier in (review.get("deal_ids") or [])[:12]
                if str(identifier) in deals]
    items = "".join(
        '<li><strong>{title}</strong> · {stage} · {value} · closes {close} · no next activity recorded</li>'.format(
            title=html_deal(result or {}, deal), id=escape(str(deal.get("id"))),
            stage=escape(str(deal.get("stage") or "Unknown stage")),
            value=escape(_money(deal.get("value"), currency)),
            close=escape(str(deal.get("expected_close_date") or "date not set")),
        ) for deal in selected)
    return ('<section class="detail-section"><h3>Close dates to review</h3>'
            f'<p>{count} included open {"deal has" if count == 1 else "deals have"} no next activity recorded. Check timing with the buyer before changing a date; no CRM dates were changed.</p>'
            f'<ol class="review-list">{items}</ol>'
            + (f'<p>Showing 12 of {count}; the saved result contains the full list.</p>' if count > 12 else "")
            + '</section>')


def _closed_detail(currency, values, result=None):
    items = []
    for outcome, key in (("Won", "closed_won"), ("Lost", "closed_lost")):
        for deal in values.get(key) or []:
            items.append('<li><strong>{outcome}:</strong> {title} · {value} · {date}</li>'.format(
                outcome=outcome, title=html_deal(result or {}, deal),
                id=escape(str(deal.get("id"))), value=escape(_money(deal.get("value"), currency)),
                date=escape(str(deal.get("date") or "date not set")),
            ))
    body = '<ul class="review-list">' + "".join(items) + '</ul>' if items else '<p>No deals recorded as won or lost in this period yet.</p>'
    return '<section class="detail-section"><h3>Closed this period</h3>{body}</section>'.format(body=body)


def prepare_triage_payload(result, selected_ids):
    """Return selected named risks for chat handoff without exposing a tool operation."""
    selected = {str(item) for item in selected_ids}
    deals = [
        {"id": item.get("id"), "title": item.get("title") or f"Deal {item.get('id')}"}
        for key, item in _at_risk_deals(result).items() if key in selected
    ]
    return {"deals": deals, "next_step": "Ask the user whether to run Deal Triage for these selected deals."}


def _render_html(result):
    """Project only supplied calculation facts; this function has no MCP or compute dependency."""
    if result.get("data_unavailable"):
        return "<!doctype html><title>Forecast unavailable</title><main><h1>Forecast unavailable</h1><p>" + escape(result.get("reason", "Required evidence unavailable")) + "</p></main>"
    activity = (result.get("evidence_status") or {}).get("activity") or {}
    unweighted = result.get("mode") == "unweighted" and any(
        values.get("deals") or values.get("open_value")
        for values in (result.get("currency_sections") or {}).values())
    warning = ""
    if not activity.get("available", True):
        count = activity.get("affected_deals", 0)
        noun = "deal" if count == 1 else "deals"
        warning = f'<aside class="warning">Activity evidence incomplete for {count} {noun}; adjusted forecast may be optimistic.</aside>'
    adjustment_status = result.get("adjustment_status")
    if adjustment_status in {"not_configured", "disabled"}:
        state = "not configured" if adjustment_status == "not_configured" else "turned off"
        warning += (f'<aside class="warning">Risk adjustments are {state}. '
                    'Stale activity, missing follow-ups and passed close dates have not reduced this amount.</aside>')
    if unweighted:
        warning += '<aside class="warning">Usable probabilities are missing. This is open deal value, not a likelihood estimate; any accepted risk adjustments are shown separately.</aside>'
    sections = []
    for currency, values in sorted((result.get("currency_sections") or {}).items()):
        comparison = (result.get("target_comparisons") or {}).get(currency) or {}
        silent = sum("silent" in deal.get("haircut_rules", []) for deal in values.get("deals", []))
        total = len(values.get("deals", []))
        if total and silent == total:
            warning += f'<aside class="warning">The inactivity adjustment affects {silent} of {total} open deals in {escape(currency)}. Review whether the saved {result.get("silence_days", 7)}-day threshold fits your sales motion; settings have not been changed.</aside>'
        target = "Not configured" if comparison.get("target") is None else _money(comparison["target"], currency)
        difference = comparison.get("projected_difference")
        if comparison.get("target") is None:
            answer = "No target is configured for this currency."
        elif difference is None:
            answer = "The target comparison is unavailable in this saved result."
        elif unweighted and (values.get("won_to_date") or 0) >= comparison["target"]:
            answer = "Won revenue has already met the target."
        elif unweighted:
            answer = "Cannot estimate likelihood against the target without usable probabilities."
        elif difference > 0:
            answer = f"Likely revenue is {_money(difference, currency)} below target."
        elif difference < 0:
            answer = f"Likely revenue is {_money(abs(difference), currency)} above target."
        else:
            answer = "Likely revenue meets the target."
        charts = _currency_charts(currency, values, comparison, not activity.get("available", True), adjustment_status, unweighted, result)
        detail = (_activity_charts(currency, values, unweighted, result)  +
                  _stage_detail(currency, values, result) + _date_review(currency, values, result) + _closed_detail(currency, values, result))
        revenue_label = "Value including won revenue" if unweighted else "Likely revenue this period"
        calculation_label = "Value before risk adjustments" if unweighted else "Weighted forecast"
        sections.append("<section><h2>{currency}</h2><p class=\"outcome\">{answer}</p><dl class=\"forecast-metrics\"><div><dt>Won so far</dt><dd>{won}</dd></div><div><dt>{revenue_label}</dt><dd>{adjusted}</dd></div><div><dt>{calculation_label}</dt><dd>{weighted}</dd></div><div><dt>Best case</dt><dd>{best}</dd></div><div><dt>Target</dt><dd>{target}</dd></div></dl>{charts}{detail}</section>".format(
            currency=escape(currency), answer=escape(answer), revenue_label=revenue_label, calculation_label=calculation_label, won=escape(_money(values.get("won_to_date"), currency)), adjusted=escape(_money(values.get("adjusted_forecast"), currency)), weighted=escape(_money(values.get("weighted_forecast"), currency)), best=escape(_money(values.get("best_case"), currency)), target=escape(target), charts=charts, detail=detail))
    risk_items = []
    for item in result.get("concentration_risks") or []:
        title = item.get("title") or f"Deal {item.get('id')}"
        risk_items.append(f'<li class="risk-item">{html_deal(result, item, title)} · {item.get("share", 0):.0%} of adjusted forecast</li>')
    risks = "".join(risk_items) or "<li>No concentration risk identified.</li>"
    triage_items = "".join(
        f'<label><input type="checkbox" name="triage-deal" value="{escape(key, quote=True)}"> '
        f'{html_deal(result, item)} (ID {escape(key)})</label>'
        for key, item in _at_risk_deals(result).items()
    )
    if triage_items:
        triage = (
            '<section class="triage-section"><h2>Prepare triage</h2><p>Select at-risk deals, then copy their IDs into chat. '
            'The dashboard does not run Deal Triage.</p>'
            '<label for="triage-search">Find a deal by name or ID</label><input id="triage-search" type="search" aria-controls="triage-deals">'
            f'<div class="triage-list" id="triage-deals">{triage_items}</div>'
            '<button type="button" id="prepare-triage">Prepare triage</button>'
            '<p id="triage-status" role="status" aria-live="polite"></p>'
            '<textarea id="triage-handoff" aria-label="Triage handoff message" readonly rows="3"></textarea></section>'
        )
    else:
        empty_risks = ("Risk adjustments are not active; this dashboard has not checked which deals need triage."
                       if adjustment_status in {"not_configured", "disabled"} else
                       "No at-risk deals are available in this saved result.")
        triage = f'<section class="triage-section"><h2>Prepare triage</h2><p>{empty_risks}</p></section>'
    probability_source = {"stage": "Likelihood estimates use Pipedrive deal stages",
                          "deal": "Likelihood estimates use deal probabilities",
                          "ladder": "Likelihood estimates use your saved stage settings",
                          "unweighted": "Open deals are counted without likelihood weighting",
                          "mixed": "Likelihood estimates use mixed available sources"}.get(
                              result.get("mode"), "Likelihood source is unavailable")
    script = """<script>document.getElementById('triage-search')?.addEventListener('input',function(){
const query=this.value.toLowerCase();
for(const item of document.querySelectorAll('.triage-list>label')){item.hidden=!item.textContent.toLowerCase().includes(query);}
});document.getElementById('prepare-triage')?.addEventListener('click',async function(){
const ids=Array.from(document.querySelectorAll('input[name="triage-deal"]:checked'),item=>item.value);
const status=document.getElementById('triage-status');
if(!ids.length){status.textContent='Select at least one deal.';return;}
const message='Selected Forecast deal IDs for triage: '+ids.join(', ')+'. I will ask explicitly in chat before Deal Triage runs.';
const output=document.getElementById('triage-handoff');output.value=message;output.focus();output.select();
try{await navigator.clipboard.writeText(message);status.textContent='Copied. Paste into chat and explicitly request Deal Triage when ready.';}
catch(error){status.textContent='Paste into chat and explicitly request Deal Triage when ready.';}
});</script>"""
    return """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Forecast — {period} — {today}</title><style>
:root{{--pipedrive-green:#017737;--pipedrive-deep:#014722;--pipedrive-mint:#D7F9D3;--paper:#f7faf8;--red:#bd3d38;--amber:#a96800;--ink:#182b22;--muted:#5d7066;--line:#d9e5dd}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font-family:ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;line-height:1.45}}main{{max-width:960px;margin:2rem auto;padding:0 1rem}}
.masthead{{background:var(--pipedrive-deep);color:white;padding:1.5rem 2rem;border-radius:12px}}.masthead p{{margin:0;color:var(--pipedrive-mint)}}h1{{margin:.4rem 0;font-size:clamp(2rem,5vw,3rem)}}.masthead .mode{{color:white}}
.warning{{margin:1rem 0;padding:1rem;background:#fff7df;border-left:4px solid var(--amber);border-radius:4px}}section{{border:1px solid var(--line);border-radius:10px;background:white;padding:1rem;margin:1rem 0;container-type:inline-size}}section h2{{margin-top:0}}.outcome{{font-size:1.15rem;font-weight:700;color:var(--pipedrive-deep)}}dl{{display:grid;grid-template-columns:1fr auto;gap:.5rem}}dt{{color:var(--muted)}}dd{{margin:0;font-weight:700}}.risk-list{{list-style:none;padding:0}}.risk-list li{{margin:.5rem 0}}.risk-item{{padding:.75rem 1rem;background:white;border:1px solid var(--line);border-left:4px solid var(--red);border-radius:4px}}
.charts{{margin-top:1.5rem;border-top:1px solid var(--line);padding-top:.5rem}}.charts h3{{margin:1.4rem 0 .6rem}}.chart-row{{display:grid;grid-template-columns:minmax(10rem,1.4fr) minmax(5rem,2fr) auto;gap:.7rem;align-items:center;margin:.5rem 0}}.chart-row strong{{text-align:right;white-space:nowrap}}.bar-track{{height:1rem;background:var(--pipedrive-mint);border-radius:5px;overflow:hidden}}.bar-track i{{display:block;height:100%;background:var(--pipedrive-green)}}.chart-reason{{margin:.1rem 0 1rem;color:var(--muted);font-size:.85rem}}
.detail-section{{margin:1.5rem 0;padding:1rem;background:var(--paper)}}.detail-section h3{{margin:0 0 .4rem}}.detail-section>p{{color:var(--muted);font-size:.9rem}}.deal-drilldown{{margin:.2rem 0 .8rem 1rem;padding:0 0 .5rem 0;border-bottom:1px solid var(--line)}}summary{{cursor:pointer;color:var(--pipedrive-green);font-weight:700}}summary:focus-visible{{outline:3px solid var(--amber);outline-offset:2px}}.deal-drilldown ul,.review-list{{padding-left:1.3rem}}.deal-drilldown li,.review-list li{{margin:.5rem 0}}.review-list li{{padding:.5rem 0;border-bottom:1px solid var(--line)}}
.month-row{{display:grid;grid-template-columns:7rem 1fr 5rem;gap:1rem;align-items:center;padding:.75rem 0;border-bottom:1px solid var(--line)}}.month-row .chart-row{{grid-template-columns:minmax(10rem,1.4fr) minmax(5rem,2fr) auto;margin:.2rem 0}}.month-row .chart-row:last-child .bar-track i{{background:var(--pd-color-purple)}}.month-row>span{{text-align:right;color:var(--muted);font-size:.8rem}}@media(max-width:650px){{.month-row{{grid-template-columns:1fr}}.month-row .chart-row{{grid-template-columns:1fr;gap:.2rem;margin:.65rem 0}}.month-row>span{{text-align:left}}}}
.recency-scatter{{display:block;width:100%;max-width:660px;height:auto;margin:1rem auto}}.recency-scatter path{{fill:none;stroke:var(--muted)}}.recency-scatter text{{fill:var(--muted);font-size:12px}}.recency-scatter circle{{fill:var(--pd-color-purple)}}
.forecast-metrics{{grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:.75rem}}.forecast-metrics>div{{padding:.75rem;background:var(--paper);border-radius:6px}}.forecast-metrics dt{{font-size:.8rem}}.forecast-metrics dd{{margin:.5rem 0 0;font-size:1.1rem;white-space:nowrap}}@media(max-width:650px){{.forecast-metrics{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}.triage-section{{background:var(--pipedrive-mint)}}.triage-list{{display:grid;gap:.5rem;margin:1rem 0;max-height:24rem;overflow-y:auto;padding:1rem;background:white;border-radius:6px}}.triage-list label[hidden]{{display:none}}#triage-search{{display:block;width:100%;padding:.65rem;font:inherit;margin-top:.5rem}}.charts{{display:grid;grid-template-columns:1fr 1fr;gap:1rem}}.risk-panel{{grid-column:1/-1}}@media(max-width:650px){{.charts{{grid-template-columns:1fr}}}}@container(max-width:700px){{.charts{{grid-template-columns:1fr}}}}button{{padding:.65rem 1rem;background:var(--pipedrive-green);color:white;border:0;border-radius:5px;font:inherit;font-weight:700;cursor:pointer}}button:focus-visible,input:focus-visible,textarea:focus-visible{{outline:3px solid var(--amber);outline-offset:2px}}textarea{{display:block;width:100%;margin-top:.5rem;font:inherit;border:1px solid var(--line);border-radius:4px}}footer{{padding:1rem 0;color:var(--muted);font-size:.85rem}}@media(max-width:480px){{main{{margin:0 auto;padding:.5rem}}.masthead{{padding:1.25rem}}dl{{grid-template-columns:1fr}}dd{{margin:-.5rem 0 .5rem}}.chart-row{{grid-template-columns:1fr}}.chart-row strong{{text-align:left}}}}@media print{{body{{background:white}}main{{margin:0;max-width:none}}.masthead,.warning,.risk-item,.triage-section,footer{{break-inside:avoid}}}}
:root{{--pd-color-green:#017737;--pd-color-green-dark:#014722;--pd-color-green-light:#D7F9D3;--pd-color-yellow:#FFDE00;--pd-color-purple:#6861F2;--pd-color-purple-dark:#3B3A97;--pd-color-purple-light:#CFD0F9;--pipedrive-green:var(--pd-color-green);--pipedrive-deep:var(--pd-color-green-dark);--pipedrive-mint:var(--pd-color-green-light);--pd-font-family:ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;--pd-font-title-xxl-font:700 25px/1.2 var(--pd-font-family);--pd-font-title-xl-font:700 21px/1.3 var(--pd-font-family);--pd-font-title-l-font:700 16px/1.35 var(--pd-font-family);--pd-font-title-m-font:700 14px/1.4 var(--pd-font-family);--pd-font-body-l-font:400 16px/1.5 var(--pd-font-family);--pd-font-body-l-strong-font:700 16px/1.5 var(--pd-font-family);--pd-font-body-font:400 14px/1.5 var(--pd-font-family);--pd-font-body-strong-font:700 14px/1.5 var(--pd-font-family);--pd-font-body-s-font:400 12px/1.45 var(--pd-font-family);--pd-font-body-s-strong-font:700 12px/1.45 var(--pd-font-family);--pd-font-link-font:600 14px/1.5 var(--pd-font-family);--pd-font-link-s-font:600 12px/1.45 var(--pd-font-family);--pd-font-link-l-font:600 16px/1.5 var(--pd-font-family);--pd-font-link-alt-font:400 14px/1.5 var(--pd-font-family);--pd-font-link-alt-s-font:400 12px/1.45 var(--pd-font-family);--pd-font-button-font:700 14px/1.4 var(--pd-font-family);--pd-font-button-s-font:700 12px/1.4 var(--pd-font-family);--pd-font-badge-font:700 10px/1.3 var(--pd-font-family);--pd-font-caption-font:400 13px/1.4 var(--pd-font-family);--pd-font-caption-s-font:400 11px/1.4 var(--pd-font-family);--pd-font-monospace-font:400 14px/1.5 ui-monospace,SFMono-Regular,monospace}}
body{{font:var(--pd-font-body-font)}}h1{{font:var(--pd-font-title-xxl-font)}}h2{{font:var(--pd-font-title-xl-font)}}h3{{font:var(--pd-font-title-l-font)}}.outcome{{font:var(--pd-font-body-l-strong-font)}}.masthead p{{font:var(--pd-font-caption-s-font);font-weight:700}}.chart-reason,footer{{font:var(--pd-font-caption-font)}}.detail-section>p,summary{{font:var(--pd-font-link-s-font)}}.triage-section{{background:var(--pd-color-purple-light)}}button{{background:var(--pd-color-yellow);color:var(--pd-color-green-dark);font:var(--pd-font-button-font)}}button:focus-visible,input:focus-visible,textarea:focus-visible,summary:focus-visible{{outline-color:var(--pd-color-purple)}}
</style><main><header class="masthead"><p>PIPEDRIVE · FORECAST</p><h1>Forecast — {period}</h1><p>{scope}</p></header>{warning}{sections}<h2>Concentration risks</h2><ul class="risk-list">{risks}</ul>{triage}<footer>{source}. Rendered from the completed Forecast result. No Pipedrive records were changed.</footer></main>{script}</html>""".format(scope=escape(str(result.get("scope_label") or "")), today=escape(str(result.get("today", ""))), period=escape(str(result.get("period", "Current period"))), source=escape(probability_source), warning=warning, sections="".join(sections), risks=risks, triage=triage, script=script)


def render_html(result, *args, **kwargs):
    if result.get('report_schema_version') == 2:
        from report_contract import render_html as render_profile
        return render_profile(result)
    html = _render_html(result, *args, **kwargs)
    if result.get("data_unavailable"):
        return html
    return html.replace("</style>", CHART_CSS + "</style>", 1).replace("</main>", "</main>" + CHART_SCRIPT, 1)


def native_artifact_payload(result):
    return {"mime_type": "text/html", "filename": os.path.basename(default_output_path(result)), "html": render_html(result)}


def write_html(result, path):
    """Write one selected private local HTML file from an already computed result."""
    target = os.fspath(path)
    parent = os.path.dirname(target)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(target, "w", encoding="utf-8") as handle:
        handle.write(render_html(result))
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Completed Forecast JSON result")
    parser.add_argument("--output", help="Visible local HTML path")
    parser.add_argument("--sha256", action="store_true", help="print canonical HTML SHA-256 without creating a file")
    args = parser.parse_args(argv)
    with open(args.input, encoding="utf-8") as source:
        result = json.load(source)
    if args.sha256:
        print(hashlib.sha256(render_html(result).encode("utf-8")).hexdigest())
        return 0
    path = write_html(result, args.output or default_output_path(result))
    print(f"Rendered candidate: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
