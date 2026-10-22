"""Verified record labels and checks shared by replies and operator audits."""

from html.parser import HTMLParser
import re

from deal_context import record_ref, markdown_ref, link_not_expected
from pdcore import STAGE_ORDER_LINE, VISIBLE_RECORD_ID_PATTERN, md_unescape, person_name, visible_record_ids  # noqa: F401

LINK = re.compile(r'\[((?:\\.|[^\]\\\n])+)\]\(([^\s)]+)\)')


def link_record_names(text, records, preferred=None):
    """Link each record name in text to that record, leaving existing links alone. A name two records share stays plain,
    unless it is the name of ``preferred``: the deal the line is about, which is the identity the line means."""
    refs = {}
    for record in records:
        if not link_not_expected(record):
            refs.setdefault(record['name'], []).append(record)
    names = sorted((name for name in refs if name != 'Name unavailable'), key=len, reverse=True)
    if not names:
        return str(text)
    pattern = re.compile(r'(?<!\w)(' + '|'.join(re.escape(name) for name in names) + r')(?!\w)')

    def replace(match):
        candidates = refs[match[0]]
        identities = {(entry['type'], str(entry['id'])) for entry in candidates}
        if len(identities) > 1:
            if preferred and preferred.get('name') == match[0] and not link_not_expected(preferred):
                return markdown_ref(preferred)
            return match[0]
        return markdown_ref(candidates[0])
    parts, end = [], 0
    for match in LINK.finditer(str(text)):
        parts.extend((pattern.sub(replace, str(text)[end:match.start()]), match[0]))
        end = match.end()
    parts.append(pattern.sub(replace, str(text)[end:]))
    return ''.join(parts)


def scope_records(config, lens=None):
    scope = config.get('scope') or {}
    owners, pipelines = scope.get('owner_ids') or [], scope.get('pipeline_ids') or []
    lens = lens or {}
    if lens.get('type') in ('owner', 'owner_pipeline'):
        owner = int(str(lens['id']).split(':')[0])
        if owners and owner not in owners:
            raise ValueError('Revenue lens differs from the accepted owner scope')
        owners = [owner]
    if lens.get('type') in ('pipeline', 'owner_pipeline'):
        pipeline = int(str(lens['id']).split(':')[-1])
        if pipelines and pipeline not in pipelines:
            raise ValueError('Revenue lens differs from the accepted pipeline scope')
        pipelines = [pipeline]
    refs = []
    for kind, keys, catalogue in (('user', owners, config.get('owners') or {}),
                                   ('pipeline', pipelines, config.get('pipelines') or {}),
                                   ('pipeline', scope.get('exclude_pipeline_ids') or [], config.get('pipelines') or {})):
        for key in keys:
            metadata = catalogue.get(str(key)) or {}
            ref = record_ref({'id': key, 'name': metadata.get('name') or metadata.get('label')}, kind, config)
            ref['scope'] = True
            if kind == 'pipeline' and key in (scope.get('exclude_pipeline_ids') or []):
                ref['scope_role'] = 'excluded'
            refs.append(ref)
    return refs


def scope_label(refs):
    """The scope sentence from the records whose names are known; '' when scope records exist but none is named."""
    named = [ref for ref in refs if ref.get('name_available') is not False]
    if refs and not named:
        return ''

    def label(ref):
        text = markdown_ref(ref)
        return text if ref.get('url') else text.removesuffix(' · Link unavailable')
    included = [label(ref) for ref in named if ref.get('scope_role') != 'excluded']
    excluded = [label(ref) for ref in named if ref.get('scope_role') == 'excluded']
    text = ' · '.join(included) or 'Accepted business scope'
    if excluded:
        text += '; Excluded: ' + ' · '.join(excluded)
    if any(not ref.get('url') and not link_not_expected(ref) for ref in named):
        text += ' · Link unavailable for scope records without verified URLs'
    return text


def named_scope_records(report, refs, account_context=None):
    """Scope records with a name filled in wherever a source knows one. The saved settings often name no owner or pipeline, so the
    account context supplies them (first), then names the report already carries: its saved scope records, owner map, context users
    and pipeline labels. A record no source names stays unnamed; scope_label leaves it out."""
    account_context = account_context or {}
    owners, pipelines = {}, {}

    def learn(names, key, name):
        if key is not None and name:
            names.setdefault(str(key), person_name(name) if names is owners else name)
    for user in list(account_context.get('users') or []) + [account_context.get('user') or {}]:
        learn(owners, user.get('id'), user.get('name'))
    for pipeline in account_context.get('pipelines') or []:
        learn(pipelines, pipeline.get('id'), pipeline.get('name'))
    for ref in report.get('scope_records') or []:
        if ref.get('name_available') is not False:
            learn(owners if ref.get('type') == 'user' else pipelines, ref.get('id'), ref.get('name'))
    for key, value in (report.get('owners') or {}).items():
        learn(owners, key, value.get('name') if isinstance(value, dict) else value)
    context = report.get('context') or {}
    for user in list(context.get('users') or []) + [context.get('user') or {}]:
        learn(owners, user.get('id'), user.get('name'))
    for key, value in (report.get('pipelines') or {}).items():
        learn(pipelines, key, (value or {}).get('label'))
    named = []
    for ref in refs:
        found = (owners if ref.get('type') == 'user' else pipelines).get(str(ref.get('id')))
        named.append({**ref, 'name': found, 'name_available': True} if ref.get('name_available') is False and found else ref)
    return named


