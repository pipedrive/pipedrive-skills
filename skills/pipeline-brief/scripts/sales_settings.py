"""Confirmed sales profiles, revision-safe storage and short setup transitions."""

from copy import deepcopy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from account_context import OWNER_ME, PRIORITY_FIELD_CAP, deal_field_choices, priority_field_options, resolve_scope, trial_demo_signs, user_names
from pdcore import order_unknown_in_scope, person_name, stage_context, stage_order_notices
from pipedrive_links import normalize_domain, preserve_company_settings

SKILLS = ('pipeline-brief', 'deal-triage', 'forecast')
MAX_OPTIONS = 4  # the host's selectable question tool takes at most four choices per card (final_response.MAX_OPTIONS)
MORE_PIPELINES, MORE_PEOPLE = 'More pipelines', 'More people'


def paged(options, more_label, page=None):
    """The options one card can carry (0.7.18, decision 4A): every option when at most MAX_OPTIONS, otherwise three of
    them and `more_label` as the fourth, the next page on the next card, the last page whole when it fits. Returns the
    options and the paging (`page`, `pages`, `more`), None when the list fits on one card. The rep may name a choice
    from any page; the chat writes the next page number into the answers (`scope_page`, `owner_page`) and asks again."""
    options = list(options)
    if len(options) <= MAX_OPTIONS:
        return options, None
    pages, start = [], 0
    while start < len(options):
        remaining = options[start:]
        if len(remaining) <= MAX_OPTIONS:
            pages.append(remaining)
            break
        pages.append(remaining[:MAX_OPTIONS - 1] + [more_label])
        start += MAX_OPTIONS - 1
    try:
        number = min(max(int(page or 1), 1), len(pages))
    except (TypeError, ValueError):
        number = 1
    shown = pages[number - 1]
    return shown, {'page': number, 'pages': len(pages), 'more': more_label if shown and shown[-1] == more_label else None}
SALES_MOTIONS = ('engagement_led', 'speed_to_lead', 'hybrid')
SALES_MOTION_CHOICES = {'Warmest and biggest deals first': 'engagement_led', 'Newest leads first': 'speed_to_lead',
                        'Both: newest leads first, then warmest deals': 'hybrid'}
SELLS_THROUGH = ('trials', 'demos', 'both', 'neither')
# The lost deals a rep may still work, worded for what the account sells through. A demo counts like a trial: its own
# section, outside open totals and forecast. Being Lost alone never qualifies a deal.
LOST_GROUPS = {'trials': ('the expired trials you still pursue', 'an expired trial'),
               'demos': ('the deals lost after a demo that you still pursue', 'a deal lost after a demo'),
               'both': ('the expired trials and the deals lost after a demo that you still pursue', 'either')}


# The only rule keys the profile reads, and the keys each rule may carry. A key outside this table used to read as "off".
RULE_SHAPES = {'silence': ('enabled', 'days'), 'no_next_activity': ('enabled',), 'notes_stage_mismatch': ('enabled',),
               'stage_stale': ('enabled', 'settings'), 'close_slip': ('enabled', 'days'), 'single_thread': ('enabled', 'from_stage_id')}


def validate_rules(rules):
    """Reject a rule key the profile does not read, or a rule that is not {enabled, ...}, instead of treating it as off."""
    if rules in (None, {}):
        return
    accepted = ', '.join(RULE_SHAPES)
    shape = '; '.join(key + ' {' + ', '.join(keys) + '}' for key, keys in RULE_SHAPES.items())
    if not isinstance(rules, dict):
        raise ValueError(f'rules must be an object of rule objects. Accepted rule keys: {accepted}. Shapes: {shape}')
    for key, rule in rules.items():
        if key not in RULE_SHAPES:
            raise ValueError(f'Unknown rule key "{key}". Accepted rule keys: {accepted}. Each rule has the shape {{enabled, days}} '
                             f'(for example {{"silence": {{"enabled": true, "days": 7}}}}); the shapes are {shape}')
        if not isinstance(rule, dict) or type(rule.get('enabled')) is not bool or set(rule) - set(RULE_SHAPES[key]):
            raise ValueError(f'Rule "{key}" must be an object with a true or false "enabled" and only these keys: {", ".join(RULE_SHAPES[key])}')
        if rule['enabled'] and key in ('close_slip', 'silence') and (type(rule.get('days')) is not int or rule['days'] < (1 if key == 'silence' else 0)):
            raise ValueError(f'Enabled rule "{key}" needs "days" as a whole number')
        if rule['enabled'] and key == 'single_thread' and (type(rule.get('from_stage_id')) is not int or rule['from_stage_id'] < 1):
            raise ValueError('Enabled rule "single_thread" needs "from_stage_id" as a positive stage id')
        if rule['enabled'] and key == 'stage_stale' and not isinstance(rule.get('settings'), dict):
            raise ValueError('Enabled rule "stage_stale" needs "settings" as an object')


