"""Dated CRM facts, field mapping, meeting preparation and grounded draft inputs."""

from copy import deepcopy
from datetime import date, datetime, time, timedelta
import re
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from account_context import deal_field_definitions, priority_gaps, priority_values
from pdcore import collect, person_name, rows, strip_html
from pipedrive_links import configured_domain, deal_url, organization_url, person_url
from read_plan import response, streams, view_memberships, valid_record
from cohorts import scope_state, membership, evaluate


def record_ref(record, kind, config):
    domain = configured_domain(config)
    name = record.get('title') or record.get('subject') or record.get('name') or 'Name unavailable'
    if kind == 'user':
        name = person_name(name)
    if name == 'Name unavailable' and kind == 'note' and record.get('add_time'):
        day = calendar_date(record['add_time'], config.get('timezone') or 'UTC') or str(record['add_time'])[:10]
        try:
            name = 'Note, ' + str(date.fromisoformat(day).day) + ' ' + date.fromisoformat(day).strftime('%b')
        except ValueError:
            pass
    identifier = record.get('id')
    urls = {'deal': deal_url, 'person': person_url, 'organization': organization_url}
    url = urls[kind](domain, identifier) if kind in urls else None
    supplied = (config.get('record_links') or {}).get(f'{kind}:{identifier}')
    if supplied and domain:
        parsed = urlsplit(supplied)
        if parsed.scheme == 'https' and parsed.netloc.lower() == domain + '.pipedrive.com':
            url = supplied
    return {'type': kind, 'id': identifier, 'name': str(name), 'url': url,
            'name_available': name != 'Name unavailable', 'link_available': bool(url)}


def link_not_expected(record):
    """True for a record Pipedrive gives no verified URL here: activities, notes, users, and the pipelines of the report's own scope.
    Their names print as plain text; a deal, person or organization without a URL still carries the link limitation."""
    return not record.get('url') and (record.get('type') in ('activity', 'note', 'user') or record.get('scope') is True)


def markdown_ref(record):
    from pdcore import md_text
    name = md_text(record['name'])
    if record.get('url'):
        return f"[{name}]({record['url']})"
    return name if link_not_expected(record) else name + ' · Link unavailable'


def calendar_date(value, timezone):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            return date.fromisoformat(value).isoformat()
        instant = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if instant.tzinfo is None:
            return None
        return instant.astimezone(ZoneInfo(timezone)).date().isoformat()
    except (ValueError, TypeError):
        return None


def window(kind, today, timezone, days=None, exclude_today=False):
    zone = ZoneInfo(timezone)
    local = date.fromisoformat(str(today)[:10])
    if kind == 'day':
        start, end = local, local
    elif kind == 'rolling':
        if type(days) is not int or days < 1:
            raise ValueError('A rolling window requires positive days')
        end = local - timedelta(days=1) if exclude_today else local
        start = end - timedelta(days=days - 1)
    elif kind == 'month':
        start = local.replace(day=1)
        next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        end = next_month - timedelta(days=1)
    else:
        raise ValueError('Use day, rolling or month window')
    return {'kind': kind, 'timezone': timezone, 'start_date': start.isoformat(), 'end_date': end.isoformat(),
            'start': datetime.combine(start, time.min, zone).isoformat(),
            'end_exclusive': datetime.combine(end + timedelta(days=1), time.min, zone).isoformat()}


def resolve_field(metadata, record_type, label):
    fields = (metadata or {}).get(record_type) or []
    if isinstance(fields, dict):
        fields = [dict(value, code=key) for key, value in fields.items() if isinstance(value, dict)]
    candidates = []
    for field in fields:
        if not isinstance(field, dict):
            continue
        code = field.get('field_code') or field.get('code') or field.get('key')
        name = field.get('field_name') or field.get('name') or field.get('label')
        if isinstance(code, str) and name and str(name).casefold() == str(label).casefold():
            candidates.append({**deepcopy(field), 'code': code, 'name': name})
    if len(candidates) == 1:
        return {'status': 'resolved', 'field': candidates[0]}
    return {'status': 'ambiguous' if candidates else 'unavailable', 'choices': candidates}


