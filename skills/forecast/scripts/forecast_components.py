"""Forecast business charts shared by legacy and confirmed-profile dashboards."""

from html import escape
from pipedrive_links import html_deal
from display_time import display_timestamp
from dashboard_charts import money

def _money(value, currency):
    return money(value or 0, currency)


def _chart_row(label, amount, currency, scale, linked_label=None):
    width = max(0, min(100, round(float(amount or 0) * 100 / scale))) if scale > 0 else 0
    return ('<div class="chart-row"><span>{label}</span><div class="bar-track" role="img" '
            'aria-label="{description}" tabindex="0" data-tooltip="{description}"><i style="width:{width}%"></i></div><strong>{value}</strong></div>').format(
                label=linked_label or escape(label), description=escape(f"{label}: {_money(amount, currency)}"),
                width=width, value=escape(_money(amount, currency)))


def _currency_charts(currency, values, comparison, activity_warning, adjustment_status=None, unweighted=False, result=None):
    target = comparison.get("target")
    scale = max(1, *(float(value or 0) for value in (
        values.get("adjusted_forecast"), values.get("best_case"), target,
        comparison.get("manager_commit"))))
    bridge = '<h3>Target bridge</h3>'
    open_label = "Open value after risk adjustments" if unweighted else "Likely revenue from open deals"
    revenue_label = "Value including won revenue" if unweighted else "Likely revenue"
    bridge += _chart_row("Won to date", values.get("won_to_date"), currency, scale)
    if values.get("adjusted_open") is not None:
        bridge += _chart_row(open_label, values["adjusted_open"], currency, scale)
    else:
        bridge += f'<p>{open_label} is unavailable in this saved result.</p>'
    if target is None:
        bridge += '<p>Target not configured.</p>'
    else:
        difference = comparison.get("projected_difference")
        if difference is not None:
            label = (("Value below target" if difference > 0 else "Value above target" if difference < 0 else "Value matches target") if unweighted else
                     "Projected shortfall" if difference > 0 else "Projected surplus" if difference < 0 else "Target met")
            bridge += f'<p><strong>{label}:</strong> {escape(_money(abs(difference), currency))}</p>'
        if comparison.get("remaining_to_target") is not None:
            bridge += f'<p>Still to win against target: {escape(_money(comparison["remaining_to_target"], currency))}</p>'
    range_rows = ['<h3>Forecast range</h3><p>All bars use the same scale for this currency.</p>',
                  _chart_row(revenue_label, values.get("adjusted_forecast"), currency, scale),
                  _chart_row("If every included open deal closes", values.get("best_case"), currency, scale)]
    if target is not None:
        range_rows.append(_chart_row("Target", target, currency, scale))
    if comparison.get("manager_commit") is not None:
        range_rows.append(_chart_row("Manager commit (entered judgment)", comparison["manager_commit"], currency, scale))
        if comparison.get("commit_above_best_case"):
            range_rows.append('<p class="warning">Current in-scope CRM deals cannot support this Manager commit.</p>')
    if activity_warning:
        range_rows.append('<p class="warning">Activity evidence is incomplete; this amount may be optimistic.</p>')
    impacts = sorted((deal for deal in values.get("deals") or [] if (deal.get("haircut_impact") or 0) > 0),
                     key=lambda deal: -deal["haircut_impact"])[:5]
    risk_rows = ['<h3>Risk adjustments</h3>']
    if impacts:
        impact_scale = max(deal["haircut_impact"] for deal in impacts)
        for deal in impacts:
            name = deal.get("title") or "Deal name unavailable"
            labels = {"silent": "no recent completed CRM activity was recorded",
                      "no_next": "no next customer activity is scheduled",
                      "slipped": "the expected close date has passed"}
            reasons = "; ".join(labels.get(rule, rule.replace("_", " ")) for rule in deal.get("haircut_rules") or [])
            risk_rows.append(_chart_row(name, deal["haircut_impact"], currency, impact_scale, html_deal(result or {}, deal, name)))
            risk_rows.append(f'<p class="chart-reason">Forecast reduced because {escape(reasons)}.</p>')
            base_probability = deal.get("base_probability")
            adjusted_probability = deal.get("adjusted_probability")
            base_label = f"{base_probability:.0%}" if base_probability is not None else "Not recorded"
            adjusted_label = f"{adjusted_probability:.0%}" if adjusted_probability is not None else "Not recorded"
            risk_rows.append('<details><summary>Evidence and adjustment</summary>'
                f'<p>Scheduled date of last completed activity: {escape(str(deal.get("last_activity_scheduled_date") or "Not resolved"))}</p>'
                f'<p>Marked done: {escape(display_timestamp(deal.get("last_activity_marked_as_done_time"), (result or {}).get("display_timezone")))}</p>'
                f'<p>Recorded probability: {base_label}; after accepted adjustments: {adjusted_label}. These activity dates do not prove buyer contact.</p></details>')
    elif adjustment_status in {"not_configured", "disabled"}:
        risk_rows.append('<p>Risk adjustments are not active. This section cannot establish that the deals are safe.</p>')
    else:
        risk_rows.append('<p>No adjustment data.</p>')
    return '<div class="charts"><div class="chart-panel">' + bridge + '</div><div class="chart-panel">' + "".join(range_rows) + '</div><div class="risk-panel">' + "".join(risk_rows) + '</div></div>'
