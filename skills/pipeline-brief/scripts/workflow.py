"""Run the three sales skills against confirmed profiles and saved MCP evidence."""

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

from account_context import normalize, with_context_stages  # noqa: E402
from cohorts import evaluate, scope_state  # noqa: E402
from deal_context import findings, meeting_prep, segment_rules, record_ref, window, calendar_date  # noqa: E402
from pdcore import collect, person_name, rows, deal_status, run_cost, period_bounds, save_expected_reply, framed  # noqa: E402
import version  # noqa: E402
from read_plan import response, streams, view_memberships, next_reads, covers_scope, covers_read, stream_key, valid_record, reuse_saved_runs  # noqa: E402
from report_contract import attach, report_markdown, page, page_markdown, page_proposals, describe, card_rows  # noqa: E402
from final_response import validate_reply  # noqa: E402
from record_presentation import record_refs  # noqa: E402
from run_capture import PENDING, absorb, issue, plain, prune, stamp, withdraw, report as capture_report  # noqa: E402
from sales_settings import load_effective_profile, confirmed_setup, advance_setup, save_owner_update, save_verified, requested_setup  # noqa: E402


def prepare_scan(run, config, run_directory=None):
    resolved = {**deepcopy(run), 'calls': [{**deepcopy(call), 'response': deepcopy(response(call, run_directory))} for call in run.get('calls') or []]}
    valid = deepcopy(resolved)
    field_failures = []
    optional_field_failures = []
    for call in valid['calls']:
        body = call.get('response')
        if not isinstance(body, dict) or not isinstance(body.get('data'), (list, dict)):
            continue
        records = rows(body)
        kept = [record for record in records if valid_record(record, (call.get('tool') or '').split('__')[-1])]
        if len(kept) != len(records):
            target = field_failures if (call.get('tool') or '').split('__')[-1] in ('getDeals', 'getDeal', 'getStages', 'getStage') else optional_field_failures
            target.append({'tool': call.get('tool'), 'reason': 'Unreadable record identity quarantined'})
        body['data'] = kept if isinstance(body['data'], list) else kept[0] if kept else []
    tables = collect(valid)
    membership = view_memberships(config, resolved)
    deal_roles = {role: [key for key in keys if (config.get('views') or {}).get(key, {}).get('record_type') == 'deal']
                  for role, keys in (config.get('view_roles') or {}).items()}
    deal_profile = {**config, 'view_roles': deal_roles}
    cohort = evaluate(list(tables['open_deals'].values()), deal_profile, membership)
    included = {row['record']['id'] for row in cohort['included']}
    required_filters = {(config.get('views') or {})[key]['filter_id'] for key in deal_roles.get('work_pool', [])}
    coverage_streams = [stream for stream in streams(resolved).values() if stream['tool'] == 'getDeals'
                        and deal_status(stream['params'].get('status', 'open')) == 'open'
                        and covers_scope(stream['params'], config.get('scope') or {})
                        and (stream['params'].get('filter_id') in required_filters if required_filters else stream['params'].get('filter_id') is None)]
    complete = bool(coverage_streams) and all(stream['complete'] for stream in coverage_streams) and not cohort['unknown']
    if required_filters:
        observed = {stream['params'].get('filter_id') for stream in coverage_streams}
        complete = complete and required_filters <= observed
    if config.get('_report_skill') == 'forecast':
        ledger = list(streams(resolved).values())
        for status in ('won', 'lost'):
            closed = [stream for stream in ledger if stream['tool'] == 'getDeals' and deal_status(stream['params'].get('status', 'open')) == status
                      and stream['params'].get('filter_id') is None and not stream['params'].get('ids')
                      and covers_scope(stream['params'], config.get('scope') or {})
                      and not stream['params'].get('cursor') and stream['params'].get('start', 0) in (None, 0)]
            complete = complete and bool(closed) and all(stream['complete'] for stream in closed)
        stage_reads = [stream for stream in ledger if stream['tool'] in ('getStages', 'getStage')]  # saved before 0.7.0 only
        complete = complete and (bool(run.get('context_stages')) or (bool(stage_reads) and all(stream['complete'] for stream in stage_reads)))
        for table in ('open_deals', 'won_deals', 'lost_deals'):
            for record in tables[table].values():
                if scope_state(record, config.get('scope') or {}, status=None) != 'no_match':
                    if type(record.get('value')) not in (int, float) or not isinstance(record.get('currency'), str) or not record['currency'].strip():
                        field_failures.append({'record_id': record['id'], 'reason': 'Readable value and currency are required for revenue totals'})
                    if scope_state(record, config.get('scope') or {}, status=None) == 'unknown':
                        field_failures.append({'record_id': record['id'], 'reason': 'Closed-deal scope is unresolved'})
                    if table in ('won_deals', 'lost_deals'):
                        event = 'won' if table == 'won_deals' else 'lost'
                        if not calendar_date(record.get(event + '_time'), config.get('timezone')):
                            field_failures.append({'record_id': record['id'], 'reason': 'Closed-event date or timezone basis is unresolved'})
    complete = complete and not field_failures and all(not stream['params'].get('ids') for stream in coverage_streams)
    derived = deepcopy(valid)
    derived['calls'] = [call for call in derived['calls'] if call.get('purpose') not in ('probe', 'setup_probe') and not (
        (call.get('params') or {}).get('filter_id') is not None and (call.get('params') or {})['filter_id'] not in required_filters
        and (not isinstance(call.get('response'), dict) or call['response'].get('error') or call['response'].get('success') is False))]
    for call in derived['calls']:
        if call.get('purpose') in ('probe', 'setup_probe'):
            continue
        tool = (call.get('tool') or '').split('__')[-1]
        if tool not in ('getDeals', 'getDeal') or not isinstance(call.get('response'), dict) or call['response'].get('error'):
            continue
        data = rows(call['response'])
        kept = [record for record in data if (record.get('status') == 'open' and record.get('id') in included) or
                (record.get('status') in ('won', 'lost') and scope_state(record, config.get('scope') or {}, record['status']) == 'match')]
        call['response']['data'] = kept if isinstance(call['response'].get('data'), list) else kept[0] if kept else []
        if config.get('_report_skill') == 'forecast':
            for record in kept:
                if record.get('status') in ('won', 'lost'):
                    event = record['status']
                    record['local_' + event + '_date'] = calendar_date(record.get(event + '_time'), config.get('timezone'))
        if config.get('segments'):
            for record in kept:
                segment = segment_rules(record['id'], config, membership)
                record['_silence_days_override'] = segment['days'] if segment['status'] == 'resolved' else None
                record['_segment_rule'] = segment
    coverage = {'complete': complete, 'fetched': len(tables['open_deals']), 'included': len(included),
                'excluded': len(cohort['excluded']), 'unknown': len(cohort['unknown']), 'streams': streams(resolved),
                'tool_calls': len(run.get('calls') or []), 'field_failures': field_failures}
    coverage['reused_tool_calls'] = sum(bool(call.get('reused_from')) for call in run.get('calls') or [])
    coverage['fresh_tool_calls'] = coverage['tool_calls'] - coverage['reused_tool_calls']
    coverage['observed_at'] = sorted({call['observed_at'] for call in run.get('calls') or [] if call.get('observed_at')})
    coverage['optional_field_failures'] = optional_field_failures
    return {'run': derived, 'coverage': coverage, 'cohort': cohort, 'records': [row['record'] for row in cohort['included']]}