def field_value(record, mapping):
    if mapping.get('status') != 'resolved':
        return {'status': 'unknown', 'reason': 'field mapping unresolved'}
    field = mapping['field']
    code = field['code']
    value = record.get(code, (record.get('custom_fields') or {}).get(code))
    if value is None:
        return {'status': 'unknown', 'reason': 'value not returned', 'field': field['name']}
    kind = field.get('field_type') or field.get('type')
    if kind in ('double', 'monetary', 'int', 'numeric') and (type(value) not in (int, float)):
        return {'status': 'unknown', 'reason': 'value type incompatible', 'field': field['name']}
    options = field.get('options')
    if options and kind in ('enum', 'set'):
        values = value if isinstance(value, list) else [value]
        lookup = {str(option['id']): option.get('label') for option in options if isinstance(option, dict) and 'id' in option}
        if not all(str(item) in lookup for item in values):
            return {'status': 'unknown', 'reason': 'option not defined', 'field': field['name']}
        value = [lookup[str(item)] for item in values] if isinstance(value, list) else lookup[str(value)]
    return {'status': 'observed', 'field': field['name'], 'code': code, 'value': deepcopy(value)}


def approval_state(observations):
    states = []
    refs = []
    for observation in observations:
        text = observation['text'].lower()
        state = None
        if re.search(r'\b(waiting|awaiting|pending|not yet|not approved)\b.*\b(approval|approved)\b|\bapproval\b.*\b(pending|needed|required)\b', text):
            state = 'expected'
        elif re.search(r'\b(approval requested|asked for approval|requested approval)\b', text):
            state = 'requested'
        elif re.search(r'\b(budget approved|approval received|approved the budget|approval granted)\b', text):
            state = 'received'
        if state:
            states.append(state)
            refs.append(observation['evidence_ref'])
    state = 'conflicting' if 'received' in states and any(item != 'received' for item in states) else states[-1] if states else 'unclear'
    return {'state': state, 'evidence_refs': refs}


def segment_rules(identifier, config, memberships):
    segments = config.get('segments') or []
    matched = [segment for segment in segments if membership(identifier, memberships.get(segment.get('view_key'))) == 'match']
    if any(membership(identifier, memberships.get(segment.get('view_key'))) == 'unknown' for segment in segments):
        return {'status': 'unknown', 'days': None, 'reason': 'Segment membership is incomplete'}
    order = config.get('segment_precedence') or []
    if len(matched) > 1 and not all(segment.get('key') in order for segment in matched):
        return {'status': 'unknown', 'days': None, 'reason': 'Confirm precedence for overlapping named segments'}
    selected = min(matched, key=lambda segment: order.index(segment['key'])) if len(matched) > 1 else matched[0] if matched else None
    days = selected.get('silence_days') if selected else (config.get('rules') or {}).get('silence', {}).get('days')
    if selected and (type(days) is not int or days < 1):
        return {'status': 'unknown', 'days': None, 'reason': 'Segment threshold is not confirmed'}
    return {'status': 'resolved' if days else 'disabled', 'days': days, 'segment': selected.get('name') if selected else None}


def activity_outcome(activity, config):
    """Interpret returned values only against this account's recorded options."""
    value = activity.get('outcome')
    label = None
    fields = (config.get('custom_fields') or {}).get('activity') or []
    for field in fields if isinstance(fields, list) else []:
        if isinstance(field, dict) and (field.get('field_code') or field.get('code')) == 'outcome':
            label = next((option.get('label') for option in field.get('options') or []
                          if isinstance(option, dict) and str(option.get('id')) == str(value)), None)
    # Some connectors return the readable label rather than the option ID.
    if label is None and isinstance(value, str) and value.casefold() in (
            'connected', 'left voicemail', 'no answer', 'cancelled', 'no show', 'rescheduled', 'completed'):
        label = value
    state = ('attempt_only' if str(label).casefold() in ('left voicemail', 'no answer', 'cancelled', 'no show', 'rescheduled')
             else 'connected' if str(label).casefold() == 'connected' else 'unknown')
    return {'label': label, 'contact_state': state, 'value': deepcopy(value)}


def _order_key(item, today):
    """Done activities by completion, open ones by due date once due, otherwise by creation; notes by their date."""
    if item['kind'] == 'note':
        when = item.get('date')
    elif item.get('done') is True:
        when = item.get('marked_as_done_time') or item.get('created_at')
    elif item.get('due_date') and str(item['due_date'])[:10] <= today:
        when = item['due_date']
    else:
        when = item.get('created_at')
    return str(when or '').replace('T', ' ').rstrip('Z')


