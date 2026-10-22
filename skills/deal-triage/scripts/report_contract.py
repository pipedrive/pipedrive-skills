"""Saved report paging and presentation constrained by explicit evidence availability."""

from copy import deepcopy
from html import escape
import json
import re

from account_context import priority_fields_line
from deal_context import record_ref, markdown_ref, link_not_expected
from display_time import display_timestamp
from pdcore import EMAIL_HANDOFF, closing_heading, crm_quote, days_ago, visible_record_ids
from brief_priorities import order_brief, supported_touch
from dashboard_charts import money, PROBABILITY_MODE_WORDS
from record_presentation import scope_records, scope_label, named_scope_records


def open_tasks_text(row):
    return ''.join(' · open task: ' + str(task.get('subject') or 'Untitled task') +
                   (', due ' + str(task['due_date']) if task.get('due_date') else '') for task in row.get('open_tasks') or [])


def linked_text(text, report, preferred=None):
    """Link record mentions without guessing an identity for duplicate names; ``preferred`` settles a shared deal title."""
    from record_presentation import record_refs, link_record_names
    text = re.sub(r'\s*\(Activity created\)\s*$', '', str(text))
    return link_record_names(text, record_refs(report), preferred)


def quoted_text(text, report, preferred=None):
    """CRM wording quoted in a reply: record-number and plugin source sentences dropped, record names linked. An activity's
    own subject is not a link target here, or the whole subject would be linked and its deal name left inside the link."""
    from record_presentation import record_refs, link_record_names
    records = [record for record in record_refs(report) if record.get('type') != 'activity']
    return crm_quote(text, lambda value: link_record_names(value, records, preferred))


def activity_text(record, report, preferred=None):
    """An activity named by its subject. A link cannot hold another link, so a subject that names a record is quoted with
    that name linked instead of the whole subject being a link label."""
    name = record['name']
    if preferred and preferred.get('name') and name.endswith(' — ' + preferred['name']) and name[:-len(' — ' + preferred['name'])].strip() and not visible_record_ids(name):
        # "<Type> — <this deal>": the deal is named once, in its link (the same rule the write confirmations use).
        return name[:-len(' — ' + preferred['name'])].strip() + ' on ' + markdown_ref(preferred)
    subject = quoted_text(name, report, preferred)
    if subject == name:
        return markdown_ref(record)
    return '“' + subject + '”' if subject else 'an activity'


LABEL_PAIR = re.compile(r'(?:^|(?<=\s))[A-Z][A-Za-z]*(?: [A-Za-z]+){0,4}: ')


def field_dump(text):
    """A "Label: value" dump: a "Deal summary" note with pairs, or four or more pairs whose values are all short."""
    text = text.strip()
    starts = [match.start() for match in LABEL_PAIR.finditer(text)]
    if len(starts) < 2:
        return False
    if text.startswith('Deal summary'):
        return True
    if len(starts) < 4:
        return False
    values = [text[start:stop].split(': ', 1)[1] for start, stop in zip(starts, starts[1:] + [len(text)])]
    return all(len(value.split()) <= 4 for value in values)


def plugin_or_field_dump(text):
    """Triage lines the plugin wrote, and raw "Label: value" field dumps; neither is customer wording to quote."""
    return bool(re.match(r'^R\d+\s', text.strip()) or re.search(r'\bUser accepted item \d+ on \d{4}-\d{2}-\d{2}', text)
                or field_dump(text))


def useful_observations(context):
    return [item for item in context.get('observations') or []
            if not re.match(r'^Next step on\s', item['text'].strip(), re.I) and not plugin_or_field_dump(item['text'])]


def next_step(row, report):
    context = row.get('context') or {}
    if context.get('description'):
        return linked_text(context['description']['next_step'], report, row.get('record'))
    approval = (context.get('approval') or {}).get('state')
    if approval in ('expected', 'requested'):
        refs = (context.get('approval') or {}).get('evidence_refs') or []
        # An open task still ahead on the deal is the natural place to ask (9 Oct HC-03: the step ignored the open
        # meeting); an overdue one is a different problem, left to the task wording below.
        today = str(report.get('today') or '')
        tasks = [item for item in context.get('observations') or []
                 if item.get('done') is False and item.get('due_date') and item['due_date'] >= today]
        task = min(tasks, key=lambda item: item.get('due_date') or '9999-12-31') if tasks else None
        lead = 'Use ' + activity_text(task['source'], report, row.get('record')) + ' on ' + task['due_date'] + ' to check' if task else 'Check'
        if any(item.get('evidence_ref') in refs and re.search(r'\bbudget\b', item['text'], re.I) for item in context.get('observations') or []):
            return lead + ' whether the buyer has budget approval and what still needs to be decided before moving the deal forward.'
        return lead + ' whether the buyer has the approval described in the note before moving the deal forward.'
    if approval == 'conflicting':
        return 'Confirm which approval note is current before agreeing the next move.'
    tasks = [item for item in context.get('observations') or [] if item.get('done') is False]
    if tasks:
        task = min(tasks, key=lambda item: item.get('due_date') or '9999-12-31')
        due = task.get('due_date')
        action = activity_text(task['source'], report, row.get('record'))
        recorded = ' '.join(item['text'] for item in useful_observations(context)) + ' ' + task['source']['name']
        # A note such as "Deal summary … Pilot required: no" is not a pilot, so pilot and presentation advice reads only
        # task subjects and activity text, and never a "Pilot required: <answer>" field.
        scheduled = re.sub(r'\bpilot required\s*[:=-]\s*\w+', '', ' '.join(
            item['text'] for item in useful_observations(context) if item.get('kind') != 'note') + ' ' + task['source']['name'], flags=re.I)
        preparation = (' Ask whether the legal review is complete and what remains unresolved.' if re.search(r'\blegal\b', recorded, re.I)
                       else ' Confirm the pilot outcome and the next agreed milestone.' if re.search(r'\bpilot\b', scheduled, re.I)
                       else ' Ask what the internal presentation decided and what still needs approval.' if re.search(r'\binternal presentation\b', scheduled, re.I)
                       else '')
        if due and due < str(report.get('today') or ''):
            return 'Confirm the outcome of ' + action + '; if it is unfinished, agree a new date.' + preparation
        return 'Prepare for ' + action + (' on ' + due if due else '; confirm its date with the buyer') + '.' + preparation
    facts = row.get('facts') or {}
    if any(flag.get('id') == 'R5' for flag in facts.get('flags') or []) and any(
            item.get('participants') or item.get('attendees') for item in context.get('observations') or []):
        return 'Check which recorded meeting participants are involved in the decision before asking for another stakeholder.'
    if (facts.get('decision') or {}).get('text'):
        return linked_text(facts['decision']['text'], report, row.get('record'))
    flags = {flag.get('id') for flag in facts.get('flags') or []}
    if 'R4' in flags:
        return 'Confirm the buyer’s timing before changing the expected close date.'
    outcome = context.get('last_activity_outcome') or {}
    if outcome.get('contact_state') == 'attempt_only':
        return 'The last recorded attempt did not establish contact. Review any readable replies before choosing the next channel; an editable email is an option for rep review.'
    if 'R2' in flags:
        return 'Agree the next customer step and schedule it in Pipedrive.'
    if 'R1' in flags:
        return 'Review the last recorded exchange, then ask the buyer what needs to happen next.'
    return 'Check the activity history for the last outcome and the next agreed customer step.'


