"""Compact immutable history records and accuracy calculations for Forecast."""

from collections import defaultdict
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import tempfile


SNAPSHOT_FIELDS = (
    "period", "as_of", "lens", "currency", "mode", "adjusted_forecast",
    "best_case", "weighted_forecast", "target", "manager_commit", "actual",
    "evidence_status",
)


def make_snapshot(result, as_of):
    """Project a computed result to the small audit record stored in shared config."""
    snapshot = {field: deepcopy(result.get(field)) for field in SNAPSHOT_FIELDS if field != "as_of"}
    snapshot["as_of"] = as_of
    snapshot.setdefault("actual", None)
    return snapshot


def _year(period):
    match = re.match(r"^(\d{4})", str(period or ""))
    return int(match.group(1)) if match else None


def retain_history(history, fiscal_year):
    """Keep all current-year snapshots and only the newest snapshot of older periods."""
    grouped = defaultdict(list)
    for item in history or []:
        if isinstance(item, dict) and item.get("period"):
            grouped[item["period"]].append(deepcopy(item))
    retained = []
    for period, snapshots in grouped.items():
        snapshots.sort(key=lambda item: str(item.get("as_of") or ""))
        if _year(period) == fiscal_year:
            retained.extend(snapshots)
        else:
            retained.append(snapshots[-1])
    return sorted(retained, key=lambda item: (str(item.get("period") or ""), str(item.get("as_of") or "")))


def accuracy_window(history, limit=24):
    """Return MAPE/bias per mode from at most the newest closed snapshots per mode."""
    by_mode = defaultdict(list)
    for item in history or []:
        if not isinstance(item, dict):
            continue
        actual = item.get("actual")
        forecast = item.get("adjusted_forecast")
        mode = item.get("mode")
        if mode and type(actual) in (int, float) and actual != 0 and type(forecast) in (int, float):
            by_mode[mode].append(item)
    report = {}
    for mode, entries in by_mode.items():
        entries.sort(key=lambda item: str(item.get("as_of") or item.get("period") or ""))
        entries = entries[-limit:]
        if len(entries) < 3:
            report[mode] = {"state": "insufficient_history", "count": len(entries)}
            continue
        errors = [(item["adjusted_forecast"] - item["actual"]) / item["actual"] for item in entries]
        report[mode] = {
            "state": "available",
            "count": len(entries),
            "mape": sum(abs(error) for error in errors) / len(errors),
            "bias": sum(errors) / len(errors),
        }
    return report


def write_run_record(result, directory, recorded_at):
    """Atomically persist full evidence beside, but separately from, compact config history."""
    target_dir = Path(directory)
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = "forecast-" + recorded_at.replace(":", "-") + ".json"
    target = target_dir / filename
    payload = deepcopy(result)
    payload["recorded_at"] = recorded_at
    descriptor, temporary = tempfile.mkstemp(prefix="forecast-", suffix=".tmp", dir=target_dir)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, target)
    except OSError:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return target
