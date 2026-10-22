"""Exact approved actions checked against the plugin's own copy of the write tools' parameters and fresh saved CRM reads.

Until 0.7.16 the parameter check read a tools.json the chat had written from the host's tool list; the 0.7.16 release
run (b) had the chat add a missing updateActivity entry to that file by hand before the preflight. Since 0.7.17
(decision 2A, ADR-044 note) the check reads write_rules.WRITE_TOOL_PARAMETERS; `--schema` is accepted and ignored so
the documented command line still runs."""

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
from datetime import date, datetime

sys.path.insert(0, str(Path(__file__).resolve().parent))

import write_rules  # noqa: E402

WHITELIST = {tool: set(rule['allowed']) for tool, rule in write_rules.TRIAGE.items()}


def note_hash(note):
    return hashlib.sha256(str(note or '').encode()).hexdigest()


def proposal_revision(proposal):
    return hashlib.sha256(json.dumps({key: value for key, value in proposal.items() if key != 'revision'}, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


# ---------------------------------------------------------------- the report's proposals (0.7.12, ADR-044)
# 0.7.11 run (a): the saved result proposed nothing, the chat composed a reschedule, wrote proposal.json and
# acceptance.json itself, and the write went through because the only check was between those two files. A write
# is now one of the saved result's proposals: looked up by item number, matched on deal, activity and fields, with
# the acceptance revision taken from the saved item.
NOT_PROPOSAL = ("Item {number} is not one of this report's proposals; {consequence}. "
                "Run triage again, or name an item number from the report.")
DIFFERS = ("Item {number} differs from the report's proposal ({what}); nothing was written. "
           "Accept the item as proposed, edit its due date, or run triage again.")
IDENTITY_FIELDS = ('deal_id', 'type', 'subject', 'due_date')


def saved_proposals(saved):
    """The result's proposals by item number: `proposals` (compute.py), else the `proposal` on each `ranked_top10` row."""
    found = {}
    for proposal in (saved or {}).get('proposals') or []:
        found[proposal.get('item')] = proposal
    for row in (saved or {}).get('ranked_top10') or []:
        if row.get('item') is not None and isinstance(row.get('proposal'), dict) and row['item'] not in found:
            found[row['item']] = dict(row['proposal'], item=row['item'], deal_id=row.get('deal_id'), title=row.get('title'))
    return found


def scripted_item(saved, number, consequence='nothing was written'):
    """The saved proposal with this item number, or the refusal the rep reads."""
    found = saved_proposals(saved).get(number) if number is not None else None
    if found is None:
        raise ValueError(NOT_PROPOSAL.format(number=number, consequence=consequence))
    return found


def scripted_revision(saved_item):
    """The revision an acceptance must name: the one the report recorded (confirmed profiles), else the item's hash."""
    return saved_item.get('revision') or proposal_revision(saved_item)


def _own_second_confirmation(saved_item, params):
    """The item's own separately confirmed deal change: R7's status, or the R4/R9 field (SKILL.md §5)."""
    deal_id = saved_item.get('deal_id')
    if saved_item.get('status_change') and params == {'id': deal_id, 'status': saved_item['status_change']}:
        return True
    check = saved_item.get('field_check') if isinstance(saved_item.get('field_check'), dict) else {}
    field = check.get('field')
    return bool(field) and set(params) == {'id', field} and params.get('id') == deal_id


def scripted_mismatch(saved_item, proposal):
    """What differs between the saved item and the proposal about to be written; empty when they are the same action."""
    params = proposal.get('params') if isinstance(proposal.get('params'), dict) else None
    fields = params if params is not None else {key: proposal.get(key) for key in IDENTITY_FIELDS + ('id',) if key in proposal}
    tool = proposal.get('tool') or proposal.get('action') or 'addActivity'
    expected = saved_item.get('tool') or saved_item.get('action') or 'addActivity'
    if tool != expected:
        if tool == 'updateDeal' and params is not None and _own_second_confirmation(saved_item, params):
            return []
        return [f'the report proposes {expected}, not {tool}']
    what = []
    if str(_proposal_deal_id(proposal)) != str(_proposal_deal_id(saved_item)):
        what.append('a different deal')
    if expected == 'updateActivity':
        saved_activity = saved_item.get('activity_id', (saved_item.get('params') or {}).get('id'))
        activity = proposal.get('activity_id', fields.get('id'))
        if str(activity) != str(saved_activity):
            what.append('a different activity')
    saved_params = saved_item.get('params') if isinstance(saved_item.get('params'), dict) else None
    if saved_params is not None:
        compare = {key: value for key, value in saved_params.items()}
    else:
        compare = {key: saved_item[key] for key in IDENTITY_FIELDS if key in saved_item and key != 'deal_id'}
        if expected == 'updateActivity':
            compare = {'due_date': saved_item.get('due_date')}
    for key, wanted in compare.items():
        actual = fields.get(key)
        same = str(actual).lower() == str(wanted).lower() if key == 'type' else actual == wanted or str(actual) == str(wanted)
        if not same:
            what.append(f'{key} {json.dumps(actual, ensure_ascii=False)} instead of {json.dumps(wanted, ensure_ascii=False)}')
    return what


def scripted(saved_item, proposal):
    """Refuse a proposal that is not the saved item's action."""
    what = scripted_mismatch(saved_item, proposal)
    if what:
        raise ValueError(DIFFERS.format(number=proposal.get('item', saved_item.get('item')), what='; '.join(what)))
    return saved_item


FRESH_TOOLS = ('getDeal', 'getActivities', 'getActivity', 'getNotes')


def _proposal_deal_id(proposal):
    params = proposal.get('params') or {}
    return proposal.get('deal_id', params.get('deal_id', params.get('id') if proposal.get('tool') == 'updateDeal' else None))


def _about(proposal):
    """Only reads of the proposal's own deal or activity: other deals' activities would trip the duplicate check."""
    params = proposal.get('params') or {}
    deal_id = _proposal_deal_id(proposal)
    activity_id = proposal.get('activity_id', params.get('id') if proposal.get('tool') == 'updateActivity' else None)

    def same(value, wanted):
        return wanted is not None and str(value) == str(wanted)

    def match(record):
        asked = record.get('params') or {}
        body = record.get('response') or {}
        # a failed read is not evidence; a filter_id read ignores deal_id, so its rows can belong to any deal
        if body.get('success') is False or 'error' in body or asked.get('filter_id') is not None:
            return False
        ids = asked.get('ids')
        ids = ids.split(',') if isinstance(ids, str) else ids or []
        if record['tool'] == 'getDeal':
            return same(asked.get('id'), deal_id)
        if record['tool'] == 'getActivity':
            return same(asked.get('id'), activity_id)
        return same(asked.get('deal_id'), deal_id) or any(same(item, activity_id) for item in ids)
    return match


def absorb_fresh(fresh_path, proposal, acceptance):
    """Add hook captures of this proposal's deal observed since acceptance to the fresh-read ledger (ADR-030)."""
    from run_capture import absorb
    fresh_path = Path(fresh_path)
    if not fresh_path.exists():
        fresh_path.write_text('{"calls": []}\n', encoding='utf-8')
    return absorb(fresh_path, since=acceptance.get('accepted_at'), tools=FRESH_TOOLS, match=_about(proposal))


def fresh_evidence(run, proposal, acceptance, run_directory=None):
    from read_plan import response, streams
    from pdcore import collect
    accepted = datetime.fromisoformat(str(acceptance.get('accepted_at') or '').replace('Z', '+00:00'))
    if accepted.tzinfo is None:
        raise ValueError('Acceptance needs an observed timezone-aware timestamp for fresh reads')
    calls = []
    for call in run.get('calls') or []:
        if (call.get('tool') or '').split('__')[-1] not in ('getDeal', 'getActivities', 'getActivity', 'getNotes'):
            continue
        timestamp = call.get('observed_at')
        if not timestamp:
            raise ValueError('Fresh preflight reads need an observed timestamp after acceptance')
        observed = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
        if observed.tzinfo is None or observed < accepted:
            raise ValueError('Fresh preflight reads must be observed after acceptance')
        calls.append({**call, 'response': response(call, run_directory)})
    ledger = list(streams({'calls': calls}).values())
    if not ledger or any(not stream['complete'] for stream in ledger):
        raise ValueError('Complete fresh preflight reads are required')
    deal_id = _proposal_deal_id(proposal)
    tables = collect({'calls': calls})
    deal = next((tables[key][deal_id] for key in ('open_deals', 'won_deals', 'lost_deals') if deal_id in tables[key]), None)
    if not deal or not any(stream['tool'] == 'getDeal' and deal_id in stream['ids'] for stream in ledger):
        raise ValueError('Fresh exact linked-deal read required')
    full_activities = any(stream['tool'] == 'getActivities' and not stream['params'].get('ids') and
                          stream['params'].get('deal_id') == deal_id and stream['params'].get('filter_id') is None and
                          stream['params'].get('done') in (None, False) for stream in ledger)
    exact_activity = any(stream['tool'] in ('getActivities', 'getActivity') and proposal.get('activity_id', (proposal.get('params') or {}).get('id')) in stream['ids'] for stream in ledger)
    return {'complete': full_activities if proposal.get('tool') == 'addActivity' else exact_activity if proposal.get('tool') == 'updateActivity' else True,
            'notes_complete': any(stream['tool'] == 'getNotes' and stream['params'].get('deal_id') == deal_id for stream in ledger),
            'deal': deal, 'activities': list(tables['activities'].values()), 'notes': list(tables['notes'].values()), 'today': run.get('today')}


def prepare(proposal, acceptance, fresh, schema, current_user_id, execution=None, saved_item=None):
    """The exact write for an accepted item. `saved_item` is the report's proposal with that number (0.7.12): without
    it there is nothing to accept; a proposal that differs from it, or an acceptance naming another revision, is refused.
    `schema` is unused since 0.7.17: the parameter check reads write_rules.WRITE_TOOL_PARAMETERS (the old call shape kept)."""
    status = (execution or {}).get('status')
    if status in ('uncertain', 'submitted', 'written', 'verified'):
        raise ValueError(f'Action is {status}; resolve the observed outcome before any further submission')
    if saved_item is None:
        raise ValueError(NOT_PROPOSAL.format(number=proposal.get('item'), consequence='nothing was written'))
    scripted(saved_item, proposal)
    if acceptance.get('accepted') is not True or acceptance.get('item') != proposal.get('item') or acceptance.get('revision') != scripted_revision(saved_item):
        raise ValueError('The exact item and proposal revision must be accepted in chat')
    tool = proposal.get('tool')
    params = deepcopy(proposal.get('params') or {})
    if tool == 'addActivity':
        if any(not isinstance(params.get(key), str) or not params[key].strip() for key in ('subject', 'type', 'due_date')):
            raise ValueError('Activity subject, type and due date must be explicitly reviewed')
    if 'due_date' in params:
        due = date.fromisoformat(params['due_date'])
        if fresh.get('today') and due < date.fromisoformat(fresh['today']):
            raise ValueError('Due date is now in the past; revise and reconfirm')
    if proposal.get('action') == 'append_preparation':
        if tool != 'updateActivity' or not fresh.get('complete'):
            raise ValueError('A complete fresh activity read is required')
        deal = fresh.get('deal') or {}
        target = [item for item in fresh.get('activities') or [] if item.get('id') == proposal.get('activity_id')]
        if deal.get('id') != proposal.get('deal_id') or deal.get('status') != 'open' or len(target) != 1:
            raise ValueError('The accepted deal/activity linkage or status changed')
        activity = target[0]
        if activity.get('deal_id') != proposal['deal_id'] or activity.get('done') is not False:
            raise ValueError('Target activity changed or is not open')
        if note_hash(activity.get('note')) != proposal.get('note_hash'):
            raise ValueError('Existing note changed; revise and reconfirm')
        owner = activity.get('user_id', activity.get('owner_id'))
        if owner != proposal.get('owner_id') or owner != current_user_id:
            raise ValueError('Activity owner changed or is outside the accepted personal action scope')
        person = deal.get('person_id')
        person = person.get('value', person.get('id')) if isinstance(person, dict) else person
        if 'person_id' not in proposal or person != proposal['person_id'] or activity.get('person_id') != person:
            raise ValueError('Activity/deal contact changed; revise and reconfirm')
        text = proposal.get('text')
        if not isinstance(text, str) or not text.strip():
            raise ValueError('Review the exact preparation text before acceptance')
        params = {'id': proposal['activity_id'], 'note': str(activity.get('note') or '') + '\n\nPreparation:\n' + text}
    elif 'note' in params and tool == 'updateActivity':
        raise ValueError('Only the reviewed append-preparation action can update an activity note')
    elif tool == 'updateActivity':
        deal = fresh.get('deal') or {}
        activity = next((row for row in fresh.get('activities') or [] if row.get('id') == params.get('id')), {})
        person = deal.get('person_id')
        person = person.get('value', person.get('id')) if isinstance(person, dict) else person
        if not fresh.get('complete') or not activity or activity.get('done') is not False:
            raise ValueError('A complete fresh open-activity read is required')
        if deal.get('id') != proposal.get('deal_id') or deal.get('status') != 'open' or activity.get('deal_id') != deal.get('id'):
            raise ValueError('Fresh activity/deal linkage changed')
        if activity.get('user_id', activity.get('owner_id')) != current_user_id or proposal.get('owner_id') != current_user_id:
            raise ValueError('Fresh activity owner differs from the reviewed personal action')
        reviewed = proposal.get('activity_contact')
        date_only = set(params) > {'id'} and set(params) <= {'id', 'due_date', 'due_time'}
        if reviewed is not None and date_only:
            if (not isinstance(reviewed, dict) or not reviewed or set(reviewed) - {'person_id', 'participants'}
                    or any(key not in activity or activity[key] != value for key, value in reviewed.items())):
                raise ValueError('Fresh activity contact differs from the reviewed contact')
        elif activity.get('person_id') != person:
            raise ValueError('Fresh activity contact differs from the reviewed contact')
        if 'person_id' not in proposal or proposal['person_id'] != person:
            raise ValueError('Fresh activity contact differs from the reviewed contact')
        if 'participants' in params and params['participants'] != ([{'person_id': person, 'primary': True}] if person else []):
            raise ValueError('Fresh activity contact differs from reviewed participants')
    violations = write_rules.validate('deal-triage', tool, params, current_user_id=current_user_id,
                                      reviewed_append=proposal.get('action') == 'append_preparation')
    if tool not in WHITELIST or violations:
        raise ValueError('Action exceeds the Triage parameter whitelist' + (': ' + '; '.join(violations) if violations else ''))
    fields = write_rules.WRITE_TOOL_PARAMETERS
    if tool not in fields or set(params) - set(fields[tool]):
        raise ValueError("Action parameters are outside the Pipedrive write tool's parameters (the plugin's own copy)")
    if tool == 'updateDeal':
        if len(params) != 2 or params.get('status') not in (None, 'won') or proposal.get('separate_confirmation') is not True:
            raise ValueError('Deal fixes need their separate exact confirmation and one approved field')
        deal = fresh.get('deal') or {}
        if not fresh.get('complete') or deal.get('id') != params.get('id') or deal.get('status') != proposal.get('original_status') or deal.get('stage_id') != proposal.get('original_stage_id'):
            raise ValueError('Deal status/stage changed; revise and reconfirm')
    if tool == 'addActivity':
        deal = fresh.get('deal') or {}
        if not fresh.get('complete') or deal.get('id') != params.get('deal_id') or deal.get('status') != 'open':
            raise ValueError('Complete fresh open-deal/activity reads are required')
        if deal.get('owner_id') != current_user_id or params.get('user_id', current_user_id) != current_user_id:
            raise ValueError('Cross-owner activities are not allowed; the deal owner and user_id must be the current user')
        person = deal.get('person_id')
        person = person.get('value', person.get('id')) if isinstance(person, dict) else person
        expected = [{'person_id': person, 'primary': True}] if person is not None else []
        if params.get('participants', []) != expected:
            raise ValueError('Accepted contact differs from the fresh deal contact')
        if any(item.get('subject') == params.get('subject') and item.get('type') == params.get('type') for item in fresh.get('activities') or []):
            raise ValueError('An equivalent activity already exists; do not create it again')
    if tool == 'addNote':
        if not isinstance(params.get('content'), str) or not params['content'].strip():
            raise ValueError('Review the exact nonempty note content')
        deal = fresh.get('deal') or {}
        if deal.get('id') != params.get('deal_id') or not fresh.get('notes_complete'):
            raise ValueError('Fresh linked-record and complete duplicate-note evidence required')
        if any(item.get('content') == params.get('content') for item in fresh.get('notes') or []):
            raise ValueError('An equivalent note already exists')
    return {'tool': tool, 'params': params, 'item': proposal['item'], 'revision': acceptance['revision']}


def captured_current_user(fresh_path):
    """The signed-in user's id from the newest getAccountContext hook capture beside the run (0.7.21, review 2.5): the
    answer Pipedrive gave, not an id the chat typed. The write hook checks a user_id against the same capture."""
    import account_context
    capture = account_context.latest_capture(fresh_path)
    if capture is None:
        raise ValueError('no account-context capture to take the current user from: call getAccountContext in this run '
                         '(the hook saves it), or pass --current-user-id')
    return account_context.load_context(capture)['user']['id']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('acceptance', 'fresh'):
        parser.add_argument('--' + key, type=Path, required=True)
    parser.add_argument('--schema', type=Path, help='accepted and ignored since 0.7.17: the write check reads the plugin\'s own copy '
                                                     'of the write tools\' parameters (write_rules.WRITE_TOOL_PARAMETERS)')
    parser.add_argument('--result', type=Path, required=True, help="the saved triage result; the write must be one of its proposals")
    parser.add_argument('--item', type=int, required=True, help='the accepted item number in that result')
    parser.add_argument('--proposal', type=Path, help="the proposal file (the saved item's own second confirmation, or a copy of the item); "
                                                       'without it the saved item is the proposal')
    parser.add_argument('--current-user-id', type=int, help='since 0.7.21 optional: without it the signed-in user comes from the '
                                                              'newest getAccountContext capture beside the run')
    parser.add_argument('--execution', type=Path)
    parser.add_argument('--runs', type=Path, default=Path('./.pipedrive-sales/runs'),
                        help='the runs folder; the prepared write is saved there for the plugin write hook (0.7.13)')
    args = parser.parse_args()
    try:
        saved = json.loads(args.result.read_text(encoding='utf-8')) if args.result.exists() else {}
        saved_item = scripted_item(saved, args.item)
        proposal = json.loads(args.proposal.read_text()) if args.proposal else dict(saved_item)
        proposal.setdefault('item', args.item)
        acceptance = json.loads(args.acceptance.read_text())
        captured = absorb_fresh(args.fresh, proposal, acceptance)
        if captured:
            from run_capture import report
            print(report(captured), file=sys.stderr)
        fresh = fresh_evidence(json.loads(args.fresh.read_text()), proposal, acceptance, args.fresh.parent)
        result = prepare(proposal, acceptance, fresh, None,
                         current_user_id=args.current_user_id if args.current_user_id is not None else captured_current_user(args.fresh),
                         execution=json.loads(args.execution.read_text()) if args.execution else None,
                         saved_item=saved_item)
        write_rules.record_prepared_action(args.runs, result['tool'], result['params'], item=result['item'],
                                           revision=result['revision'], run=str(args.fresh), skill='deal-triage')
    except (ValueError, KeyError, OSError) as error:
        parser.error(str(error))
    print(json.dumps(result, ensure_ascii=False))


def reconcile(prepared, call, read_back):
    state = {'item': prepared['item'], 'revision': prepared['revision'], 'status': 'uncertain'}
    if (call.get('tool') or '').split('__')[-1] != prepared['tool'] or call.get('params') != prepared['params']:
        return {**state, 'reason': 'Observed call does not match the approved parameters'}
    body = call.get('response')
    if isinstance(body, dict) and body.get('success') is False:
        return {**state, 'status': 'failed', 'reason': 'Observed explicit failure'}
    if not isinstance(body, dict) or body.get('success') is not True or not isinstance(body.get('data'), dict):
        return {**state, 'reason': 'Submission outcome not established'}
    record = (read_back or {}).get('data')
    if (read_back or {}).get('success') is not True or not isinstance(record, dict) or record.get('id') != body['data'].get('id'):
        return {**state, 'reason': 'Successful matching read-back is required'}
    if any(record.get(key) != value for key, value in prepared['params'].items()):
        return {**state, 'reason': 'Read-back differs from approved material fields'}
    return {**state, 'status': 'verified', 'record_id': record['id'], 'observed_at': call.get('observed_at')}


if __name__ == '__main__':
    main()