def lost_work_records(run, config, run_directory=None):
    tables = collect(run)
    ongoing = {activity.get('deal_id') for activity in tables['activities'].values() if activity.get('done') is False}
    criteria = config.get('lost_work') or {}
    reasons = {value.strip().casefold() for value in criteria.get('lost_reasons') or []}
    view = (config.get('views') or {}).get(criteria.get('view_key'))
    members = set()
    if view:
        for call in run.get('calls') or []:
            params = call.get('params') or {}
            if (call.get('tool') or '').split('__')[-1] == 'getDeals' and deal_status(params.get('status')) == 'lost' and params.get('filter_id') == view['filter_id']:
                members.update(row['id'] for row in rows(response(call, run_directory)) if valid_record(row, 'getDeals') and row.get('status') == 'lost')
    return [record for record in tables['lost_deals'].values() if record.get('id') in ongoing
            and scope_state(record, config.get('scope') or {}, 'lost') == 'match'
            and (not view or record['id'] in members)
            and (not reasons or str(record.get('lost_reason') or '').strip().casefold() in reasons)]


def execute(run, settings, skill, engine_path, today=None, period=None, lens=None, run_directory=None, context=None, selected_ids=None, pending_reads=None):
    context = context or normalize(run.get('account_context') or {})
    if settings.get('schema_version') == 2 and not context.get('available'):
        raise ValueError('Current-session account context is required to validate this profile binding')
    config = load_effective_profile(settings, skill, context if context.get('available') else None)
    if not config.get('_workflow_v2'):
        raise ValueError('Review and migrate setup before using the confirmed-profile workflow')
    current_day = today or run.get('today')
    if not current_day:
        raise ValueError('Provide the intended local report date; replay does not infer a new date')
    engagement = config.get('engagement_window') or {}
    engagement_dates = window('rolling', current_day, config['timezone'], engagement.get('days'), engagement.get('exclude_today', False)) if engagement else None
    config['_engagement_window'] = engagement_dates
    run = with_context_stages(run, context)
    scan = prepare_scan(run, config, run_directory)
    derived = scan['run']
    config = {**config, 'schema_version': 1, '_validated_cohort': True}
    config['filter_rules'] = [{**config['views'][key], 'meaning': config['views'][key].get('meaning') or config['views'][key]['label']}
                              for key in (config.get('view_roles') or {}).get('risk', []) if config['views'][key]['record_type'] == 'deal']
    spec = importlib.util.spec_from_file_location('_profile_compute_' + skill.replace('-', '_'), engine_path)
    engine = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(engine)
    with tempfile.TemporaryDirectory(prefix='pd-profile-') as directory:
        config_path, run_path = Path(directory) / 'config.json', Path(directory) / 'derived-run.json'
        config_path.write_text(json.dumps({**config, 'tester_footer': False}))  # the schema-2 reply adds the footer, once
        run_path.write_text(json.dumps(derived))
        if skill == 'forecast':
            accepted_haircuts = deepcopy((config.get('forecast') or {}).get('haircuts') or {})
            enabled = {'silent': config.get('silence_days') is not None,
                       'no_next': bool(config.get('flag_no_next_activity')),
                       'slipped': config.get('close_slip_slack_days') is not None}
            if 'haircuts' in (config.get('forecast') or {}):
                config['forecast'] = {**config['forecast'], 'haircuts': {key: value for key, value in accepted_haircuts.items() if enabled.get(key)}}
            # Only the calls of an incomplete activity stream are left out (0.7.16 phase 2, ADR-031 note): the 0.7.15 release
            # run (a) had a complete planned read beside a scan that stopped at an unread continuation, and every activity
            # call was dropped, so no deal had recency. An incomplete stream still makes the activity context partial.
            incomplete = {key for key, stream in scan['coverage']['streams'].items()
                          if stream['tool'] in ('getActivities', 'getActivity') and not stream['complete']}
            if incomplete:
                derived['calls'] = [call for call in derived['calls']
                                    if (call.get('tool') or '').split('__')[-1] not in ('getActivities', 'getActivity')
                                    or stream_key((call.get('tool') or '').split('__')[-1], call.get('params') or {}) not in incomplete]
            if not scan['coverage']['complete']:
                derived['calls'] = [call for call in derived['calls'] if (call.get('tool') or '').split('__')[-1] != 'getDeals']
            result = engine.run_forecast(derived, config, period, current_day, lens, run_directory)
            result['report_skill'] = 'forecast'
            result['accepted_adjustment_settings'] = accepted_haircuts
        else:
            args = SimpleNamespace(config=str(config_path), input=str(run_path), today=current_day, budget=None,
                                   pass_no=2, top_n=30, show=5, mode='brief', remaining_context_tokens=None,
                                   deal_ids=selected_ids, include_leads=False)
            result = engine.run_triage(args) if skill == 'deal-triage' else engine.run_brief(args)
    context_records = [record for record in scan['records'] if record['id'] in selected_ids] if selected_ids is not None else scan['records']
    contextual = findings(run, context_records, config, run_directory)
    reasons = {row['record']['id']: row for row in scan['cohort']['included']}
    for finding in contextual:
        finding['matched_views'] = reasons[finding['record']['id']]['matched_views']
        finding['suppression_unknown'] = reasons[finding['record']['id']]['suppression_unknown']
        if config.get('segments'):
            finding['segment_rule'] = segment_rules(finding['record']['id'], config, view_memberships(config, run, run_directory))
    if pending_reads:
        scan['coverage']['pending_reads'] = pending_reads
    report = attach(result, config, scan['coverage'], contextual, context if context.get('available') else None)
    if skill == 'pipeline-brief' and not report.get('integrity_stop'):
        from datetime import date, timedelta
        tables = collect(scan['run'])
        report['movement_coverage'] = {}
        movements = report.setdefault('aggregates', {}).setdefault('movement', {})
        start = report.get('window_start') or (date.fromisoformat(current_day) - timedelta(days=7)).isoformat()
        for status in ('won', 'lost'):
            observed = [stream for stream in scan['coverage']['streams'].values()
                        if stream['tool'] == 'getDeals' and deal_status(stream['params'].get('status')) == status
                        and stream['params'].get('filter_id') is None and not stream['params'].get('ids')
                        and covers_scope(stream['params'], config.get('scope') or {})]
            records = list(tables[status + '_deals'].values())
            readable = [row for row in records if calendar_date(row.get(status + '_time'), config['timezone'])]
            state = 'not_read' if not observed else 'complete' if all(stream['complete'] for stream in observed) and len(readable) == len(records) else 'partial'
            items = [row for row in readable if start <= calendar_date(row[status + '_time'], config['timezone']) <= current_day]
            report['movement_coverage'][status] = {'status': state, 'window_start': start, 'window_end': current_day}
            movements[status] = {'count': len(items) if observed else None, 'items': items, 'value_by_currency': {}}
            for row in items:
                currency = row.get('currency') or 'UNSPECIFIED'
                if type(row.get('value')) in (int, float):
                    movements[status]['value_by_currency'][currency] = movements[status]['value_by_currency'].get(currency, 0) + row['value']
    if skill == 'forecast':
        included = {(kind, row['id']) for section in (report.get('currency_sections') or {}).values()
                    for kind in ('won', 'lost') for row in section.get('closed_' + kind) or []}
        tables = collect(scan['run'])
        report['calendar_events'] = [{'event': kind, 'record': record_ref(row, 'deal', config),
                                      'recorded_time': row[kind + '_time'], 'local_date': row['local_' + kind + '_date']}
                                     for kind in ('won', 'lost') for row in tables[kind + '_deals'].values()
                                     if (kind, row['id']) in included and isinstance(row.get(kind + '_time'), str)
                                     and calendar_date(row[kind + '_time'], 'UTC') != row.get('local_' + kind + '_date')]
    report['engagement_window'] = engagement_dates
    report['view_labels'] = {key: view.get('label') for key, view in (config.get('views') or {}).items()}
    if skill == 'deal-triage':
        from pdwrite import proposal_revision
        tables = collect(scan['run'])
        for proposal in report.get('proposals') or []:
            deal = tables['open_deals'].get(proposal.get('deal_id')) or {}
            activity = tables['activities'].get(proposal.get('activity_id')) or {}
            owner = activity.get('user_id', activity.get('owner_id')) if proposal.get('action') == 'updateActivity' else deal.get('owner_id')
            person = deal.get('person_id')
            person_id = person.get('value', person.get('id')) if isinstance(person, dict) else person
            proposal['owner_id'] = owner
            proposal['owner_name'] = person_name((config.get('owners', {}).get(str(owner)) or {}).get('name'))
            proposal['owner_record'] = record_ref({'id': owner, 'name': proposal['owner_name']}, 'user', config)
            proposal['person_id'] = person_id
            proposal['person_name'] = (tables['persons'].get(person_id) or {}).get('name') or (person.get('name') if isinstance(person, dict) else None)
            proposal['person_record'] = record_ref({'id': person_id, 'name': proposal['person_name']}, 'person', config)
            if activity:
                proposal['activity_record'] = record_ref(activity, 'activity', config)
                proposal['activity_contact'] = {key: deepcopy(activity[key]) for key in ('person_id', 'participants') if key in activity}
            proposal['tool'] = proposal['action']
            proposal['params'] = {'id': proposal['activity_id'], 'due_date': proposal['due_date']} if proposal['action'] == 'updateActivity' else {
                # No user_id: the connector's addActivity has none (its assignee is owner_id, which the whitelist forbids), so
                # the activity is the authenticated user's; pdwrite.prepare still refuses a deal another user owns (0.7.17).
                'deal_id': proposal['deal_id'], 'type': proposal['type'], 'subject': proposal['subject'], 'due_date': proposal['due_date'],
                'participants': [{'person_id': person_id, 'primary': True}] if person_id else []}
            proposal['revision'] = proposal_revision({key: value for key, value in proposal.items() if key != 'revision'})
    if skill == 'pipeline-brief' and config.get('meeting_types'):
        report['meeting_preparation'] = meeting_prep(run, config, current_day, run_directory)
    if skill != 'forecast' and (config.get('lost_work') or {}).get('enabled'):
        report['lost_work_rows'] = findings(run, lost_work_records(scan['run'], config, run_directory), config, run_directory)
        # The open task is why a lost deal is still worked (lost_work_records); show it, not just the deal name.
        activities = collect(scan['run'])['activities'].values()
        for row in report['lost_work_rows']:
            row['open_tasks'] = [{'subject': activity.get('subject'), 'type': activity.get('type'), 'due_date': activity.get('due_date')}
                                 for activity in sorted(activities, key=lambda item: str(item.get('due_date') or ''))
                                 if activity.get('deal_id') == row['record']['id'] and activity.get('done') is False]
    report['markdown'] = report_markdown(report)
    report['tool_calls'] = len(run.get('calls') or [])
    report.update(version.stamp(*run_cost(run), Path(engine_path).resolve().parents[1]))
    return report


