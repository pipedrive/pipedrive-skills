"""Build and validate rep-facing responses from one saved version-two report."""

from copy import deepcopy
import hashlib
from pathlib import Path

from deal_context import link_not_expected
from record_presentation import scope_records, report_scope_label, record_refs, check_text, readable
import json
import re

from pipedrive_links import link_markdown
from report_contract import claims_approval, report_markdown
from sales_settings import load_effective_profile
from scheduling import propose_job
from version import tester_line
from pipedrive_links import normalize_domain
from pdcore import md_text, md_unescape
from urllib.parse import urlsplit


MAX_OPTIONS = 4  # the host's selectable question tool takes at most four choices per question
MORE_OPTIONS = 'More options'


def follow_up_questions(report, capabilities, answered=None):
    """Return separate pending choices; the host supplies the selectable UI."""
    if report.get('interactive') is False or report.get('mode') == 'scheduled' or report.get('data_unavailable') or report.get('integrity_stop'):
        return []
    if report.get('report_skill') == 'deal-triage' and any(
            (report.get('action_states') or {}).get(str(proposal['item']), {}).get('status', 'pending')
            not in ('written', 'skipped', 'verified') for proposal in report.get('proposals') or []):
        return []
    from artifact_delivery import delivery_options
    answered = answered or {}
    questions = []
    routes = delivery_options(report, capabilities)
    dashboard = bool(routes.get('options'))
    document = capabilities.get('native_document') is True and (
        report.get('report_skill') != 'forecast' or report.get('scan_coverage', {}).get('complete') is True)
    if 'output' not in answered and (dashboard or document):
        # A host question card takes at most MAX_OPTIONS choices (0.7.17 phase 1 check (i): a six-option card failed), so
        # with both routes the card is Dashboard / Claude Doc / Both / More options, and More options opens a second card
        # with Visual artifact / All three / Nothing else (ADR-024 note). With one route the card fits as it is.
        if dashboard and document:
            options = ['Dashboard', 'Claude Doc', 'Both', MORE_OPTIONS]
            text = 'What would you like me to create? More options holds Visual artifact, All three and Nothing else.'
        else:
            options = (['Dashboard', 'Visual artifact'] if dashboard else ['Claude Doc']) + ['Nothing else']
            text = 'What would you like me to create?'
        questions.append({'id': 'output', 'text': text, 'options': options})
    elif answered.get('output') == MORE_OPTIONS and 'output_more' not in answered and dashboard and document:
        questions.append({'id': 'output_more', 'text': 'Which of the other outputs?',
                          'options': ['Visual artifact', 'All three', 'Nothing else']})
    if report.get('report_skill') == 'forecast':
        if report.get('adjustment_status') in ('disabled', 'not_configured') and 'risk_review' not in answered:
            questions.append({'id': 'risk_review', 'text': 'Review how inactive deals affect this estimate?',
                              'options': ['Review the settings', 'Keep the current estimate']})
        sections = report.get('currency_sections') or {}
        comparisons = report.get('target_comparisons') or {}
        target_missing = sections and all(section.get('target') is None and (comparisons.get(currency) or {}).get('target') is None
                                           for currency, section in sections.items())
        if target_missing and 'target' not in answered:
            questions.append({'id': 'target', 'text': 'Compare this with a sales target?',
                              'options': ['Set a target', 'Skip for now']})
    if report.get('report_skill') == 'deal-triage' and 'preparation' not in answered and any(
            (row.get('context') or {}).get('observations') and not (row.get('context') or {}).get('suppressed')
            and not (row.get('context') or {}).get('suppression_unknown') for row in report.get('work_rows') or []):
        questions.append({'id': 'preparation', 'text': 'Would you like help preparing the next customer conversation?',
                          'options': ['Draft an email', 'Prepare call notes', 'Skip for now']})
    return questions


