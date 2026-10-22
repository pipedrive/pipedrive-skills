"""Plain-language, rep-facing explanations for Pipeline Brief rule signals."""

from datetime import date

from pdcore import days_ago


def _date_for_rep(value):
    try:
        parsed = date.fromisoformat(str(value))
        return f"{parsed.day} {parsed.strftime('%B')} {parsed.year}"
    except ValueError:
        return str(value)


def rep_signal(flag):
    """Return business language, next step, and an audit identifier for one rule flag."""
    rule_id = flag.get("id", "unknown")
    if rule_id == "R1":
        source = str(flag.get("source") or "")
        estimated = "proxy" in source.lower() or source in {"update_time", "update_time (fallback)", "last_activity_id"}
        return {
            "headline": f"Last CRM touch proxy: {days_ago(flag['days'])}",
            "detail": (f"Last recorded update: {_date_for_rep(flag['since'])} ({flag['days']} days; review threshold {flag.get('threshold', 'not recorded')} days). Check the activity history before treating this as buyer silence."
                       if estimated else f"The latest dated CRM activity proxy is {_date_for_rep(flag['since'])} ({flag['days']} days; review threshold {flag.get('threshold', 'not recorded')} days). This does not establish buyer contact."),
            "next_step": "Reconnect with the buyer and book the next follow-up.",
            "source": source,
            "audit_id": rule_id,
        }
    if rule_id == "R2":
        return {
            "headline": "No next activity is scheduled",
            "detail": "This open deal has no planned customer step.",
            "next_step": "Schedule the next customer activity.",
            "audit_id": rule_id,
        }
    if rule_id == "R3":
        never_moved = (" The last stage-change date is unavailable; this count uses the date the deal was created."
                       if flag.get("never_moved") else "")
        return {
            "headline": (f"{flag['days']} days since deal creation; stage-change date unavailable"
                         if flag.get('never_moved') else f"Stalled in this stage for {flag['days']} days"),
            "detail": f"The stage threshold is {flag['threshold']} days.{never_moved}",
            "next_step": "Confirm the deal's stage and agree the next customer step.",
            "audit_id": rule_id,
        }
    if rule_id == "R4":
        return {
            "headline": f"Close date passed {days_ago(flag['days'])}",
            "detail": f"Expected close date: {flag['expected_close_date']}.",
            "next_step": "Confirm the timing with the buyer and update the close date.",
            "audit_id": rule_id,
        }
    if rule_id == "R5":
        contacts = flag["participants"]
        return {
            "headline": ("Only one contact is involved in this deal" if contacts == 1
                         else "No contacts are involved in this deal"),
            "detail": "The deal may depend on a single person.",
            "next_step": "Identify and involve another relevant stakeholder.",
            "audit_id": rule_id,
        }
    if rule_id == "R7":
        return {
            "headline": "Recent note may not match the deal stage",
            "detail": f"Note on {flag['note_date']}: \"{flag['excerpt']}\".",
            "next_step": "Review the note and confirm the deal's current status.",
            "audit_id": rule_id,
        }
    return {
        "headline": "This deal needs review",
        "detail": "A configured rule flagged this deal.",
        "next_step": "Review the deal and confirm the next customer step.",
        "audit_id": rule_id,
    }


def rep_text(flag):
    """Render one signal for text briefs, placing internal terminology last."""
    signal = rep_signal(flag)
    return f"{signal['headline']}. {signal['detail']} Next step: {signal['next_step']} [Rule {signal['audit_id']}]"


def next_action(deal):
    """Choose one review step that respects the activity already on this deal."""
    flags = deal.get('flags') or []
    if flags and flags[0].get('id') == 'R1':
        if deal.get('last_touch_source') == 'update_time (fallback)' or flags[0].get('source') == 'update_time (fallback)':
            return 'Check the activity history before deciding whether to reconnect with the buyer.'
        if deal.get('has_next') is True or deal.get('next_activity_id') is not None:
            return 'Review the existing next activity and confirm whether it still addresses the buyer’s next step.'
        if deal.get('next_activity_known') is True:
            return 'Reconnect with the buyer and agree the next follow-up.'
        return 'Check the recorded next activity before deciding on a follow-up.'
    return rep_signal(flags[0])['next_step'] if flags else 'Review the deal and confirm the next customer step.'
