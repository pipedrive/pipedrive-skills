"""Keep activity scheduling and completion evidence separate from buyer contact."""

from datetime import date


DATE_EXPLANATION = (
    "Scheduled date is when an activity was planned. Marked done is when its completion "
    "was recorded in Pipedrive; imports or late updates can change that timestamp. "
    "Recency uses the scheduled date of a completed activity as a CRM activity proxy, "
    "with marked-done date as a labelled fallback. Neither date proves buyer contact."
)


def _date(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def completed_activity_evidence(activities, today):
    today = _date(today)
    events = []
    for activity in activities:
        if activity.get("done") not in (True, 1):
            continue
        scheduled = _date(activity.get("due_date"))
        recorded = _date(activity.get("marked_as_done_time"))
        proxy = scheduled or recorded
        if not proxy or proxy > today:
            continue
        events.append({
            "activity_id": activity.get("id"),
            "subject": activity.get("subject"),
            "scheduled_date": scheduled.isoformat() if scheduled else None,
            "marked_as_done_time": activity.get("marked_as_done_time"),
            "recency_date": proxy.isoformat(),
            "recency_source": "completed activity scheduled date" if scheduled else "marked-done date fallback",
        })
    events.sort(key=lambda item: (item["recency_date"], str(item["activity_id"])))
    latest = events[-1] if events else {}
    proxy = _date(latest.get("recency_date"))
    return {
        "completed_activity_evidence": events,
        "last_completed_activity_date": latest.get("recency_date"),
        "last_activity_scheduled_date": latest.get("scheduled_date"),
        "last_activity_marked_as_done_time": latest.get("marked_as_done_time"),
        "last_activity_date_source": latest.get("recency_source"),
        "days_since_activity": (today - proxy).days if proxy else None,
    }
