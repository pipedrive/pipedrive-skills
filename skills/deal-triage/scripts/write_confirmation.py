"""Confirm an accepted Triage write from evidence: linked names, readable dates, no record identifiers.

The model used to word write confirmations itself, and they skipped the presentation check that reports pass
(HC-04, 9 Oct: "deal 1, activity 9001"). This builds the confirmation from the accepted proposal, the fresh
reads and the read-back the plugin hook captured after acceptance, checks it with record_presentation, and
prints it to send unchanged. A write with no matching read-back is reported as uncertain, never as done.
The version-1 path (write_preflight.py) has no hook capture: it hands the saved read-back response to --read-back.

    python3 scripts/write_confirmation.py --proposal P --acceptance A --fresh F --config C --result S [--item N]
    python3 scripts/write_confirmation.py --proposal P --read-back R --fresh F --config C --result S [--item N]
    python3 scripts/write_confirmation.py --turn --accepted 1,3 [--skipped [2]] [--runs DIR] [--result S] [--run RUNFILE]
    python3 scripts/write_confirmation.py --ask 2 [--result S]
    python3 scripts/write_confirmation.py --edit 2 --due-date 2026-10-22 [--result S]

--result is the saved triage result. The confirmation records the item's state there (0.7.2, 2a), so the
saved result stops showing an accepted write as pending. Each per-item confirmation also goes to the turn ledger
(runs/confirmations.json, 0.7.3). --turn prints the whole accept turn's reply from that ledger: the model sends
it unchanged as its entire message, so no item is worded by the model.
0.7.12 (ADR-044): every accepted number must be one of the saved result's proposals; --turn says so for any other
number instead of counting it. --ask prints the item's question from the saved proposal (the chat relays it, as the
selectable question or in chat); --edit changes an item's due date in the saved result and asks again.
0.7.15 (decision 4A, ADR-042 note): in turn mode --run is optional, the newest pd-triage-*.json in the runs folder stands
in when it is absent or not a file, and --skipped may be given with no numbers (DT-02 round 1 and the 0.7.14 release run
(b) spent turns on an invented run path and a bare --skipped).
"""

import argparse
from datetime import date
import json
import re
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from deal_context import markdown_ref, record_ref  # noqa: E402
from pdcore import collect, crm_quote, framed, save_expected_reply, visible_record_ids  # noqa: E402
from pdwrite import NOT_PROPOSAL, absorb_fresh, proposal_revision, saved_proposals, scripted_revision  # noqa: E402
from read_plan import observation_time, response  # noqa: E402
from record_presentation import check_text, link_record_names  # noqa: E402
from write_rules import record_action_state, record_confirmation, save_result, stopped_line, turn_entries  # noqa: E402

READ_BACK_TOOLS = {'addActivity': ('getActivity', 'getActivities'), 'updateActivity': ('getActivity', 'getActivities'),
                   'addNote': ('getNotes', 'getNote'), 'updateDeal': ('getDeal',)}
UNCHECKED = {'participants', 'note'}  # returned in a different shape than they are sent
REFUSED_LINE = 'Item {number} was not written: Pipedrive refused it.'
NOT_PREPARED = ("Item {number} is not one of this report's proposals, so nothing was prepared for it. "
                "Run triage again, or name an item number from the report.")
OFF_REPORT = "Item {number} is not one of this report's proposals, but a write was recorded for it outside the report's checks: {text}"
DEFAULT_RESULT = Path('./.pipedrive-sales/results/deal-triage-full.json')
RECONNECT_LINE = 'Pipedrive refused that request. Reconnect the Pipedrive connector in your client settings and try again.'


def _readable(day):
    try:
        value = date.fromisoformat(str(day))
    except ValueError:
        return str(day)
    return f'{value.day} {value.strftime("%B %Y")}'