def findings(run, records, config, run_directory=None):
    result = []
    note_streams = [stream for stream in streams(run, run_directory).values() if stream['tool'] == 'getNotes']
    for record in records:
        observations = []
        seen = set()
        for index, call in enumerate(run.get('calls') or []):
            tool = (call.get('tool') or '').split('__')[-1]
            if tool not in ('getNotes', 'getActivities', 'getActivity'):
                continue
            body = response(call, run_directory)
            if call.get('error') or call.get('isError') or (isinstance(body, dict) and (body.get('error') or body.get('success') is False)):
                continue
            for item in rows(body):
                if not valid_record(item, tool) or item.get('deal_id') != record.get('id'):
                    continue
                text = strip_html(str(item.get('content') or item.get('note') or '')).strip()
                outcome = activity_outcome(item, config) if tool != 'getNotes' else {}
                if not text and not outcome.get('label'):
                    continue
                kind = 'note' if tool == 'getNotes' else 'activity'
                if (kind, item.get('id')) in seen:
                    continue
                seen.add((kind, item.get('id')))
                recorded_date = item.get('add_time') or item.get('update_time') or item.get('due_date')
                local_date = None
                if recorded_date:
                    try:
                        parsed = datetime.fromisoformat(str(recorded_date).replace('Z', '+00:00'))
                        local_date = parsed.astimezone(ZoneInfo(config['timezone'])).date().isoformat() if parsed.tzinfo and config.get('timezone') else str(recorded_date) if len(str(recorded_date)) == 10 else None
                    except ValueError:
                        pass
                period = config.get('_engagement_window')
                observations.append({'text': text or 'Recorded activity outcome: ' + outcome['label'], 'kind': kind, 'source': record_ref(item, kind, config),
                                     'outcome': outcome, 'activity_type': item.get('type'),
                                     'participants': deepcopy(item.get('participants')), 'attendees': deepcopy(item.get('attendees')),
                                     'date': recorded_date, 'date_meaning': 'CRM note/activity record date; buyer exchange date unverified',
                                     'created_at': item.get('add_time'), 'updated_at': item.get('update_time'),
                                     'due_date': item.get('due_date'), 'due_time': item.get('due_time'),
                                     'done': item.get('done') if kind == 'activity' else None,
                                     'marked_as_done_time': item.get('marked_as_done_time'),
                                     'in_engagement_window': period['start_date'] <= local_date <= period['end_date'] if period and local_date else None,
                                     'observed_at': call.get('observed_at'),
                                     'evidence_ref': f"call:{index}:{kind}:{item.get('id')}"})
        fields = []
        for label in config.get('engagement_fields') or []:
            mapping = resolve_field(config.get('custom_fields') or {}, 'deal', label)
            fields.append(field_value(record, mapping))
        priority = priority_values(record, config.get('priority_fields'), deal_field_definitions(config), config.get('timezone'))
        today = str(run.get('today') or '')[:10]
        observations.sort(key=lambda item: _order_key(item, today))
        latest_id = record.get('last_activity_id')
        latest = next((item for item in observations if item['kind'] == 'activity' and item['source']['id'] == latest_id), None)
        result.append({'record': record_ref(record, 'deal', config), 'facts': deepcopy(record), 'observations': observations,
                       # A notes stream without deal_id is the scope-wide read (0.7.11) and covers every deal; a per-deal stream covers its deal.
                       'notes_coverage': 'complete' if any(stream['params'].get('deal_id') in (None, record['id']) and stream['complete'] for stream in note_streams)
                                         else 'partial' if any(stream['params'].get('deal_id') in (None, record['id']) for stream in note_streams) else 'not_read',
                       'last_activity_outcome': deepcopy((latest or {}).get('outcome') or {}),
                       'approval': approval_state(observations), 'engagement': fields, 'priority_fields': priority,
                       'last_buyer_reply': None, 'user_context': deepcopy(config.get('view_rationale') or {}),
                       'missing_facts': ['Buyer reply/outcome is not established by CRM activity dates'],
                       'suppressed': record.get('do_not_contact') is True or record.get('opt_out') is True})
    priority_gaps(result, config.get('priority_fields'))
    return result


