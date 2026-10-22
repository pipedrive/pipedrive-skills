"""Bounded supported MCP read plans and independent stream coverage."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from account_context import PRIORITY_FIELD_CAP, resolve_scope, scope_gap
from pdcore import load_response_file, rows, deal_status
from cohorts import scope_state

DEAL_EVIDENCE_FIELDS = ('next_activity_id', 'last_activity_id', 'activities_count',
                        'done_activities_count', 'undone_activities_count', 'email_messages_count',
                        'last_incoming_mail_time', 'last_outgoing_mail_time', 'participants_count', 'notes_count')
LIST_TOOLS = {'getDeals', 'getActivities', 'getNotes', 'getLeads', 'getPersons', 'getOrganizations'}
# Notes are read once for the whole scope, newest first, this many days back (0.7.11): one paged getNotes instead of one
# call per candidate, so the notes checks (R7, buying signals) cover every open deal in scope, not the top five.
NOTES_WINDOW_DAYS = 90
NOTES_PAGE = 50


def response(call, run_directory=None):
    if call.get('response') is not None:
        return call['response']
    if call.get('response_file'):
        path = Path(call['response_file'])
        if not path.is_absolute() and run_directory:
            path = Path(run_directory) / path
        return load_response_file(path)
    return None


def stream_key(tool, params):
    selector = {key: value for key, value in params.items() if key not in ('cursor', 'start', 'limit')}
    if tool == 'getDeals':
        selector['status'] = deal_status(selector.get('status', 'open'))
    for key in ('ids', 'include_fields', 'custom_fields'):
        if isinstance(selector.get(key), list):
            selector[key] = sorted({str(value) for value in selector[key]})
    return tool + ':' + hashlib.sha256(json.dumps(selector, sort_keys=True).encode()).hexdigest()[:20]


def covers_read(state, read):
    if state['tool'] != read['tool'] or not state.get('complete'):
        return False
    observed = {key: value for key, value in state['params'].items() if key not in ('limit', 'cursor', 'start')}
    wanted = {key: value for key, value in read['params'].items() if key not in ('limit', 'cursor', 'start')}
    if state['tool'] == 'getDeals':
        observed['status'] = deal_status(observed.get('status', 'open'))
        wanted['status'] = deal_status(wanted.get('status', 'open'))
    for key in set(observed) | set(wanted):
        if key in ('include_fields', 'custom_fields', 'ids'):
            if key == 'ids' and key not in wanted:
                return False
            if key == 'ids' and key not in observed:
                if any(selector in observed for selector in ('owner_id', 'pipeline_id', 'deal_id', 'filter_id', 'done', 'type')):
                    return False
                continue
            if not {str(value) for value in wanted.get(key, [])} <= {str(value) for value in observed.get(key, [])}:
                return False
        elif key in ('owner_id', 'pipeline_id') and key not in observed:
            continue
        elif observed.get(key) != wanted.get(key):
            return False
    return True


def observation_time(value):
    if not isinstance(value, str):
        return None
    try:
        instant = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return instant.astimezone(timezone.utc) if instant.tzinfo else None
    except ValueError:
        return None


def reuse_saved_runs(profile, run, schema, directory, now=None, max_age_seconds=900, page_size=50):
    """Reuse original complete streams from this account within the explicit age limit."""
    from pdcore import WRITE_TOOLS
    now = now or datetime.now(timezone.utc)
    if type(max_age_seconds) is not int or max_age_seconds < 0:
        raise ValueError('Cache age must be a nonnegative number of seconds')
    result = deepcopy(run)
    binding = profile.get('binding')
    if not binding or result.get('binding') != binding or max_age_seconds == 0:
        return result
    candidates, mutations = [], []
    for path in sorted(Path(directory).glob('*.json')):
        try:
            previous = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(previous, dict) or previous.get('binding') != binding:
            continue
        retained = (path.parent / 'responses').resolve()
        unsafe = False
        for call in previous.get('calls') or []:
            if call.get('response_file') and call.get('response') is None:
                source = Path(call['response_file'])
                source = source if source.is_absolute() else path.parent / source
                if source.suffix != '.json' or not source.resolve().is_relative_to(retained) or not source.is_file():
                    unsafe = True
                    break
        if unsafe:
            continue
        for call in previous.get('calls') or []:
            if (call.get('tool') or '').split('__')[-1] in WRITE_TOOLS:
                instant = observation_time(call.get('observed_at'))
                if instant is None:
                    return result
                mutations.append(instant)
        for state in streams(previous, path.parent).values():
            if not state['complete']:
                continue
            calls = [call for call in previous.get('calls') or []
                     if stream_key((call.get('tool') or '').split('__')[-1], call.get('params') or {})
                     == stream_key(state['tool'], state['params'])]
            times = [observation_time(call.get('observed_at')) for call in calls]
            if not calls or any(instant is None for instant in times):
                continue
            oldest = min(times)
            if not 0 <= (now - oldest).total_seconds() <= max_age_seconds:
                continue
            candidates.append((oldest, state, calls, path))
    candidates.sort(key=lambda candidate: candidate[0], reverse=True)
    while True:
        plan = next_reads(profile, result, schema, max_calls=len(result.get('calls') or []) + 100, page_size=page_size,
                          run_directory=directory)
        if plan['status'] != 'next_reads':
            break
        read = plan['next_reads'][0]
        match = next((candidate for candidate in candidates if covers_read(candidate[1], read)
                      and (not mutations or candidate[0] > max(mutations))), None)
        if match is None:
            break
        _, _, calls, path = match
        for call in calls:
            reused = deepcopy(call)
            if reused.get('response_file'):
                source = Path(reused['response_file'])
                if not source.is_absolute():
                    source = path.parent / source
                import os
                reused['response_file'] = os.path.relpath(source.resolve(), Path(directory).resolve())
            reused['reused_from'] = path.name
            result.setdefault('calls', []).append(reused)
    return result


def covers_scope(params, scope):
    """A locally filtered result proves completeness only when the read covers the scope."""
    allowed = {'status', 'owner_id', 'pipeline_id', 'filter_id', 'limit', 'cursor', 'start',
               'sort_by', 'sort_direction', 'include_fields', 'custom_fields'}
    if set(params) - allowed:
        return False
    for selector, scope_key in (('owner_id', 'owner_ids'), ('pipeline_id', 'pipeline_ids')):
        value = params.get(selector)
        accepted = scope.get(scope_key) or []
        if value is not None and (len(accepted) != 1 or value != accepted[0]):
            return False
    return not params.get('ids')


def valid_record(record, tool):
    if not isinstance(record, dict):
        return False
    identifier = record.get('id')
    return (type(identifier) is int and identifier > 0) or (tool == 'getLeads' and isinstance(identifier, str) and bool(identifier.strip()))


def streams(run, run_directory=None):
    result = {}
    for call in run.get('calls') or []:
        if call.get('purpose') in ('probe', 'setup_probe'):
            continue
        tool = (call.get('tool') or '').split('__')[-1]
        if not tool.startswith('get') or tool in ('getAccountContext', 'get_account_context'):
            continue
        params = call.get('params') or {}
        key = stream_key(tool, params)
        state = result.setdefault(key, {'tool': tool, 'params': deepcopy(params), 'ids': [], 'pages': 0,
                                        'complete': False, 'next_cursor': None, 'next_start': None,
                                        'reason': 'not read', 'invalid_continuation': False})
        body = response(call, run_directory)
        if call.get('error') or call.get('isError') or not isinstance(body, (dict, list)) or (
                isinstance(body, dict) and (body.get('success') is False or body.get('error'))):
            state.update({'complete': False, 'reason': 'read unavailable', 'failed': True})
            continue
        additional = body.get('additional_data') or {} if isinstance(body, dict) else {}
        pagination = additional.get('pagination') or {}
        empty_list = (tool in LIST_TOOLS and isinstance(body, dict) and body.get('success') is True
                      and body.get('data') is None and ('next_cursor' in additional
                           or type(pagination.get('more_items_in_collection')) is bool))
        if isinstance(body, dict) and not isinstance(body.get('data'), (dict, list)) and not empty_list:
            state.update({'complete': False, 'reason': 'malformed record data', 'failed': True})
            continue
        if any(not valid_record(row, tool) for row in rows(body)):
            state.update({'complete': False, 'reason': 'malformed record identity', 'failed': True})
            continue
        cursor = params.get('cursor')
        start = params.get('start', 0)
        if state['pages'] and (cursor != state['next_cursor'] or (state['next_start'] is not None and start != state['next_start'])):
            state['invalid_continuation'] = True
        if not state['pages'] and (cursor is not None or start not in (0, None)):
            state['invalid_continuation'] = True
        state['pages'] += 1
        state['ids'] = list(dict.fromkeys(state['ids'] + [row['id'] for row in rows(body) if isinstance(row, dict) and 'id' in row]))
        additional = body.get('additional_data') or {} if isinstance(body, dict) else {}
        pagination = additional.get('pagination') or {}
        state['next_cursor'] = additional.get('next_cursor')
        state['next_start'] = pagination.get('next_start') if pagination.get('more_items_in_collection') is True else None
        more = state['next_cursor'] is not None or pagination.get('more_items_in_collection') is True
        if pagination.get('more_items_in_collection') is True and state['next_start'] is None and state['next_cursor'] is None:
            state.update({'failed': True, 'complete': False, 'reason': 'Continuation was declared without a usable cursor or offset'})
            continue
        state['complete'] = not more and not state['invalid_continuation']
        state['failed'] = False
        state['reason'] = 'cursor/offset did not follow observed continuation' if state['invalid_continuation'] else 'unread continuation' if more else None
    return result


def schema_fields(schema):
    if 'tools' in schema:
        return {tool['name'].split('__')[-1]: list((tool.get('inputSchema') or {}).get('properties') or {}) for tool in schema['tools']}
    return schema


def deal_parameters(params, schema, tool='getDeals'):
    result = deepcopy(params)
    properties = next(((entry.get('inputSchema') or {}).get('properties') or {}
                       for entry in schema.get('tools', []) if entry['name'].split('__')[-1] == tool), {})
    if properties.get('status', {}).get('type') == 'array' and isinstance(result.get('status'), str):
        result['status'] = [result['status']]
    if 'include_fields' in schema_fields(schema).get(tool, []):
        allowed = (properties.get('include_fields', {}).get('items') or {}).get('enum')
        include = [name for name in DEAL_EVIDENCE_FIELDS if allowed is None or name in allowed]
        if include:
            result['include_fields'] = include
    return result


def planned_scope(profile):
    """The scope every planned read filters by. A scope that names pipelines or an owner without their ids takes them from the
    session's account context; if it still lacks one, the plan stops: a scoped profile never gets an unfiltered read."""
    scope = profile.get('scope') or {}
    if scope_gap(scope):
        context = profile.get('_account_context')
        if context:
            scope = resolve_scope(scope, context)[0]
    gap = scope_gap(scope)
    if gap:
        raise ValueError(gap)
    return scope


