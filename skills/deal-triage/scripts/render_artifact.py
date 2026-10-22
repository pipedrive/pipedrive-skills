"""Render a standalone, read-only Deal Triage dashboard from a computed result."""

import json
import hashlib
import os
import sys
import argparse
from collections import Counter
from html import escape
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from display_time import display_timestamp, display_timezone


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dashboard_charts import CSS as CHART_CSS, SCRIPT as CHART_SCRIPT, brief_charts, risk_charts, forecast_charts
from pipedrive_links import html_deal

def _text(value, fallback="Not recorded"):
    return escape(str(fallback if value is None or value == "" else value))


def _owner_name(result, owner_id):
    owner = (result.get("owners") or {}).get(str(owner_id)) if owner_id is not None else None
    return (owner or {}).get("name") or (f"Owner {owner_id}" if owner_id is not None else "Unassigned")


def build_presentation(result, role="rep", capabilities=None):
    """Project an existing triage result without ranking, scoring, or API access."""
    source = result.get("presentation") or {}
    deal_by_id = {deal.get("id"): deal for deal in result.get("deals") or []}
    lead_by_id = {lead.get("id"): lead for lead in result.get("leads") or []}
    proposals = {proposal.get("deal_id"): proposal for proposal in result.get("proposals") or []}
    items = []
    for position, item in enumerate((source.get("items") or []) + (source.get("lead_items") or []), 1):
        deal = (lead_by_id.get(item.get("lead_id")) if "lead_id" in item else deal_by_id.get(item.get("deal_id"))) or {}
        proposal = item.get("proposal") or proposals.get(item.get("deal_id"))
        owner = _owner_name(result, item.get("owner_id", deal.get("owner_id")))
        number = item.get("item") or position
        execution = (result.get("action_states") or {}).get(str(number)) or {"status": "pending"}
        items.append({
            "execution": execution,
            "number": item.get("item") or position,
            "deal_id": item.get("deal_id"),
            "record_label": "Lead" if "lead_id" in item else "Deal",
            "record_id": item.get("lead_id", item.get("deal_id")),
            "title": deal.get("title") or "Untitled deal",
            "stage": deal.get("stage") or "Unknown stage",
            "value": deal.get("value"),
            "currency": deal.get("currency") or "",
            "owner": owner,
            "severity": item.get("severity") or deal.get("severity") or "Unspecified",
            "flags": item.get("flags") or deal.get("flags") or [],
            "evidence": item.get("primary_evidence"),
            "full_evidence": item.get("evidence") or deal.get("evidence") or [],
            "rule_wording": item.get("rule_wording") or [],
            "internal_task": item.get("internal_task"),
            "proposal": proposal,
            "existing_next_step": item.get("existing_next_step"),
            "decision": item.get("decision") or deal.get("decision"),
        })
    support = source.get("supporting_findings") or {}
    partial = support.get("partial_coverage") or result.get("partial_line")
    pending = sum(bool(item["proposal"]) and item["execution"]["status"] == "pending" for item in items)
    return {
        "today": result.get("today") or "latest",
        "display_timezone": display_timezone(result.get("display_timezone")),
        "role": role,
        "risk_counts": source.get("risk_counts") or {},
        "owner_counts": source.get("owner_counts") or {},
        "owner_risks": source.get("owner_risks") or {},
        "proposal_count": pending,
        "covered_count": source.get("covered_count", 0),
        "escalation_count": source.get("escalation_count", 0),
        "items": items,
        "existing": support.get("existing_next_steps") or [],
        "clusters": support.get("clusters") or [],
        "partial": partial,
        "coverage": result.get("coverage_line") or "Coverage unavailable",
        "run_log": result.get("run_log_line") or "Run log unavailable",
        "disclosures": source.get("disclosures") or result.get("disclosures") or [],
    }


def default_output_path(result, directory="."):
    """Return the visible local filename for this completed triage run."""
    date = str(result.get("today") or "latest").replace("/", "-")
    return os.path.join(directory, f"deal-triage-{date}.html")


