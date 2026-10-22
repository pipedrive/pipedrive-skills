"""Render recorded instants in a verified account timezone without changing source data."""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def display_timezone(value):
    if isinstance(value, str):
        try:
            ZoneInfo(value)
            return value
        except (ValueError, ZoneInfoNotFoundError):
            pass
    return "UTC"


def display_timestamp(value, zone_name=None):
    if not value:
        return "Not recorded"
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    source = str(value)
    try:
        instant = datetime.fromisoformat(source.replace("Z", "+00:00"))
    except ValueError:
        return source
    if instant.tzinfo is None:
        return source
    zone = ZoneInfo(display_timezone(zone_name))
    return instant.astimezone(zone).strftime("%-d %b %Y, %H:%M %Z")