def scope_params(scope, fields, params):
    """Add the owner and pipeline selector a one-owner or one-pipeline scope reads by. A schema that lists getDeals but not the
    selector stops the plan: a scoped profile never gets an unfiltered read. Several owners or pipelines stay a local filter."""
    for key in ('owner_ids', 'pipeline_ids'):
        values = scope.get(key) or []
        singular = key[:-1]
        if len(values) != 1:
            continue
        if singular in fields.get('getDeals', []):
            params[singular] = values[0]
        elif 'getDeals' in fields:
            raise ValueError(f'The getDeals tool schema lists no {singular} parameter, so a read scoped to one '
                             f'{"owner" if singular == "owner_id" else "pipeline"} cannot be filtered and the plan stops here; '
                             f'it will not read every deal for a scoped profile. Give --schema the connector\'s real tool list '
                             f'(tool name to parameter names, for example {{"getDeals": ["status", "{singular}", "limit"]}}).')
    return params


def selectors(profile, schema, page_size):
    fields = schema_fields(schema)
    scope = planned_scope(profile)
    # ADR-036: stage data comes only from the session's account context. No stage tool is planned, whatever the schema lists.
    if not profile.get('_context_stages'):
        raise ValueError('Stage data unavailable: the session account context lists no stages')
    selected = []
    roles = profile.get('view_roles') or {}
    pool = [key for key in roles.get('work_pool', []) if (profile.get('views') or {}).get(key, {}).get('record_type') == 'deal']
    if not pool:
        params = scope_params(scope, fields, {'status': 'open', 'limit': page_size})
        selected.append({'tool': 'getDeals', 'params': params, 'role': 'core'})
    if profile.get('_report_skill') in ('forecast', 'pipeline-brief'):
        for status in ('won', 'lost'):
            params = scope_params(scope, fields, {'status': status, 'limit': page_size})
            selected.append({'tool': 'getDeals', 'params': params, 'role': 'core'})
    elif (profile.get('lost_work') or {}).get('enabled'):
        params = {'status': 'lost', 'limit': page_size}
        lost_view = (profile.get('views') or {}).get(profile['lost_work'].get('view_key'))
        if lost_view:
            params['filter_id'] = lost_view['filter_id']
        scope_params(scope, fields, params)
        selected.append({'tool': 'getDeals', 'params': params, 'role': 'optional'})
    if profile.get('_report_skill') == 'pipeline-brief' and (profile.get('lost_work') or {}).get('enabled'):
        lost_view = (profile.get('views') or {}).get(profile['lost_work'].get('view_key'))
        if lost_view:
            selected.append({'tool': 'getDeals', 'params': {'status': 'lost', 'filter_id': lost_view['filter_id'], 'limit': page_size}, 'role': 'optional'})
    view_keys = list(dict.fromkeys(key for role in ('work_pool', 'priority', 'risk', 'exclusion') for key in roles.get(role, [])))
    view_keys.extend(segment['view_key'] for segment in profile.get('segments') or [] if segment.get('view_key') not in view_keys)
    for key in view_keys:
        view = (profile.get('views') or {}).get(key)
        if not view or view.get('record_type') not in ('deal', 'activity'):
            raise ValueError('Confirmed typed view metadata is required')
        tool = 'getDeals' if view['record_type'] == 'deal' else 'getActivities'
        params = {'filter_id': view['filter_id'], 'limit': page_size}
        if tool == 'getDeals':
            params['status'] = 'open'
        selected.append({'tool': tool, 'params': params, 'view_key': key, 'role': 'core' if key in pool else 'optional'})
    chosen = [field['key'] for field in profile.get('priority_fields') or [] if isinstance(field, dict) and field.get('key')][:PRIORITY_FIELD_CAP]
    for read in selected:
        if read['tool'] == 'getDeals':
            read['params'] = deal_parameters(read['params'], schema)
            # C-PB-R1: the rep's chosen fields ride along on the open-deal reads, and only when the schema lists custom_fields (S5).
            if chosen and read['params'].get('status') in ('open', ['open']) and 'custom_fields' in fields.get('getDeals', []):
                read['params']['custom_fields'] = list(chosen)
        if read['tool'] not in fields or set(read['params']) - set(fields[read['tool']]):
            raise ValueError('Requested read parameters are not supported by the authenticated schema')
    return selected