def build_reply(report, settings, mode='brief', local_files=(), unavailable=(), plan=None, question=None, answer=None):
    profile = load_effective_profile(settings, report.get('report_skill'))
    presented = deepcopy(report)
    presented['scope_records'] = scope_records(profile, report.get('lens'))
    refs = record_refs(presented)
    files = []
    for filename in local_files:
        path = Path(filename)
        if path.suffix.lower() not in ('.md', '.html'):
            raise ValueError('Local delivery read-back supports Markdown and HTML')
        data = path.read_bytes()
        if not data.strip():
            raise ValueError('A delivered file cannot be empty')
        text = data.decode('utf-8')
        checks = check_text(text, refs, html=path.suffix.lower() == '.html')
        if any(check['status'] != 'pass' for check in checks):
            raise ValueError('Delivered file fails named-record presentation checks')
        if path.suffix.lower() == '.md' and text.strip() != report_markdown(report).strip():
            raise ValueError('Delivered Markdown differs from the saved computed report')
        visible = readable(text, html=path.suffix.lower() == '.html')
        if report.get('coverage_line') and report['coverage_line'] not in visible:
            raise ValueError('Delivered file omits saved coverage')
        files.append({'path': str(path), 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                      'kind': 'Markdown document' if path.suffix.lower() == '.md' else 'HTML output'})
    return render_reply(report, profile, mode, files, unavailable, plan, question, answer)


def _domain_or_none(value):
    return normalize_domain(value) if value else None


def render_reply(report, profile, mode='brief', files=(), unavailable=(), plan=None, question=None, answer=None):
    """Render from verified delivery metadata; build_reply performs file read-back."""
    if report.get('report_schema_version') != 2 or mode not in ('brief', 'delivery'):
        raise ValueError('Use a version-two report and brief or delivery mode')
    skill = report.get('report_skill')
    if report.get('settings_revision') != profile['_settings_revision']:
        raise ValueError('Report settings revision differs; use the saved report with its accepted settings')
    if report.get('scope_fingerprint') != profile['_scope_fingerprint'] or report.get('profile_id') != profile['_profile_id']:
        raise ValueError('Report profile or scope differs from the accepted settings')
    if _domain_or_none(report.get('output_domain')) != _domain_or_none((profile.get('output') or {}).get('domain')):
        raise ValueError('Report account domain differs from the accepted settings')
    presented = deepcopy(report)
    presented['scope_records'] = scope_records(profile, report.get('lens'))
    presented['scope_label'] = report_scope_label(report, presented['scope_records'])
    primary = report_markdown(presented)
    refs = record_refs(presented)
    if set(unavailable) - {'native_document', 'scheduler'}:
        raise ValueError('Choose native_document or scheduler as an observed unavailable action')
    if plan is not None:
        required = {'mode': 'scheduled', 'read_only': True, 'text_only': True, 'interactive': False,
                    'created': False, 'destination_confirmed': False}
        if any(plan.get(key) != value for key, value in required.items()):
            raise ValueError('Schedule proposal must explicitly preserve execution safeguards and pending review')
        checked = propose_job(profile, plan, {})
        if checked['scope'] != plan.get('scope') or checked['settings_revision'] != plan.get('settings_revision'):
            raise ValueError('Schedule proposal differs from the accepted scope or revision')
    lines = [primary] if mode == 'brief' else ['# Saved output', report.get('coverage_line') or '']
    if mode == 'delivery':
        lines.extend(['Scope: ' + presented['scope_label']] if presented['scope_label'] else [])
    if mode == 'brief':
        count = (report.get('scan_coverage') or {}).get('tool_calls', report.get('tool_calls'))
        if type(count) is int:
            lines.append('Built from ' + str(count) + ' recorded Pipedrive reads. Preparing this reply made no further CRM calls.')
        if question is None and len(report.get('work_rows') or []) > 5:
            more = 'work items' if report.get('comparative_ranking_available') is False else 'priorities'
            lines.append('Would you like more ' + more + '? I can show the remaining rows from these results without another CRM scan. Refresh starts a new scan.')
    for file in files:
        lines.append('Saved a local ' + file['kind'] + ': `' + file['path'] + '`.')
    if 'native_document' in unavailable:
        lines.append('Native Claude Doc creation is unavailable in this session. No native document was created; any file listed here is local.')
    if 'scheduler' in unavailable:
        lines.append('A verified scheduler with durable settings access is unavailable. No recurring job was created.')
    if plan is not None:
        lines.extend(['Schedule proposal for review: ' + ', '.join(plan['days']) + ' at ' + plan['time'] + ' ' + plan['timezone'] + '.',
                      'Use the current report window at run time: ' + plan['window_rule'].replace('_', ' ') + '.',
                      'Destination proposed: ' + plan['destination'] + '. Please confirm the destination before activation.',
                      'Each run is read-only, text-only and noninteractive. It uses the accepted scope and the pinned settings revision. This is a proposed plan; no job was created.'])
    if question is not None:
        allowed = follow_up_questions(report, {'local_html': True, 'native_document': True})
        templates = [entry for entry in allowed if entry['id'] == question.get('id')]
        if not templates or question.get('text') != templates[0]['text'] or not question.get('options') or any(
                option not in templates[0]['options'] for option in question['options']):
            raise ValueError('Use a pending report question with its supported choices')
        lines.append(question['text'] + '\n\n' + '\n'.join(str(index) + '. ' + choice for index, choice in enumerate(question['options'], 1))
                     + '\n\nOr type your own choice.')
    if profile.get('tester_footer') is True and tester_line(report):
        lines.append(tester_line(report))
    result = {'response_schema_version': 1, 'report_skill': skill, 'mode': mode,
              'settings_revision': report['settings_revision'], 'scope_fingerprint': report['scope_fingerprint'],
              'markdown': '\n\n'.join(line for line in ([answer_markdown(report, profile, answer, refs)] if answer is not None else []) + lines if line),
              'record_refs': refs, 'files': files,
              'unavailable': sorted(set(unavailable))}
    if question is not None:
        result['question'] = deepcopy(question)
    checked = validate_reply(result['markdown'], result)
    if not checked['passed']:
        raise ValueError('Computed reply fails presentation checks: ' + ', '.join(checked['failures']))
    return result


