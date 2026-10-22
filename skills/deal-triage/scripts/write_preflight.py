"""Check a confirmed activity proposal against fresh deal and activity reads.

Thin caller of the shared write rules (scripts/write_rules.py, ADR-019). The whitelist and
fresh-read rules live there; this script keeps the command line the deal-triage skill body
documents and adds the triage-specific rules: a new activity carries the deal's contact, and what the guard
returns must pass write_rules.validate for deal-triage (ADR-038), so no parameter outside the one whitelist is printed.

Since 0.7.14 (ADR-044 note) the chat types nothing into this check: without --proposal the saved item is the proposal;
without --deal-response / --activities-response the fresh reads are this session's newest hook captures of the deal and
its activities (runs/captured/); a proposal whose contact is null or missing takes the deal's contact from the fresh read.
DT-02 on 0.7.14 (19 Oct) had the chat copy a saved item with person_id null into its own proposal file and the check
refused the write as a changed contact, so nothing was written.
"""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import write_rules  # noqa: E402
from pdwrite import scripted, scripted_item  # noqa: E402


def preflight(proposal, deal_response, activity_response, today, execution=None):
    deal_response = write_rules.unwrap_capture(deal_response)
    activity_response = write_rules.unwrap_capture(activity_response)
    if proposal.get('deal_id') is None:
        raise ValueError('This preflight supports deal activities; lead activities need a fresh lead check')
    deal = deal_response.get('data') if isinstance(deal_response, dict) else None
    person = deal.get('person_id') if isinstance(deal, dict) else None
    person = person.get('value', person.get('id')) if isinstance(person, dict) else person
    new_activity = proposal.get('action', 'addActivity') == 'addActivity'
    if new_activity:
        if proposal.get('person_id') is None:
            # The saved item names no contact (the report does not show one): the deal's contact from the fresh read is it.
            proposal['person_id'] = person
        elif proposal.get('person_id') != person:
            raise ValueError('Deal contact changed; revise and reconfirm')
    result = write_rules.stale_guard(proposal, deal_response, activity_response, today, execution)
    if new_activity and person is not None:
        result['params']['participants'] = [{'person_id': person, 'primary': True}]
    violations = write_rules.validate('deal-triage', result['tool'], result['params'])
    if violations:
        raise ValueError('; '.join(violations))
    return result


def newest_capture(runs, tool, key, deal_id):
    """This session's newest hook capture of `tool` whose params carry key == deal_id (runs/captured/), else None."""
    found = []
    for path in Path(runs, 'captured').glob('*.json'):
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        params = record.get('params') if isinstance(record, dict) and isinstance(record.get('params'), dict) else {}
        if str(record.get('tool') or '').split('__')[-1] == tool and str(params.get(key)) == str(deal_id):
            found.append((str(record.get('observed_at') or ''), record))
    return max(found, key=lambda item: item[0])[1] if found else None


NO_CAPTURE = ("no {tool} read of deal {deal_id} is captured in {runs}/captured: re-read the deal and its activities with the connector "
              "in this turn (the plugin hook saves them), or pass --deal-response and --activities-response on a host without hooks")


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--proposal',type=Path,help='an edited proposal; without it the saved item is the proposal (0.7.14)')
    parser.add_argument('--deal-response',type=Path,help="the fresh deal read; without it this session's newest hook capture of the deal (0.7.14)")
    parser.add_argument('--activities-response',type=Path,help="the fresh activity read; without it this session's newest hook capture for the deal (0.7.14)")
    parser.add_argument('--today',required=True)
    parser.add_argument('--result',required=True,type=Path,help="the saved triage result; the write must be one of its proposals (0.7.12)")
    parser.add_argument('--item',required=True,type=int,help='the accepted item number in that result')
    parser.add_argument('--runs',type=Path,default=Path('./.pipedrive-sales/runs'),help='the runs folder; the prepared write is saved there for the plugin write hook (0.7.13)')
    args=parser.parse_args()
    try:
        saved=json.loads(args.result.read_text(encoding='utf-8')) if args.result.exists() else {}
        proposal=json.loads(args.proposal.read_text()) if args.proposal else dict(scripted_item(saved,args.item))
        proposal.setdefault('item',args.item)
        saved_item=scripted(scripted_item(saved,args.item),proposal)
        deal_id=proposal.get('deal_id')
        deal_response=json.loads(args.deal_response.read_text()) if args.deal_response else newest_capture(args.runs,'getDeal','id',deal_id)
        if deal_response is None:
            raise ValueError(NO_CAPTURE.format(tool='getDeal',deal_id=deal_id,runs=args.runs))
        activity_response=(json.loads(args.activities_response.read_text()) if args.activities_response
                           else newest_capture(args.runs,'getActivities','deal_id',deal_id))
        if activity_response is None:
            raise ValueError(NO_CAPTURE.format(tool='getActivities',deal_id=deal_id,runs=args.runs))
        result=preflight(proposal,deal_response,activity_response,args.today)
        write_rules.record_prepared_action(args.runs,result['tool'],result['params'],item=args.item,revision=saved_item.get('revision'),skill='deal-triage')
    except (ValueError,OSError,KeyError) as error: parser.error(str(error))
    print(json.dumps(result))


if __name__=='__main__': main()