def ranked_context_candidates(profile, run, run_directory=None):
    """Use the same saved rule engine and ordering as the displayed shortlist."""
    import importlib.util
    import tempfile
    from types import SimpleNamespace
    from workflow import prepare_scan, with_context_stages
    from deal_context import findings
    from report_contract import attach
    skill = profile.get('_report_skill')
    if skill not in ('pipeline-brief', 'deal-triage') or not run.get('today'):
        return None
    engine_path = Path(__file__).resolve().parent / 'compute.py'
    if not engine_path.exists():
        engine_path = Path(__file__).resolve().parents[1] / 'skills' / skill / 'scripts/compute.py'
    # The report takes stages from the account context when the connector has none (Beta); the preview must too,
    # or it stops on missing_stage and chooses no deals for notes.
    if profile.get('_account_context'):
        run = with_context_stages(run, profile['_account_context'])
    scan = prepare_scan(run, profile, run_directory)
    if not scan['coverage']['complete']:
        return None
    spec = importlib.util.spec_from_file_location('_context_preview', engine_path)
    engine = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(engine)
    config = {**deepcopy({key: value for key, value in profile.items() if key != '_account_context'}), 'schema_version': 1, '_validated_cohort': True}
    config['filter_rules'] = [{**config['views'][key], 'meaning': config['views'][key].get('meaning') or config['views'][key]['label']}
                              for key in (config.get('view_roles') or {}).get('risk', []) if config['views'][key]['record_type'] == 'deal']
    with tempfile.TemporaryDirectory(prefix='pd-context-preview-') as directory:
        config_path, run_path = Path(directory) / 'config.json', Path(directory) / 'run.json'
        config_path.write_text(json.dumps(config))
        run_path.write_text(json.dumps(scan['run']))
        args = SimpleNamespace(config=str(config_path), input=str(run_path), today=run['today'], budget=None,
                               pass_no=2, top_n=30, show=5, mode='brief', remaining_context_tokens=None,
                               deal_ids=None, include_leads=False)
        result = engine.run_triage(args) if skill == 'deal-triage' else engine.run_brief(args)
    contextual = findings(run, scan['records'], profile, run_directory)
    memberships = {row['record']['id']: row['matched_views'] for row in scan['cohort']['included']}
    for row in contextual:
        row['matched_views'] = memberships[row['record']['id']]
    return [row['record']['id'] for row in attach(result, profile, scan['coverage'], contextual)['work_rows'][:5]]