ANSWER_POINTS = 6


def answer_evidence(report, profile):
    """What an answer point may cite: recorded notes/activities, a deal's facts, and report-level facts."""
    pool = {}
    for key in ('work_rows', 'lost_work_rows', 'contextual_findings'):
        for row in report.get(key) or []:
            context = row.get('context') or row
            for observation in context.get('observations') or []:
                if observation.get('evidence_ref'):
                    pool[observation['evidence_ref']] = observation
            record = row.get('record') or {}
            if record.get('type', 'deal') == 'deal' and record.get('id') is not None:
                pool[f"deal:{record['id']}:facts"] = {'facts': row.get('facts') or context.get('facts'),
                                                       'engagement': context.get('engagement'),
                                                       'approval': context.get('approval'), 'open_tasks': row.get('open_tasks')}
    pool['report:fields'] = {'custom_fields': profile.get('custom_fields'), 'engagement_fields': profile.get('engagement_fields')}
    pool['report:engagement_window'] = report.get('engagement_window')
    pool['report:meetings'] = report.get('meeting_preparation')
    pool['report:lost_work'] = [{'record': row.get('record'), 'open_tasks': row.get('open_tasks')} for row in report.get('lost_work_rows') or []]
    pool['report:coverage'] = {'line': report.get('coverage_line'), 'scan': report.get('scan_coverage')}
    return {key: value for key, value in pool.items() if value not in (None, [], {})}


def answer_markdown(report, profile, answer, records=()):
    """A short answer to the user's question above the brief; every point rests on cited recorded evidence."""
    question = answer.get('question') if isinstance(answer, dict) else None
    points = answer.get('points') if isinstance(answer, dict) else None
    if not isinstance(question, str) or not question.strip() or '\n' in question or len(question) > 300:
        raise ValueError('The answer needs the user\'s question as one line of at most 300 characters')
    if not isinstance(points, list) or not points or len(points) > ANSWER_POINTS:
        raise ValueError(f'The answer needs 1 to {ANSWER_POINTS} points (at most {ANSWER_POINTS} points)')
    pool = answer_evidence(report, profile)
    lines = ['**Your question:** ' + question.strip()]
    for number, point in enumerate(points, 1):
        text, refs = (point.get('text'), point.get('evidence_refs')) if isinstance(point, dict) else (None, None)
        if (not isinstance(text, str) or not text.strip() or '\n' in text or len(text) > 400
                or re.search(r'https?://|\[[^]]*\]\(', text)):
            raise ValueError(f'Answer point {number} must be plain text on one line, at most 400 characters, without links')
        if not isinstance(refs, list) or not refs:
            raise ValueError(f'Answer point {number} must cite recorded evidence')
        unknown = [ref for ref in refs if ref not in pool]
        if unknown:
            raise ValueError(f'Answer point {number} cites unknown evidence {unknown[0]}')
        evidence = json.dumps([pool[ref] for ref in refs], ensure_ascii=False)
        invented = sorted(set(re.findall(r'\d+(?:[.,]\d+)*', text)) - set(re.findall(r'\d+(?:[.,]\d+)*', evidence)))
        if invented:
            raise ValueError(f'Answer point {number} uses {invented[0]}, which is not in its cited evidence')
        approved = any((item.get('approval') or {}).get('state') == 'received' for item in (pool[ref] for ref in refs) if isinstance(item, dict))
        if claims_approval(text) and not approved:
            raise ValueError(f'Answer point {number} claims approval the evidence does not establish')
        lines.append('- ' + text.strip())
    return link_records(link_markdown('\n'.join(lines), report), records)