def validate_schema(settings):
    if not isinstance(settings, dict) or settings.get('schema_version', 1) not in (1, 2):
        raise ValueError('Unsupported settings schema; update the installed skills before writing')
    return settings.get('schema_version', 1)


def valid_timezone(value):
    try:
        return ZoneInfo(value).key if isinstance(value, str) else None
    except (ValueError, ZoneInfoNotFoundError):
        return None


def scope_fingerprint(profile):
    fields = ('scope', 'views', 'view_roles', 'combination', 'priority_order', 'rules', 'segments',
              'segment_precedence', 'engagement_window', 'meeting_types', 'timezone', 'sales_motion', 'ranking', 'forecast',
              'lost_work', 'context_enabled')
    payload = {key: profile[key] for key in fields if key in profile}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def load_effective_profile(settings, skill, context=None):
    version = validate_schema(settings)
    if skill not in SKILLS:
        raise ValueError('Unknown report skill')
    if version == 1:
        return deepcopy(settings)
    binding = settings.get('binding') or {}
    if not binding.get('company_domain') or type(binding.get('authenticated_user_id')) is not int:
        raise ValueError('Confirmed account binding is required')
    if context is not None and (normalize_domain(binding['company_domain']) != context.get('domain') or
                                binding['authenticated_user_id'] != (context.get('user') or {}).get('id')):
        raise ValueError('Account/user binding differs; review setup before reusing this profile')
    profile = (settings.get('profiles') or {}).get(skill)
    if not isinstance(profile, dict) or not isinstance(profile.get('scope'), dict):
        raise ValueError('Confirmed skill scope is required')
    scope = profile['scope']
    if not scope_has_owner(scope):
        raise ValueError('Confirmed owner scope is required; run setup again to choose whose deals')
    for key in ('owner_ids', 'pipeline_ids', 'exclude_pipeline_ids'):
        if key in scope and (not isinstance(scope[key], list) or any(type(value) is not int or value <= 0 for value in scope[key])):
            raise ValueError('Scope identifiers must be positive integer lists')
    result = {key: deepcopy(value) for key, value in settings.items() if key not in ('profiles',)}
    result.update(deepcopy(profile))
    result['timezone'] = valid_timezone(profile.get('timezone') or settings.get('timezone'))
    if not result['timezone']:
        raise ValueError('A valid named timezone is required')
    rules = profile.get('rules') or {}
    validate_rules(rules)
    lost_view = (profile.get('lost_work') or {}).get('view_key')
    if lost_view:
        view = (settings.get('views') or {}).get(lost_view) or {}
        if view.get('record_type') != 'deal' or view.get('verified') is not True:
            raise ValueError('Lost-work filter must be a verified deal view')
    for segment in profile.get('segments') or []:
        if not segment.get('key') or not segment.get('name') or not isinstance(segment.get('view_key'), str) or type(segment.get('silence_days')) is not int or segment['silence_days'] < 1:
            raise ValueError('Named segments need a confirmed view and positive silence threshold')
    silence = rules.get('silence') or {}
    days = silence.get('days')
    if silence.get('enabled') is True and (type(days) is not int or days < 1):
        raise ValueError('Enabled silence rule requires positive days')
    result['silence_days'] = days if silence.get('enabled') is True else None
    result['flag_no_next_activity'] = (rules.get('no_next_activity') or {}).get('enabled') is True
    # Triage checks notes against the stage unless the profile turns it off: quick setup never asks, so version-2 triage
    # never raised R7 or the WON question (0.7.9 release run (b); decision A, 17 Oct). Other skills keep it opt-in.
    mismatch = rules.get('notes_stage_mismatch')
    result['flag_notes_stage_mismatch'] = (mismatch.get('enabled') is True if isinstance(mismatch, dict)
                                           else skill == 'deal-triage')
    stage_rule = rules.get('stage_stale') or {}
    result['stage_stale_days'] = deepcopy(stage_rule.get('settings') or {}) if stage_rule.get('enabled') is True else {'default': None, 'by_stage_id': {}, 'use_pipedrive_rotting': False}
    result['close_slip_slack_days'] = (rules.get('close_slip') or {}).get('days') if (rules.get('close_slip') or {}).get('enabled') is True else None
    result['single_thread_from_stage_id'] = (rules.get('single_thread') or {}).get('from_stage_id') if (rules.get('single_thread') or {}).get('enabled') is True else None
    result['buying_signal_evidence'] = profile.get('context_enabled', True)
    result['record_links'] = {**deepcopy(settings.get('record_links') or {}), **deepcopy(profile.get('record_links') or {})}
    result['_workflow_v2'] = True
    result['_report_skill'] = skill
    result['_profile_id'] = profile.get('id') or skill
    selected = {key for entries in (profile.get('view_roles') or {}).values() for key in entries}
    selected.update(segment.get('view_key') for segment in profile.get('segments') or [])
    if lost_view:
        selected.add(lost_view)
    if context is not None:
        # A scope saved before 0.7.1 can hold names only; this run's copy takes the ids from the live context. Saved settings stay as they are.
        result['scope'] = resolve_scope(scope, context)[0]
    result['_scope_fingerprint'] = scope_fingerprint({**profile, 'timezone': result['timezone'],
                                                    'views': {key: result.get('views', {}).get(key) for key in sorted(selected)}})
    result['_settings_revision'] = settings.get('revision', 0)
    result['output'] = {**deepcopy(settings.get('output') or {}), **deepcopy(profile.get('output') or {})}
    if skill == 'forecast' and any((profile.get('view_roles') or {}).get(role) for role in ('work_pool', 'priority', 'exclusion')):
        raise ValueError('Daily views cannot define a revenue lens; use a confirmed Forecast scope')
    return result