def native_artifact_payload(result, role="rep", capabilities=None):
    """Return the exact canonical HTML used for both native and local delivery."""
    return {
        "mime_type": "text/html",
        "filename": os.path.basename(default_output_path(result)),
        "html": render_html(result, role, capabilities),
    }


def _handoff(item, presentation):
    execution = item.get("execution") or {}
    if execution.get("status", "pending") != "pending":
        status = execution["status"]
        detail = ("Created/updated activity: " + str((item.get("proposal") or {}).get("subject") or "Activity name unavailable")
                  + " · Write time: " + display_timestamp(execution.get("executed_at"), presentation["display_timezone"])) if status == "written" else {
                      "accepted": "Accepted; execution has not been confirmed.",
                      "skipped": "Skipped by the user.", "failed": "Write failed; review this item in chat before retrying.",
                      "uncertain": "The write was submitted but no matching read-back was found; check it in Pipedrive and do not resubmit."}.get(status, status)
        dates = ''
        if status == 'written':
            dates = '<details><summary>Write timestamps (' + _text(presentation["display_timezone"]) + ')</summary>' + ''.join(
                '<p>' + label + ': ' + _text(display_timestamp(execution.get(key), presentation["display_timezone"])) + '</p>'
                for label, key in (('Activity created', 'created_at'), ('Activity updated', 'updated_at'),
                                   ('Write response observed', 'observed_at'))) + '</details>'
        return '<p class="execution-status"><strong>' + _text(status.title()) + '</strong> · ' + _text(detail) + '</p>' + dates
    proposal = item["proposal"]
    if not proposal:
        return "<p class=\"review\">Review item {number} in chat.</p>".format(number=item["number"])
    brief = "{kind}: {subject} · due {due}".format(
        kind=_text(proposal.get("type"), "Task"), subject=_text(proposal.get("subject")), due=_text(proposal.get("due_date")))
    if proposal.get("action") == "addActivity" and proposal.get("person_id") is not None:
        brief += " · Contact: " + _text(proposal.get("person_name"), "Name unavailable")
    if presentation["role"] == "manager":
        task = item.get("internal_task")
        if not task:
            return '<p class="handoff">Internal task details unavailable. Review item {} in chat.</p>'.format(item["number"])
        brief = "task: {subject} · due {due}<br>{note}".format(
            subject=_text(task["subject"]), due=_text(task["due_date"]), note=_text(task["note"]))
        return "<p class=\"handoff\"><strong>Copy task brief</strong> for {owner}: {brief}. Review item {number} in chat.</p>".format(
            owner=_text(item["owner"]), brief=brief, number=item["number"])
    return "<p class=\"review\">Review item {number} in chat.</p>".format(number=item["number"])


def _work_group(item):
    status = (item.get("execution") or {}).get("status", "pending")
    if status != "pending":
        return {"written": "Completed", "accepted": "Accepted", "skipped": "Skipped", "failed": "Failed", "uncertain": "Uncertain"}[status]
    kind = (item.get("decision") or {}).get("kind")
    if kind == "proposed_activity" or (item.get("record_label") == "Lead" and item.get("proposal")):
        return "Act now"
    if kind == "existing_task_review" or item.get("existing_next_step"):
        return "Review existing tasks"
    return "Check missing evidence"


def _risk_charts(result, presentation):
    return risk_charts(result, {item.get("deal_id"): str(item.get("number")) for item in presentation["items"]})


