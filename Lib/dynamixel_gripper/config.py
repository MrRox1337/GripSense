"""
Where the API's tuning values come from, and what they mean when absent.

Every knob has a default here, so a config file with no `grasp:` or `slip:`
block still produces a working gripper. That matters more than it sounds: it is
what lets someone copy this package next to a half-filled YAML file and have it
run, rather than having to reconstruct this project's configuration first.

Nothing in this module holds state or touches the servo.
"""

from pathlib import Path

import yaml

from .gripper import to_signed32

__all__ = [
    "DEFAULT_CURRENT",
    "DEFAULT_GRASP",
    "DEFAULT_POSITION_UNIT_DEG",
    "DEFAULT_SLIP",
    "clamp01",
    "load_yaml",
    "resolve_limits",
    "section",
]

DEFAULT_GRASP = {
    "profile_velocity": 480,
    "miss_tolerance_ticks": 15,
    "hold_current_fraction": 0.5,
    "settle_timeout": 8.0,
    "idle_poll_interval": 0.1,
}

DEFAULT_SLIP = {
    "poll_interval": 0.005,
    "baseline_settle": 1.0,
    "baseline_samples": 20,
    "drop_fraction": 0.35,
    "min_drop_raw": 15,
    "confirm_samples": 3,
    "timeout": 25.0,
}

DEFAULT_CURRENT = {"min": 100, "max": 120}

# Used only when no calibrated limits are supplied and the config has no
# position: block either. See resolve_limits.
DEFAULT_POSITION_UNIT_DEG = 0.087891


def clamp01(value):
    """Normalised inputs are clamped rather than rejected: a caller that asks
    for 1.2 wants "as far as it goes", not an exception."""
    return min(1.0, max(0.0, float(value)))


def load_yaml(path):
    with open(Path(path), "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def section(config, name, defaults):
    """One config block merged over its defaults, key by key."""
    return {**defaults, **((config or {}).get(name) or {})}


def resolve_limits(config, limits, position_unit_deg=DEFAULT_POSITION_UNIT_DEG):
    """
    (max_open, min_open, calibrated) in raw position ticks.

    Calibrated limits are repaired on the way in. A limits file written before
    Present Position was sign-corrected stores a closed limit that wrapped to
    just under 2**32, and driving to that is not survivable - so the same
    conversion the driver now applies to reads is applied to what was written
    by the driver that did not.

    `calibrated` is False when this falls back to the nominal travel in the
    config, which callers should surface rather than silently drive to limits
    belonging to a different set of fingers.
    """
    if limits and "max_open" in limits and "min_open" in limits:
        return (
            to_signed32(int(limits["max_open"])),
            to_signed32(int(limits["min_open"])),
            True,
        )

    position = (config or {}).get("position") or {}
    if "max_open" not in position:
        return None, None, False

    max_open = int(position["max_open"])
    travel_deg = float(position.get("travel_deg", 0.0))
    min_open = max_open - round(travel_deg / position_unit_deg)
    return max_open, min_open, False