BRIEF_SCOPE_YES, BRIEF_SCOPE_OTHER = 'Yes', 'Choose another scope'
OWNER_NOUN = {'pipeline-brief': 'the brief', 'deal-triage': 'the triage', 'forecast': 'the forecast'}

def scope_has_owner(scope):
    """A scope is complete only when it names whose deals it covers: me, the team, or a user. Missing means no answer, never team."""
    return isinstance(scope, dict) and any(scope.get(key) not in (None, '', []) for key in ('owner', 'owners', 'owner_ids'))

def saved_scope(settings, skill):
    """The scope of a saved version-2 profile, or None when nothing is saved for this skill."""
    if not isinstance(settings, dict) or settings.get('schema_version') != 2:
        return None
    profile = (settings.get('profiles') or {}).get(skill)
    scope = profile.get('scope') if isinstance(profile, dict) else None
    return deepcopy(scope) if isinstance(scope, dict) else None

def owner_step(skill, context, problems=(), page=None):
    """The one owner question: whose deals. Options are me, the team and the account's named teammates, paged four to a
    card when the account has more than two teammates (`owner_page` picks the page)."""
    noun = OWNER_NOUN.get(skill, 'this report')
    teammates = []
    for person in user_names(context):
        if not person['me'] and person['name'] not in teammates:
            teammates.append(person['name'])
    text = f"Whose deals should {noun} cover: yours, the whole team's, or a named teammate's?"
    if problems:
        text = '; '.join(problems) + '. ' + text
    options, paging = paged(['Me', 'Team', *teammates], MORE_PEOPLE, page)
    return {'next_question': {'id': 'owner', 'text': text, 'options': options, **(paging or {})}, 'may_persist': False}

def save_owner_update(settings, skill, scope):
    """A saved profile that lacked an owner gets its scope, resolved to ids, and nothing else changes."""
    result = deepcopy(settings)
    result['profiles'][skill]['scope'] = deepcopy(scope)
    return result


def brief_scope_offer(settings, skill):
    """The saved Pipeline Brief scope to offer a Forecast that has none of its own, else None. A Brief profile never becomes a
    Forecast profile by itself: the rep is asked, and only a Yes copies it."""
    if skill != 'forecast' or not isinstance(settings, dict) or settings.get('schema_version') != 2:
        return None
    profiles = settings.get('profiles') or {}
    if isinstance((profiles.get('forecast') or {}).get('scope'), dict):
        return None
    scope = (profiles.get('pipeline-brief') or {}).get('scope')
    return deepcopy(scope) if scope_has_owner(scope) else None


def brief_scope_text(scope, context):
    """Names only, as the rep knows them: the pipelines, then the owner."""
    pipeline_names = {str(pipeline.get('id')): pipeline.get('name') for pipeline in context.get('pipelines') or []}
    user_names = {str(user.get('id')): user.get('name') for user in list(context.get('users') or []) + [context.get('user') or {}]}
    named = [str(name) for name in scope.get('pipelines') or [] if name] if isinstance(scope.get('pipelines'), list) else \
        [str(scope['pipelines'])] if scope.get('pipelines') else []
    pipelines = ', '.join(named or [pipeline_names[str(key)] for key in scope.get('pipeline_ids') or [] if pipeline_names.get(str(key))]) or 'all pipelines'
    owner = scope.get('owner')
    owners = [str(name) for name in (owner if isinstance(owner, list) else [owner]) if name]
    if not owners:
        owners = [user_names[str(key)] for key in scope.get('owner_ids') or [] if user_names.get(str(key))]
    me = person_name((context.get('user') or {}).get('name')) or 'you'
    owners = [me if name.casefold() in ('me', 'my deals', 'only my deals', 'mine') else 'the whole team' if name.casefold() == 'team' else person_name(name) for name in owners]
    return pipelines + ', ' + (', '.join(owners) or 'owner not set')


