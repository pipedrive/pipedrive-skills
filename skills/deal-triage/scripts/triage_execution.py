"""Attach item execution state only from matching saved write responses."""

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pdcore import load_response_file  # noqa: E402


def _response(call, run_directory=None):
    response = call.get("response")
    if response is None and call.get("response_file"):
        path = Path(call["response_file"])
        if not path.is_absolute() and run_directory is not None:
            path = Path(run_directory) / path
        response = load_response_file(path)
    return response


READ_BACK_TOOLS = {'addActivity': ('getActivity', 'getActivities'), 'updateActivity': ('getActivity', 'getActivities'),
                   'addNote': ('getNotes', 'getNote'), 'updateDeal': ('getDeal',)}


def read_back_confirms(calls, index, run_directory=None):
    """Version-1 path (C-DT-M11): a successful write counts as written only when a later `write_read_back` call
    returns the written record (same id) with every accepted field; otherwise it is uncertain and never retried."""
    from write_confirmation import matching_row, wanted_fields
    call = calls[index]
    tool = (call.get('tool') or '').split('__')[-1]
    data = (_response(call, run_directory) or {}).get('data')
    identifier = data.get('id') if isinstance(data, dict) else None
    fields = wanted_fields({'params': call.get('params') or {}})
    for later in calls[index + 1:]:
        if later.get('purpose') != 'write_read_back' or (later.get('tool') or '').split('__')[-1] not in READ_BACK_TOOLS.get(tool, ()):
            continue
        row = matching_row(_response(later, run_directory), fields)
        if row is not None and identifier is not None and row.get('id') == identifier:
            return True
    return False


def action_states(result, run, run_directory=None):
    if result.get('report_schema_version') == 2 and result.get('proposals'):
        from pdwrite import reconcile, proposal_revision
        states = {str(proposal['item']): {'status': 'pending'} for proposal in result['proposals']}
        proposals = {str(proposal['item']): proposal for proposal in result['proposals']}
        for prepared in run.get('prepared_actions') or []:
            if str(prepared['item']) not in states:
                continue
            current = proposals[str(prepared['item'])]
            if prepared.get('revision') != proposal_revision(current) or (current.get('action') != 'append_preparation' and prepared.get('params') != current.get('params')):
                states[str(prepared['item'])] = {'status': 'pending', 'reason': 'Prepared revision differs from the current proposal; historical execution is retained in the ledger'}
                continue
            matched = [(index, call) for index, call in enumerate(run.get('calls') or []) if (call.get('tool') or '').split('__')[-1] == prepared['tool'] and call.get('params') == prepared['params']]
            if not matched:
                states[str(prepared['item'])] = {'status': 'accepted', 'revision': prepared['revision']}
                continue
            write_index, observed_call = matched[-1]
            call = {**observed_call, 'response': _response(observed_call, run_directory)}
            read = next((_response(candidate, run_directory) for candidate in reversed((run.get('calls') or [])[write_index + 1:])
                         if candidate.get('purpose') == 'write_read_back' and candidate.get('item') == prepared['item']
                         and (candidate.get('tool') or '').split('__')[-1] == {'addActivity': 'getActivity', 'updateActivity': 'getActivity', 'addNote': 'getNotes', 'updateDeal': 'getDeal'}.get(prepared['tool'])), None)
            if prepared['tool'] == 'addNote' and isinstance(read, dict) and isinstance(read.get('data'), list):
                identifier = (call.get('response') or {}).get('data', {}).get('id')
                read = {**read, 'data': next((row for row in read['data'] if row.get('id') == identifier), None)}
            states[str(prepared['item'])] = reconcile(prepared, call, read)
        return states
    states = {}
    for proposal in result.get('proposals') or []:
        number = proposal['item']
        status = 'skipped' if number in (run.get('skipped_items') or []) else 'accepted' if number in (run.get('accepted_items') or []) else 'pending'
        state = {'status':status, 'activity_id':None, 'executed_at':None}
        for index, call in enumerate(run.get('calls') or []):
            if (call.get('tool') or '').split('__')[-1] != proposal.get('action', 'addActivity'):
                continue
            params=call.get('params') or {}
            record_key='lead_id' if 'lead_id' in proposal else 'deal_id'
            if proposal.get('action')=='updateActivity':
                matched = params.get('id') == proposal.get('activity_id') and params.get('due_date') == proposal.get('due_date')
            else:
                matched = all(params.get(key)==proposal.get(key) for key in (record_key,'subject','type','due_date'))
                if 'person_id' in proposal:
                    matched = matched and params.get('participants', []) == ([{'person_id': proposal['person_id'], 'primary': True}] if proposal['person_id'] is not None else [])
            if not matched:
                continue
            response=_response(call, run_directory) or {}
            data=response.get('data') if isinstance(response,dict) else None
            if isinstance(response,dict) and response.get('success') is True and isinstance(data,dict) and data.get('id') is not None and (data.get(record_key) is None or data.get(record_key) == proposal.get(record_key)):
                state={'status':'written','activity_id':data['id'],
                       'executed_at':(data.get('update_time') or call.get('observed_at')) if proposal.get('action')=='updateActivity' else (data.get('add_time') or call.get('observed_at')),
                       'created_at':data.get('add_time'), 'updated_at':data.get('update_time'), 'observed_at':call.get('observed_at')}
                if not read_back_confirms(run['calls'], index, run_directory):
                    state={**state,'status':'uncertain','reason':'The write succeeded but no matching read-back was saved; check it in Pipedrive and do not resubmit'}
                break
            if isinstance(response, dict) and (response.get('success') is False or response.get('error')):
                state={'status':'failed','activity_id':None,'executed_at':call.get('observed_at')}
        states[str(number)]=state
    return states