def _render_html(result, role="rep", capabilities=None):
    """Return a read-only dashboard with local filtering and copy controls."""
    presentation = build_presentation(result, role, capabilities)
    risk_charts = _risk_charts(result, presentation)
    partial = ("<section class=\"notice\">{}</section>".format(_text(presentation["partial"]))
               if presentation["partial"] else "")
    metric_rows = '<article class="metric"><small>AWAITING REVIEW</small><strong>{}</strong><span>proposals</span></article>'.format(_text(presentation["proposal_count"]))
    metric_rows += "".join("<article class=\"metric {level}\"><small>{level} RISK</small><strong>{count}</strong><span>flagged items</span></article>".format(
        level=_text(level).lower(), count=_text(presentation["risk_counts"].get(level, 0))) for level in ("High", "Medium", "Low"))
    metric_rows += '<article class="metric"><small>ALREADY COVERED</small><strong>{}</strong><span>items</span></article>'.format(_text(presentation["covered_count"]))
    metric_rows = metric_rows or "<article class=\"metric\"><small>RISK</small><strong>0</strong><span>flagged items</span></article>"
    group_counts = {label: 0 for label in ("Act now", "Review existing tasks", "Check missing evidence", "Completed", "Accepted", "Skipped", "Failed", "Uncertain")}
    for item in presentation["items"]:
        group_counts[_work_group(item)] += 1
    group_rows = "".join(
        '<article class="work-group"><strong>{count}</strong><span>{label}</span></article>'.format(
            count=count, label=label,
        ) for label, count in group_counts.items() if count
    )
    first = next((item for item in presentation["items"] if item["execution"]["status"] == "pending"), None)
    headline = (f"Start with {first['title']}: {(first.get('decision') or {}).get('text') or 'Review this deal in chat.'}"
                if first else "No pending numbered decisions remain in this saved result.")
    cards = []
    for item in presentation["items"]:
        historical = item["execution"]["status"] != "pending"
        signals = "".join("<span class=\"chip warning\">{}</span>".format(_text(("At scan: " if historical else "") + wording)) for wording in item["rule_wording"]) or "<span class=\"chip warning\">Review</span>"
        existing = item["existing_next_step"]
        existing_copy = _handoff(item, presentation) if historical else ("" if existing and item.get("decision") else
                         "<p class=\"existing\"><strong>Existing task:</strong> {} due {}; review it in Pipedrive.</p>".format(
                             _text(existing.get("subject"), "Activity"), _text(existing.get("due_date"))) if existing else _handoff(item, presentation))
        evidence = "".join("<p>{}</p>".format(_text(line)) for line in item["full_evidence"])
        proposal = item["proposal"]
        if proposal:
            reason = item["rule_wording"][0] if item["rule_wording"] else proposal["why"]
            evidence += '<p><strong>Why this action:</strong> {}</p>'.format(_text(reason))
        evidence = evidence or "<p>No further evidence recorded in Pipedrive.</p>"
        proposal_copy = ('<p><strong>Suggested activity:</strong> {} — {} · due {}</p>'.format(
            _text(proposal["type"]), _text(proposal["subject"]), _text(proposal["due_date"]))
                         if proposal and not item.get("decision") else "")
        copy_command = ('<button type="button" class="copy-command" data-command="accept {number}" '
                        'aria-label="Copy accept {number} command">Copy accept {number}</button>'.format(
                            number=_text(item["number"]))) if proposal and item["execution"]["status"] == "pending" else ""
        group = _work_group(item)
        cards.append("""<article class=\"queue-card {severity}\" data-group=\"{group_key}\"><span class=\"rank\">{number}</span><div>
<h3>{title}</h3><p class=\"meta\">{stage} · Owner: {owner} · {severity} risk</p><p class=\"work-type\">{group}</p><p><strong>Next decision:</strong> {decision}</p><div class=\"chips\">{signals}</div>{proposal_copy}{next_step}
{copy_command}<details><summary>Full evidence</summary>{evidence}</details></div></article>""".format(
            record_label=item["record_label"], record_id=_text(item["record_id"]), proposal_copy=proposal_copy,
            number=_text(item["number"]), title=(html_deal(result, {"id": item["deal_id"], "title": item["title"]})
                                               if item["record_label"] == "Deal" else _text(item["title"])), deal_id=_text(item["deal_id"]), stage=_text(item["stage"]),
            owner=_text(item["owner"]), severity=_text(item["severity"]).lower(), signals=signals, next_step=existing_copy,
            group=group, group_key=escape(group.lower().replace(" ", "-")), copy_command=copy_command,
            decision=_text("Action " + item["execution"]["status"] + ". Original proposal shown in evidence." if historical else (item.get("decision") or {}).get("text"), "Review this deal's evidence in chat."),
            evidence=evidence))
    queue = "".join(cards) or "<p class=\"empty\">No numbered triage items are available.</p>"
    existing_rows = "".join("<li>Item {} has an existing next step.</li>".format(_text(item.get("item"))) for item in presentation["existing"]) or "<li>No existing next steps in the shown queue.</li>"
    cluster_rows = "".join("<li>{}</li>".format(_text(cluster.get("label") or cluster.get("name") or cluster)) for cluster in presentation["clusters"]) or "<li>No suppressed clusters recorded.</li>"
    data_rows = []
    for item in presentation["items"]:
        if any(flag.get("id") == "R11" for flag in item["flags"]):
            data_rows.append("<li>Item {} has a data-quality finding; review it in chat.</li>".format(_text(item["number"])))
    data = "".join(data_rows) or "<li>No data-quality findings in the shown queue.</li>"
    disclosures = "".join("<p>{}</p>".format(_text(line)) for line in dict.fromkeys(presentation["disclosures"])
                          if line and line not in (presentation["coverage"], presentation["run_log"]))
    if role == "manager":
        role_summary = "<ul>" + "".join("<li>{}: High {} · Medium {} · Low {}</li>".format(
            _text(owner), counts.get("High", 0), counts.get("Medium", 0), counts.get("Low", 0))
            for owner, counts in presentation["owner_risks"].items()) + "</ul>Escalations requiring separate confirmation: " + _text(presentation["escalation_count"])
    else:
        role_summary = "<ul>" + "".join("<li>Item {}: {} · due {}</li>".format(
            _text(item["number"]), _text(item["proposal"]["subject"]), _text(item["proposal"]["due_date"]))
            for item in presentation["items"] if item["proposal"]) + "</ul>"
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Deal triage — {today}</title><style>
:root{{--pipedrive-green:#017737;--pipedrive-deep:#014722;--pipedrive-mint:#D7F9D3;--paper:#f7faf8;--ink:#182b22;--muted:#5d7066;--line:#d9e5dd;--amber:#a96800;--red:#bd3d38}}*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:16px/1.45 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}}main{{width:min(960px,calc(100% - 32px));margin:40px auto 64px}}header{{padding:30px;background:var(--pipedrive-deep);color:#fff;border-radius:16px 16px 4px 4px}}.eyebrow{{margin:0 0 7px;color:#7ee0a8;font-size:.76rem;font-weight:700;letter-spacing:.08em}}h1{{margin:0;font-size:clamp(2.3rem,7vw,4.4rem);letter-spacing:-.06em;line-height:.95}}h2{{margin:34px 0 12px;font-size:1.35rem}}h3{{margin:0;font-size:1.05rem}}.context,.notice,.metric-grid,.queue-card,.support{{background:#fff;border:1px solid var(--line)}}.context{{padding:12px 18px;border-top:0;border-radius:0 0 10px 10px;color:var(--muted)}}.notice{{margin-top:18px;padding:13px 16px;border-left:4px solid var(--amber);color:#614200}}.metric-grid{{display:grid;grid-template-columns:repeat(5,1fr);border-radius:10px;overflow:hidden;margin-top:20px}}.metric{{padding:18px;border-right:1px solid var(--line)}}.metric:last-child{{border:0}}.metric small{{display:block;color:var(--muted);font-weight:700;font-size:.72rem}}.metric strong{{display:block;font-size:2rem;color:var(--pipedrive-green)}}.metric.high strong{{color:var(--red)}}.metric.medium strong{{color:var(--amber)}}.metric span,.meta{{color:var(--muted);font-size:.83rem}}.queue{{display:grid;gap:10px}}.queue-card{{display:grid;grid-template-columns:42px 1fr;gap:14px;padding:18px;border-left:4px solid var(--amber);border-radius:8px}}.queue-card.high{{border-left-color:var(--red)}}.rank{{font-size:1.6rem;font-weight:750;color:#a9b8b0}}.chips{{display:flex;flex-wrap:wrap;gap:6px}}.chip{{padding:3px 7px;background:var(--pipedrive-mint);color:var(--pipedrive-green);font-size:.74rem;font-weight:700;border-radius:4px}}.review,.handoff,.existing{{margin:10px 0 0}}.handoff{{padding:9px 11px;background:var(--pipedrive-mint);border-radius:5px}}.existing{{color:var(--muted)}}details{{margin-top:11px;color:var(--muted);font-size:.84rem}}summary{{cursor:pointer;color:var(--pipedrive-green);font-weight:700}}summary:focus-visible{{outline:3px solid var(--amber);outline-offset:3px}}.support-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}.support{{padding:16px;border-radius:8px}}.support h2{{margin:0 0 8px;font-size:1rem}}.support ul{{margin:0;padding-left:20px;color:var(--muted);font-size:.85rem}}footer{{margin-top:28px;padding-top:14px;border-top:1px solid var(--line);color:var(--muted);font-size:.8rem}}footer p{{margin:5px 0}}@media(max-width:600px){{main{{width:min(100% - 20px,960px);margin-top:10px}}header{{padding:25px 20px}}.metric-grid,.support-grid{{grid-template-columns:1fr}}.metric{{border-right:0;border-bottom:1px solid var(--line)}}.metric:last-child{{border-bottom:0}}}}@media print{{body{{background:#fff}}main{{width:100%;margin:0}}header{{border-radius:0}}.notice,.queue-card,.support,footer,details{{break-inside:avoid}}details>*{{display:block}}}}
.lead-answer{{margin:24px 0 0;font-size:clamp(1.15rem,2.5vw,1.5rem);font-weight:650;line-height:1.35;color:var(--pipedrive-deep)}}.group-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:18px}}.work-group{{display:flex;gap:10px;align-items:center;padding:14px 16px;background:white;border:1px solid var(--line);border-radius:8px}}.work-group strong{{font-size:1.8rem;color:var(--ink)}}.work-group span{{font-size:.86rem;font-weight:700}}.work-type{{display:inline-block;margin:10px 0 0;padding:3px 8px;border-radius:4px;background:#eef1ef;color:var(--ink);font-size:.76rem;font-weight:700}}.chip.warning{{background:#fff7df;color:#614200}}.audit{{margin-top:26px;padding:16px;border:1px solid var(--line);border-radius:10px;background:white}}.audit>summary{{font-size:.95rem}}.audit .context{{border-radius:0 0 8px 8px}}@media(max-width:600px){{.work-group{{display:block;padding:10px}}.work-group strong{{display:block;font-size:1.45rem}}.work-group span{{font-size:.74rem}}}}@media(max-width:340px){{.group-grid{{grid-template-columns:1fr}}}}
.visual-grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}.visual-panel{{background:#fff;border:1px solid var(--line);border-radius:10px;padding:16px;min-width:0}}.visual-panel p{{color:var(--muted);font-size:.85rem}}.rule-row{{display:grid;grid-template-columns:9rem 1fr 2rem;align-items:center;gap:.6rem;margin:.5rem 0}}.rule-row small{{color:var(--muted)}}.rule-track{{height:13px;background:var(--pd-color-purple-light);border-radius:4px;overflow:hidden}}.rule-track i{{display:block;height:100%;background:var(--pd-color-purple)}}.visual-panel svg{{display:block;width:100%;height:auto}}.visual-panel svg .axis{{fill:none;stroke:var(--muted);stroke-width:1}}.visual-panel svg text{{fill:var(--muted);font-size:12px}}.visual-panel svg .queued{{fill:var(--pd-color-purple)}}.visual-panel svg .other{{fill:var(--pipedrive-green);opacity:.7}}@media(max-width:700px){{.visual-grid{{grid-template-columns:1fr}}}}
.queue-controls{{display:flex;flex-wrap:wrap;gap:.5rem;margin-bottom:1rem}}.queue-controls button,.copy-command{{font:var(--pd-font-button-s-font);border:1px solid var(--line);border-radius:5px;background:white;color:var(--pipedrive-deep);padding:.45rem .7rem;cursor:pointer}}.queue-controls button[aria-pressed="true"]{{background:var(--pipedrive-deep);color:white}}.copy-command{{margin-top:.7rem}}[hidden]{{display:none!important}}button:focus-visible{{outline:3px solid var(--pd-color-yellow);outline-offset:2px}}@media print{{.queue-controls,.copy-command,.copy-status{{display:none}}}}
:root{{--pd-color-green:#017737;--pd-color-green-dark:#014722;--pd-color-green-light:#D7F9D3;--pd-color-yellow:#FFDE00;--pd-color-purple:#6861F2;--pd-color-purple-dark:#3B3A97;--pd-color-purple-light:#CFD0F9;--pipedrive-green:var(--pd-color-green);--pipedrive-deep:var(--pd-color-green-dark);--pipedrive-mint:var(--pd-color-green-light);--pd-font-family:ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;--pd-font-title-xxl-font:700 25px/1.2 var(--pd-font-family);--pd-font-title-xl-font:700 21px/1.3 var(--pd-font-family);--pd-font-title-l-font:700 16px/1.35 var(--pd-font-family);--pd-font-title-m-font:700 14px/1.4 var(--pd-font-family);--pd-font-body-l-font:400 16px/1.5 var(--pd-font-family);--pd-font-body-l-strong-font:700 16px/1.5 var(--pd-font-family);--pd-font-body-font:400 14px/1.5 var(--pd-font-family);--pd-font-body-strong-font:700 14px/1.5 var(--pd-font-family);--pd-font-body-s-font:400 12px/1.45 var(--pd-font-family);--pd-font-body-s-strong-font:700 12px/1.45 var(--pd-font-family);--pd-font-link-font:600 14px/1.5 var(--pd-font-family);--pd-font-link-s-font:600 12px/1.45 var(--pd-font-family);--pd-font-link-l-font:600 16px/1.5 var(--pd-font-family);--pd-font-link-alt-font:400 14px/1.5 var(--pd-font-family);--pd-font-link-alt-s-font:400 12px/1.45 var(--pd-font-family);--pd-font-button-font:700 14px/1.4 var(--pd-font-family);--pd-font-button-s-font:700 12px/1.4 var(--pd-font-family);--pd-font-badge-font:700 10px/1.3 var(--pd-font-family);--pd-font-caption-font:400 13px/1.4 var(--pd-font-family);--pd-font-caption-s-font:400 11px/1.4 var(--pd-font-family);--pd-font-monospace-font:400 14px/1.5 ui-monospace,SFMono-Regular,monospace}}
body{{font:var(--pd-font-body-font)}}h1{{font:var(--pd-font-title-xxl-font);letter-spacing:normal}}h2{{font:var(--pd-font-title-xl-font)}}h3,.support h2{{font:var(--pd-font-title-l-font)}}.lead-answer{{font:var(--pd-font-body-l-strong-font)}}.metric strong,.work-group strong{{font:var(--pd-font-title-xxl-font)}}.eyebrow,.metric small{{font:var(--pd-font-caption-s-font);font-weight:700}}.context,.meta,footer{{font:var(--pd-font-caption-font)}}.chip,.work-type{{font:var(--pd-font-badge-font)}}details,.support ul{{font:var(--pd-font-body-s-font)}}summary{{font:var(--pd-font-link-s-font)}}@media(max-width:600px){{.group-grid{{grid-template-columns:1fr}}.work-group{{display:flex}}}}
</style></head><body><main aria-label="Deal triage dashboard"><header><p class="eyebrow">PIPEDRIVE · DEAL TRIAGE</p><h1>Deal triage</h1><p>{today} · {role} view · read-only scan</p></header>{partial}<p class="lead-answer">{headline}</p><section class="group-grid" aria-label="Work by next step">{groups}</section>{risk_charts}<h2 id="queue">Your next steps</h2><div class="queue-controls" role="group" aria-label="Filter next steps"><button type="button" data-filter="all" aria-pressed="true">All</button><button type="button" data-filter="act-now" aria-pressed="false">Act now</button><button type="button" data-filter="review-existing-tasks" aria-pressed="false">Review existing tasks</button><button type="button" data-filter="check-missing-evidence" aria-pressed="false">Check missing evidence</button></div><p class="copy-status" role="status" aria-live="polite"></p><section class="queue" aria-labelledby="queue">{queue}</section><section class="metric-grid" aria-label="Action summary">{metrics}</section><section class="context"><strong>Role summary:</strong> {role_summary}</section><aside class="limitations">{disclosures}</aside><p>{coverage}</p><p>{run_log}</p><footer><p>Original scan: {today}. Execution status reflects saved write responses. Viewing this dashboard does not change records.</p></footer></main><script>document.querySelectorAll('[data-filter]').forEach(button=>button.addEventListener('click',()=>{{document.querySelectorAll('[data-filter]').forEach(other=>other.setAttribute('aria-pressed',String(other===button)));document.querySelectorAll('.queue-card').forEach(card=>{{card.hidden=button.dataset.filter!=='all'&&card.dataset.group!==button.dataset.filter;}});}}));document.querySelectorAll('[data-command]').forEach(button=>button.addEventListener('click',async()=>{{const value=button.dataset.command;try{{await navigator.clipboard.writeText(value);document.querySelector('.copy-status').textContent='Copied '+value+'. Paste into chat to request this action.';}}catch(error){{document.querySelector('.copy-status').textContent='Copy this command into chat: '+value;}}}}));</script></body></html>""".format(
        today=_text(presentation["today"]), role=_text(role),
        role_summary=role_summary, partial=partial, headline=_text(headline), groups=group_rows, risk_charts=risk_charts,
        metrics=metric_rows, queue=queue, existing=existing_rows, clusters=cluster_rows, data=data,
        coverage=_text(presentation["coverage"]), run_log=_text(presentation["run_log"]), disclosures=disclosures)


def render_html(result, *args, **kwargs):
    if result.get('report_schema_version') == 2:
        from report_contract import render_html as render_profile
        return render_profile(result)
    html = _render_html(result, *args, **kwargs)
    if result.get("data_unavailable"):
        return html
    return html.replace("</style>", CHART_CSS + "</style>", 1).replace("</main>", "</main>" + CHART_SCRIPT, 1)


def write_html(result, path, role="rep", capabilities=None):
    """Write a local copy of the same HTML returned for native delivery."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as output:
        output.write(render_html(result, role, capabilities))
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output")
    parser.add_argument("--sha256", action="store_true", help="print canonical HTML SHA-256 without creating a file")
    parser.add_argument("--role", choices=("rep", "manager"), default="rep")
    parser.add_argument("--capabilities", required=True, help="JSON file containing resolved observed capabilities")
    args = parser.parse_args()
    from artifact_delivery import delivery_options
    with open(args.input, encoding="utf-8") as source:
        result = json.load(source)
    with open(args.capabilities, encoding="utf-8") as source:
        capabilities = json.load(source)
    options = delivery_options(result, capabilities)
    if not args.sha256 and not any(option["id"] == "local_html" for option in options.get("options", [])):
        parser.error(options.get("reason") or "Private local HTML capability unavailable")
    if args.sha256:
        print(hashlib.sha256(render_html(result, args.role, capabilities).encode("utf-8")).hexdigest())
        return 0
    if not args.output:
        parser.error("--output is required unless --sha256 is used")
    write_html(result, args.output, args.role, capabilities)
    print(f"Rendered candidate: {args.output} (private local HTML; verify before claiming delivery)")
    print(result["coverage_line"])


if __name__ == "__main__":
    main()