def write_new(path, text):
    """Never overwrite a saved output: an existing file keeps its content and this one takes the next free name
    (report-2.json), so a second run in the same folder needs no improvised name (9 Oct live runs)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    number = 1
    while True:
        candidate = path if number == 1 else path.with_name(f'{path.stem}-{number}{path.suffix}')
        try:
            with candidate.open('x', encoding='utf-8') as handle:
                handle.write(text)
            return candidate
        except FileExistsError:
            number += 1


def row_key(row):
    return f"{row['record'].get('type') or 'deal'}:{row['record'].get('id')}"


def shown_cursor(path):
    """The show-more sidecar beside a saved report, keyed by the report's bytes: a regenerated report starts again."""
    cursor_path = path.with_name(path.stem + '.pages.json')
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    saved = json.loads(cursor_path.read_text(encoding='utf-8')) if cursor_path.exists() else {}
    return cursor_path, digest, saved if saved.get('report_sha256') == digest else {}


def record_shown(path, report, markdown):
    """After the checked first reply: remember every saved row the reply gave a card (a heading that opens with the
    deal's link), so show more starts with the first deal the rep has not seen. A one-line mention, such as Kitchen's
    "On the pass" closing list, still leaves the deal's card for show more (0.7.2 live runs)."""
    cursor_path, digest, saved = shown_cursor(path)
    saved_rows = card_rows(report)
    keys = [row_key(row) for row in saved_rows]
    shown = set(saved.get('shown') or keys[:saved.get('served_end', 0)])
    headings = [line.lstrip('#').strip() for line in markdown.splitlines() if line.startswith('#')]
    for row, key in zip(saved_rows, keys):
        url = row['record'].get('url')
        if url and any(heading.startswith('[') and heading.split('](', 1)[-1].startswith(url + ')') for heading in headings):
            shown.add(key)
    cursor_path.write_text(json.dumps({'report_sha256': digest, 'shown': [key for key in keys if key in shown],
                                       'more': any(key not in shown for key in keys)}, ensure_ascii=False, indent=2),
                           encoding='utf-8')