def with_brief_scope(answers, settings, skill):
    """Answers with the Brief scope copied in (names and ids) when the rep answered Yes and gave no scope of their own.
    The sales_motion option label the rep chose becomes its value (0.7.2 live run: the label was saved and ranking fell
    back to rescue order)."""
    answers = answers or {}
    if answers.get('sales_motion') in SALES_MOTION_CHOICES:
        answers = {**answers, 'sales_motion': SALES_MOTION_CHOICES[answers['sales_motion']]}
    offered = brief_scope_offer(settings, skill)
    if offered is None or isinstance(answers.get('scope'), dict) or answers.get('use_brief_scope') != BRIEF_SCOPE_YES:
        return answers
    return {**answers, 'scope': offered}


def setup_review_hash(answers):
    """sha256 of the answers a review covers: everything except the confirmation and its hash."""
    body = {key: value for key, value in answers.items() if key not in ('confirmed', 'review_sha256')}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def reviewed(answers):
    return answers.get('confirmed') is True and answers.get('review_sha256') == setup_review_hash(answers)


def stage_order_pending(answers, context):
    """ADR-040: the in-scope pipelines whose stage order Pipedrive did not supply, as ids and as the review's lines."""
    if not (context or {}).get('available') or not context.get('stages'):
        return [], []
    scope = resolve_scope(answers.get('scope') or {}, context)[0]
    pipeline_ids, excluded = scope.get('pipeline_ids') or [], scope.get('exclude_pipeline_ids') or []
    ctx = stage_context({stage['id']: stage for stage in context['stages']}, pipeline_ids)
    names = {pipeline['id']: pipeline['name'] for pipeline in context.get('pipelines') or []}
    ctx['pipeline_labels'] = {pid: names.get(pid, 'Pipeline name unavailable') for pid in ctx['pipeline_labels']}
    return order_unknown_in_scope(ctx, pipeline_ids, excluded), stage_order_notices(ctx, pipeline_ids, excluded)