def report_scope_label(report, refs, account_context=None):
    return scope_label(named_scope_records(report, refs, account_context))


def record_refs(value):
    found = {}

    def walk(node):
        if isinstance(node, dict):
            if all(key in node for key in ('type', 'id', 'name', 'url')):
                found[(node['type'], str(node['id']))] = node
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)
    walk(value)
    config = {'output': {'domain': value.get('output_domain')}}
    for section in (value.get('currency_sections') or {}).values():
        for key in ('deals', 'closed_won', 'closed_lost'):
            for deal in section.get(key) or []:
                ref = record_ref(deal, 'deal', config)
                found[('deal', str(ref['id']))] = ref
    return list(found.values())


class VisibleHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.ignored = 0
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.ignored += 1
        if self.ignored:
            return
        if tag == 'a':
            self.hrefs.append(dict(attrs).get('href', ''))
            self.parts.append('[')
        elif tag in ('p', 'div', 'li', 'section', 'h1', 'h2', 'h3', 'tr', 'br'):
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.ignored = max(0, self.ignored - 1)
            return
        if self.ignored:
            return
        if tag == 'a' and self.hrefs:
            self.parts.append('](' + self.hrefs.pop() + ')')
        elif tag in ('p', 'div', 'li', 'section', 'h1', 'h2', 'h3', 'tr'):
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.ignored:
            self.parts.append(data)


def readable(text, html=False):
    if html:
        parser = VisibleHTML()
        parser.feed(text)
        return ''.join(parser.parts)
    return text


def check_text(text, refs, html=False):
    text = readable(text, html)
    parts, spans, end, size = [], [], 0, 0
    for match in LINK.finditer(text):
        before = md_unescape(text[end:match.start()])  # record names are quoted as literal Markdown (md_text, 0.7.20)
        label = md_unescape(match[1])
        parts.extend([before, label])
        size += len(before)
        spans.append((size, size + len(label), label, match[2]))
        size += len(label)
        end = match.end()
    parts.append(md_unescape(text[end:]))
    visible = ''.join(parts)
    identifiers = visible_record_ids(text, [ref['name'] for ref in refs if ref.get('name')])
    checks = [{'criterion': 'visible_record_ids', 'status': 'fail' if identifiers else 'pass',
               'reason': 'Visible record identifiers found.' if identifiers else 'No visible record identifiers found.',
               'evidence': identifiers}]
    names, kinds_by_name = {}, {}
    for ref in refs:
        if ref.get('name') and not link_not_expected(ref):
            names.setdefault(ref['name'], set()).add(ref.get('url'))
            kinds_by_name.setdefault(ref['name'], set()).add(ref.get('type'))
    problems, unknown = [], []
    quotes = [(match.start(), match.end()) for match in re.finditer('“[^”]*”', visible)]
    pipeline_only = {name for name, kinds in kinds_by_name.items() if kinds == {'pipeline'}}
    source_names = {ref.get('name') for ref in refs if ref.get('type') in ('activity', 'note')}
    for name, urls in names.items():
        for mention in re.finditer(r'(?<!\w)' + re.escape(name) + r'(?!\w)', visible):
            if name in source_names and any(start <= mention.start() and mention.end() <= stop for start, stop in quotes):
                continue
            if any(start <= mention.start() and mention.end() <= stop and label != name and label in names
                   for start, stop, label, url in spans):
                continue
            if name in pipeline_only and any(
                    notice.start() <= mention.start() and mention.end() <= notice.end()
                    for notice in re.finditer(re.escape(STAGE_ORDER_LINE.format(name)), visible)):
                continue
            if any(urls):
                linked = any(start <= mention.start() and mention.end() <= stop and label == name and url in urls
                             for start, stop, label, url in spans)
                if not linked:
                    problems.append(name + ': an occurrence is unlinked or links to a different record')
                    break
            else:
                line = visible.splitlines()[visible[:mention.start()].count('\n')]
                if 'link unavailable' not in line.lower():
                    unknown.append(name + ': no verified URL and no explicit link limitation')
                    break
    status = 'fail' if problems else 'not_verified' if unknown else 'pass'
    checks.append({'criterion': 'record_links', 'status': status,
                   'reason': 'Mentioned records use verified links or explicit link limitations.' if status == 'pass'
                   else 'Record-link requirements failed.' if problems else 'Some record links cannot be verified.',
                   'evidence': sorted(set(problems + unknown))})
    return checks