RECORDED = ('written', 'failed', 'uncertain')


def keep_recorded(saved, states):
    """A state the confirmation recorded stays unless the run now shows a write result (0.7.2)."""
    for key, state in (saved.get('action_states') or {}).items():
        if state.get('status') in RECORDED and (states.get(key) or {}).get('status', 'pending') in ('pending', 'accepted'):
            states[key] = state
    return states


def refresh(result, run, run_directory=None):
    import re
    from copy import deepcopy
    updated=deepcopy(result)
    updated['action_states']=keep_recorded(result, action_states(updated,run,run_directory))
    if result.get('report_schema_version') == 2:
        from report_contract import report_markdown
        counts = {status: sum(state.get('status') == status or (status == 'verified' and state.get('status') == 'written')
                              for state in updated['action_states'].values())
                  for status in ('pending', 'accepted', 'verified', 'uncertain', 'failed')}
        updated['run_log_line'] = ' · '.join(status + ': ' + str(count) for status, count in counts.items())
        updated['markdown'] = report_markdown(updated) + '\n\n' + updated['run_log_line']
        return updated
    created=[]; written=0; failed=0; uncertain=0
    write_tools={'addActivity','addNote','updateActivity','updateDeal'}
    for index,call in enumerate(run.get('calls') or []):
        tool=(call.get('tool') or '').split('__')[-1]
        response=_response(call, run_directory) or {}
        if tool not in write_tools: continue
        if isinstance(response,dict) and response.get('success') is True:
            if not read_back_confirms(run['calls'], index, run_directory):
                uncertain+=1
                continue
            written+=1
            if tool=='addActivity' and isinstance(response.get('data'),dict) and response['data'].get('id') is not None:
                created.append(response['data']['id'])
        else: failed+=1
    log=(f"Proposed {len(updated.get('proposals') or [])} · accepted {len(run.get('accepted_items') or [])} · "
         f"skipped {len(run.get('skipped_items') or [])} · written {written} · {len(run.get('calls') or [])} tool calls")
    if created: log+=f' · created activities: {len(created)}'
    if failed: log+=f' · failed writes: {failed}'
    if uncertain: log+=f' · uncertain writes: {uncertain}'
    old_log=updated.get('run_log_line') or ''
    updated['run_log_line']=log; updated['created_activity_ids']=created
    updated['triage_stats_delta']={'proposed':len(updated.get('proposals') or []),'accepted':len(run.get('accepted_items') or []),'written':written,'skipped':len(run.get('skipped_items') or [])}
    lines=[]
    states=updated['action_states']
    subjects={str(proposal['item']): proposal.get('subject') or 'Activity name unavailable'
              for proposal in updated.get('proposals') or []}
    for line in (updated.get('markdown') or '').splitlines():
        if line==old_log: continue
        if 'Nothing has been written yet.' in line and (written or uncertain): continue
        commands=re.findall(r'\[accept (\d+)\]',line)
        if commands and any(states.get(n,{}).get('status')!='pending' for n in commands):
            n=commands[0]; state=states[n]
            line=f"   Execution: {state['status']}"+(f" · {subjects[n]}" if state.get('activity_id') else '')
        lines.append(line)
    summaries=[f"Item {n}: {state['status']}"+(f" · {subjects[n]}" if state.get('activity_id') else '') for n,state in states.items()]
    updated['markdown']='Original scan: '+str(updated.get('today'))+'. Findings below describe that scan.\nExecution status: '+'; '.join(summaries)+'.\n\n'+'\n'.join(lines)+'\n'+log
    updated['disclosures']=[line for line in updated.get('disclosures') or [] if line!=old_log and not ((written or uncertain) and 'Nothing has been written yet.' in line)]+[log]
    if isinstance(updated.get('presentation'), dict):
        updated['presentation']['disclosures']=updated['disclosures']
    return updated


def main():
    import argparse
    import json
    from pathlib import Path
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--result',required=True);parser.add_argument('--run',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args(); path=Path(args.output)
    if path.exists(): parser.error('Choose a new output path; preserve the original snapshot')
    run_path=Path(args.run)
    result=refresh(json.loads(Path(args.result).read_text()),json.loads(run_path.read_text()),run_path.parent)
    path.write_text(json.dumps(result,indent=2)+'\n')
    print(result['run_log_line'])


if __name__=='__main__':
    main()
