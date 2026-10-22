#!/usr/bin/env python3
"""Store a verified Pipeline Brief artifact preference in the active settings file."""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from settings import candidate_paths, update_verified


PREFERENCE_DURATIONS = ("always_offer", "never_offer", "ask_once_per_session")
RESPONSES = ("accepted", "declined")


def apply_choice(settings, preference, response):
    """Apply a confirmed response under the approved D3 preference duration.

    The returned settings are deliberately side-effect free. Callers persist them only
    when ``persisted`` is true, via ``update_preference`` and verified read-back.
    """
    if preference not in PREFERENCE_DURATIONS:
        raise ValueError(f"preference must be one of: {', '.join(PREFERENCE_DURATIONS)}")
    if response not in RESPONSES:
        raise ValueError(f"response must be one of: {', '.join(RESPONSES)}")

    current = dict(settings or {})
    output = current.get("output") or {}
    if not isinstance(output, dict):
        raise ValueError("settings output must be an object")

    if preference == "ask_once_per_session":
        return {"settings": current, "persisted": False, "session_preference": response}

    next_output = dict(output)
    if preference == "never_offer":
        next_output["artifact_preference"] = "never_offer"
        return {
            "settings": {**current, "output": next_output},
            "persisted": True,
            "session_preference": None,
        }

    had_opt_out = next_output.get("artifact_preference") == "never_offer"
    next_output.pop("artifact_preference", None)
    return {
        "settings": {**current, "output": next_output},
        "persisted": had_opt_out,
        "session_preference": None,
    }


def update_preference(project_root, home, preference):
    """Persist a D3 preference only when its policy changes saved settings."""
    if preference not in PREFERENCE_DURATIONS:
        raise ValueError(f"preference must be one of: {', '.join(PREFERENCE_DURATIONS)}")
    for path in candidate_paths(project_root, home):
        try:
            with Path(path).open(encoding="utf-8") as source:
                settings = json.load(source)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(settings, dict):
            continue
        if settings.get("output") is not None and not isinstance(settings["output"], dict):
            continue
        choice = apply_choice(settings, preference, "accepted")
        if not choice["persisted"]:
            return {
                "settings": choice["settings"],
                "path": None,
                "persisted": False,
                "session_preference": choice["session_preference"],
            }

        read_back, saved_path, persisted = update_verified(settings, [path], lambda _: choice["settings"])
        if persisted:
            return {"settings": read_back, "path": saved_path, "persisted": True, "session_preference": None}
        return {"settings": settings, "path": None, "persisted": False, "session_preference": None}
    return {"settings": None, "path": None, "persisted": False, "session_preference": None}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--home", required=True)
    parser.add_argument("--preference", required=True, choices=PREFERENCE_DURATIONS)
    args = parser.parse_args()
    print(json.dumps(update_preference(args.project_root, args.home, args.preference), sort_keys=True))


if __name__ == "__main__":
    main()