def meeting_prep(run, config, today, run_directory=None):
    dates = window('day', today, config['timezone'])
    normalized = {'calls': [{**call, 'response': response(call, run_directory)} for call in run.get('calls') or []]}
    valid = deepcopy(normalized)
    for call in valid['calls']:
        body = call.get('response')
        if isinstance(body, dict) and isinstance(body.get('data'), (list, dict)):
            kept = [row for row in rows(body) if valid_record(row, (call.get('tool') or '').split('__')[-1])]
            body['data'] = kept if isinstance(body['data'], list) else kept[0] if kept else []
    tables = collect(valid)
    meetings, unknown = [], []
    types = config.get('meeting_types') or []
    memberships = view_memberships(config, normalized)
    roles = {role: [key for key in keys if (config.get('views') or {}).get(key, {}).get('record_type') == 'activity']
             for role, keys in (config.get('view_roles') or {}).items()}
    for activity in tables['activities'].values():
        if activity.get('type') not in types:
            continue
        if not activity.get('due_date'):
            unknown.append(record_ref(activity, 'activity', config))
            continue
        if activity['due_date'] != dates['start_date']:
            continue
        deal = tables['open_deals'].get(activity.get('deal_id')) or tables['lost_deals'].get(activity.get('deal_id')) or tables['won_deals'].get(activity.get('deal_id'))
        scoped = {**activity}
        if deal:
            scoped['pipeline_id'] = deal.get('pipeline_id')
        state = scope_state(scoped, config.get('scope') or {}, status=None)
        if state == 'unknown':
            unknown.append(record_ref(activity, 'activity', config))
            continue
        if state != 'match':
            continue
        selected = evaluate([scoped], {**config, 'view_roles': roles}, memberships, record_type='activity', status=None)
        if selected['unknown']:
            unknown.append(record_ref(activity, 'activity', config))
            continue
        if not selected['included']:
            continue
        meetings.append({'record': record_ref(activity, 'activity', config), 'due_date': activity['due_date'],
                         'due_time': activity.get('due_time'), 'timezone': dates['timezone'],
                         'state': 'completed' if activity.get('done') in (True, 1) else 'upcoming' if activity.get('done') in (False, 0) else 'state unknown',
                         'time_basis': 'unverified recorded clock time',
                         'deal': record_ref(deal, 'deal', config) if deal else None,
                         'note': strip_html(str(activity.get('note') or '')).strip(), 'transcript_available': False,
                         'limitations': ['An activity does not establish a transcript, attendance or cancellation state']})
    meetings.sort(key=lambda item: (item['state'] == 'completed', item['due_time'] or '99:99', item['record']['name']))
    # Per-deal lookups (ids, getActivity) add detail but cannot show every meeting today: the listing streams
    # are the source. A lookup row that failed validation could still hide a meeting.
    sources = [stream for stream in streams(normalized).values() if stream['tool'] == 'getActivities' and not stream['params'].get('ids')]
    dropped = any(len(rows(call['response'])) != len(rows(kept['response'])) for call, kept in zip(normalized['calls'], valid['calls'])
                  if (call.get('tool') or '').split('__')[-1] in ('getActivities', 'getActivity'))
    complete = bool(types and sources) and not unknown and not dropped and all(stream['complete'] for stream in sources)
    return {'meetings': meetings, 'unknown': unknown, 'complete': complete, 'window': dates,
            'reason': None if complete else 'Meeting source/type/scope coverage is incomplete'}


def validate_claims(claims, finding):
    evidence = {item['evidence_ref']: item for item in finding.get('observations') or []}
    for claim in claims:
        if not isinstance(claim.get('text'), str) or not claim['text'].strip():
            raise ValueError('A grounded claim requires nonempty text')
        refs = claim.get('evidence_refs') or []
        if not refs or any(ref not in evidence for ref in refs):
            raise ValueError('Claim references unavailable evidence')
        if claim.get('kind') not in ('observation', 'interpretation'):
            raise ValueError('Label claims as observation or interpretation')
        if claim['kind'] == 'observation' and not any(str(claim.get('text') or '') in evidence[ref]['text'] for ref in refs):
            raise ValueError('Observation must quote saved evidence; rephrasing needs interpretation review')
    return deepcopy(claims)