def _when(params, row=None):
    """The due date and time of a reschedule; a time-only edit takes the date from the read-back."""
    day = params.get('due_date') or (row or {}).get('due_date')
    text = _readable(day) if day else 'the accepted date'
    return f'{text} at {params["due_time"]}' if params.get('due_time') else text


def _rows(body):
    data = (body or {}).get('data') if isinstance(body, dict) else None
    return data if isinstance(data, list) else [data] if isinstance(data, dict) else []


def _same(key, actual, wanted):
    if isinstance(wanted, bool):
        return str(actual).lower() in (str(wanted).lower(), str(int(wanted)))
    if key == 'due_time':  # sent as HH:MM, a read may add seconds
        return str(actual)[:5] == str(wanted)[:5]
    return str(actual) == str(wanted)


def wanted_fields(proposal):
    return {key: value for key, value in (proposal.get('params') or {}).items() if key not in UNCHECKED}


def matching_row(body, wanted):
    return next((row for row in _rows(body) if isinstance(row, dict)
                 and all(_same(key, row.get(key), value) for key, value in wanted.items())), None)


def _saved_read_back(proposal, path):
    """The row of a hand-saved read-back response (version-1 path) that matches every accepted field."""
    body = json.loads(Path(path).read_text(encoding='utf-8'))
    if isinstance(body, dict) and isinstance(body.get('response'), dict):
        body = body['response']
    return matching_row(body, wanted_fields(proposal))


def _read_back(proposal, accepted, captured):
    """The first record read after acceptance that matches every accepted field."""
    tools = READ_BACK_TOOLS.get(proposal.get('tool'), ())
    wanted = wanted_fields(proposal)
    found = []
    for path in sorted(captured.glob('*.json')) if accepted and captured.is_dir() else []:
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        observed = observation_time(record.get('observed_at'))
        if record.get('tool') not in tools or observed is None or observed < accepted:
            continue
        row = matching_row(record.get('response'), wanted)
        if row is not None:
            found.append((observed, row))
    return min(found, key=lambda item: item[0])[1] if found else None


def _quoted_subject(subject, deal_ref):
    """The subject as quoted CRM text: its record numbers and plugin sentences dropped, the deal's name linked."""
    return crm_quote(subject, lambda value: link_record_names(value, [deal_ref]))


def _deal_type(subject, deal_ref):
    """The type word of a subject that is "<Type> — <this deal's title>": the deal is then named once, in the link."""
    name = deal_ref.get('name') or ''
    suffix = ' — ' + name
    text = subject or ''
    if name and text.endswith(suffix) and text[:-len(suffix)].strip() and not visible_record_ids(text):
        return text[:-len(suffix)].strip().lower()
    return None


def _titled(kind, subject, deal_ref):
    if _deal_type(subject, deal_ref):
        return str(kind)
    text = _quoted_subject(subject, deal_ref)
    return f'{kind} “{text}”' if text else str(kind)


def _subject_or(subject, deal_ref, fallback):
    if _deal_type(subject, deal_ref):
        return 'the ' + _deal_type(subject, deal_ref)
    return '“' + (_quoted_subject(subject, deal_ref) or fallback) + '”'


