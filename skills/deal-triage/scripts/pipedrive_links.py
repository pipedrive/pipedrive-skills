"""Shared company URL settings and links to saved Pipedrive deal records."""

import argparse
from copy import deepcopy
from html import escape
import json
import os
from pathlib import Path
import re
import tempfile


SUBDOMAIN = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"


def normalize_domain(value):
    """Accept a company subdomain or Pipedrive URL; never infer another host."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Enter your company Pipedrive URL or subdomain")
    value = value.strip().lower()
    if re.fullmatch(SUBDOMAIN, value):
        return value
    match = re.fullmatch(r"(?:https?://)?(" + SUBDOMAIN + r")\.pipedrive\.com(?:[/?#][^\s]*)?", value)
    if not match:
        raise ValueError("Use a company URL such as https://acme.pipedrive.com")
    return match.group(1)


def configured_domain(settings):
    value = (settings.get("output") or {}).get("domain")
    if value is None:
        return None
    try:
        return normalize_domain(value)
    except ValueError:
        return None


def preserve_company_settings(settings, existing):
    """Keep the shared domain when another skill replaces its setup settings."""
    updated = deepcopy(settings)
    previous_domain = configured_domain(existing)
    output = updated.get("output") or {}
    if "domain" not in output and previous_domain:
        output["domain"] = previous_domain
        updated["output"] = output
    if output.get("domain") is not None:
        output["domain"] = normalize_domain(output["domain"])
    return updated


def record_url(domain, path, record_id):
    if type(record_id) is not int or record_id <= 0 or domain is None:
        return None
    try:
        domain = normalize_domain(domain)
    except ValueError:
        return None
    return f"https://{domain}.pipedrive.com/{path}/{record_id}"


def deal_url(domain, deal_id):
    return record_url(domain, "deal", deal_id)


def person_url(domain, person_id):
    return record_url(domain, "person", person_id)


def organization_url(domain, organization_id):
    return record_url(domain, "organization", organization_id)


def saved_deals(result):
    """Collect only record IDs present in this completed saved result."""
    rows = list(result.get("deals") or [])
    for section in (result.get("currency_sections") or {}).values():
        for key in ("deals", "closed_won", "closed_lost"):
            rows.extend(section.get(key) or [])
    aggregates = result.get("aggregates") or {}
    for key in ("won", "lost", "entered_stage"):
        rows.extend(((aggregates.get("movement") or {}).get(key) or {}).get("items") or [])
    rows.extend((aggregates.get("closing_this_week") or {}).get("items") or [])
    return {row["id"]: row for row in rows
            if type(row.get("id")) is int and row["id"] > 0}


def allowed_deal_links(result):
    links = {url for key in saved_deals(result) if (url := deal_url(result.get("output_domain"), key))}
    if result.get('report_schema_version') == 2:
        from urllib.parse import urlsplit
        domain = normalize_domain(result['output_domain']) if result.get('output_domain') else None
        def visit(value):
            if isinstance(value, dict):
                if value.get('type') in ('deal', 'activity', 'person', 'organization', 'note', 'user', 'pipeline', 'stage', 'lead') and value.get('id') and isinstance(value.get('url'), str):
                    parsed = urlsplit(value['url'])
                    if domain and parsed.scheme == 'https' and parsed.netloc.lower() == domain + '.pipedrive.com':
                        links.add(value['url'])
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)
        visit({key: result.get(key) for key in ('work_rows', 'contextual_findings', 'meeting_preparation', 'lost_work_rows', 'proposals', 'scope_records', 'calendar_events')})
    return links


def html_deal(result, deal, label=None):
    label = str(label if label is not None else deal.get("title") or "Deal name unavailable")
    url = deal_url(result.get("output_domain"), deal.get("id"))
    if url and deal.get("id") in saved_deals(result):
        return f'<a href="{escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">{escape(label)}</a>'
    return escape(label)


def markdown_deal(result, deal):
    """Use the saved name as the label and its exact ID only in the URL; without one, the report path's link limitation."""
    from deal_context import markdown_ref  # deal_context imports this module, so the import waits for the call
    url = deal_url(result.get("output_domain"), deal.get("id"))
    return markdown_ref({"type": "deal", "name": deal.get("title") or "Deal name unavailable",
                         "url": url if deal.get("id") in saved_deals(result) else None})