def claims_approval(text):
    """True when prose asserts approval arrived; questions such as "check whether it arrived" do not count."""
    assertions = re.sub(r'\b(?:ask|check|confirm|find out)\s+(?:whether|if)\b[^.!?]*', '', text, flags=re.I)
    return bool(re.search(r'\b(?:approval (?:has |was )?(?:arrived|received|granted|been received|been granted)|'
                          r'budget (?:is |was |has been )?approved)\b', assertions, re.I))


def describe(report, descriptions):
    """Keep authored context prose separate from calculated facts and write proposals."""
    result = deepcopy(report)
    rows = {row['record']['id']: row for row in result.get('work_rows') or []}
    seen = set()
    for description in descriptions:
        if set(description) != {'record_id', 'summary', 'next_step', 'evidence_refs'}:
            raise ValueError('A description needs a record, summary, next step and evidence references')
        identifier = description['record_id']
        if identifier not in rows or identifier in seen:
            raise ValueError('Describe each saved work record at most once')
        seen.add(identifier)
        context = rows[identifier].get('context') or {}
        anchors = {item['evidence_ref']: item for item in context.get('observations') or []}
        refs = description['evidence_refs']
        if not isinstance(refs, list) or not refs or not all(ref in anchors for ref in refs):
            raise ValueError('Each description must refer to existing evidence for this record')
        evidence = json.dumps([anchors[ref] for ref in refs], ensure_ascii=False)
        evidence += json.dumps(rows[identifier]['facts'], ensure_ascii=False)
        numbers = set(re.findall(r'\d+(?:[.,]\d+)*', evidence))
        for key in ('summary', 'next_step'):
            text = description[key]
            if not isinstance(text, str) or not text.strip() or len(text) > 650 or '\n' in text:
                raise ValueError('Use a short paragraph and one practical next step')
            if set(re.findall(r'\d+(?:[.,]\d+)*', text)) - numbers:
                raise ValueError('Description introduces numbers absent from its saved evidence')
            if re.search(r'https?://|\[[^]]+\]\(', text):
                raise ValueError('Write plain prose; record links are supplied from saved identities')
        if (context.get('approval') or {}).get('state') != 'received' and claims_approval(
                description['summary'] + ' ' + description['next_step']):
            raise ValueError('The saved notes do not establish approval received')
        context['description'] = deepcopy(description)
        rows[identifier]['context'] = context
        for finding in result.get('contextual_findings') or []:
            if finding['record']['id'] == identifier:
                finding['description'] = deepcopy(description)
    result['markdown'] = report_markdown(result)
    return result


def dashboard_allowed(report):
    if report.get('report_schema_version') != 2 or report.get('data_unavailable') or report.get('integrity_stop'):
        return False
    coverage = report.get('scan_coverage') or {}
    return report.get('report_skill') != 'forecast' or coverage.get('complete') is True


