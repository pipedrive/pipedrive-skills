"""Export a completed Pipedrive skill result as a document without CRM access."""

import argparse
from html import escape
import json
from pathlib import Path
import re
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

from activity_evidence import DATE_EXPLANATION
from report_identity import report_skill
from pipedrive_links import allowed_deal_links, link_markdown, markdown_deal


STYLE = """
@page { size: A4; margin: 18mm 17mm; }
:root { --green:#017737; --deep:#014722; --mint:#D7F9D3; --purple:#6861F2;
        --ink:#182b22; --muted:#5d7066; --line:#d9e5dd; }
* { box-sizing:border-box; }
body { margin:0; color:var(--ink); background:#f7faf8;
       font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif; }
main { max-width:850px; margin:2rem auto; padding:2rem 2.5rem; background:white;
       border:1px solid var(--line); border-radius:12px; }
.eyebrow { color:var(--green); text-transform:uppercase; letter-spacing:.1em;
           font-size:11px; font-weight:700; }
h1 { font-size:25px; line-height:1.2; color:var(--deep); margin:.5rem 0 1.5rem; }
h2 { font-size:21px; line-height:1.3; color:var(--deep); border-bottom:2px solid var(--mint);
     padding-bottom:.3rem; margin:1.5rem 0 .6rem; break-after:avoid; }
h3 { font-size:16px; line-height:1.35; margin:1.2rem 0 .5rem; break-after:avoid; }
p,li { overflow-wrap:anywhere; } p { margin:.45rem 0; } li { margin:.3rem 0; }
.queue-item { border-left:3px solid var(--purple); padding:.4rem .7rem;
              margin:.55rem 0; background:#f7f7ff; break-inside:avoid; }
.indented { margin-left:1rem; color:var(--muted); }
strong { color:var(--deep); } ul { padding-left:1.3rem; }
footer { border-top:1px solid var(--line); color:var(--muted);
         font-size:11px; margin-top:2rem; padding-top:.6rem; }
@media print { body { background:white; } main { border:0; border-radius:0;
  margin:0; padding:0; max-width:none; } h2,h3,p,li { orphans:2; widows:2; } }
"""


def _inline(value, allowed_links=()):
    parts = re.split(r"(\[(?:\\.|[^\]\\\n])+\]\(https://[^\s)]+\))", value)
    output = []
    for part in parts:
        match = re.fullmatch(r"\[((?:\\.|[^\]\\\n])+)\]\((https://[^\s)]+)\)", part)
        if match and match.group(2) in allowed_links:
            output.append('<a href="' + escape(match.group(2), quote=True)
                          + '" target="_blank" rel="noopener noreferrer">' + escape(re.sub(r"\\([\[\]\\])", r"\1", match.group(1))) + '</a>')
        else:
            bold = re.split(r"(\*\*[^*]+\*\*)", part)
            output.extend("<strong>" + escape(text[2:-2]) + "</strong>"
                          if text.startswith("**") and text.endswith("**") else escape(text) for text in bold)
    return "".join(output)


def markdown_fragment(markdown, allowed_links=()):
    """Render escaped Markdown blocks without a document wrapper."""
    blocks = []
    in_list = False
    section_labels = {"Start with these decisions:", "Complete action queue:",
                      "Other saved-filter findings (outside the action queue):",
                      "Leads to contact:", "How this was calculated:"}
    lines = markdown.splitlines()
    consumed = set()
    for index, line in enumerate(lines):
        if index in consumed:
            continue
        stripped = line.strip()
        if in_list and not (stripped.startswith("- ") or stripped.startswith("* ")):
            blocks.append("</ul>")
            in_list = False
        if not stripped:
            continue
        if stripped.startswith('|') and index + 1 < len(lines):
            headings = stripped.strip('|').split('|')
            separator = lines[index + 1].strip().strip('|').split('|')
            if len(headings) == len(separator) and all(re.fullmatch(r'\s*:?-{3,}:?\s*', cell) for cell in separator):
                blocks.append('<div class="calculation-table"><table><thead><tr>' + ''.join('<th scope="col">' + _inline(cell.strip(), allowed_links) + '</th>' for cell in headings) + '</tr></thead><tbody>')
                consumed.add(index + 1)
                for row_index in range(index + 2, len(lines)):
                    cells = lines[row_index].strip().strip('|').split('|')
                    if not lines[row_index].strip().startswith('|') or len(cells) != len(headings):
                        break
                    blocks.append('<tr>' + ''.join('<td>' + _inline(cell.strip(), allowed_links) + '</td>' for cell in cells) + '</tr>')
                    consumed.add(row_index)
                blocks.append('</tbody></table></div>')
                continue
        if stripped.startswith("# "):
            continue
        if stripped in section_labels:
            blocks.append("<h2>" + _inline(stripped[:-1], allowed_links) + "</h2>")
        elif stripped.startswith("### "):
            blocks.append("<h3>" + _inline(stripped[4:], allowed_links) + "</h3>")
        elif stripped.startswith("## "):
            blocks.append("<h2>" + _inline(stripped[3:], allowed_links) + "</h2>")
        elif stripped.startswith(("- ", "* ")):
            if not in_list:
                blocks.append("<ul>")
                in_list = True
            blocks.append("<li>" + _inline(stripped[2:], allowed_links) + "</li>")
        elif line.startswith("   "):
            blocks.append("<p class='indented'>" + _inline(stripped, allowed_links) + "</p>")
        elif re.match(r"\d+\.\s", stripped):
            blocks.append("<p class='queue-item'>" + _inline(stripped, allowed_links) + "</p>")
        else:
            blocks.append("<p>" + _inline(stripped, allowed_links) + "</p>")
    if in_list:
        blocks.append("</ul>")
    return "".join(blocks)


