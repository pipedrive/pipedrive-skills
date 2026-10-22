"""Verified local configuration helpers for the read-only Forecast skill."""

from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipedrive_links import preserve_company_settings
from sales_settings import save_verified as save_shared


LENS_TYPES = {"team", "owner", "pipeline", "owner_pipeline"}
STARTER_HAIRCUTS = {"silent": 0.5, "no_next": 0.7, "slipped": 0.5}


def forecast_defaults():
    """Return fresh Forecast defaults without changing shared configuration."""
    return {
        "fiscal_year_start_month": 1,
        "haircuts": dict(STARTER_HAIRCUTS),
        "targets": {},
        "manager_commits": {},
        "probability_ladder": None,
        "probability_ladder_asked": False,
        "slipped_handling": "include",
        "coverage_bands": {"healthy": 3.0, "at_risk": 2.0},
        "concentration_threshold": 0.4,
        "history": [],
    }


def target_key(period, currency, lens_type, lens_id):
    """Return the stable local key for one scope-matched target or commitment."""
    if lens_type not in LENS_TYPES:
        raise ValueError(f"unknown forecast lens: {lens_type}")
    if not all(isinstance(value, str) and value.strip() for value in (period, currency, lens_id)):
        raise ValueError("period, currency, and lens_id must be non-empty strings")
    return "|".join((period, currency.upper(), lens_type, lens_id))


def _probability(value, name):
    if type(value) not in (int, float) or not 0 <= value <= 1:
        raise ValueError(f"{name} must be a number from 0 through 1")


def validate_forecast(forecast):
    """Validate only fields Forecast owns, allowing shared configuration to evolve."""
    if not isinstance(forecast, dict):
        raise ValueError("forecast configuration must be an object")
    month = forecast.get("fiscal_year_start_month", 1)
    if type(month) is not int or not 1 <= month <= 12:
        raise ValueError("fiscal_year_start_month must be an integer from 1 through 12")
    haircuts = forecast.get("haircuts", STARTER_HAIRCUTS)
    if not isinstance(haircuts, dict) or set(haircuts) != set(STARTER_HAIRCUTS):
        raise ValueError("haircuts must define silent, no_next, and slipped")
    for name, value in haircuts.items():
        _probability(value, f"haircuts.{name}")
    if forecast.get("slipped_handling", "include") not in {"include", "separate"}:
        raise ValueError("slipped_handling must be include or separate")
    for field in ("targets", "manager_commits"):
        values = forecast.get(field, {})
        if not isinstance(values, dict):
            raise ValueError(f"{field} must be an object")
        for key, value in values.items():
            parts = key.split("|") if isinstance(key, str) else []
            if len(parts) != 4:
                raise ValueError(f"{field} key must contain period, currency, lens, and id")
            target_key(*parts)
            if type(value) not in (int, float) or value < 0:
                raise ValueError(f"{field} values must be non-negative numbers")
    return True


def apply_change(settings, change):
    """Return a validated copy containing one proposed Forecast-only update."""
    if not isinstance(settings, dict) or not isinstance(change, dict):
        raise ValueError("settings and change must be objects")
    next_settings = deepcopy(settings)
    current = forecast_defaults()
    existing = next_settings.get("forecast")
    if isinstance(existing, dict):
        current.update(deepcopy(existing))
    current.update(deepcopy(change))
    validate_forecast(current)
    next_settings["forecast"] = current
    return next_settings


def save_verified(settings, paths):
    """Atomically write one complete config and return only a read-back verified result."""
    return save_shared(settings, paths)


def update_confirmed(settings, paths, change):
    """Persist exactly one caller-confirmed Forecast change after read-back verification."""
    return save_verified(apply_change(settings, change), paths)