def attach(result, config, coverage, findings, account_context=None):
    report = deepcopy(result)
    report['report_schema_version'] = 2
    report['settings_revision'] = config.get('_settings_revision')
    report['scope_fingerprint'] = config.get('_scope_fingerprint')
    report['profile_id'] = config.get('_profile_id')
    report['scan_coverage'] = deepcopy(coverage)
    report['contextual_findings'] = deepcopy(findings)
    for finding in report['contextual_findings']:
        for observation in finding.get('observations') or []:
            observation['display_date'] = display_timestamp(observation.get('date'), config.get('timezone'))
    report['timezone'] = config.get('timezone')
    if not report.get('output_domain'):
        report['output_domain'] = (config.get('output') or {}).get('domain')
    report['timezone_source'] = config.get('timezone_source')
    report['scope_records'] = named_scope_records(report, scope_records(config, result.get('lens')), account_context)
    report['scope_label'] = scope_label(report['scope_records'])
    if report.get('report_skill') == 'forecast':
        explanation_source = result.get('markdown') or ''
        excluded_line = (result.get('excluded_open_deals') or {}).get('line')
        if excluded_line:  # the report prints this line once, in its notices
            explanation_source = explanation_source.replace(excluded_line + '\n', '').replace(excluded_line, '')
        report['forecast_explanation'] = re.sub(r'^Scope:.*$', lambda match: 'Scope: ' + report['scope_label'], explanation_source, flags=re.M) \
            if report['scope_label'] else re.sub(r'^Scope:.*\n?', '', explanation_source, flags=re.M)
    rows = list(report.get('ranked') or report.get('deals') or [])
    existing = {row.get('id') for row in rows}
    if report.get('report_skill') != 'forecast' and not report.get('data_unavailable') and not report.get('integrity_stop'):
        eligible = report.get('deals', [item['facts'] for item in report['contextual_findings'] if item.get('facts')])
        rows.extend(row for row in eligible if row.get('id') not in existing)
    contexts = {item['record']['id']: item for item in report['contextual_findings']}
    for row in rows:
        outcome = (contexts.get(row.get('id')) or {}).get('last_activity_outcome') or {}
        if outcome.get('contact_state') == 'attempt_only' and row.get('last_touch_source') == 'done activity':
            row['last_activity_contact_state'] = 'attempt_only'
    if report.get('report_skill') in ('pipeline-brief', 'deal-triage'):
        rule = (report.get('rule_execution') or {}).get('R1')
        if rule is not None and rows:
            verified = sum(supported_touch(row) for row in rows)
            proxy = sum(bool(row.get('last_touch')) and 'update_time' in str(row.get('last_touch_source')) for row in rows)
            rule.update(verified=verified, proxy=proxy, unavailable=max(rule.get('unavailable', 0), len(rows) - verified - proxy))
    features = {}
    for name, rule, enabled in (('silence', 'R1', config.get('silence_days') is not None),
                                ('next_activity', 'R2', bool(config.get('flag_no_next_activity')))):
        evidence = (report.get('rule_execution') or {}).get(rule, {})
        unavailable = evidence.get('unavailable', 0)
        proxy = evidence.get('proxy', 0) if name == 'silence' else 0
        activity_streams = [stream for stream in (coverage.get('streams') or {}).values() if stream.get('tool') in ('getActivities', 'getActivity')]
        partial_activity = any(not stream.get('complete') for stream in activity_streams)
        if report.get('report_skill') == 'forecast' and name == 'silence' and enabled:
            unavailable = ((result.get('evidence_status') or {}).get('activity') or {}).get('available') is not True
        if name == 'silence' and any((finding.get('segment_rule') or {}).get('status') == 'unknown' for finding in findings):
            unavailable = True
        if report.get('report_skill') == 'forecast' and name == 'next_activity' and enabled:
            unavailable = any('next_activity_id' not in finding.get('facts', {}) for finding in findings)
        features[name] = {'enabled': enabled, 'available': enabled and not unavailable and not proxy and coverage.get('complete') is True,
                          'status': 'disabled' if not enabled else 'unavailable' if unavailable else 'partial' if proxy or partial_activity or not coverage.get('complete') else 'complete'}
        if enabled and evidence:
            features[name]['evidence_counts'] = {key: evidence.get(key, 0) for key in ('verified', 'proxy', 'unavailable')}
        if partial_activity and name == 'silence':
            features[name]['available'] = False
    context_streams = [stream for stream in (coverage.get('streams') or {}).values() if stream.get('tool') in ('getNotes', 'getActivities', 'getActivity')]
    context_partial = bool(coverage.get('optional_field_failures')) or any(not stream.get('complete') for stream in context_streams)
    features['notes_and_activities'] = {'enabled': True, 'available': bool(context_streams) and not context_partial,
                           'status': 'partial' if context_partial else 'complete' if context_streams else 'unavailable'}
    features['worklist'] = {'enabled': True, 'available': not result.get('data_unavailable') and not result.get('integrity_stop'),
                            'status': 'complete' if coverage.get('complete') else 'partial'}
    report['feature_availability'] = features
    report['comparative_ranking_available'] = features['silence']['available']
    order = config.get('priority_order') or []
    def ordering(row):
        matches = (contexts.get(row.get('id')) or {}).get('matched_views') or []
        bucket = min((order.index(key) for key in matches if key in order), default=len(order))
        return bucket, row.get('expected_close_date') or '9999-12-31', str(row.get('title') or '').casefold(), str(row.get('id'))
    if report.get('report_skill') == 'pipeline-brief':
        rows, report['work_sections'], report['ordering_notice'] = order_brief(rows, contexts, {**config, '_call_first': result.get('call_first_deals') or []}, str(report.get('today') or ''))
        report['comparative_ranking_available'] = any(supported_touch(row) for row in rows)
    elif report.get('report_skill') == 'deal-triage':
        rows = sorted(rows, key=lambda row: ordering(row)[0])
        report['ordering_notice'] = 'Accepted priority view order, then the saved risk ranking; proxy-only recency is disclosed separately.'
    elif not features['silence']['available']:
        rows = sorted(rows, key=ordering)
        report['ordering_notice'] = ('Accepted priority-view order, then recorded close date and deal name.' if order
                                     else 'Ordered by recorded close date, then deal name.')
    elif order:
        rows = sorted(rows, key=lambda row: ordering(row)[0])
        report['ordering_notice'] = 'Confirmed priority view order, then the saved published ranking.'
    report['work_rows'] = [{'record': record_ref(row, 'deal', config), 'facts': {**deepcopy(row), **{
                            key: deepcopy((contexts.get(row.get('id')) or {}).get('facts', {}).get(key))
                            for key in ('value', 'currency', 'expected_close_date')
                            if contexts.get(row.get('id'), {}).get('facts') is not None}},
                            'context': contexts.get(row.get('id'))} for row in rows]
    excluded = report.get('excluded_open_deals') if report.get('report_skill') == 'forecast' and not report.get('data_unavailable') else None
    if isinstance(excluded, dict) and 'in_forecast_count' in excluded:
        # A forecast counts only the open deals whose close date falls in its period, so "fetched" and "included" differ.
        report['coverage_line'] = (f"Coverage: {excluded['in_forecast_count']} of {coverage.get('fetched', 0)} open deals in {report.get('period')} · "
                                   f"unresolved {coverage.get('unknown', 0)} · " + ('complete selected scope' if coverage.get('complete') else 'partial selected scope'))
    else:
        report['coverage_line'] = (f"Coverage: fetched {coverage.get('fetched', 0)} · included {coverage.get('included', 0)} · "
                                   f"unresolved {coverage.get('unknown', 0)} · " + ('complete selected scope' if coverage.get('complete') else 'partial selected scope'))
    report['feature_notice'] = ' '.join(FEATURE_SENTENCES[name][state['status']] for name, state in features.items() if name != 'worklist')
    counts = features['silence'].get('evidence_counts')
    if isinstance(excluded, dict) and 'in_forecast_count' in excluded:
        loaded = ('No open deals in the selected scope were loaded.' if not coverage.get('fetched') else
                  'No open deals in the selected scope are in this forecast.' if not excluded['in_forecast_count'] else
                  f"All {coverage.get('fetched', 0)} open deals in the selected scope were loaded; {excluded['in_forecast_count']} have close dates "
                  f"in {report.get('period')} or earlier and are in this forecast.")
    else:
        loaded = ('No open deals in the selected scope were loaded.' if not coverage.get('included') else
                  f"All {coverage.get('included', 0)} open deals in the selected scope were loaded.")
    explanation = [loaded if coverage.get('complete') else
                   'The selected deal list is incomplete. This report covers only the verified included deals.']
    if partial_activity:
        explanation.append('Some activity pages were not fully loaded. Other recorded activities may exist.')
    elif counts and counts['proxy']:
        explanation.append('Deal-update dates do not establish buyer contact.')
    elif features['silence']['status'] == 'unavailable':
        explanation.append('Some inputs needed for the accepted silence check are unverified.')
    if coverage.get('pending_reads'):
        # report --allow-pending (0.7.16 phase 2, ADR-032 note): the planned reads the run never got are named in the report.
        report['pending_reads'] = deepcopy(coverage['pending_reads'])
        named = [read['tool'] + (f" for {len(read['params']['ids'])} ids" if isinstance(read.get('params', {}).get('ids'), list) else '')
                 for read in coverage['pending_reads']]
        explanation.append('Planned reads not made: ' + ', '.join(named) + '. Evidence those reads would give is missing.')
    unverified = sum(not supported_touch(row) for row in rows)
    if features['silence']['enabled'] and not features['silence']['available'] and unverified:
        explanation.append(f"{unverified} of {len(rows)} {'deal has' if len(rows) == 1 else 'deals have'} no verified touch, "
                           + ('so it is' if len(rows) == 1 else 'so they are') + ' ranked without activity-based risk.')
    report['coverage_explanation'] = ' '.join(explanation)
    if counts and counts['proxy']:
        report['feature_notice'] += (' Silence evidence: ' + str(counts['verified']) + ' verified CRM touches, '
                                     + str(counts['proxy']) + ' CRM-update proxies, ' + str(counts['unavailable'])
                                     + ' unknown. CRM updates do not establish buyer contact.')
    if not coverage.get('complete'):
        report['partial_line'] = 'Partial coverage: this report describes fetched records; full-scope ranking and totals are unavailable.'
    return report


BRIEF_SECTION_KEYS = ('opportunities', 'rescue', 'unknown')


def section_rows(report):
    """The rows the brief section lists show (BRIEF_SECTION_ROWS per section), in section order, each row once."""
    if not report.get('work_sections'):
        return []
    rows, seen = report.get('work_rows') or [], set()
    listed = []
    for key in BRIEF_SECTION_KEYS:
        identifiers = report['work_sections'].get(key) or []
        for row in [row for row in rows if row['record']['id'] in identifiers][:BRIEF_SECTION_ROWS]:
            if row['record']['id'] not in seen:
                seen.add(row['record']['id'])
                listed.append(row)
    return listed


def card_rows(report):
    """Brief cards in one order: the section rows first, then the other saved rows in their saved order."""
    if not report.get('work_sections'):
        return list(report.get('work_rows') or [])
    listed = section_rows(report)
    listed_ids = {row['record']['id'] for row in listed}
    return listed + [row for row in report.get('work_rows') or [] if row['record']['id'] not in listed_ids]


def amount_text(value, currency):
    """A recorded amount in the one money format, or Not recorded when the CRM holds no number."""
    if type(value) not in (int, float):
        return 'Not recorded'
    return money(value, currency or '').strip()


LARGEST_DEAL_ROWS = 5


def largest_deals(section):
    """The open deals behind a forecast currency, largest value first; the first LARGEST_DEAL_ROWS are listed in the report."""
    return sorted(section.get('deals') or [], key=lambda deal: (-(deal.get('value') or 0), deal.get('expected_close_date') or '9999-12-31',
                                                               str(deal.get('title') or '')))


def title_detail(report, row):
    """A repeated title gets the deal's value, or else its close date, after the link so two rows with one name read apart."""
    record, facts = row['record'], row['facts']
    same = [other['facts'] for other in report.get('work_rows') or [] if other['record']['name'] == record['name']]
    if len(same) < 2:
        return ''
    if type(facts.get('value')) in (int, float) and sum(other.get('value') == facts['value'] for other in same) == 1:
        return ' · ' + money(facts['value'], facts.get('currency') or '').strip()
    if facts.get('expected_close_date') and sum(other.get('expected_close_date') == facts['expected_close_date'] for other in same) == 1:
        return ' · closes ' + str(facts['expected_close_date'])
    return ''


