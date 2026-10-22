"""Safe account-context normalization for Pipeline Brief.

Account context is optional during MCP rollout.  This module never persists or mutates
local settings; callers retain their existing probe and confirmed-note fallback when a
payload is absent, malformed, or from an unsupported schema version.
"""

ACCEPTED_VERSIONS = {"1"}


def _empty(version=None):
    return {
        "available": False,
        "version": version,
        "account_id": None,
        "user": None,
        "timezone": None,
        "date_format": None,
        "currency": None,
        "pipelines": [],
        "users": [],
    }


def _list_of_dicts(value):
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def load(payload):
    """Return a stable, safe context shape from a versioned optional payload.

    Version 1 needs a usable authenticated user before it can replace the legacy identity
    probe. Locale fields are independently optional, so a partially populated accepted
    context can still provide identity without fabricating locale defaults.
    """
    if not isinstance(payload, dict):
        return _empty()
    if "current_user" in payload or isinstance(payload.get("data"), dict) and "current_user" in payload["data"]:
        from account_context import normalize
        normalized = normalize(payload)
        if not normalized.get("available"):
            return _empty()
        return {**_empty(), **normalized, "version": "current_user-v1", "date_format": None}
    version = str(payload.get("version")) if payload.get("version") is not None else None
    if version not in ACCEPTED_VERSIONS:
        return _empty(version)
    user = payload.get("user")
    if not isinstance(user, dict) or user.get("id") is None:
        return _empty(version)
    return {
        "available": True,
        "version": version,
        "account_id": str(payload["account_id"]) if payload.get("account_id") is not None else None,
        "user": dict(user),
        "timezone": payload.get("timezone") if isinstance(payload.get("timezone"), str) else None,
        "date_format": payload.get("date_format") if isinstance(payload.get("date_format"), str) else None,
        "currency": payload.get("currency") if isinstance(payload.get("currency"), str) else None,
        "pipelines": _list_of_dicts(payload.get("pipelines")),
        "users": _list_of_dicts(payload.get("users")),
    }