def link_markdown(markdown, result):
    """Replace numeric deal labels with saved names and exact record links."""
    deals = saved_deals(result)
    # Resolve explicit references first to preserve identity for duplicate titles.
    for key, deal in deals.items():
        title = deal.get("title")
        named = markdown_deal(result, deal)
        if title:
            markdown = re.sub(re.escape(title) + r"\s*\(Deal #" + str(key) + r"\)",
                              lambda match: named, markdown)
        url = deal_url(result.get("output_domain"), key)
        if url:
            markdown = markdown.replace(f"[Deal #{key}]({url})", named)
            if title:
                # Legacy prose already contains a named record reference in parentheses.
                markdown = markdown.replace(f"{title} ({named})", named)
    parts = re.split(r"(\[(?:\\.|[^\]\\\n])*\]\([^\s)]+\)|https?://[^\s)]+)", markdown)
    markdown = "".join(part if index % 2 else re.sub(
        r"\bDeal #(\d+)\b", lambda match: markdown_deal(result, deals[int(match[1])])
        if int(match[1]) in deals else "Deal name unavailable", part)
        for index, part in enumerate(parts))
    labels = {}
    titles = {}
    for key, deal in deals.items():
        url = deal_url(result.get("output_domain"), key)
        if not url:
            continue
        title = deal.get("title")
        if isinstance(title, str) and title:
            titles.setdefault(title, set()).add(url)
    lead_titles = {lead.get("title") for lead in result.get("leads") or []}
    labels.update({title: next(iter(urls)) for title, urls in titles.items()
                   if len(urls) == 1 and title not in lead_titles})
    if not labels:
        return markdown
    from pdcore import md_text, md_unescape  # pdcore imports nothing from here at module level
    forms = {}
    for label, url in labels.items():  # prose quotes a title as md_text does (0.7.20), so both spellings are linked
        forms[label] = url
        forms[md_text(label)] = url
    pattern = re.compile("(?<![\\w])(?:" + "|".join(re.escape(label) for label in sorted(forms, key=len, reverse=True)) + ")(?![\\w])")
    # Existing links and URLs are kept intact, so repeated rendering is idempotent.
    parts = re.split(r"(\[(?:\\.|[^\]\\\n])*\]\([^\s)]+\)|https?://[^\s)]+)", markdown)
    return "".join(part if index % 2 else pattern.sub(
        lambda match: "[" + md_text(md_unescape(match.group())) + "](" + forms[match.group()] + ")", part)
        for index, part in enumerate(parts))


def save_company_url(path, value):
    """Update just the company domain, preserving every other shared setting."""
    domain = normalize_domain(value)
    path = Path(path)
    settings = json.loads(path.read_text()) if path.exists() else {}
    updated = deepcopy(settings)
    if updated.get("output") is None:
        updated["output"] = {}
    updated["output"]["domain"] = domain
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix="company-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as handle:
            handle.write(json.dumps(updated, indent=2, sort_keys=True) + "\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    if json.loads(path.read_text()) != updated:
        raise ValueError("Company URL settings read-back did not match")
    return updated


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--company-url", help="Explicitly supplied company URL or subdomain to save")
    args = parser.parse_args()
    path = Path(args.config)
    settings = save_company_url(path, args.company_url) if args.company_url is not None else (
        json.loads(path.read_text()) if path.exists() else {})
    domain = configured_domain(settings)
    print(json.dumps({"domain": domain, "company_url": f"https://{domain}.pipedrive.com" if domain else None,
                      "needs_company_url": domain is None, "persisted": path.exists() and domain is not None,
                      "updated": args.company_url is not None,
                      "path": str(path)}))


if __name__ == "__main__":
    main()
