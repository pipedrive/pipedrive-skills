"""Retain local run evidence and advance only the completed skill's checkpoint."""

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))

from report_identity import report_skill  # noqa: E402
from pdcore import load_response_file  # noqa: E402

SKILLS = ('pipeline-brief', 'deal-triage', 'forecast')


def checkpoint(settings, skill):
    if settings.get('_workflow_v2'):
        key = skill + ':' + str(settings.get('_profile_id')) + ':' + str(settings.get('_scope_fingerprint'))
        return (settings.get('profile_runs') or {}).get(key)
    return (settings.get('last_runs') or {}).get(skill, settings.get('last_run'))


def complete(settings, skill, result, outputs_complete, setup_verified):
    if skill not in SKILLS or report_skill(result) != skill:
        raise ValueError('Checkpoint must match the completed report skill')
    if not outputs_complete or not setup_verified:
        raise ValueError('Verify setup and finish selected outputs before advancing the checkpoint')
    if result.get('pass') == 1:
        raise ValueError('Finish the final compute pass before advancing the checkpoint')
    if result.get('report_schema_version') == 2 and (result.get('scan_coverage') or {}).get('complete') is not True:
        raise ValueError('Incomplete profile coverage cannot advance the checkpoint')
    if any(result.get(key) for key in ('data_unavailable', 'integrity_stop', 'open_cursor_unfollowed', 'partial_line')):
        raise ValueError('Incomplete scope or evidence cannot advance the checkpoint')
    if any(state.get('unavailable') for state in (result.get('rule_execution') or {}).values() if isinstance(state, dict)):
        raise ValueError('Unavailable rule evidence cannot advance the checkpoint')
    if result.get('report_schema_version') == 2 and any(
            state.get('enabled') and state.get('available') is not True
            for name, state in (result.get('feature_availability') or {}).items() if name in ('silence', 'next_activity')):
        raise ValueError('Incomplete accepted rule evidence cannot advance the checkpoint')
    if (result.get('reconciliation') or {}).get('passed') is False:
        raise ValueError('Failed reconciliation cannot advance the checkpoint')
    today = result.get('today')
    datetime.strptime(today, '%Y-%m-%d')
    updated = deepcopy(settings)
    if result.get('report_schema_version') == 2:
        if not result.get('profile_id') or not result.get('scope_fingerprint'):
            raise ValueError('Profile identity and scope fingerprint are required')
        if result.get('settings_revision') is not None and result['settings_revision'] != settings.get('revision'):
            raise ValueError('Settings revision changed during this report')
        key = skill + ':' + str(result['profile_id']) + ':' + result['scope_fingerprint']
        previous = (updated.get('profile_runs') or {}).get(key)
        if previous and today < previous:
            raise ValueError('An older report cannot move a checkpoint backwards')
        updated.setdefault('profile_runs', {})[key] = today
        return updated
    # Freeze the old shared baseline for every skill before changing one of them.
    updated['last_runs'] = {name: checkpoint(settings, name) for name in SKILLS}
    previous = updated['last_runs'][skill]
    if previous and today < previous:
        raise ValueError('An older report cannot move a checkpoint backwards')
    updated['last_runs'][skill] = today
    return updated


def retain(run_path, result_path, directory, attachments=()):
    run_path, result_path = Path(run_path), Path(result_path)
    run = json.loads(run_path.read_text())
    result = json.loads(result_path.read_text())
    files = [('run.json', run_path), ('result.json', result_path)]
    for index, call in enumerate(run.get('calls') or []):
        if call.get('response_file'):
            source = Path(call['response_file'])
            if not source.is_absolute():
                source = run_path.parent / source
            files.append((f'response-{index}.json', source))
        elif 'response' not in call:
            raise ValueError(f'Call {index} has no saved raw response')
    for index, attachment in enumerate(attachments):
        source=Path(attachment)
        files.append((f'asset-{index}-{source.name}', source))
    # Read all dependencies before creating the archive; never invent missing evidence.
    contents = [(name, source, source.read_bytes()) for name, source in files]
    for name, source, content in contents:
        if name.startswith('response-'):
            response = load_response_file(source)
            if isinstance(response, dict) and str(response.get('error', '')).startswith('response_file '):
                raise ValueError(response['error'])
    target = Path(directory) / (report_skill(result) + '-' + str(result.get('today')) + '-' + datetime.now(timezone.utc).strftime('%H%M%S%f'))
    target.mkdir(parents=True, exist_ok=False)
    manifest = []
    for name, source, content in contents:
        (target / name).write_bytes(content)
        manifest.append({'file': name, 'original_path': str(source), 'sha256': hashlib.sha256(content).hexdigest()})
    replay=deepcopy(run)
    for index, call in enumerate(replay.get('calls') or []):
        if call.get('response_file'):
            call['response_file']=f'response-{index}.json'
    (target / 'replay-run.json').write_text(json.dumps(replay, indent=2)+'\n')
    (target / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--skill', choices=SKILLS, required=True)
    parser.add_argument('--run', required=True)
    parser.add_argument('--result', required=True)
    parser.add_argument('--evidence-dir', required=True)
    parser.add_argument('--attachment', action='append', default=[], help='Builder output, layout, hash/proof or document delivery response to retain')
    parser.add_argument('--outputs-complete', action='store_true')
    parser.add_argument('--setup-verified', action='store_true')
    args = parser.parse_args()
    path = Path(args.config)
    settings = json.loads(path.read_text())
    result = json.loads(Path(args.result).read_text())
    updated = complete(settings, args.skill, result, args.outputs_complete, args.setup_verified)
    archive = retain(args.run, args.result, args.evidence_dir, args.attachment)
    from sales_settings import save_verified
    updated, saved_path, persisted = save_verified(updated, [path])
    if not persisted:
        raise ValueError('Checkpoint persistence could not be verified')
    value = (updated.get('profile_runs') or {}).get(args.skill + ':' + str(result.get('profile_id')) + ':' + str(result.get('scope_fingerprint'))) if result.get('report_schema_version') == 2 else checkpoint(updated, args.skill)
    print(json.dumps({'persisted': True, 'skill': args.skill, 'checkpoint': value, 'evidence': str(archive.resolve())}))


if __name__ == '__main__':
    main()