def card_markdown(report, row):
    facts, context = row['facts'], row.get('context') or {}
    lines = ['## ' + markdown_ref(row['record']) + title_detail(report, row),
             'Recorded value: ' + amount_text(facts.get('value'), facts.get('currency')),
             'Recorded close date: ' + str(facts.get('expected_close_date') or 'Not recorded')]
    lines.extend(risk_lines(facts))
    if report.get('work_sections'):
        lines.append('Why today: ' + why_today(row, report))
    lines.append('Next step: ' + next_step(row, report))
    lines.extend(context_lines(report, context))
    return '\n\n'.join(str(line) for line in lines if line)


def page_proposals(report, rows):
    """The saved proposals of the deals on one page, in item order (0.7.15, decision 2A, ADR-044 note): a page prints its
    own items, so an accept by number works for any page, not only the first."""
    ids = {row['record']['id'] for row in rows}
    return [proposal for proposal in report.get('proposals') or [] if proposal.get('deal_id') in ids]


def page_markdown(report, reply):
    lines = [reply.get('range')] + [card_markdown(report, row) for row in reply['rows']]
    for proposal in page_proposals(report, reply['rows']):
        lines.extend(proposal_lines(report, proposal))
    lines.append(reply['notice'])
    return '\n\n'.join(str(line) for line in lines if line)


def page(report, offset=0, limit=5):
    """One page of the card order. The first page of a brief is exactly the section rows, so every card is a listed row;
    Show more then pages through the rest in saved order."""
    if type(offset) is not int or offset < 0 or type(limit) is not int or limit < 1:
        raise ValueError('Use nonnegative offset and positive page size')
    rows = card_rows(report)
    listed = len(section_rows(report))
    size = listed if offset == 0 and listed else limit
    return {'rows': deepcopy(rows[offset:offset + size]), 'next_offset': offset + size if offset + size < len(rows) else None,
            'snapshot': report.get('scope_fingerprint'), 'total_saved_rows': len(rows)}


def context_markdown(report):
    lines = [report.get('coverage_line') or '', report.get('coverage_explanation') or '',
             report.get('feature_notice') or '', report.get('ordering_notice') or '']
    for row in page(report)['rows']:
        context = row.get('context') or {}
        lines.append('\n' + markdown_ref(row['record']))
        for observation in (context.get('observations') or [])[-3:]:
            text = str(observation['text']).replace('\n', ' ')
            lines.append('- Recorded: ' + text + ' · ' + str(observation.get('date') or 'Date not recorded'))
        for key in context.get('matched_views') or []:
            lines.append('- Included by: ' + str((report.get('view_labels') or {}).get(key) or key))
    for meeting in (report.get('meeting_preparation') or {}).get('meetings') or []:
        lines.append('\nMeeting: ' + markdown_ref(meeting['record']) + ' · ' + str(meeting.get('due_time') or 'Time not recorded'))
        if meeting.get('note'):
            lines.append('- Recorded activity note: ' + meeting['note'].replace('\n', ' '))
    return '\n'.join(line for line in lines if line)


def proposal_lines(report, proposal):
    params = proposal.get('params') or proposal
    context = next((row for row in report.get('work_rows') or [] if row['record']['id'] == proposal.get('deal_id', params.get('deal_id'))), None)
    lines = ['Item ' + str(proposal['item']) + ': ' + str(params.get('subject') or ('Reschedule the existing follow-up' if proposal.get('tool', proposal.get('action')) == 'updateActivity' else 'Review preparation'))]
    if context:
        lines.append('Deal: ' + markdown_ref(context['record']))
    for label, key in (('Type', 'type'), ('Due date', 'due_date'), ('Recorded due time', 'due_time'), ('Preparation', 'note'), ('Text', 'content')):
        if key in params:
            lines.append(label + ': ' + str(params[key]))
    if proposal.get('tool', proposal.get('action')) == 'updateActivity' and any(flag.get('id') == 'R1' for flag in (context or {}).get('facts', {}).get('flags') or []):
        lines.append('Effect: moving this task clears its overdue status only. The deal stays quiet until a completed activity or inbound email is recorded.')
    owner = proposal.get('owner_record') or {}
    contact = proposal.get('person_record') or {}
    lines.append('Owner: ' + (markdown_ref(owner) if owner.get('name_available') else str(proposal['owner_name']) if proposal.get('owner_name') else 'Owner not resolved; confirm before approving'))
    if proposal.get('tool', proposal.get('action')) != 'updateActivity':
        lines.append('Contact: ' + ('No contact linked' if proposal.get('person_id') is None and 'person_id' in proposal else markdown_ref(contact) if contact.get('name_available') else str(proposal['person_name']) if proposal.get('person_name') else 'Contact not resolved; confirm before outreach'))
    if proposal.get('activity_record'):
        lines.append('Existing activity: ' + activity_text(proposal['activity_record'], report, (context or {}).get('record')))
    if proposal.get('text'):
        lines.append('Append text: ' + proposal['text'])
    if proposal.get('status_change') in ('won', 'lost'):
        # R7 (0.7.11): the reply itself says the status change waits for its own yes. HC-06 passed in 0.7.10 only because the
        # chat added that sentence after the checked reply; the script output never said it.
        change = str(proposal['status_change']).upper()
        lines.append(f'Status: the latest note says this deal was {proposal["status_change"]}, but it is still open. Marking it as '
                     f'{change} needs its own confirmation before anything is written; this item only adds the check.')
    state = (report.get('action_states') or {}).get(str(proposal['item']), {})
    lines.append('Execution: ' + str(state.get('status') or 'pending; nothing submitted'))
    if proposal.get('tool', proposal.get('action')) == 'addActivity' and str(params.get('type')).lower() == 'email':
        lines.append(EMAIL_HANDOFF.format(proposal['item']))
    return lines