def confirm(proposal, acceptance, fresh_path, config, read_back=None, saved=None):
    fresh_path = Path(fresh_path)
    absorb_fresh(fresh_path, proposal, acceptance)
    params = proposal.get('params') or {}
    deal_id = proposal.get('deal_id', params.get('deal_id', params.get('id') if proposal.get('tool') == 'updateDeal' else None))
    run = json.loads(fresh_path.read_text(encoding='utf-8'))
    tables = collect({'calls': [{**call, 'response': response(call, fresh_path.parent)} for call in run.get('calls') or []]})
    deal = next((tables[key][deal_id] for key in ('open_deals', 'won_deals', 'lost_deals') if deal_id in tables[key]), None)
    if deal is None:
        # The fresh file held no deal record (DT-02, 17 Oct: the turn marker was passed as --fresh): name the deal from
        # the saved triage result instead of "the accepted deal · Link unavailable".
        deal = next(({'id': deal_id, 'title': row['title']} for row in (saved or {}).get('ranked_top10', []) + (saved or {}).get('ranked', [])
                     if row.get('id', row.get('deal_id')) == deal_id and row.get('title')), None)
    deal_ref = record_ref(deal or {'id': deal_id, 'title': 'the accepted deal'}, 'deal', config)
    linked = markdown_ref(deal_ref)
    row = (_saved_read_back(proposal, read_back) if read_back is not None
           else _read_back(proposal, observation_time(acceptance.get('accepted_at')), fresh_path.parent / 'captured'))
    tool = proposal.get('tool')
    if row is None:
        # An unconfirmed write still names what was sent, so the user knows what to look for (9 Oct HC-04).
        if tool == 'addActivity':
            change = (f'The {_titled(params.get("type"), params.get("subject"), deal_ref)} on {linked}, due '
                      f'{_readable(params.get("due_date"))},')
        elif tool == 'addNote':
            change = f'The accepted note on {linked}'
        elif tool == 'updateActivity' and ('due_date' in params or 'due_time' in params):
            change = f'The accepted move of an activity on {linked} to {_when(params)}'
        elif tool == 'updateActivity' and 'done' in params:
            change = f'The accepted {"completion" if params["done"] else "reopening"} of an activity on {linked}'
        else:
            change = f'The accepted change on {linked}'
        markdown = (f'{change} was submitted, but no read-back after it matches the accepted details. Check it in '
                    'Pipedrive before doing anything else; do not resubmit it.')
        status = 'uncertain'
    else:
        if tool == 'addActivity':
            markdown = (f'Created the {_titled(params.get("type"), params.get("subject"), deal_ref)} on {linked}, due '
                        f'{_readable(params.get("due_date"))}.')
        elif tool == 'addNote':
            markdown = f'Added the accepted note to {linked}.'
        elif tool == 'updateActivity' and 'done' in params:
            markdown = (f'Marked {_subject_or(row.get("subject"), deal_ref, "the accepted activity")} on {linked} as '
                        f'{"done" if params["done"] else "not done"}.')
        elif tool == 'updateActivity':
            markdown = f'Moved {_subject_or(row.get("subject"), deal_ref, "the accepted activity")} on {linked}, now due {_when(params, row)}.'
        else:
            markdown = f'Updated {linked} with the accepted change.'
        markdown += ' A read-back after the write matches the accepted details. Nothing else was changed.'
        status = 'verified'
    failures = [check for check in check_text(markdown, [deal_ref]) if check['status'] != 'pass']
    if failures:
        raise ValueError('Write confirmation failed presentation checks: ' + json.dumps(failures))
    record_id = row.get('id') if row is not None else None
    return {'status': status, 'markdown': markdown, 'record_id': record_id}


def deal_title(proposal, saved):
    """The deal's title as the report shows it: on the proposal, else the row of any list in the result that names it."""
    if proposal.get('title'):
        return proposal['title']
    deal_id = proposal.get('deal_id')
    for key in ('ranked_top10', 'ranked', 'deals', 'work_rows'):
        for row in saved.get(key) or []:
            if isinstance(row, dict) and row.get('title') and row.get('id', row.get('deal_id')) == deal_id:
                return row['title']
    return None


def field_fact(check, deal):
    if not isinstance(check, dict):
        return None
    if check.get('field') == 'expected_close_date' and check.get('recorded'):
        return f'the close date of {deal} passed on {check["recorded"]}'
    if check.get('field') == 'value':
        return f'{deal} has no deal value recorded'
    return None