def advance_setup(answers, context, skill=None, settings=None):
    answers = with_brief_scope(deepcopy(answers or {}), settings, skill)
    validate_rules(answers.get('rules'))
    def question(identifier, text, options=None, **extra):
        return {'next_question': {'id': identifier, 'text': text, 'options': options or [], **extra}, 'may_persist': False}
    if not context.get('available') or not context.get('domain') or type((context.get('user') or {}).get('id')) is not int:
        return {**question('account_context', 'Read current Pipedrive account context before asking for setup choices.'),
                'required_read': {'tool': 'getAccountContext', 'reason': 'Resolve the calling user and pipeline names before a scope question.'}}
    saved = saved_scope(settings, skill)
    if saved is not None and not scope_has_owner(saved):
        # A saved profile keeps its pipelines; only whose deals is missing, so only that is asked.
        chosen = answers.get('scope') if isinstance(answers.get('scope'), dict) else {}
        owner = {key: chosen[key] for key in ('owner', 'owners') if chosen.get(key) not in (None, '', [])}
        if not owner:
            return owner_step(skill, context, page=answers.get('owner_page'))
        merged = {**saved, **owner}
        resolved, problems = resolve_scope(merged, context)
        if problems:
            return owner_step(skill, context, problems, page=answers.get('owner_page'))
        return {'next_question': None, 'may_persist': False, 'owner_update': {'scope': resolved}}
    if answers.get('setup_mode') not in ('quick', 'advanced'):
        return question('setup_mode', 'How would you like to set up your work?', ['Quick setup', 'I want more advanced setup'])
    offered = brief_scope_offer(settings, skill)
    if offered is not None and not isinstance(answers.get('scope'), dict) and answers.get('use_brief_scope') != BRIEF_SCOPE_OTHER:
        return question('brief_scope', f'Use the same scope as your brief ({brief_scope_text(offered, context)})?', [BRIEF_SCOPE_YES, BRIEF_SCOPE_OTHER])
    if not isinstance(answers.get('scope'), dict):
        names = list(dict.fromkeys(pipeline['name'] for pipeline in context.get('pipelines') or [] if pipeline.get('name')))
        user = person_name((context.get('user') or {}).get('name'))
        identity = f'You are signed in as {user}. ' if user else ''
        options, paging = paged(names, MORE_PIPELINES, answers.get('scope_page'))
        return question('scope', identity + 'Your scope is not confirmed yet. Which named pipelines should I include or exclude, including any delivery or support work?', options, **(paging or {}))
    problems = resolve_scope(answers['scope'], context)[1]
    if problems:
        names = list(dict.fromkeys(pipeline['name'] for pipeline in context.get('pipelines') or [] if pipeline.get('name')))
        options, paging = paged(names, MORE_PIPELINES, answers.get('scope_page'))
        return question('scope', '; '.join(problems) + '. Which named pipelines should I include or exclude?', options, **(paging or {}))
    if not scope_has_owner(answers['scope']):
        return owner_step(skill, context, page=answers.get('owner_page'))
    if answers.get('sales_motion') not in SALES_MOTIONS:
        return question('sales_motion', 'What should this list help you work on first?', list(SALES_MOTION_CHOICES))
    if skill == 'pipeline-brief' and not reviewed(answers) and 'priority_fields' not in answers and deal_field_choices(context):
        return question('priority_fields', f'Which Pipedrive fields do you look at to decide what to work on? Choose up to {PRIORITY_FIELD_CAP}, or skip. They show on each deal card; they do not change the order.',
                        priority_field_options(context))
    if not valid_timezone(answers.get('timezone') or context.get('timezone')):
        return question('timezone', 'Which named timezone should I use for today and scheduled work?')
    if not answers.get('filters_finished'):
        return question('filters', 'Share a link with your saved filter selected, or work with this scope without filters.',
                        ['Work with this scope, no filters', 'Share a saved filter link'])
    if not reviewed(answers) and 'lost_work' not in answers and trial_demo_signs(context):
        return question('lost_work', 'Do you sell through trials, or through demos?', ['Trials', 'Demos', 'Both', 'Neither'])
    lost = answers.get('lost_work') or {}
    if lost.get('enabled') and lost.get('sells_through') != 'neither':
        view = (answers.get('views') or {}).get(lost.get('view_key')) or {}
        selected_view = view.get('record_type') == 'deal' and view.get('verified') is True
        reasons = lost.get('lost_reasons')
        recorded_reasons = lost.get('criterion_confirmed') is True and isinstance(reasons, list) and bool(reasons) and all(isinstance(value, str) and value.strip() for value in reasons)
        if not selected_view and not recorded_reasons:
            group, unit = LOST_GROUPS.get(lost.get('sells_through'), LOST_GROUPS['trials'])
            return question('lost_work_criterion', f'Which saved deal filter or exact recorded lost reasons identify {group}? An unfinished task alone does not identify {unit}. You can skip this group.',
                            ['Share a saved deal filter', 'Choose recorded lost reasons', 'Skip this group'])
    if not reviewed(answers):
        review = {'review': setup_review(answers, stage_order_pending(answers, context)[1]), 'review_sha256': setup_review_hash(answers)}
        if answers.get('confirmed') is True:
            review['reason'] = ('Confirmed setup needs the review_sha256 copied from this question; review these answers again and confirm with that hash'
                                if 'review_sha256' not in answers else
                                'These answers changed after the review, so the review_sha256 no longer matches. Review them again and confirm with the new hash')
        enabled = any(isinstance(rule, dict) and rule.get('enabled') is True for rule in (answers.get('rules') or {}).values())
        if not enabled:
            return question('confirm_setup', 'Review the named scope, views and ordering. Optional checks are off, so silence and missing next activity will not flag deals. Save this setup, or choose optional checks first?',
                            ['Save with optional checks off', 'Choose optional checks'], **review)
        return question('confirm_setup', 'Review the named scope, views, ordering and rules. Save these choices?', **review)
    return {'next_question': None, 'may_persist': True}


def setup_review(answers, notices=()):
    """One line a rep can check before confirming: names and choices only, never an id. Stage-order lines follow it."""
    scope = answers.get('scope') or {}
    pipelines = ', '.join(str(name) for name in scope.get('pipelines') or []) or 'all pipelines'
    excluded = ', '.join(str(name) for name in scope.get('exclude_pipelines') or [])
    owners = [str(name) for name in (scope.get('owner') if isinstance(scope.get('owner'), list) else [scope.get('owner')]) if name]
    owner = ', '.join(owners) or 'owner not set'
    if answers.get('owner_source') == 'request' and owners:
        owner = ('you' if owner.casefold() in OWNER_ME else owner) + ' (from your request)'
    lost = answers.get('lost_work') or {}
    sells = lost.get('sells_through') if lost.get('enabled') else None
    parts = [f'Pipelines: {pipelines}' + (f' (not {excluded})' if excluded else ''), f'Owner: {owner}',
             f"Ranking: {answers.get('sales_motion') or 'not set'}",
             'Lost work still pursued: ' + (f'{sells} only after you name them' if sells and sells != 'neither' else 'off')]
    return '\n'.join(['. '.join(parts) + '.', *notices])