def context_lines(report, context):
    lines = []
    if context.get('notes_coverage') == 'not_read':
        lines.append('Deal notes were not read for this item; the context below uses the saved activity evidence.')
    elif context.get('notes_coverage') == 'partial':
        lines.append('Deal notes are incomplete for this item; later notes may change the next step.')
    description = context.get('description')
    observations = useful_observations(context)
    outcome = context.get('last_activity_outcome') or {}
    if outcome.get('contact_state') == 'attempt_only':
        lines.append('Latest recorded outcome: ' + str(outcome['label']) + '. This records an attempt, not contact with the buyer. Customer-contact recency remains unverified.')
    if description:
        lines.append(linked_text(description['summary'], report, context.get('record')))
    else:
        notes = [item for item in observations if item['kind'] == 'note']
        completed = [item for item in observations if item.get('done') is True]
        if notes:
            note = notes[-1]
            quote = quoted_text(note['text'].replace('\n', ' '), report, context.get('record'))
            lines.append(('Latest note: "' + quote + '"' if quote else 'Latest note')
                         + ' (' + str(note.get('display_date') or note.get('date') or 'date not recorded') + ').')
        if completed:
            activity = completed[-1]
            quote = quoted_text(activity['text'].replace('\n', ' '), report, context.get('record'))
            scheduled = 'Scheduled ' + activity['due_date'] + '.' if activity.get('due_date') else 'Scheduled date not recorded.'
            lines.append(' '.join(part for part in ['Last completed activity: ' + activity_text(activity['source'], report, context.get('record')) + '.',
                                                    '"' + quote + '"' if quote else '', scheduled] if part))
        open_notes = [item for item in observations if item.get('done') is False]
        if open_notes:
            current = open_notes[-1]
            quote = quoted_text(current['text'].replace('\n', ' '), report, context.get('record'))
            lines.append('Note on an unfinished activity' + (': "' + quote + '"' if quote else '')
                         + '. The task remains unfinished; this note is not a completed-contact timestamp.')
        if not notes and not completed and not open_notes:
            lines.append('The recorded evidence does not explain the latest customer outcome.')
    tasks = [item for item in context.get('observations') or [] if item.get('done') is False]
    if tasks:
        task = min(tasks, key=lambda item: item.get('due_date') or '9999-12-31')
        lines.append('Unfinished task: ' + activity_text(task['source'], report, context.get('record'))
                     + ('; due ' + task['due_date'] if task.get('due_date') else '; due date not recorded') + '.')
    for key in context.get('matched_views') or []:
        lines.append('Included by: ' + str((report.get('view_labels') or {}).get(key) or 'Named view unavailable'))
    if any(item.get('participants') or item.get('attendees') for item in observations):
        lines.append('Meeting participant or guest records are available. Deal-linked contact count may not describe everyone involved; attendance does not establish decision authority.')
    if (context.get('approval') or {}).get('state') == 'conflicting':
        lines.append('The approval notes disagree; confirm which one is current.')
    if context.get('priority_fields'):
        lines.append(priority_fields_line(context['priority_fields']))
    for signal in context.get('engagement') or []:
        lines.append(str(signal.get('field') or 'Engagement field') + ': ' + str(signal.get('value') if signal.get('status') == 'observed' else 'Unknown — ' + signal.get('reason', 'Unavailable')))
    if context.get('segment_rule'):
        segment = context['segment_rule']
        lines.append('Quiet threshold: ' + (str(segment['days']) + ' days · ' + str(segment.get('segment') or 'Default') if segment.get('status') == 'resolved' else 'Unknown — ' + str(segment.get('reason') or 'Disabled')))
    if context.get('suppressed') or context.get('suppression_unknown'):
        lines.append('Outreach unavailable until contact suppression is resolved.')
    return lines


def source_lines(report, context):
    lines = ['CRM record dates leave the time of the buyer’s last reply unverified.']
    for item in [item for item in context.get('observations') or [] if not plugin_or_field_dump(item['text'])][-3:]:
        quote = quoted_text(item['text'].replace('\n', ' '), report, context.get('record'))
        lines.append('Source: ' + activity_text(item['source'], report, context.get('record')) + (' — "' + quote + '"' if quote else ''))
        if item.get('created_at'):
            lines.append('Created: ' + display_timestamp(item['created_at'], report.get('timezone')))
        elif item.get('updated_at'):
            lines.append('Updated: ' + display_timestamp(item['updated_at'], report.get('timezone')))
        elif item.get('date') and item.get('date') != item.get('due_date'):
            lines.append('Recorded date (meaning unverified): ' + display_timestamp(item['date'], report.get('timezone')))
        if item['kind'] == 'activity':
            lines.append('Due: ' + str(item.get('due_date') or 'Not recorded')
                         + '; ' + ('marked complete' if item.get('done') is True else 'unfinished' if item.get('done') is False else 'completion unverified') + '.')
            if item.get('marked_as_done_time'):
                lines.append('Completion logged: ' + display_timestamp(item['marked_as_done_time'], report.get('timezone')))
        if item.get('in_engagement_window') is not None:
            lines.append(('Within' if item['in_engagement_window'] else 'Outside') + ' the accepted engagement window.')
    return lines


def probability_sources(report):
    counts = {}
    labels = {'deal': 'deal-level', 'stage': 'stage-level', 'local': 'accepted local'}
    for section in (report.get('currency_sections') or {}).values():
        for deal in section.get('deals') or []:
            label = labels.get(deal.get('probability_source'), 'other recorded') if deal.get('base_probability') is not None else 'without usable probability'
            counts[label] = counts.get(label, 0) + 1
    return counts


BRIEF_SECTION_ROWS = 3


def brief_sections_markdown(report):
    rows = {row['record']['id']: row for row in report.get('work_rows') or []}
    lines = []
    for key, title in (('opportunities', "Today's opportunities"), ('rescue', 'Deals to rescue'), ('unknown', 'Engagement not established')):
        identifiers = report['work_sections'][key]
        selected = [row for row in report['work_rows'] if row['record']['id'] in identifiers][:BRIEF_SECTION_ROWS]
        lines.append('### ' + title + ' (' + (str(len(identifiers)) if len(identifiers) <= len(selected)
                                              else 'showing ' + str(len(selected)) + ' of ' + str(len(identifiers))) + ')')
        lines.extend('- ' + markdown_ref(row['record']) + title_detail(report, row) + ': ' + why_today(row, report) for row in selected)
        if not identifiers:
            lines.append('No supported records in this group.')
    return '\n'.join(lines)


def why_today(row, report):
    context, facts = row.get('context') or {}, row['facts']
    tasks = [item for item in context.get('observations') or [] if item.get('done') is False and item.get('due_date') == report.get('today')]
    if tasks:
        return 'A recorded next step is due today; check its outcome and prepare the next decision.'
    if (context.get('approval') or {}).get('state') in ('expected', 'requested'):
        return 'Recorded approval is outstanding; confirm it before advancing the deal.'
    if supported_touch(facts):
        return 'Latest supported CRM touch: ' + days_ago(facts['days_silent']) + '; value and accepted priority views inform this position.'
    return 'Engagement is unverified; recorded value and dates do not establish buyer interest.'


def movement_lines(report):
    lines = []
    for status, state in (report.get('movement_coverage') or {}).items():
        movement = ((report.get('aggregates') or {}).get('movement') or {}).get(status) or {}
        if state['status'] == 'not_read':
            lines.append(status.capitalize() + ' movement: not read for this window.')
        else:
            lines.append(status.capitalize() + ': ' + str(movement.get('count') or 0) + ' checked in the report window; ' + state['status'] + ' evidence.')
            for record in (movement.get('items') or [])[:3]:
                lines.append('- ' + markdown_ref(record_ref(record, 'deal', {'output': {'domain': report.get('output_domain')}})))
    return lines


def concentration_html(report):
    from pipedrive_links import html_deal
    risks = report.get('concentration_risks') or []
    return '<ul>' + ''.join('<li>' + html_deal(report, risk) + ' · ' + escape(str(risk.get('reason') or risk.get('risk') or 'Review this deal’s contribution to the estimate.')) + '</li>' for risk in risks) + '</ul>' if risks else '<p>No concentration findings are retained in this snapshot.</p>'


ADJUSTMENT_WORDS = {'not_configured': 'not configured', 'disabled': 'off', 'active': 'active'}


def forecast_labels(report, section=None):
    """The figures per currency, in order: won, the open deals' likely revenue (HC-02), the period total, then the range and target."""
    unweighted = report.get('mode') == 'unweighted'
    projected = (section or {}).get('projected_difference')
    target_label = 'Projected surplus' if projected is not None and projected < 0 else 'Projected shortfall'
    figures = ([('Value including won revenue', 'adjusted_forecast'), ('Open deal value', 'open_value')] if unweighted
               else [('Likely revenue from open deals', 'adjusted_open'), ('Likely revenue this period', 'adjusted_forecast'),
                     ('Weighted forecast', 'weighted_forecast')])
    return [('Won to date', 'won_to_date')] + figures + [('If every included open deal closes', 'best_case'), ('Target', 'target'),
            ('Still to win', 'remaining_to_target'), (target_label, 'projected_difference'), ('Manager commit (entered judgment)', 'manager_commit')]


