#!/usr/bin/env python3
"""Verified local settings storage for pipeline-brief."""

import argparse
import json
import os
import tempfile
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipedrive_links import preserve_company_settings
from sales_settings import save_verified as save_shared


def candidate_paths(project_root, home):
    return [Path(project_root) / ".pipedrive-sales" / "config.json",
            Path(home) / ".pipedrive-sales" / "config.json"]


def save_verified(settings, paths):
    """Save settings once and return (settings, path, persisted).

    A path counts only after its exact JSON content can be read back. The second path is
    tried only when the first cannot be used; it is not a backup.
    """
    return save_shared(settings, paths)


def update_verified(settings, paths, mutate):
    """Apply one confirmed settings change and verify the complete replacement."""
    next_settings = mutate(dict(settings))
    return save_verified(next_settings, paths)


def advance_setup(discovered_pipelines, answers, personal=False):
    """Return the one setup question that must be answered next.

    This is deliberately side-effect free: callers may only persist the complete settings
    after every required answer has been confirmed.
    """
    pipelines = list(discovered_pipelines or [])
    answers = dict(answers or {})
    suspected = next((pipeline for pipeline in pipelines if pipeline.get("suspected_non_sales")), None)

    if answers.get("pipeline_scope") not in {"all", "sales_only"}:
        if suspected:
            question = (
                f"Pipeline {suspected['id']} is {suspected['first_stage']} → "
                f"{suspected['last_stage']}. Include all pipelines, or sales pipelines only?"
            )
        else:
            question = "Include all discovered pipelines?"
        return {"next_question": {"id": "pipeline_scope", "text": question}, "may_persist": False}

    if personal and not answers.get("owner_confirmed"):
        return {"next_question": {"id": "owner_confirmation"}, "may_persist": False}

    if not answers.get("sales_motion"):
        return {"next_question": {"id": "sales_motion"}, "may_persist": False}

    if answers.get("silence_days") is None:
        return {"next_question": {"id": "silence_days"}, "may_persist": False}

    if not isinstance(answers.get("flag_no_next_activity"), bool):
        return {"next_question": {"id": "flag_no_next_activity"}, "may_persist": False}

    return {"next_question": None, "may_persist": True}


def save_confirmed_setup(settings, paths, discovered_pipelines, answers, personal=False):
    """Persist a complete setup only after its final required answer is confirmed."""
    state = advance_setup(discovered_pipelines, answers, personal=personal)
    if not state["may_persist"]:
        return settings, None, False
    return save_verified(settings, paths)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--home", required=True)
    parser.add_argument("--settings-json", required=True)
    args = parser.parse_args()
    settings = json.loads(args.settings_json)
    read_back, path, persisted = save_verified(settings, candidate_paths(args.project_root, args.home))
    print(json.dumps({"settings": read_back, "path": path, "persisted": persisted}, sort_keys=True))


if __name__ == "__main__":
    main()