def second_confirmation_line(number, proposal, saved):
    """The skill's second confirmation for a status change or an escalated item (SKILL.md §5): asked, not written."""
    deal = deal_title(proposal, saved) or 'this deal'
    if proposal.get('action') == 'updateDeal' and proposal.get('field', 'status') == 'status' and proposal.get('value'):
        change = f'mark {deal} as {str(proposal["value"]).upper()}'
    elif proposal.get('status_change'):
        # R7 (0.7.9): the note says the deal was won or lost; the question names that change, not the verify task.
        change = f'mark {deal} as {str(proposal["status_change"]).upper()}'
    elif fact := field_fact(proposal.get('field_check'), deal):
        # R4/R9 (0.7.10): name the field and what is recorded; the confirmed write is still the review activity.
        change = f'{fact}; I will add the {str(proposal.get("type") or "activity").lower()} "{proposal.get("subject")}"'
    else:
        change = f'{str(proposal.get("type") or "activity").upper()} "{proposal.get("subject")}" on {deal}'
    return f'Item {number} needs a separate confirmation before I write it: {change}. Reply "confirm {number}" or "skip {number}".'


def needs_second_confirmation(proposal):
    return bool(proposal.get('needs_second_confirmation')) or (proposal.get('action') == 'updateDeal' and proposal.get('field', 'status') == 'status')


def status_change_pending(proposal, state):
    """An accepted status change (R7, or an updateDeal status proposal) whose own write has not happened. It is asked
    whatever became of the item's verify task: in the DT-02 rerun (17 Oct) the preflight refused that task because an open
    activity covers the next step, the chat dropped the item, and the WON question never showed (0.7.11, decision 2)."""
    if not proposal:
        return False
    if not (proposal.get('status_change') or (proposal.get('action') == 'updateDeal' and proposal.get('field', 'status') == 'status')):
        return False
    return (state or {}).get('status') not in ('written', 'uncertain', 'failed')


def newest_run_file(runs):
    """The newest triage run file in the runs folder (pd-triage-*.json), or None."""
    folder = Path(runs)
    candidates = sorted(folder.glob('pd-triage-*.json'), key=lambda path: path.stat().st_mtime) if folder.is_dir() else []
    return candidates[-1] if candidates else None


def resolve_run(given, runs):
    """The run file for the turn's call count: the one given when it is a file, else the newest in the runs folder (0.7.15)."""
    if given is not None and given.is_file():
        return given
    found = newest_run_file(runs)
    if given is not None:
        print(f'--run {given} is not a file; using {found if found is not None else "no run file (the tool-call count is left out)"}', file=sys.stderr)
    return found


