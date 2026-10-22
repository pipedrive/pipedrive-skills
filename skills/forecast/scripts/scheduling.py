"""Validate optional host jobs without treating CRM activities as agent schedules."""

from copy import deepcopy
from datetime import time

from sales_settings import valid_timezone


def propose_job(profile, request, capabilities):
    revision = profile.get('revision', profile.get('_settings_revision'))
    zone = valid_timezone(profile.get('timezone'))
    scope = profile.get('scope')
    skill = profile.get('_report_skill') or request.get('skill')
    if not zone or not scope or revision is None or skill not in ('pipeline-brief', 'deal-triage', 'forecast'):
        raise ValueError('Accepted skill, revision, timezone and scope are required')
    expected = {'settings_revision': revision, 'profile_id': profile.get('_profile_id'),
                'timezone': zone, 'scope': scope, 'skill': skill,
                'mode': 'scheduled', 'read_only': True, 'text_only': True, 'interactive': False,
                'created': False, 'destination_confirmed': False}
    if any(key in request and (request[key] != value or type(request[key]) is not type(value))
           for key, value in expected.items()):
        raise ValueError('Schedule request differs from accepted settings or execution safeguards')
    days = request.get('days')
    if not isinstance(days, list) or not days or any(day not in ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday') for day in days):
        raise ValueError('Choose named schedule days')
    clock = time.fromisoformat(request.get('time', ''))
    if clock.tzinfo is not None:
        raise ValueError('Schedule time must be a local clock time in the accepted named timezone')
    if request.get('window_rule') not in ('today', 'rolling', 'current_month', 'current_quarter') or not request.get('destination'):
        raise ValueError('A runtime window and proposed destination are required')
    return {**deepcopy(expected),
            'days': list(dict.fromkeys(days)), 'time': request['time'], 'window_rule': request['window_rule'],
            'destination': request['destination'],
            'host_status': 'available_for_review' if capabilities.get('scheduler') is True and capabilities.get('durable_settings') is True else 'unsupported'}


def validate_job(profile, job, capabilities):
    if capabilities.get('scheduler') is not True or capabilities.get('durable_settings') is not True:
        return {'status': 'unsupported', 'reason': 'No verified scheduler with durable access to these settings', 'created': False}
    if job.get('settings_revision') != profile.get('revision', profile.get('_settings_revision')):
        raise ValueError('Review the current settings revision before scheduling')
    zone = valid_timezone(job.get('timezone'))
    if not zone or zone != profile.get('timezone'):
        raise ValueError('Schedule timezone differs from the accepted profile timezone')
    if job.get('skill') not in ('pipeline-brief', 'deal-triage', 'forecast'):
        raise ValueError('Choose an installed skill')
    if job.get('window_rule') not in ('today', 'rolling', 'current_month', 'current_quarter'):
        raise ValueError('Use a runtime window rule rather than a frozen report date')
    days = job.get('days')
    if not isinstance(days, list) or not days or any(day not in ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday') for day in days):
        raise ValueError('Choose named schedule days')
    time.fromisoformat(job.get('time', ''))
    if not job.get('destination') or not profile.get('scope'):
        raise ValueError('Accepted destination and business scope are required')
    return {'status': 'ready_for_host_creation', 'created': False,
            'job': {**deepcopy(job), 'profile_id': profile.get('_profile_id'), 'mode': 'scheduled',
                    'read_only': True, 'text_only': True, 'interactive': False}}