LINKED = re.compile(r'(\[(?:\\.|[^\]\\\n])*\]\([^\s)]+\)|https?://[^\s)]+)')


def link_records(markdown, refs):
    """Name every record the reply check knows the way the brief does: linked when it has one link (lost deals
    included), marked "· Link unavailable" on its line when it has none (a deal, person or organization without a URL). Activities,
    notes, users and scope pipelines print as plain text. CW-04, 9 Oct."""
    urls = {}
    for ref in refs:
        if ref.get('name') and not link_not_expected(ref):
            urls.setdefault(ref['name'], set()).add(ref.get('url'))
    linkable = {name: next(iter(found)) for name, found in urls.items() if len(found) == 1 and None not in found}
    unlinkable = {name for name, found in urls.items() if found == {None}}
    lines = []
    # A name appears in prose as md_text quoted it (0.7.20), so both spellings are linked and the label is the quoted one.
    forms = {}
    for name, url in linkable.items():
        forms[name] = url
        forms[md_text(name)] = url
    for line in markdown.split('\n'):
        if linkable:
            pattern = re.compile('(?<![\\w\\[])(?:' + '|'.join(re.escape(name) for name in sorted(forms, key=len, reverse=True)) + ')(?![\\w\\]])')
            parts = LINKED.split(line)
            line = ''.join(part if index % 2 else pattern.sub(
                lambda match: '[' + md_text(md_unescape(match.group())) + '](' + forms[match.group()] + ')', part)
                for index, part in enumerate(parts))
        visible = LINKED.sub('', line)
        if any(re.search(r'(?<!\w)' + re.escape(name) + r'(?!\w)', visible) for name in unlinkable) and 'link unavailable' not in line.lower():
            line += ' · Link unavailable'
        lines.append(line)
    return '\n'.join(lines)


LINK_HOST_SUFFIXES = ('.pipedrive.com',)
LINK_HOSTS = {'claude.ai'}  # the verified doc and dashboard lines


def link_hosts(text, refs):
    """Every link in the reply (a Markdown link or a bare URL) is https and points at a Pipedrive account, at claude.ai, or
    at a host one of the reply's own record links uses (0.7.20, review 2.3): a renamed record cannot carry a link to
    another site into the rep's reply."""
    allowed = {urlsplit(ref['url']).netloc.lower() for ref in refs if isinstance(ref.get('url'), str) and ref['url']} | LINK_HOSTS
    foreign = []
    for match in LINKED.finditer(text):
        destination = match.group(1)
        if destination.startswith('['):
            destination = destination[destination.rfind('](') + 2:-1]
        parsed = urlsplit(destination)
        host = parsed.netloc.lower()
        if parsed.scheme != 'https' or not (host in allowed or host.endswith(LINK_HOST_SUFFIXES)):
            foreign.append(destination)
    return {'criterion': 'link_hosts', 'status': 'fail' if foreign else 'pass',
            'reason': 'A link points outside the reply\'s own record hosts.' if foreign else 'Every link points at a record host.',
            'evidence': sorted(set(foreign))}


def validate_reply(text, response):
    checks = check_text(text, response.get('record_refs') or [])
    checks.append(link_hosts(text, response.get('record_refs') or []))
    exact = text.strip() == response['markdown'].strip()
    if exact:
        reason = 'The proposed reply preserves the rendered response.'
    elif response['markdown'].strip() in text:
        reason = ('The reply adds text to the rendered response. Put explanations for the user in an answer file and '
                  'rebuild with --answer answer.json; send the rebuilt reply unchanged.')
    else:
        reason = ('The reply changes or leaves out the rendered response; keep the brief in the reply. Explanations go '
                  'in --answer answer.json, never in place of the brief or with the brief moved to a file.')
    checks.append({'criterion': 'rendered_response', 'status': 'pass' if exact else 'fail', 'reason': reason, 'evidence': []})
    failures = [check['criterion'] for check in checks if check['status'] != 'pass']
    return {'passed': not failures, 'checks': checks, 'failures': failures}