def current_turn(path):
    """The rep's current message, as hooks/mark_turn.py recorded it in .pipedrive-sales/runs/turn.json; None without it."""
    folder = next((parent for parent in path.resolve().parents if parent.name == '.pipedrive-sales'), None)
    try:
        return json.loads((folder / 'runs' / 'turn.json').read_text(encoding='utf-8')).get('turn') if folder else None
    except (OSError, ValueError):
        return None


def serve_page(path, report, offset=None, advance=True):
    """Show more: the next deals of the saved card order the rep has not seen, whether the first reply or an earlier
    page showed them. An explicit offset jumps there, and later pages continue after the jump. With advance False
    (the JSON look-ahead) nothing is marked shown: only a printed page counts (0.7.2 live run: unseen pages were lost).
    A second printed page in the same turn reprints the first one, with or without --offset: one show more is one page
    (0.7.2 live runs: Kitchen followed a page with page --offset in the same turn and lost five deals)."""
    cursor_path, digest, saved = shown_cursor(path)
    saved_rows = card_rows(report)
    keys = [row_key(row) for row in saved_rows]
    shown = set(saved.get('shown') or keys[:saved.get('served_end', 0)])
    turn = current_turn(path) if advance else None
    if turn and saved.get('turn') == turn and saved.get('page'):
        served = saved['page']
        reply = {'rows': deepcopy([row for row, key in zip(saved_rows, keys) if key in served]),
                 'snapshot': report.get('scope_fingerprint'), 'total_saved_rows': len(saved_rows)}
    elif offset is not None:
        reply = page(report, offset)
        shown = set(keys[:offset + len(reply['rows'])])
        served = [row_key(row) for row in reply['rows']]
    else:
        size = len(page(report)['rows']) if not shown else 5
        batch = [row for row, key in zip(saved_rows, keys) if key not in shown][:size]
        served = [row_key(row) for row in batch]
        shown.update(served)
        reply = {'rows': deepcopy(batch), 'snapshot': report.get('scope_fingerprint'), 'total_saved_rows': len(saved_rows)}
    remaining = [index for index, key in enumerate(keys) if key not in shown]
    reply['next_offset'] = remaining[0] if remaining else None
    # Counted in the order the rep saw deals, so a page the rep never saw shows as a gap (0.7.2 live run).
    end, count = len(shown), len(reply['rows'])
    if count:
        reply['range'] = (f'Deals {end - count + 1} to {end} of {len(keys)}' if count > 1
                          else f'One more deal: {end} of {len(keys)}')
    reply['notice'] = ('More deals remain. Say "show more" to see the next ones.' if reply['next_offset'] is not None
                       else 'Nothing remains: every deal in this report has been shown.')
    reply['markdown'] = page_markdown(report, reply)
    # The page's items name their owner and contact, so their refs are part of the page's link check (0.7.15).
    reply['record_refs'] = record_refs({'work_rows': reply['rows'], 'proposals': page_proposals(report, reply['rows']),
                                        'output_domain': report.get('output_domain')})
    checked = validate_reply(reply['markdown'], reply)
    if not checked['passed']:
        raise ValueError('Show more page failed its reply check: ' + ', '.join(checked['failures']))
    if advance:
        # The last 20 printed pages, so a live-run zip shows which runs happened in which turn.
        log = (saved.get('log') or [])[-19:] + [{'turn': turn, 'offset': offset, 'first': served[0] if served else None,
                                                  'count': len(served), 'at': datetime.now(timezone.utc).isoformat(timespec='seconds')}]
        cursor_path.write_text(json.dumps({'report_sha256': digest, 'shown': [key for key in keys if key in shown],
                                           'more': reply['next_offset'] is not None, 'turn': turn, 'page': served,
                                           'log': log}, ensure_ascii=False, indent=2),
                               encoding='utf-8')
    return reply