def requested_setup(settings, skill, request, context, capabilities=None):
    """Preview missing business choices for a requested task without changing settings."""
    validate_schema(settings)
    if skill not in SKILLS:
        raise ValueError('Unknown report skill')
    action = request.get('action') or skill
    if action not in (*SKILLS, 'deep_triage', 'dashboard', 'document', 'visual_artifact', 'all_three'):
        raise ValueError('Choose a supported sales task or output')
    result = {'action': action, 'missing': [], 'next_question': None, 'may_persist': False}
    if action == 'all_three' and capabilities is not None and (capabilities.get('native_document') is not True or not any(capabilities.get(key) is True for key in ('native_artifact', 'local_html'))):
        return {**result, 'status': 'unavailable', 'limitation': 'All three requires a native Claude Doc tool and an HTML/artifact route. Offer the supported outputs; additional setup cannot add a missing host tool.'}
    if action == 'document' and capabilities is not None and capabilities.get('native_document') is not True:
        return {**result, 'status': 'unavailable', 'limitation': 'This session cannot create a native Claude Doc. A local document can still be offered; additional setup cannot add the missing host tool.'}
    if action == 'visual_artifact' and capabilities is not None and not any(capabilities.get(key) is True for key in ('native_artifact', 'local_html')):
        return {**result, 'status': 'unavailable', 'limitation': 'This session has no verified HTML/artifact route. Keep the text result; additional setup cannot add a missing host tool.'}
    details = request.get('details') or {}
    saved = (settings.get('profiles') or {}).get(skill) or (settings if settings.get('schema_version', 1) == 1 else {})
    profile = {**deepcopy(saved), **deepcopy(details)}
    if settings.get('schema_version', 1) == 1 and 'rules' not in profile:
        profile['rules'] = migration_preview(profile, skill)['rules']
    report = request.get('report') or {}
    missing = result['missing']
    raw_domain = (settings.get('output') or {}).get('domain') or context.get('domain') or report.get('output_domain')
    domain = normalize_domain(raw_domain) if raw_domain else None
    if action in ('dashboard', 'document', 'visual_artifact', 'all_three') and not settings and not details and not context.get('available') and report.get('report_schema_version') == 2 and report.get('report_skill') == skill and domain and valid_timezone(report.get('timezone')):
        return {**result, 'status': 'ready',
                'notice': 'Reuse the saved report without CRM discovery. Locate its accepted saved settings and run presentation and delivery checks before creating the output.'}
    if not domain or not context.get('available') and not settings.get('binding'):
        missing.append({'id': 'account_context', 'text': 'Connect Pipedrive so I can read the account details and link the records.'})
    binding = settings.get('binding') or {}
    if context.get('available') and binding and (binding.get('company_domain') != context.get('domain') or
            binding.get('authenticated_user_id') != (context.get('user') or {}).get('id')):
        missing.append({'id': 'account_binding', 'text': 'These settings belong to another Pipedrive account or user. Choose a separate settings location for this account.'})
    if not isinstance(profile.get('scope'), dict):
        missing.append({'id': 'scope', 'text': 'Which pipelines should I include?'})
    elif not scope_has_owner(profile['scope']) and not (settings and settings.get('schema_version', 1) == 1
                                                         and 'scope' not in details and 'owner_ids' in profile['scope']):
        # Version-1 settings saved the whole team as owner_ids: []; only version-2 profiles must name an owner.
        missing.append({'id': 'owner', 'text': 'Whose deals should this cover: yours, the whole team\'s, or a named teammate\'s?'})
    if not valid_timezone(profile.get('timezone') or settings.get('timezone') or context.get('timezone') or report.get('timezone')):
        missing.append({'id': 'timezone', 'text': 'Which timezone should I use for dates and scheduled work?'})
    if action == 'forecast' and not (request.get('period') or (profile.get('forecast') or {}).get('period') or report.get('period')):
        missing.append({'id': 'period', 'text': 'Which month or quarter should the forecast cover?'})
    if action == 'deep_triage' and not any(isinstance(rule, dict) and rule.get('enabled') is True for rule in (profile.get('rules') or {}).values()):
        missing.append({'id': 'rules', 'text': 'Which checks should identify deals needing attention? Choose the checks and their thresholds, or keep this to a context review.'})
    if not missing:
        return {**result, 'status': 'ready_for_review' if details else 'ready',
                'details': deepcopy(details), 'notice': 'Review new choices before using them; save reusable settings only after explicit confirmation.' if details else ''}
    limitations = {'forecast': 'A revenue estimate needs an agreed scope and period. Keep the existing brief, or return to the forecast later.',
                   'deep_triage': 'A context review is still possible for an agreed scope. No new risk checks, thresholds or CRM actions will be assumed.',
                   'dashboard': 'Keep the available text result. A dashboard cannot add information that is absent from the saved report.',
                   'visual_artifact': 'Keep the available text result. A visual artifact cannot add information that is absent from the saved report.',
                   'all_three': 'Keep the available text result. These outputs cannot add information that is absent from the saved report.',
                   'document': 'Keep the available text result, or return to document creation after choosing the missing details.'}
    limitation = limitations.get(action, 'Return to this report when its scope and required account details are available.')
    if request.get('decision') == 'skip':
        return {**result, 'status': 'skipped', 'limitation': limitation}
    if request.get('interactive') is False or request.get('mode') == 'scheduled':
        return {**result, 'status': 'input_needed', 'limitation': limitation}
    if any(item['id'] == 'account_context' for item in missing) and not request.get('context_unavailable'):
        return {**result, 'status': 'discovery_needed',
                'required_read': {'tool': 'getAccountContext', 'reason': 'Resolve the calling user and pipeline names before a scope question.'}}
    if request.get('decision') == 'complete':
        return {**result, 'status': 'pending_setup', 'next_question': deepcopy(missing[0])}
    labels = {'account_context': 'the Pipedrive account connection', 'account_binding': 'settings for this account',
              'scope': 'which deals to include', 'owner': 'whose deals to cover', 'timezone': 'the timezone', 'period': 'the forecast period', 'rules': 'the checks and thresholds'}
    task = {'deep_triage': 'review these deals in depth', 'dashboard': 'create the dashboard', 'document': 'create the Claude Doc', 'visual_artifact': 'create the visual artifact', 'all_three': 'create the Dashboard, Claude Doc and Visual artifact',
            'forecast': 'run the forecast'}.get(action, 'run this report')
    text = ('To ' + task + ', I need ' + ', '.join(labels[item['id']] for item in missing)
            + '. Complete setup is the usual choice for a first run: a few questions, saved once so later runs skip them. '
            'Skip for now is one run that saves nothing. Which do you want?')
    # Complete setup first and marked as the default (0.7.17, decision 3A): three 0.7.15/0.7.16 Cowork runs from a fresh
    # workspace never saw this question and ran the version-1 path with nothing saved.
    return {**result, 'status': 'offer_setup', 'limitation': limitation,
            'next_question': {'id': 'complete_setup', 'text': text, 'options': ['Complete setup', 'Skip for now'],
                              'default': 'Complete setup'}}