def forecast_notices(report):
    lines = (['Scope: ' + report['scope_label']] if report.get('scope_label') else []) + ['Probability mode: ' + PROBABILITY_MODE_WORDS.get(report.get('mode'), 'not recorded') + '.']
    if report.get('mode') == 'unweighted':
        lines.append('Usable probabilities are unavailable; these are recorded open values, not a likelihood forecast.')
    sources = probability_sources(report)
    if sources:
        lines.append('Probability sources: ' + ', '.join(str(count) + ' ' + label for label, count in sources.items()) + '.')
    if sources.get('without usable probability'):
        lines.append('Some amounts use recorded values without usable probabilities; review them separately.')
    if (report.get('excluded_open_deals') or {}).get('line'):
        lines.append(report['excluded_open_deals']['line'])
    lines.append('Risk adjustments: ' + ADJUSTMENT_WORDS.get(report.get('adjustment_status'), str(report.get('adjustment_status') or 'Not recorded')))
    if report.get('adjustment_status') == 'disabled':
        lines.append('Previously saved adjustment percentages have no effect because the silence, no-next-activity and passed-close-date rules are disabled. These risk haircuts are not applied to this forecast.')
    return lines


def forecast_intro(report, currency, section):
    from dashboard_charts import money
    estimate = section.get('adjusted_forecast')
    if estimate is None:
        return ''
    amount = money(estimate, currency)
    if report.get('mode') == 'unweighted':
        return 'The recorded revenue total is ' + amount + '. Without usable probabilities, this is not a likelihood estimate.'
    if probability_sources({'currency_sections': {currency: section}}).get('without usable probability'):
        return 'The estimate is ' + amount + '. Some included deals lack usable probabilities; those amounts use recorded values.'
    return 'Based on the chances recorded in Pipedrive, the forecast is ' + amount + ' for ' + str(report.get('period') or 'the selected period') + '.'


def risk_lines(facts):
    flags = facts.get('flags') or []
    supported = [flag for flag in flags if flag.get('id') != 'R1' or supported_touch(facts)]
    lines = ['At risk under your accepted checks.'] if supported else ['Recency needs checking.'] if flags else []
    for flag in flags:
        code = flag.get('id')
        if code == 'R1':
            source = str(flag.get('source') or facts.get('last_touch_source') or 'Source unavailable')
            if 'update_time' in source:
                lines.append('CRM update is ' + str(flag.get('days')) + ' days old against the ' + str(flag.get('threshold'))
                             + '-day threshold. This is a proxy; it does not establish the last buyer contact.')
            elif 'mail' in source:
                # The silence check counts inbound mail, and an inbound mail is the buyer's own message.
                lines.append('The latest inbound email from the buyer was ' + days_ago(flag.get('days'))
                             + ('; your silence check is ' + str(flag['threshold']) + ' days' if flag.get('threshold') is not None else '') + '.')
            else:
                lines.append('The latest recorded CRM touch was ' + days_ago(flag.get('days'))
                             + ('; your silence check is ' + str(flag['threshold']) + ' days' if flag.get('threshold') is not None else '')
                             + '. This activity date does not establish the last buyer reply.')
        elif code == 'R2':
            lines.append('No next activity is recorded.')
        elif code == 'R3':
            lines.append('Recorded stage has not changed for ' + str(flag.get('days')) + ' days; threshold ' + str(flag.get('threshold')) + ' days.')
        elif code == 'R4':
            lines.append('Recorded expected close date has passed by ' + str(flag.get('days')) + ' days.')
        elif code == 'R5':
            lines.append('At most one participant is recorded for this deal; other stakeholders are unverified.')
        elif code == 'R6':
            lines.append('Matches an accepted risk view.')
        elif code == 'R7':
            lines.append('Recorded notes may conflict with the stage; review the source before changing it.')
    return lines


def report_markdown(report):
    lines = ['# ' + report_title(report) + ' — ' + str(report.get('period') or report.get('today') or 'Date unavailable'),
             'Timezone: ' + str(report.get('timezone') or 'Unavailable'), report.get('coverage_line') or '',
             report.get('coverage_explanation') or '', report.get('feature_notice') or '']
    if report.get('partial_line'):
        lines.append(report['partial_line'])
    lines.extend(report.get('stage_order_notices') or [])
    captured = (report.get('scan_coverage') or {}).get('observed_at') or []
    if (report.get('scan_coverage') or {}).get('reused_tool_calls') and captured:
        lines.append('Using saved CRM evidence collected between ' + display_timestamp(captured[0], report.get('timezone'))
                     + ' and ' + display_timestamp(captured[-1], report.get('timezone')) + '. Refresh starts a new scan.')
    if report.get('engagement_window'):
        dates = report['engagement_window']
        lines.append('Engagement window: ' + dates['start_date'] + ' to ' + dates['end_date'] + ' · ' + dates['timezone'] + '. This does not change the forecast period.')
    if report.get('report_skill') != 'forecast' and report.get('scope_label'):
        lines.append('Scope: ' + report['scope_label'])
    if report.get('data_unavailable') or report.get('integrity_stop'):
        lines.append('Required report inputs are unavailable. Review the saved coverage and field failures before refreshing.')
    elif report.get('report_skill') == 'forecast':
        lines.extend(forecast_notices(report))
        for currency, section in (report.get('currency_sections') or {}).items():
            comparison = (report.get('target_comparisons') or {}).get(currency) or {}
            section = {**section, **{key: value for key, value in comparison.items() if key in ('target', 'manager_commit')},
                       **{key: comparison[key] for key in ('remaining_to_target', 'projected_difference') if key in comparison}}
            lines.append('## ' + currency)
            lines.append(forecast_intro(report, currency, section))
            for label, key in forecast_labels(report, section):
                if key in section and section[key] is not None:
                    lines.append(label + ': ' + money(abs(section[key]) if key == 'projected_difference' else section[key], currency))
            deals = largest_deals(section)
            if deals:
                lines.append('### Largest included deals')
            for deal in deals[:LARGEST_DEAL_ROWS]:
                lines.append('- ' + markdown_ref(record_ref(deal, 'deal', {'output': {'domain': report.get('output_domain')}}))
                             + ' · ' + amount_text(deal.get('value'), currency) + ' · Recorded close date: ' + str(deal.get('expected_close_date') or 'Not recorded'))
            if len(deals) > LARGEST_DEAL_ROWS:
                lines.append(str(len(deals) - LARGEST_DEAL_ROWS) + ' more open deals are in the saved file.')
            probabilities = {(deal.get('probability_source'), deal.get('base_probability')) for deal in section.get('deals') or []}
            if len(probabilities) == 1:
                source, probability = next(iter(probabilities))
                if source == 'deal' and isinstance(probability, (int, float)):
                    lines.append('Recorded deal probabilities: ' + format(probability * 100, '.12g') + '% for the included open deals.')
            for caveat in section.get('warnings') or []:
                lines.append('- ' + str(caveat))
        for disclosure in report.get('disclosures') or []:
            lines.append(str(disclosure))
        for event in report.get('calendar_events') or []:
            lines.append(event['event'].capitalize() + ' event: ' + markdown_ref(event['record']) + ' · Recorded timestamp: ' + event['recorded_time']
                         + ' · Local date: ' + event['local_date'] + ' · ' + report['timezone'] + '. This event is included using the accepted local calendar.')
    else:
        lines.append(report.get('ordering_notice') or '')
        if report.get('work_sections'):
            lines.append(brief_sections_markdown(report))
        lines.extend(movement_lines(report))
        for row in page(report)['rows']:
            lines.append(card_markdown(report, row))
        if len(report.get('work_rows') or []) > 5:
            lines.append('More records are available. Show more uses these results; refresh starts a new scan.')
    if report.get('meeting_preparation'):
        meeting_source = report['meeting_preparation']
        lines.append('Meeting coverage: ' + ('complete' if meeting_source.get('complete') else 'partial — ' + str(meeting_source.get('reason') or 'Source incomplete')))
    for meeting in (report.get('meeting_preparation') or {}).get('meetings') or []:
        lines.extend(['## Meeting: ' + markdown_ref(meeting['record']), 'State: ' + meeting['state'],
                      'Recorded time: ' + str(meeting.get('due_time') or 'Not recorded') + ' · ' + meeting['time_basis'],
                      'Recorded note: ' + str(meeting.get('note') or 'Not recorded')])
        if meeting.get('deal'):
            lines.append('Linked deal: ' + markdown_ref(meeting['deal']))
    for row in report.get('lost_work_rows') or []:
        lines.append('Lost work still under review: ' + markdown_ref(row['record']) + ' · Existing status preserved' + open_tasks_text(row))
    # The first reply carries the first page's items and the lead items; every later page prints its own (page_markdown).
    first_page = {row['record']['id'] for row in page(report)['rows']} if report.get('work_rows') else None
    for proposal in report.get('proposals') or []:
        if first_page is not None and proposal.get('deal_id') is not None and proposal['deal_id'] not in first_page:
            continue
        lines.extend(proposal_lines(report, proposal))
    if report.get('report_skill') == 'deal-triage':
        lines.append('Nothing changes in Pipedrive until you approve an item. Rendering this report changes no CRM records.')
    else:
        lines.append('Review exact proposed items in chat before approval. Rendering this report changes no CRM records.')
    return '\n\n'.join(str(line) for line in lines if line)