def uncovered_planned_reads(run_path, run):
    """The reads the last `plan` issued for this run that the run still does not cover (0.7.16 phase 2, ADR-032 note).
    None without a list for this run; [] when every listed read is in the run; otherwise the reads, numbers as text."""
    path = Path(run_path).parent / PENDING
    if not path.exists():
        return None
    try:
        marker = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if marker.get('run') != Path(run_path).name:
        return None
    states = [{**state, 'params': plain(state['params'])} for state in streams(run, Path(run_path).parent).values()]
    uncovered = []
    for read in marker.get('reads') or []:
        try:
            tool, params = json.loads(read['key'])
        except (KeyError, ValueError, TypeError):
            continue
        if not any(covers_read(state, {'tool': tool, 'params': params}) for state in states):
            uncovered.append({'tool': tool, 'params': params})
    return uncovered


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('setup', 'setup-check', 'plan', 'report', 'describe', 'questions', 'page', 'reply', 'check-reply', 'schedule-plan'))
    parser.add_argument('--config')
    parser.add_argument('--input', required=True)
    parser.add_argument('--schema')
    parser.add_argument('--context')
    parser.add_argument('--skill', choices=('pipeline-brief', 'deal-triage', 'forecast'))
    parser.add_argument('--period')
    parser.add_argument('--today')
    parser.add_argument('--lens')
    parser.add_argument('--deal-ids')
    parser.add_argument('--mode', choices=('brief', 'delivery'), default='brief')
    parser.add_argument('--local-file', action='append', default=[])
    parser.add_argument('--unavailable', action='append', choices=('native_document', 'scheduler'), default=[])
    parser.add_argument('--allow-pending', action='store_true',
                        help='report although a planned read is still uncovered; the report then says what was not read')
    parser.add_argument('--schedule-plan')
    parser.add_argument('--capabilities')
    parser.add_argument('--candidate')
    parser.add_argument('--descriptions', help='Evidence-bound short summaries and next steps for saved records')
    parser.add_argument('--answer', help='Evidence-bound answer to a user question the brief does not cover (reply/check-reply)')
    parser.add_argument('--answered', help='Explicit choices already supplied by the user; suppress repeat questions')
    parser.add_argument('--question-fallback', action='store_true', help='Include one pending numbered question in the checked reply when the host has no selectable question UI')
    parser.add_argument('--fresh', action='store_true', help='Bypass cross-run reuse for a requested fresh or scheduled scan')
    parser.add_argument('--cache-max-age-seconds', type=int, default=900)
    parser.add_argument('--output', help='Save report/reply JSON to a new file and print a short receipt')
    parser.add_argument('--markdown-output', help='Save the same reply Markdown alongside --output')
    parser.add_argument('--markdown', action='store_true')
    parser.add_argument('--emit-markdown', action='store_true',
                        help='Print only the candidate after check-reply passes; use as the final handoff')
    parser.add_argument('--offset', type=int, default=None,
                        help='page only: first saved row to show; without it, page continues after the last page served for this report')
    parser.add_argument('--max-calls', type=int, default=20)
    parser.add_argument('--page-size', type=int, default=50)
    parser.add_argument('--max-batch', type=int, default=8)
    parser.add_argument('--remaining-context-tokens', type=int, default=15000)
    args = parser.parse_args()
    if args.emit_markdown and args.action not in ('check-reply', 'page'):
        parser.error('--emit-markdown is supported for check-reply and page only')
    if args.question_fallback and args.action not in ('reply', 'check-reply'):
        parser.error('--question-fallback is supported for reply and check-reply only')
    if args.output and args.action not in ('report', 'describe', 'reply'):
        parser.error('--output is supported for report, describe and reply only')
    if args.markdown_output and (args.action != 'reply' or not args.output):
        parser.error('--markdown-output requires reply with --output')
    destinations = [Path(value).resolve() for value in (args.output, args.markdown_output) if value]
    if len(set(destinations)) != len(destinations):
        parser.error('JSON and Markdown need different output paths')
    if args.action != 'page' and (not args.config or not args.skill):
        parser.error('--config and --skill are required for this action')
    if args.action == 'check-reply' and not args.candidate:
        parser.error('--candidate is required for reply checks')
    if args.action == 'plan' and not args.schema:
        parser.error('--schema is required for the read planner')
    if args.action == 'describe' and not args.descriptions:
        parser.error('--descriptions is required to save authored context')
    if args.action == 'report' and args.skill == 'forecast' and not args.period:
        parser.error('--period is required for Forecast')
    if args.action == 'report' and args.skill == 'forecast':
        try:
            period_bounds(args.period)
        except ValueError as error:
            parser.error(str(error))
    run_path = Path(args.input)
    # Plugin-hook captures of this run's reads (Claude Code, Cowork) join the run before planning or computing (ADR-030).
    captured = absorb(run_path) if args.action in ('plan', 'report') else 0
    run = json.loads(run_path.read_text())
    if args.action == 'page':
        result = serve_page(run_path, run, args.offset, advance=args.emit_markdown)
        if args.emit_markdown:
            save_expected_reply(result['markdown'], 'page', run_path)
            print(framed(result['markdown']))
            return 0
    else:
        settings = json.loads(Path(args.config).read_text()) if Path(args.config).exists() else {}
        context = normalize(json.loads(Path(args.context).read_text())) if args.context else normalize(run.get('account_context') or {})
        if args.action == 'setup-check':
            capabilities = json.loads(Path(args.capabilities).read_text()) if args.capabilities else None
            result = requested_setup(settings, args.skill, run, context, capabilities)
        elif args.action == 'describe':
            if run.get('report_skill') != args.skill:
                parser.error('The saved report must belong to the selected skill')
            result = describe(run, json.loads(Path(args.descriptions).read_text()))
            from final_response import build_reply
            build_reply(result, settings)
        elif args.action == 'questions':
            from final_response import follow_up_questions
            capabilities = json.loads(Path(args.capabilities).read_text()) if args.capabilities else {}
            answered = json.loads(Path(args.answered).read_text()) if args.answered else {}
            if run.get('report_skill') != args.skill:
                parser.error('The saved report must belong to the selected skill')
            result = {'questions': follow_up_questions(run, capabilities, answered),
                      'capability_check_needed': not args.capabilities}
            from pdcore import note_card_asked
            note_card_asked(run_path)
        elif args.action in ('reply', 'check-reply'):
            from final_response import build_reply, validate_reply, follow_up_questions
            if run.get('report_skill') != args.skill:
                parser.error('The saved report must belong to the selected skill')
            proposal = json.loads(Path(args.schedule_plan).read_text()) if args.schedule_plan else None
            question = None
            if args.question_fallback:
                capabilities = json.loads(Path(args.capabilities).read_text()) if args.capabilities else {}
                answered = json.loads(Path(args.answered).read_text()) if args.answered else {}
                questions = follow_up_questions(run, capabilities, answered)
                question = questions[0] if questions else None
            answer = json.loads(Path(args.answer).read_text()) if args.answer else None
            result = build_reply(run, settings, args.mode, args.local_file, args.unavailable, proposal, question, answer)
            if args.action == 'check-reply':
                candidate = Path(args.candidate).read_text(encoding='utf-8')
                result = validate_reply(candidate, result)
                if args.emit_markdown:
                    if result['passed']:
                        record_shown(run_path, run, candidate)
                        save_expected_reply(candidate, 'reply', run_path)
                        # The questions this reply leaves for the rep go through the card in the same turn; the Stop hook
                        # sends the turn back once when it ends without one (0.7.19 phase 2, ADR-024 note).
                        import shlex
                        from pdcore import note_card_due
                        capabilities = json.loads(Path(args.capabilities).read_text()) if args.capabilities else {}
                        answered = json.loads(Path(args.answered).read_text()) if args.answered else {}
                        pending = follow_up_questions(run, capabilities, answered)
                        command = [sys.argv[0], 'questions', '--skill', args.skill, '--input', args.input]
                        if args.capabilities:
                            command += ['--capabilities', args.capabilities]
                        if args.answered:
                            command += ['--answered', args.answered]
                        note_card_due(pending, ' '.join(shlex.quote(str(part)) for part in command), run_path)
                        print(framed(candidate))
                        return 0
                    print(json.dumps(result, ensure_ascii=False, indent=2), file=sys.stderr)
                    return 1
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 0 if result['passed'] else 1
            if args.markdown and not args.output:
                print(result['markdown'])
                return 0
        elif args.action == 'schedule-plan':
            from scheduling import propose_job
            profile = load_effective_profile(settings, args.skill, context if context.get('available') else None)
            capabilities = json.loads(Path(args.capabilities).read_text()) if args.capabilities else {}
            result = propose_job(profile, run, capabilities)
        elif args.action == 'setup':
            state = advance_setup(run, context, args.skill, settings)
            result = state
            if state.get('owner_update'):
                candidate = save_owner_update(settings, args.skill, state['owner_update']['scope'])
                load_effective_profile(candidate, args.skill, context if context.get('available') else None)
                saved, path, persisted = save_verified(candidate, [args.config])
                result = {'next_question': None, 'may_persist': False, 'owner_saved': True, 'settings': saved, 'path': path, 'persisted': persisted}
            elif state['may_persist']:
                candidate = confirmed_setup(settings, run, context, args.skill)
                if args.context:
                    import hashlib
                    import os
                    candidate['context_snapshot'] = {'path': os.path.relpath(Path(args.context).resolve(), Path(args.config).resolve().parent),
                                                     'path_base': 'settings_directory', 'sha256': hashlib.sha256(Path(args.context).read_bytes()).hexdigest()}
                saved, path, persisted = save_verified(candidate, [args.config])
                result = {**state, 'settings': saved, 'path': path, 'persisted': persisted}
        elif args.action == 'plan':
            config = load_effective_profile(settings, args.skill, context if context.get('available') else None)
            config['_remaining_context_tokens'] = args.remaining_context_tokens
            config['_context_stages'] = bool(context.get('available') and context.get('stages'))
            if config['_context_stages']:
                config['_account_context'] = context
            if args.deal_ids:
                config['_candidate_ids'] = [int(value) for value in args.deal_ids.split(',')]
            schema = json.loads(Path(args.schema).read_text())
            binding = config.get('binding')
            if run.get('binding') and run['binding'] != binding:
                parser.error('The saved evidence belongs to a different account or user')
            if not run.get('calls') and binding:
                run['binding'] = deepcopy(binding)
            if not run.get('calls') and not run.get('started_at'):
                stamp(run)
                prune(run_path.parent / 'captured')
            if not args.fresh:
                run = reuse_saved_runs(config, run, schema, run_path.parent,
                                       max_age_seconds=args.cache_max_age_seconds, page_size=args.page_size)
            with run_path.open('w', encoding='utf-8') as handle:
                handle.write(json.dumps(run, ensure_ascii=False, indent=2) + '\n')
            result = next_reads(config, run, schema, args.max_calls, args.page_size, run_path.parent, args.max_batch)
            # Planned reads come back to Claude as receipts; the capture hook keeps the full response (ADR-032).
            # Only PIPEDRIVE_SALES_RECEIPTS=off, read by the hook from Claude Code's own environment, turns them off:
            # a switch Claude can reach gets used (9 Oct live runs).
            if result.get('next_reads'):
                issue(run_path, result['next_reads'], run.get('capture_session_id'))
            else:
                withdraw(run_path)
            result['reused_calls'] = sum(bool(call.get('reused_from')) for call in run.get('calls') or [])
            result['captured_calls'] = captured
            if captured:
                result['capture_note'] = capture_report(captured)
        else:
            pending = uncovered_planned_reads(run_path, run)
            if pending and not args.allow_pending:
                # The 0.7.15 release run (a) reported with the planned per-deal activity read never made (ADR-032 note).
                print(json.dumps({'error': 'planned reads pending', 'pending_reads': pending,
                                  'next_step': 'Make these reads, run `plan` again, then `report`. Only when the host refused a read, '
                                               'run `report --allow-pending`: the report then says what was not read.'}, ensure_ascii=False), file=sys.stderr)
                return 2
            if pending is not None and not pending:
                withdraw(run_path)
            result = execute(run, settings, args.skill, Path(__file__).with_name('compute.py'), args.today, args.period,
                             json.loads(args.lens) if args.lens else None, run_path.parent, context,
                             [int(value) for value in args.deal_ids.split(',')] if args.deal_ids else None,
                             pending_reads=pending or None)
    if args.output:
        output = write_new(Path(args.output), json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        receipt = {'path': str(output.resolve()), 'report_skill': args.skill,
                   'coverage': result.get('scan_coverage')}
        if args.markdown_output:
            receipt['markdown_path'] = str(write_new(Path(args.markdown_output), result['markdown']).resolve())
        if output != Path(args.output):
            print(f'{args.output} already exists; saved as {output}', file=sys.stderr)
        print(result['markdown'] if args.markdown else json.dumps(receipt, ensure_ascii=False))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