def migration_preview(settings, skill):
    """A reviewable translation; its hash must be accepted before any v1 write."""
    validate_schema(settings)
    rules = {'silence': {'enabled': settings.get('silence_days') is not None, 'days': settings.get('silence_days')},
             'no_next_activity': {'enabled': settings.get('flag_no_next_activity') is True},
             'notes_stage_mismatch': {'enabled': settings.get('flag_notes_stage_mismatch') is True},
             'stage_stale': {'enabled': bool(settings.get('stage_stale_days')), 'settings': deepcopy(settings.get('stage_stale_days'))},
             'close_slip': {'enabled': settings.get('close_slip_slack_days') is not None, 'days': settings.get('close_slip_slack_days')},
             'single_thread': {'enabled': settings.get('single_thread_from_stage_id') is not None,
                               'from_stage_id': settings.get('single_thread_from_stage_id')}}
    views = {f"legacy:deal:{rule['filter_id']}": {**deepcopy(rule), 'record_type': 'deal', 'role': 'risk',
              'label': rule.get('meaning') or 'Saved risk view', 'verified': False}
             for rule in settings.get('filter_rules') or []}
    body = {'skill': skill, 'scope': deepcopy(settings.get('scope') or {}), 'rules': rules, 'views': views,
            'notice': 'Saved risk views keep their risk role. Verify access and review local owner/pipeline intersections.',
            'old_sha256': hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest()}
    body['review_sha256'] = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    return body