def activity_scan(profile, run, supported, params):
    """Lost work needs open activities; meeting preparation needs those due today, open or finished.
    Open activities plus those finished since local midnight cover both without the owner's whole history.
    A connector without the done/updated_since filters keeps the full scan."""
    if 'done' not in supported:
        return [params]
    reads = [{**params, 'done': False}]
    if profile.get('meeting_types'):
        if 'updated_since' not in supported or not run.get('today') or not profile.get('timezone'):
            return [params]
        midnight = datetime.combine(date.fromisoformat(str(run['today'])[:10]), time.min, ZoneInfo(profile['timezone']))
        reads.append({**params, 'done': True, 'updated_since': midnight.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')})
    return reads


class SchemaGap(ValueError):
    """Optional context reads are planned but their tools are missing from the saved tool list (tools.json)."""

    def __init__(self, tools):
        self.tools = list(tools)
        super().__init__('; '.join(f"{tool} schema not captured: save the connector's full tool list and re-plan" for tool in self.tools))


def notes_read(run, fields):
    """One scope-wide notes read: no deal_id, newest first and the last NOTES_WINDOW_DAYS days where the schema supports it,
    paged by the planner through `start`. Until 0.7.10 the planner read the top five candidates' notes one deal at a time,
    so a lower-ranked deal whose note said "signed" was never checked (handover 0.7.10). Pages of NOTES_PAGE (0.7.12): a
    100-note page of a 33-deal account was 70,000 to 98,000 characters, above what Cowork hands the chat (0.7.11 runs)."""
    params = {'limit': NOTES_PAGE}
    supported = fields.get('getNotes', [])
    if 'sort' in supported:
        params['sort'] = 'add_time DESC'
    if 'start_date' in supported and run.get('today'):
        params['start_date'] = (date.fromisoformat(str(run['today'])[:10]) - timedelta(days=NOTES_WINDOW_DAYS)).isoformat()
    return params


def enrichment(profile, run, schema, page_size, run_directory=None, budget=None):
    fields = schema_fields(schema)
    context_on = bool(profile.get('buying_signal_evidence'))
    missing = []
    profile = {**profile, 'scope': planned_scope(profile)}
    records = {}
    for call in run.get('calls') or []:
        if (call.get('tool') or '').split('__')[-1] not in ('getDeals', 'getDeal') or call.get('purpose') in ('probe', 'setup_probe'):
            continue
        for record in rows(response(call, run_directory)):
            if valid_record(record, 'getDeals') and scope_state(record, profile.get('scope') or {}) == 'match':
                records[record['id']] = record
    selected = profile.get('_candidate_ids')
    if selected is None:
        selected = ranked_context_candidates(profile, run, run_directory)
    candidates = [records[key] for key in selected if key in records] if selected is not None else sorted(records.values(), key=lambda row: (row.get('expected_close_date') or '9999-12-31', str(row.get('title') or '')))[:30]
    planned = []
    def optional(tool, params, purpose):
        if tool not in fields:
            # Context on (the default) makes a missing tool a capture mistake: the plan stops rather than drop the read silently.
            if context_on and tool not in missing:
                missing.append(tool)
            return
        if not set(params) - set(fields[tool]):
            planned.append({'tool': tool, 'params': params, 'role': 'optional', 'purpose': purpose})
    if (profile.get('buying_signal_evidence') or profile.get('silence_days') or profile.get('flag_no_next_activity')
            or profile.get('flag_notes_stage_mismatch')):
        activity_records = records.values() if profile.get('silence_days') or profile.get('flag_no_next_activity') else candidates
        identifiers = list(dict.fromkeys(record[key] for record in activity_records for key in ('last_activity_id', 'next_activity_id') if record.get(key)))
        for offset in range(0, len(identifiers), 100):
            optional('getActivities', {'ids': identifiers[offset:offset + 100], 'limit': 100}, 'candidate_context')
        if profile.get('buying_signal_evidence') or profile.get('flag_notes_stage_mismatch'):
            optional('getNotes', notes_read(run, fields), 'candidate_context')
    if profile.get('meeting_types') or (profile.get('lost_work') or {}).get('enabled'):
        params = {'limit': page_size}
        owners = (profile.get('scope') or {}).get('owner_ids') or []
        if len(owners) == 1 and 'owner_id' in fields.get('getActivities', []):
            params['owner_id'] = owners[0]
        for bounded in activity_scan(profile, run, fields.get('getActivities', []), params):
            optional('getActivities', bounded, 'meeting_or_lost_work')
        linked = []
        for call in run.get('calls') or []:
            if (call.get('tool') or '').split('__')[-1] not in ('getActivities', 'getActivity'):
                continue
            linked.extend(row.get('deal_id') for row in rows(response(call, run_directory)) if isinstance(row, dict) and row.get('deal_id'))
        for identifier in list(dict.fromkeys(linked))[:5]:
            if identifier not in records:
                optional('getDeal', deal_parameters({'id': identifier}, schema, 'getDeal'), 'meeting_scope_context')
    if profile.get('engagement_fields'):
        for record in candidates[:5]:
            optional('getDeal', deal_parameters({'id': record['id']}, schema, 'getDeal'), 'engagement_values')
    if profile.get('_report_skill') == 'deal-triage' and profile.get('buying_signal_evidence'):
        def contact(record):
            value = record.get('person_id')
            return value.get('value', value.get('id')) if isinstance(value, dict) else value
        first = dict.fromkeys(contact(record) for record in candidates[:5])
        # The triage queue shows at most ten deals (deal-triage shows min(show, 10)): the first five always get a
        # contact read, and the rest only while the call budget has room left.
        for identifier in dict.fromkeys(contact(record) for record in candidates[:10]):
            if identifier and (identifier in first or budget is None or len(planned) < budget):
                optional('getPerson', {'id': identifier}, 'display_metadata')
    if missing:
        raise SchemaGap(missing)
    return planned


# A batch holds reads planned from the same evidence only. Each phase's parameters come from the
# reads before it: deal lists give activity IDs and the candidate order, activities can reorder the
# candidates whose notes are read, and per-deal details and contacts follow the settled order.
READ_PHASES = {'selection': 0, 'candidate_context': 1, 'meeting_or_lost_work': 1,
               'selected_context': 2, 'meeting_scope_context': 2, 'engagement_values': 3, 'display_metadata': 4}


def next_reads(profile, run, schema, max_calls=20, page_size=50, run_directory=None, max_batch=1):
    if type(page_size) is not int or not 1 <= page_size <= 100:
        raise ValueError('Page size must be between one and 100')
    if type(max_calls) is not int or max_calls < 1:
        raise ValueError('A positive shared call budget is required')
    if type(max_batch) is not int or max_batch < 1:
        raise ValueError('A positive batch size is required')
    if profile.get('_remaining_context_tokens', 15000) < 15000:
        return {'status': 'partial', 'reason': 'Context reserve reached', 'next_reads': [], 'streams': streams(run, run_directory)}
    observed_sizes = [(len(json.dumps(response(call, run_directory), ensure_ascii=False).encode()), len(rows(response(call, run_directory))))
                      for call in run.get('calls') or [] if (call.get('tool') or '').split('__')[-1] == 'getDeals' and rows(response(call, run_directory))]
    if observed_sizes:
        per_row = max(size / count for size, count in observed_sizes)
        page_size = min(page_size, max(1, int(90000 / per_row)))
    coverage = streams(run, run_directory)
    result = {'status': 'complete', 'next_reads': [], 'streams': coverage, 'reason': None}
    spent = sum(not call.get('reused_from') for call in run.get('calls') or [])
    try:
        selected = selectors(profile, schema, page_size)
        planned = selected + enrichment(profile, run, schema, page_size, run_directory, budget=max_calls - spent - len(selected))
    except ValueError as error:
        gap = {'schema_missing': error.tools} if isinstance(error, SchemaGap) else {}
        return {**result, 'status': 'blocked', 'reason': str(error), **gap}
    unavailable, batch, batch_phase = [], [], None
    for read in planned:
        state = coverage.get(stream_key(read['tool'], read['params']))
        if not state:
            state = next((entry for entry in coverage.values() if covers_read(entry, read)), None)
        if state and state['complete']:
            continue
        if state and (state.get('failed') or state.get('invalid_continuation')):
            if read.get('role') == 'optional':
                unavailable.append({'tool': read['tool'], 'view_key': read.get('view_key'), 'reason': state['reason']})
                continue
            if batch:
                break
            return {**result, 'status': 'partial', 'reason': state['reason']}
        # An unknown purpose has no phase and always travels alone.
        phase = READ_PHASES.get(read.get('purpose', 'selection'), object())
        if batch and (phase != batch_phase or stream_key(read['tool'], read['params']) in
                      {stream_key(chosen['tool'], chosen['params']) for chosen in batch}):
            continue
        if spent + len(batch) >= max_calls:
            if batch:
                break
            return {**result, 'status': 'partial', 'reason': 'run call budget reached', 'optional_unavailable': unavailable}
        params = deepcopy(read['params'])
        if state and state['next_cursor'] is not None:
            if 'cursor' not in schema_fields(schema).get(read['tool'], []):
                if batch:
                    break
                return {**result, 'status': 'blocked', 'reason': 'Continuation parameter is unavailable'}
            params['cursor'] = state['next_cursor']
        if state and state['next_start'] is not None:
            if 'start' not in schema_fields(schema).get(read['tool'], []):
                if batch:
                    break
                return {**result, 'status': 'blocked', 'reason': 'Offset continuation parameter is unavailable'}
            params['start'] = state['next_start']
        batch.append({**read, 'params': params})
        batch_phase = phase
        if len(batch) >= max_batch or phase not in READ_PHASES.values():
            break
    if batch:
        return {**result, 'status': 'next_reads', 'optional_unavailable': unavailable, 'next_reads': batch}
    return {**result, 'optional_unavailable': unavailable}


def view_memberships(profile, run, run_directory=None):
    coverage = streams(run, run_directory)
    result = {}
    for key, view in (profile.get('views') or {}).items():
        tool = 'getDeals' if view.get('record_type') == 'deal' else 'getActivities'
        matched = [stream for stream in coverage.values() if stream['tool'] == tool and stream['params'].get('filter_id') == view.get('filter_id')]
        result[key] = {'record_type': view.get('record_type'), 'ids': list(dict.fromkeys(identifier for stream in matched for identifier in stream['ids'])),
                       'complete': bool(matched) and all(stream['complete'] for stream in matched)}
    return result