def turn_reply(accepted, skipped, runs, result=None, run=None):
    """The whole accept turn's reply: each accepted item's ledger text in order, then the run log line."""
    saved = json.loads(Path(result).read_text(encoding='utf-8')) if result is not None and Path(result).exists() else {}
    states = saved.get('action_states') or {}
    proposals = saved_proposals(saved)
    entries = turn_entries(runs, accepted)
    lines, refused_seen = [], False
    for number in accepted:
        entry = entries[number]
        state = states.get(str(number)) or {}
        if number not in proposals:
            # 0.7.12 run (a): the chat accepted an item the report never proposed. The preflight refuses it now; the turn
            # reply says so instead of counting it, and names a write that reached the ledger anyway.
            lines.append(OFF_REPORT.format(number=number, text=entry['markdown']) if entry is not None and entry.get('status') in ('verified', 'uncertain')
                         else NOT_PREPARED.format(number=number))
        elif (entry or {}).get('status') == 'failed' or (entry is None and state.get('status') == 'failed'):
            refused_seen = True
            lines.append(REFUSED_LINE.format(number=number))
        elif entry is not None:
            lines.append(entry['markdown'])
            if entry.get('status') == 'refused' and status_change_pending(proposals.get(number), state):
                # The preflight refused the verify task; the status change still waits for its own yes.
                lines.append(second_confirmation_line(number, proposals[number], saved))
        elif state.get('status') == 'refused' and state.get('reason'):
            reason = state['reason'].rstrip('.')
            lines.append(f'Item {number} was not written: {reason[:1].lower() + reason[1:]}.')
            if status_change_pending(proposals.get(number), state):
                lines.append(second_confirmation_line(number, proposals[number], saved))
        elif refused_seen:
            # After a 403 the remaining accepted items are never sent (DT-09).
            lines.append(f'Item {number} was not sent: after Pipedrive refused a write, the rest stay proposals only.')
        elif number in proposals and needs_second_confirmation(proposals[number]) and state.get('status') in (None, 'pending', 'accepted'):
            # DT-02 (0.7.8 evals): an accepted WON change is asked again, not written; the reply carries the question.
            lines.append(second_confirmation_line(number, proposals[number], saved))
        else:
            lines.append(f'Item {number} has no recorded write. Check it in Pipedrive before sending it again.')
    if refused_seen:
        lines.insert(0, RECONNECT_LINE)
    stopped = stopped_line(runs)
    if stopped:
        # 0.7.13 (ADR-045): the write hook stopped a call this turn; the reply says so in the script's words.
        lines.insert(0, stopped)
    accepted = [number for number in accepted if number in proposals]
    written = sum(1 for number in accepted if entries[number] is not None and entries[number]['status'] == 'verified')
    counts = [f'accepted {len(accepted)}', f'skipped {len(skipped)}', f'written {written}']
    if result is not None and Path(result).exists():
        counts.insert(0, f'Proposed {len(saved.get("proposals") or []) or len(proposals)}')
    if run is not None:
        counts.append(f'{len(json.loads(Path(run).read_text(encoding="utf-8")).get("calls") or [])} tool calls')
    return '\n'.join(lines + ['', ' · '.join(counts)] if lines else [' · '.join(counts)])


def ask_names(number, saved):
    """The record names the item's question may show: the deal's title and the activity subject (not record numbers)."""
    proposal = saved_proposals(saved).get(number) or {}
    return [name for name in (deal_title(proposal, saved), proposal.get('subject')) if name]


def ask_text(number, saved):
    """The question for one report item, from the saved proposal (0.7.12): the chat relays it and never words it."""
    proposals = saved_proposals(saved)
    if number not in proposals:
        return NOT_PROPOSAL.format(number=number, consequence='there is nothing to accept')
    proposal = proposals[number]
    deal = deal_title(proposal, saved) or 'this deal'
    params = proposal.get('params') if isinstance(proposal.get('params'), dict) else {}
    due = _readable(params.get('due_date') or proposal.get('due_date'))
    kind = str(proposal.get('type') or 'activity')
    subject = crm_quote(proposal.get('subject') or '') or 'the proposed step'
    if (proposal.get('tool') or proposal.get('action')) == 'updateActivity':
        change = f'move the {kind.lower()} “{subject}” on {deal} to {due}'
    else:
        change = f'{kind.upper()} “{subject}” on {deal}, due {due}'
    return f'Item {number}: {change}. Reply "accept {number}", "edit {number} <new due date>" or "skip {number}".'