FEATURE_SENTENCES = {
    'silence': {'disabled': 'Silence check is off.', 'unavailable': 'Silence check could not run; its inputs are unverified.',
                'partial': 'Silence check ran on partial activity data.', 'complete': 'Silence check ran on complete data.'},
    'next_activity': {'disabled': 'Next-activity check is off.', 'unavailable': 'Next-activity check could not run; its inputs are unverified.',
                      'partial': 'Next-activity check ran on partial data.', 'complete': 'Next-activity check ran on complete data.'},
    'notes_and_activities': {'unavailable': 'Notes and activity context is unavailable.', 'partial': 'Notes and activity context is partial.',
                             'complete': 'Notes and activity context is complete.'},
}


def report_title(report):
    if report.get('report_skill') == 'pipeline-brief' and report.get('comparative_ranking_available') is False:
        return "Today's worklist"
    return {'pipeline-brief': "Today's priorities", 'deal-triage': 'Deals needing attention',
            'forecast': 'Revenue forecast'}.get(report.get('report_skill'), 'Sales work')


def chart_sections(report):
    from dashboard_charts import stage_charts, risk_charts, forecast_charts
    if report.get('report_skill') == 'forecast':
        report = {**report, 'display_timezone': report.get('timezone')}
        from forecast_components import _currency_charts
        return ''.join(_currency_charts(currency, section, (report.get('target_comparisons') or {}).get(currency) or {},
                                       not report.get('feature_availability', {}).get('silence', {}).get('available'),
                                       report.get('adjustment_status'), report.get('mode') == 'unweighted', report) + forecast_charts(currency, section, report.get('mode') == 'unweighted', report)
                       for currency, section in (report.get('currency_sections') or {}).items())
    chart = deepcopy(report)
    chart['display_timezone'] = report.get('timezone')
    chart['deals'] = [deepcopy(row['facts']) for row in report.get('work_rows') or []]
    features = report.get('feature_availability') or {}
    for deal in chart['deals']:
        deal['flags'] = [flag for flag in deal.get('flags') or []
                         if (flag.get('id') != 'R1' or features.get('silence', {}).get('enabled') and supported_touch(deal))
                         and (flag.get('id') != 'R2' or features.get('next_activity', {}).get('enabled') and deal.get('next_activity_known', True))]
        source = str(deal.get('last_touch_source') or '')
        if 'update_time' in source or not deal.get('last_activity_scheduled_date'):
            deal['days_silent'] = None
    notice = ('<p class="chart-reason">Rule counts describe supported findings among checked records; incomplete evidence is not a full-scope zero. Disabled checks and proxy-only silence are omitted. '
              'Activity charts exclude deal-update proxies and unknown activity dates; the source tables retain the dated records.</p>')
    return (stage_charts(chart) if report.get('report_skill') == 'pipeline-brief' else '') + notice + risk_charts(chart)