def confirmed_setup(settings, answers, context, skill):
    answers = with_brief_scope(answers, settings, skill)
    if not reviewed(answers):
        raise ValueError('Confirm setup with review_sha256 ' + setup_review_hash(answers) + ' copied from the confirm_setup question')
    state = advance_setup(answers, context, skill, settings)
    if not state['may_persist']:
        raise ValueError('Complete and confirm the business choices before saving setup')
    validate_schema(settings)
    if (answers.get('lost_work') or {}).get('sells_through') not in (None, *SELLS_THROUGH):
        raise ValueError('Lost work is for trials, demos, both or neither')
    chosen = answers.get('priority_fields')
    if chosen and (not isinstance(chosen, list) or len(chosen) > PRIORITY_FIELD_CAP
                   or any(field not in deal_field_choices(context) for field in chosen)):
        raise ValueError(f'Priority fields must be at most {PRIORITY_FIELD_CAP} deal fields listed in the account context')
    migration = None
    if any(key in settings for key in ('scope', 'filter_rules', 'silence_days', 'forecast')) and settings.get('schema_version', 1) == 1:
        migration = migration_preview(settings, skill)
        if answers.get('migration_review_sha256') != migration['review_sha256']:
            raise ValueError('Review the migration preview and accept its current hash before saving')
        supplied = answers.get('views') or {}
        dropped = answers.get('removed_legacy_filters') or []
        for view in migration['views'].values():
            replacement = [entry for entry in supplied.values() if entry.get('filter_id') == view['filter_id'] and entry.get('record_type') == 'deal']
            if not replacement and view['filter_id'] not in dropped:
                raise ValueError('Verify each legacy risk filter or explicitly accept its removal during migration')
            if replacement and any(entry.get('role') != 'risk' for entry in replacement) and not answers.get('legacy_role_changes_confirmed'):
                raise ValueError('Explicitly accept legacy risk-role changes during migration')
    from account_context import metadata
    from cohorts import finish_views
    result = metadata(settings, context)
    existing_binding = settings.get('binding')
    binding = {'company_domain': context['domain'], 'authenticated_user_id': context['user']['id']}
    if existing_binding and existing_binding != binding:
        raise ValueError('Account binding changed; keep the existing profile and select a separate settings location')
    lost_view = (answers.get('lost_work') or {}).get('view_key')
    views = deepcopy(answers.get('views') or {})
    collection = finish_views({'views': {key: view for key, view in views.items() if key != lost_view}}, answers.get('combination'), answers.get('priority_order'))
    if not collection['complete']:
        raise ValueError('Confirm filter combination and priority order')
    for view in views.values():
        if view.get('verified') is not True or view.get('record_type') not in ('deal', 'activity') or type(view.get('filter_id')) is not int or view['filter_id'] < 1:
            raise ValueError('Verify typed filter access before saving setup')
    profile = {key: deepcopy(answers[key]) for key in ('scope', 'sales_motion', 'rules', 'segments', 'segment_precedence',
               'engagement_window', 'ranking', 'meeting_types', 'engagement_fields', 'lost_work', 'forecast', 'output', 'record_links',
               *(('priority_fields',) if skill == 'pipeline-brief' else ())) if key in answers}
    if migration and 'rules' not in profile:
        profile['rules'] = migration['rules']
    profile['scope'] = resolve_scope(answers['scope'], context)[0]
    profile['setup_mode'] = answers['setup_mode']
    profile['combination'] = collection['combination']
    profile['priority_order'] = collection['priority_order']
    profile['view_roles'] = {role: [key for key, view in views.items() if view['role'] == role and key != lost_view] for role in ('work_pool', 'priority', 'risk', 'exclusion')}
    result.update({'schema_version': 2, 'revision': settings.get('revision', 0), 'binding': binding,
                   'timezone': valid_timezone(answers.get('timezone') or result.get('timezone') or context.get('timezone')),
                   'timezone_source': 'accepted_override' if answers.get('timezone') else settings.get('timezone_source', 'saved') if settings.get('timezone') else 'current_user.timezone_name',
                   'custom_fields': deepcopy(context.get('custom_fields') or {})})
    result.setdefault('views', {}).update(views)
    result.setdefault('profiles', {})[skill] = profile
    result['stage_order_notified'] = stage_order_pending(answers, context)[0]
    load_effective_profile(result, skill, context)
    if migration:
        result.setdefault('migrations', []).append({'old_sha256': migration['old_sha256'],
               'review_sha256': migration['review_sha256'], 'skill': skill,
               'new_profile_sha256': scope_fingerprint(profile), 'accepted_changes': deepcopy(answers)})
    return result


def save_verified(settings, paths):
    validate_schema(settings)
    for raw_path in paths:
        path = Path(raw_path)
        temporary = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.with_suffix('.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                existing = json.loads(path.read_text()) if path.exists() else {}
                validate_schema(existing)
                if existing.get('schema_version', 1) == 2 and settings.get('schema_version', 1) != 2:
                    raise ValueError('Cannot replace version-2 settings with a legacy schema')
                if settings.get('schema_version') == 2:
                    if path.exists() and settings.get('revision') != existing.get('revision', 0):
                        raise ValueError('Settings revision changed; reload before applying this choice')
                    updated = {**deepcopy(existing), **deepcopy(settings), 'revision': existing.get('revision', 0) + 1}
                else:
                    updated = {**deepcopy(existing), **deepcopy(settings)}
                updated = preserve_company_settings(updated, existing)
                fd, temporary = tempfile.mkstemp(prefix='config-', suffix='.tmp', dir=path.parent)
                with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                    json.dump(updated, handle, ensure_ascii=False, indent=2, sort_keys=True)
                    handle.write('\n')
                os.replace(temporary, path)
                temporary = None
                read_back = json.loads(path.read_text())
                if read_back != updated:
                    raise ValueError('Settings read-back did not match the confirmed revision')
                return read_back, str(path), True
        except BlockingIOError:
            raise ValueError('Settings are being changed; reload before saving')
        except (OSError, json.JSONDecodeError):
            if path.exists():
                return deepcopy(settings), None, False
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
    return deepcopy(settings), None, False