def edit_item(result_path, number, due_date, today=None):
    """Change one report item's due date in the saved result and return the new question (0.7.12, decision E).
    Only the due date is editable; the item's revision is recomputed, so an earlier acceptance no longer names it."""
    path = Path(result_path)
    saved = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    proposals = saved_proposals(saved)
    if number not in proposals:
        return NOT_PROPOSAL.format(number=number, consequence='there is nothing to edit')
    try:
        day = date.fromisoformat(str(due_date))
    except ValueError:
        return f'Item {number} keeps its date: "{due_date}" is not a date (YYYY-MM-DD).'
    if day < (today or date.today()):
        return f'Item {number} keeps its date: {_readable(day.isoformat())} is in the past.'
    state = (saved.get('action_states') or {}).get(str(number)) or {}
    if state.get('status') in ('written', 'verified', 'uncertain', 'failed', 'submitted'):
        return f'Item {number} cannot be edited: its write is already {state["status"]}. Check it in Pipedrive.'
    proposal = next((item for item in saved.get('proposals') or [] if item.get('item') == number), None)
    if proposal is None:
        return f'Item {number} cannot be edited here: it is not in the saved proposals list. Run triage again.'
    proposal['due_date'] = day.isoformat()
    if isinstance(proposal.get('params'), dict) and 'due_date' in proposal['params']:
        proposal['params']['due_date'] = day.isoformat()
    if 'revision' in proposal:
        proposal['revision'] = proposal_revision(proposal)
    if state.get('status') in ('accepted', 'pending'):
        saved['action_states'].pop(str(number), None)
    save_result(path, saved)
    return ask_text(number, saved)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--turn', action='store_true', help='print the whole accept turn reply from the ledger and nothing else')
    parser.add_argument('--ask', metavar='N[,N…]', help="print the question for report item N from the saved result; several "
                        "numbers (1,2) print one text in order, for a request that names several deals (0.7.19 check (i))")
    parser.add_argument('--edit', type=int, metavar='N', help="change report item N's due date in the saved result, then print its question")
    parser.add_argument('--due-date', help='--edit: the new due date, YYYY-MM-DD')
    parser.add_argument('--accepted', type=lambda value: [int(part) for part in value.split(',') if part.strip()], default=[],
                        help='turn mode: the accepted item numbers, in order')
    parser.add_argument('--skipped', type=lambda value: [int(part) for part in value.split(',') if part.strip()], default=[],
                        nargs='?', const=[], help='turn mode: the skipped item numbers; may be given with no value')
    parser.add_argument('--run', type=Path, help='turn mode: the run file, whose call count goes in the run log line; '
                                                 'the newest pd-triage-*.json in the runs folder when absent or not a file')
    parser.add_argument('--runs', type=Path, help='the runs folder holding the confirmation ledger (default: ./.pipedrive-sales/runs)')
    for key in ('proposal', 'fresh', 'config'):
        parser.add_argument('--' + key, type=Path)
    parser.add_argument('--acceptance', type=Path, help='Required unless --read-back supplies the read-back')
    parser.add_argument('--read-back', type=Path, help='Saved read-back response of the written record (version-1 path)')
    parser.add_argument('--result', type=Path, help='The saved triage result; the confirmed item state is recorded in it')
    parser.add_argument('--item', type=int, help="The item number to record; defaults to the proposal's item")
    parser.add_argument('--refused', action='store_true', help='record that Pipedrive refused this item\'s write (a 403); needs only --item')
    args = parser.parse_args(argv)
    if args.ask is not None or args.edit is not None:
        result = args.result if args.result is not None else DEFAULT_RESULT
        if args.edit is not None:
            numbers = [args.edit]
        else:
            try:
                numbers = [int(part) for part in re.split(r'[,\s]+', str(args.ask).strip()) if part]
            except ValueError:
                parser.error('--ask takes item numbers, such as 2 or 1,2')
            if not numbers:
                parser.error('--ask takes item numbers, such as 2 or 1,2')
        number = numbers[0]
        saved = json.loads(result.read_text(encoding='utf-8')) if result.exists() else {}
        if args.edit is not None:
            if not args.due_date:
                parser.error('--edit needs --due-date YYYY-MM-DD')
            text = edit_item(result, number, args.due_date)
        else:
            text = '\n\n'.join(ask_text(item, saved) for item in numbers)
        stopped = stopped_line(args.runs or Path('./.pipedrive-sales/runs'))
        if stopped:
            text = stopped + '\n' + text
        names = [name for item in numbers for name in ask_names(item, saved)]
        identifiers = visible_record_ids(text, names)
        if identifiers:
            print('The question shows record identifiers, so nothing was printed: ' + json.dumps(identifiers), file=sys.stderr)
            return 3
        # Several numbers make one text that is the whole reply (0.7.19 check (i): the chat wrote the second deal's line
        # itself), so it is saved like a refusal; a single real item's question goes into the host's card and is not.
        if args.edit is None and (len(numbers) > 1 or number not in saved_proposals(saved)):
            # 0.7.13 run (iii): the refusal is the whole reply, so it is saved for the Stop hook like a turn reply. A real
            # item's question is not: it goes into the host's selectable question, which the hook cannot see.
            save_expected_reply(text, 'confirm', args.runs or Path('./.pipedrive-sales/runs'))
        print(framed(text))
        return 0
    if args.refused:
        if args.item is None:
            parser.error('--refused needs --item <n>')
        runs = args.runs or Path('./.pipedrive-sales/runs')
        markdown = REFUSED_LINE.format(number=args.item)
        record_confirmation(runs, args.item, 'failed', markdown)
        if args.result is not None and args.result.exists():
            record_action_state(args.result, args.item, {'status': 'failed'})
        print(markdown)
        return 0
    if args.turn:
        if args.runs is None:
            args.runs = Path('./.pipedrive-sales/runs')
        if args.result is None:
            args.result = DEFAULT_RESULT
        try:
            text = turn_reply(args.accepted, args.skipped, args.runs, args.result, resolve_run(args.run, args.runs))
        except (ValueError, KeyError, OSError) as error:
            print(f'No turn reply was built ({error})', file=sys.stderr)
            return 3
        # Each item was checked against its own record refs; the turn's links are the records it names.
        identifiers = visible_record_ids(text, re.findall(r'\[([^\]]+)\]\(https?://[^)\s]+\)', text))
        if identifiers:
            print('Turn reply shows record identifiers, so nothing was printed: ' + json.dumps(identifiers), file=sys.stderr)
            return 3
        save_expected_reply(text, 'confirm', args.runs)
        print(framed(text))
        return 0
    missing = [flag for flag in ('proposal', 'fresh', 'config') if getattr(args, flag) is None]
    if missing:
        parser.error('the per-item mode needs ' + ', '.join('--' + flag for flag in missing) + ' (or pass --turn)')
    if args.acceptance is None and args.read_back is None:
        parser.error('--acceptance is required unless --read-back is given')
    if args.runs is None:
        # Not the folder of --fresh: turn mode reads ./.pipedrive-sales/runs, and DT-10 kept its fresh reads elsewhere.
        args.runs = Path('./.pipedrive-sales/runs')
    try:
        proposal = json.loads(args.proposal.read_text())
        acceptance = json.loads(args.acceptance.read_text()) if args.acceptance else {}
        saved = json.loads(args.result.read_text()) if args.result is not None and args.result.exists() else None
        result = confirm(proposal, acceptance, args.fresh, json.loads(args.config.read_text()), args.read_back, saved)
    except (ValueError, KeyError, OSError) as error:
        print(f'State not recorded: no confirmation was built ({error})', file=sys.stderr)
        return 3
    print(result['markdown'])
    item = args.item if args.item is not None else proposal.get('item')
    try:
        if item is None:
            raise ValueError('the proposal has no item number; pass --item <n>')
        record_confirmation(args.runs, item, result['status'], result['markdown'])
        if args.result is None:
            raise ValueError('no --result path was given')
        if not args.result.exists():
            # No saved result means nothing can show this item as pending; an exit 3 here made the model explain the write (DT-11).
            print(f'No saved result at {args.result}; state not recorded.', file=sys.stderr)
            return 0
        state = {'status': 'written' if result['status'] == 'verified' else 'uncertain', 'id': result['record_id'],
                 'revision': proposal.get('revision')}
        record_action_state(args.result, item, {key: value for key, value in state.items() if value is not None})
    except (ValueError, KeyError, OSError) as error:
        print(f'State not recorded: {error}', file=sys.stderr)
        return 3
    return 0


if __name__ == '__main__':
    sys.exit(main())
