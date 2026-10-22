"""Purpose-specific Brief ordering; calculated risk and revenue remain unchanged."""

from math import isfinite


def supported_touch(facts):
    source = str(facts.get('last_touch_source') or '')
    days = facts.get('days_silent')
    return bool(facts.get('last_touch') and source and 'update_time' not in source
                and facts.get('last_activity_contact_state') != 'attempt_only'
                and facts.get('contact_evidence') != 'unverified'
                and type(days) in (int, float) and isfinite(days))


def order_brief(rows, contexts, config, today):
    motion = config.get('sales_motion') or 'engagement_led'
    priority = config.get('priority_order') or []
    def bucket(row):
        keys = (contexts.get(row['id']) or {}).get('matched_views') or []
        return min((priority.index(key) for key in keys if key in priority), default=len(priority))
    def value(row):
        amount = row.get('value')
        return amount if type(amount) in (int, float) and isfinite(amount) else 0
    def urgency(row):
        observations = (contexts.get(row['id']) or {}).get('observations') or []
        return any(item.get('kind') == 'activity' and item.get('done') is False
                   and item.get('due_date') == today for item in observations)
    sections = {'opportunities': [], 'rescue': [], 'unknown': []}
    for row in rows:
        supported = supported_touch(row)
        flags = [flag for flag in row.get('flags') or [] if flag.get('id') != 'R1' or supported]
        section = 'rescue' if flags else 'opportunities' if supported else 'unknown'
        sections[section].append(row['id'])
    if motion in ('engagement_led', 'hybrid'):
        def ordering(row):
            known = supported_touch(row)
            active = row['id'] in sections['opportunities']
            category = 0 if urgency(row) else 1 if active else 2 if known or row['id'] in sections['rescue'] else 3
            if category == 2:  # rescue: the deal silent longest comes first, then the larger value; unverified touch last
                return (bucket(row), category, -row['days_silent'] if known else float('inf'), -value(row), str(row.get('currency') or ''),
                        row.get('expected_close_date') or '9999-12-31', str(row.get('title') or '').casefold(), row['id'])
            return (bucket(row), category, str(row.get('currency') or ''),
                    row.get('days_silent') if known else float('inf'), -value(row),
                    row.get('expected_close_date') or '9999-12-31', str(row.get('title') or '').casefold(), row['id'])
        rows = sorted(rows, key=ordering)
        purpose = 'engagement-led' if motion == 'engagement_led' else 'hybrid'
        notice = (f'Your setup is {purpose}, so this order follows buyer engagement: accepted priority views first, then recorded tasks due today, '
                  'then opportunities with the most recent supported CRM touch, by value within each currency. '
                  'Rescue work and unknown engagement stay visible in their own sections. '
                  'CRM activity dates record touches, not buyer replies, so they do not prove buyer interest.')
        if not any(supported_touch(row) for row in rows):
            notice = (f'Your setup is {purpose}, but engagement is unverified. Accepted priority views and recorded tasks due today come first; '
                      'otherwise this worklist uses recorded value within each currency, then close date and deal name. '
                      'This is not a warmest-deal ranking.')
    else:
        call_first = config.get('_call_first') or []
        rows = sorted(rows, key=lambda row: (bucket(row), call_first.index(row['id']) if motion == 'speed_to_lead' and row['id'] in call_first else len(call_first)))
        notice = 'Accepted priority views first, then the saved ' + ('speed-to-lead' if motion == 'speed_to_lead' else 'risk') + ' ranking.'
    return rows, sections, notice