def render_html(report):
    if not dashboard_allowed(report):
        raise ValueError('Required report evidence is unavailable; keep the coverage explanation')
    skill = report.get('report_skill')
    title = report_title(report)
    title += ' — ' + str(report.get('period') or report.get('today') or 'Date unavailable')
    def link(record):
        label = escape(record['name'])
        return f'<a href="{escape(record["url"], quote=True)}" target="_blank" rel="noopener noreferrer">{label}</a>' if record.get('url') else label if link_not_expected(record) else label + ' · Link unavailable'
    body = [f'<section><h2>Coverage</h2><p>{escape(report.get("coverage_line") or "")}</p><p>{escape(report.get("coverage_explanation") or "")}</p><p>{escape(report.get("feature_notice") or "")}</p><p>Timezone: {escape(report.get("timezone") or "Unavailable")}</p></section>']
    from report_export import _inline
    from pipedrive_links import allowed_deal_links
    from dashboard_charts import CSS as chart_css, SCRIPT as chart_script
    allowed = allowed_deal_links(report)
    if report.get('scope_label') and skill != 'forecast':
        body.append('<section><p>Scope: ' + _inline(report['scope_label'], allowed) + '</p></section>')
    if report.get('partial_line'):
        body.append('<section role="status"><p>' + escape(report['partial_line']) + '</p></section>')
    if skill != 'forecast':
        totals = report.get('aggregates') or {}
        metrics = [('Open deals read', totals.get('open_count'))]
        metrics.extend(('Open value · ' + currency, amount_text(value, currency)) for currency, value in (totals.get('open_value_by_currency') or {}).items() if value is not None)
        metrics.append((closing_heading(totals.get('closing_this_week')), (totals.get('closing_this_week') or {}).get('count')))
        figures = ''.join('<article class="metric"><h3>' + escape(label) + '</h3><strong>' + escape(str(value))
                          + '</strong></article>' for label, value in metrics if value is not None)
        if figures:
            body.append('<section aria-label="Pipeline overview"><div class="metric-grid">' + figures + '</div></section>')
    body.append(chart_sections(report))
    if skill == 'forecast':
        body.append('<section>' + ''.join('<p>' + _inline(line, allowed) + '</p>' for line in forecast_notices(report)) + '</section>')
        for event in report.get('calendar_events') or []:
            body.append('<section><p>' + link(event['record']) + ' · ' + escape(event['recorded_time']) + ' · Local date: '
                        + escape(event['local_date']) + ' · ' + escape(report['timezone']) + '</p></section>')
        for currency, section in (report.get('currency_sections') or {}).items():
            comparison = (report.get('target_comparisons') or {}).get(currency) or {}
            section = {**section, **{key: value for key, value in comparison.items() if key in ('target', 'manager_commit')},
                       **{key: comparison[key] for key in ('remaining_to_target', 'projected_difference') if key in comparison}}
            metrics = forecast_labels(report, section)
            figures = ''.join('<article class="metric"><h3>' + label + '</h3><strong>' + escape(money(abs(section[key]) if key == 'projected_difference' else section[key], currency)) + '</strong></article>' for label, key in metrics if key in section and section[key] is not None)
            deals = largest_deals(section)
            rows = ''.join('<tr><td>' + link(record_ref(deal, 'deal', {'output': {'domain': report.get('output_domain')}})) + '</td><td>'
                           + escape(amount_text(deal.get('value'), currency)) + '</td><td>' + escape(str(deal.get('expected_close_date') or 'Not recorded')) + '</td></tr>'
                           for deal in deals[:LARGEST_DEAL_ROWS])
            more = len(deals) - LARGEST_DEAL_ROWS if len(deals) > LARGEST_DEAL_ROWS else 0
            largest = ('<h3>Largest included deals</h3><div style="overflow-x:auto"><table><thead><tr><th>Deal</th><th>Value</th><th>Recorded close date</th></tr></thead><tbody>'
                       + rows + '</tbody></table></div>' + ('<p>' + str(more) + ' more open deals are in the saved file.</p>' if more else '')) if deals else ''
            body.append('<section><h2>' + escape(currency) + '</h2><p>' + escape(forecast_intro(report, currency, section))
                        + '</p><div class="metric-grid">' + figures + '</div>' + largest + '</section>')
        body.append('<section><h2>Concentration risks</h2>' + concentration_html(report) + '</section>')
        if report.get('forecast_explanation'):
            from report_export import markdown_fragment
            explanation = markdown_fragment(report['forecast_explanation'], allowed)
            body.append('<section><details><summary>Calculation, target and risk details</summary>' + explanation + '</details></section>')
    else:
        if report.get('work_sections'):
            from report_export import markdown_fragment
            body.append('<section>' + markdown_fragment(brief_sections_markdown(report), allowed) + '</section>')
        if report.get('movement_coverage'):
            body.append('<section><h2>Recent movement</h2>' + ''.join('<p>' + _inline(line, allowed) + '</p>' for line in movement_lines(report)) + '</section>')
        entries = []
        for index, row in enumerate(report.get('work_rows') or []):
            context = row.get('context') or {}
            observed = ''.join('<p>' + _inline(line, allowed) + '</p>' for line in risk_lines(row['facts']) + context_lines(report, context))
            observed += '<details><summary>Source notes and activity dates</summary>' + ''.join(
                '<p>' + _inline(line, allowed) + '</p>' for line in source_lines(report, context)) + '</details>'
            reasons = ', '.join(str((report.get('view_labels') or {}).get(key) or 'Named view unavailable') for key in context.get('matched_views') or [])
            if row['facts'].get('flags'):
                reasons = ('At risk' if any(flag.get('id') != 'R1' or supported_touch(row['facts']) for flag in row['facts']['flags']) else 'Recency needs checking') + (' · ' + reasons if reasons else '')
            if report.get('work_sections'):
                reasons = why_today(row, report)
            decision = next_step(row, report)
            entries.append('<tr' + (' class="extra" hidden' if index >= 5 else '') + '><td>' + link(row['record']) + escape(title_detail(report, row)) + '</td><td>' + escape(reasons or 'Selected scope') + '</td><td>' + escape(str(row['facts'].get('expected_close_date') or 'Not recorded')) + '</td><td>' + _inline(decision, allowed) + '</td><td><details><summary>Recorded context</summary>' + observed + '</details></td></tr>')
        body.append('<section><h2>Selected work</h2><p>' + escape(report.get('ordering_notice') or '') + '</p><div style="overflow-x:auto"><table id="worklist"><thead><tr><th>Deal</th><th>Why included</th><th>Close date</th><th>Next step</th><th>Details</th></tr></thead><tbody>' + ''.join(entries) + '</tbody></table></div><button type="button" id="more">Show more saved records</button></section>')
    if report.get('meeting_preparation'):
        source = report['meeting_preparation']
        body.append('<section role="status"><p>Meeting coverage: ' + ('complete' if source.get('complete') else 'partial — ' + escape(str(source.get('reason') or 'Source incomplete'))) + '</p></section>')
    for meeting in (report.get('meeting_preparation') or {}).get('meetings') or []:
        body.append('<section><h2>' + link(meeting['record']) + '</h2><p>' + escape(meeting['state']) + ' · ' + escape(str(meeting.get('due_time') or 'Time not recorded')) + ' · ' + escape(meeting['time_basis']) + '</p><p>' + escape(meeting.get('note') or 'Note unavailable') + '</p></section>')
    for row in report.get('lost_work_rows') or []:
        body.append('<section><h2>Lost work still under review</h2><p>' + link(row['record']) + escape(open_tasks_text(row)) + '</p></section>')
    for proposal in report.get('proposals') or []:
        from report_export import _inline
        from pipedrive_links import allowed_deal_links
        body.append('<section><h2>Proposed item ' + escape(str(proposal['item'])) + '</h2>' + ''.join('<p>' + _inline(line, allowed_deal_links(report)) + '</p>' for line in proposal_lines(report, proposal)) + '<p>Review the exact item in chat before approval. Nothing is written from this dashboard.</p></section>')
    script = "document.getElementById('more')?.addEventListener('click',()=>{let n=0;for(const row of document.querySelectorAll('tr.extra[hidden]')){if(n++===5)break;row.hidden=false;}});"
    style = 'body{font:16px/1.5 system-ui;background:#f7faf8;color:#182b22;margin:2rem auto;padding:0 1rem;max-width:1180px}header{background:#014722;color:white;padding:1.5rem;border-radius:14px}section{background:white;padding:1.25rem;margin:1rem 0;border:1px solid #d9e5dd;border-radius:12px;min-width:0}h2,h3{color:#014722}table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:.7rem;border-bottom:1px solid #ddd;overflow-wrap:anywhere}a{color:#017737}button{padding:.7rem 1rem;border:1px solid #014722;background:#FFDE00;border-radius:7px}pre{white-space:pre-wrap}details{margin:.4rem 0}summary{cursor:pointer}#worklist tbody tr{background:#fff}#worklist tbody tr td:first-child{font-weight:700}#worklist td{vertical-align:top}@media(max-width:650px){body{padding:0 .5rem;margin:1rem auto}#worklist thead{display:none}#worklist,#worklist tbody,#worklist tr,#worklist td{display:block}#worklist tr{border:1px solid #d9e5dd;border-radius:10px;margin:.8rem 0;padding:.5rem}#worklist tr[hidden]{display:none}}'
    style += '.charts{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:1rem}.chart-panel,.risk-panel{padding:1rem;background:white;border:1px solid #d9e5dd;border-radius:12px;min-width:0}.chart-row{display:grid;grid-template-columns:minmax(0,1fr);gap:.3rem;margin:.7rem 0}.bar-track{height:18px;background:#edf1ed;border-radius:4px;overflow:hidden}.bar-track i{display:block;height:100%;background:#017737}'
    style += '.metric-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:1rem}.metric{background:#eff9f0;border:1px solid #d9e5dd;border-radius:10px;padding:1rem}.metric h3{font-size:.9rem;margin:0 0 .5rem}.metric strong{font-size:1.8rem;color:#014722}.chart-reason{color:#52665b;padding:0 1rem}.chart-tip a{color:#9fedac}@media(min-width:900px){.chart-row{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:1rem}}'
    return '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>' + escape(title) + '</title><style>' + style + chart_css + '</style></head><body><main><header><h1>' + escape(title) + '</h1><p>' + _inline(report.get('scope_label') or '', allowed) + '</p><p>Timezone: ' + escape(report.get('timezone') or 'Unavailable') + '</p></header>' + ''.join(body) + '</main><script>' + script + '</script>' + chart_script + '</body></html>'