def markdown_to_html(markdown, title, snapshot, allowed_links=()):
    """Render the computed Markdown as a print-friendly, inert HTML document."""
    blocks = markdown_fragment(markdown, allowed_links)
    return ("<!doctype html><html lang='en'><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>" + escape(title) + "</title><style>" + STYLE + "</style>"
            "<main><p class='eyebrow'>Pipedrive · saved result</p><h1>" + escape(title) +
            "</h1><p>Snapshot: " + escape(snapshot) + "</p>" + blocks +
            "<footer>Rendered from the completed saved result. No Pipedrive records were changed.</footer>"
            "</main></html>")


def export(result, output):
    path = Path(output)
    mode = report_skill(result)
    labels = {"deal-triage": "Deal Triage", "pipeline-brief": "Pipeline Brief", "meeting-follow-up": "Meeting follow-up",
              "deal-progression": "Deal progression", "sales-outreach": "Sales outreach"}
    title = ("Forecast — " + str(result.get("period") or "saved snapshot") if mode == "forecast"
             else labels[mode] + " — " + str(result.get("today") or "saved snapshot"))
    if result.get('report_schema_version') == 2:
        from report_contract import report_markdown
        title = {'pipeline-brief': "Today's priorities", 'deal-triage': 'Deals needing attention', 'forecast': 'Revenue forecast'}[mode]
        result = {**result, 'markdown': report_markdown(result)}
    markdown = result.get("markdown")
    if not isinstance(markdown, str) or not markdown.strip():
        raise ValueError("The saved result has no computed Markdown report")
    deals = [] if result.get('report_schema_version') == 2 else result.get("deals") or [deal for section in (result.get("currency_sections") or {}).values() for deal in section.get("deals") or []]
    if deals:
        markdown += "\n\n## Activity dates and their meaning\n\n" + DATE_EXPLANATION + "\n"
    for deal in deals:
        events = deal.get("completed_activity_evidence") or []
        if not events:
            markdown += (f"\n- {markdown_deal(result, deal)}: scheduled {deal.get('last_activity_scheduled_date') or 'not recorded'}; "
                         f"marked done {deal.get('last_activity_marked_as_done_time') or 'not recorded'}; "
                         f"recency basis {deal.get('last_touch_source') or deal.get('last_activity_date_source') or 'not recorded'}.")
        for event in events:
            markdown += (f"\n- {markdown_deal(result, deal)}, {event.get('subject') or 'Activity name unavailable'}: scheduled "
                         f"{event.get('scheduled_date') or 'not recorded'}; marked done "
                         f"{event.get('marked_as_done_time') or 'not recorded'}; recency basis "
                         f"{event.get('recency_source')}." )
    markdown = link_markdown(markdown, result)
    links = allowed_deal_links(result)
    if path.suffix.lower() == ".md":
        path.write_text(markdown + ("" if markdown.endswith("\n") else "\n"), encoding="utf-8")
    elif path.suffix.lower() == ".html":
        path.write_text(markdown_to_html(markdown, title, str(result.get("today") or "date unavailable"), links), encoding="utf-8")
    elif path.suffix.lower() == ".pdf":
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            raise RuntimeError("PDF export needs a working Playwright Chromium installation") from error
        html = markdown_to_html(markdown, title, str(result.get("today") or "date unavailable"), links)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.set_content(html, wait_until="load")
            page.pdf(path=str(path), format="A4", print_background=True)
            browser.close()
    else:
        raise ValueError("Output must end in .md, .html, or .pdf")
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Completed saved result JSON")
    parser.add_argument("--output", required=True, help="New .md, .html, or .pdf path")
    args = parser.parse_args(argv)
    target = Path(args.output)
    if target.exists():
        parser.error("Output already exists; choose a new path")
    with open(args.input, encoding="utf-8") as source:
        result = json.load(source)
    export(result, target)
    print(f"Report created: {target}")


if __name__ == "__main__":
    main()
