"""Verified view preferences and three-state local cohort evaluation."""

from copy import deepcopy
from urllib.parse import parse_qs, urlsplit

from pipedrive_links import normalize_domain

ROLES = ('work_pool', 'priority', 'risk', 'exclusion')


def parse_view_url(url, domain, observed_shapes):
    parsed = urlsplit(url)
    expected_host = normalize_domain(domain) + '.pipedrive.com'
    if parsed.scheme != 'https' or parsed.netloc.lower() != expected_host:
        raise ValueError('Use a selected-filter link from the verified Pipedrive account')
    for shape in observed_shapes:
        if parsed.path.rstrip('/') != shape['path'].rstrip('/'):
            continue
        values = parse_qs(parsed.query).get(shape['query_key']) or []
        if len(values) == 1 and values[0].isdigit() and int(values[0]) > 0:
            kind = shape['record_type']
            if kind not in ('deal', 'activity'):
                raise ValueError('Unsupported filter record type')
            return {'url': url, 'filter_id': int(values[0]), 'record_type': kind,
                    'key': f'{normalize_domain(domain)}:{kind}:{int(values[0])}'}
    raise ValueError('This link does not identify a filter using an observed supported URL shape')


def add_view(state, view):
    for key in ('label', 'meaning', 'rationale', 'url', 'record_type'):
        if not isinstance(view.get(key), str) or not view[key].strip():
            raise ValueError(f'Filter {key} is required; retain the other accepted views')
    if view.get('role') not in ROLES or view.get('verified') is not True or type(view.get('filter_id')) is not int or view['filter_id'] < 1:
        raise ValueError('Confirm filter role and access before accepting it')
    result = deepcopy(state)
    views = result.setdefault('views', {})
    key = view.get('key') or f"{view['record_type']}:{view['filter_id']}"
    views[key] = deepcopy(view)
    result['complete'] = False
    result['next_question'] = {'id': 'another_filter',
                               'text': 'Is there another filter you want to share, or should I work with this one?' if len(views) == 1 else
                                       'Is there another filter you want to share, or should I work with these filters?',
                               'options': ['Add another filter', 'Work with this filter' if len(views) == 1 else 'Work with these filters']}
    return result


def finish_views(state, combination=None, priority_order=None):
    result = deepcopy(state)
    views = result.get('views') or {}
    pool = [key for key, view in views.items() if view['role'] == 'work_pool']
    if len({views[key]['record_type'] for key in pool}) > 1:
        raise ValueError('Keep activity and deal work pools in separate profiles; their set combination is undefined')
    priority = [key for key, view in views.items() if view['role'] == 'priority']
    if len(pool) > 1 and combination not in ('any', 'all'):
        return {**result, 'complete': False, 'next_question': {'id': 'combination', 'text': 'Match any of these work views, or require all of them?'}}
    if len(priority) > 1 and (not isinstance(priority_order, list) or len(priority_order) != len(priority) or set(priority_order) != set(priority)):
        return {**result, 'complete': False, 'next_question': {'id': 'priority_order', 'text': 'Which priority view should I work first?'}}
    result.update({'combination': combination or 'any', 'priority_order': priority_order or priority,
                   'complete': True, 'next_question': None})
    return result


def scope_state(record, scope, status='open'):
    unknown = False
    for field, include, exclude in (('owner_id', scope.get('owner_ids') or [], []),
                                    ('pipeline_id', scope.get('pipeline_ids') or [], scope.get('exclude_pipeline_ids') or []),
                                    ('status', [status] if status else [], [])):
        if not include and not exclude:
            continue
        value = record.get(field)
        if value is None:
            unknown = True
        elif (include and value not in include) or value in exclude:
            return 'no_match'
    return 'unknown' if unknown else 'match'


def membership(identifier, stream):
    if identifier in (stream or {}).get('ids', []):
        return 'match'
    return 'no_match' if (stream or {}).get('complete') is True else 'unknown'


def evaluate(records, profile, memberships, record_type='deal', status='open'):
    roles = profile.get('view_roles') or {}
    pool = roles.get('work_pool') or []
    combination = profile.get('combination', 'any')
    if len(pool) > 1 and 'combination' not in profile or combination not in ('any', 'all'):
        raise ValueError('Confirm work-pool combination before scanning')
    for key in {key for entries in roles.values() for key in entries}:
        if memberships.get(key, {}).get('record_type', record_type) != record_type:
            raise ValueError('Filter record type differs from this cohort')
    result = {'included': [], 'excluded': [], 'unknown': []}
    seen = set()
    for record in records:
        identifier = record.get('id')
        if identifier is None or identifier in seen:
            continue
        seen.add(identifier)
        state = scope_state(record, profile.get('scope') or {}, status)
        states = [membership(identifier, memberships.get(key)) for key in pool]
        if state == 'match' and states:
            if combination == 'any':
                state = 'match' if 'match' in states else 'unknown' if 'unknown' in states else 'no_match'
            else:
                state = 'no_match' if 'no_match' in states else 'unknown' if 'unknown' in states else 'match'
        exclusions = [membership(identifier, memberships.get(key)) for key in roles.get('exclusion', [])]
        if 'match' in exclusions:
            state = 'no_match'
        reasons = [key for entries in roles.values() for key in entries if membership(identifier, memberships.get(key)) == 'match']
        row = {'record': deepcopy(record), 'matched_views': list(dict.fromkeys(reasons)),
               'suppression_unknown': 'unknown' in exclusions}
        result[{'match': 'included', 'no_match': 'excluded', 'unknown': 'unknown'}[state]].append(row)
    return result
