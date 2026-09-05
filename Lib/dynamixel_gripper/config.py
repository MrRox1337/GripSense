"""
Where the API's tuning values come from, and what they mean when absent.

Every knob has a default here, so a config file with no `grasp:` or `slip:`
block still produces a working gripper. That matters more than it sounds: it is
what lets someone copy this package next to a half-filled YAML file and have it
run, rather than having to reconstruct this project's configuration first.

Nothing in this module holds state or touches the servo.
"""

from datetime import datetime
from pathlib import Path

import yaml

from .gripper import to_signed32

__all__ = [
    "DEFAULT_CALIBRATION",
    "DEFAULT_CURRENT",
    "DEFAULT_GRASP",
    "DEFAULT_POSITION_UNIT_DEG",
    "DEFAULT_SLIP",
    "LIMITS_HEADER",
    "clamp01",
    "load_yaml",
    "resolve_limits",
    "save_limits",
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

# The travel-limit probe. Only used by GripperAPI.calibrate(); a config with no
# calibration: block still calibrates, on these.
DEFAULT_CALIBRATION = {
    "probe_current": 110,
    "probe_velocity": 60,
    "return_velocity": 480,
    "backoff_ticks": 20,
    "stall_current_fraction": 0.85,
    "stall_stable_samples": 5,
    "stall_position_tolerance": 3,
    "poll_interval": 0.05,
    "max_probe_ticks": 4096,
    "probe_timeout": 30.0,
}

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


# The banner written above every generated limits file. Kept here rather than
# beside the writer's caller so the package produces the same file whether it
# was calibrated headlessly or through a GUI.
LIMITS_HEADER = (
    "# Gripper travel limits in raw position ticks.\n"
    "#\n"
    "# GENERATED FILE - written by calibration, either a calibration wizard or\n"
    "# GripperAPI.calibrate(). Do not edit by hand: re-run calibration instead,\n"
    "# which is required anyway whenever fingers are swapped.\n"
)


def save_limits(path, result, position_unit_deg=DEFAULT_POSITION_UNIT_DEG):
    """
    Write a CalibrationResult to `path` and return the payload written.

    The one place the file's shape is decided, so a limits file is identical
    whether calibration ran headlessly or through the wizard. Overwrites
    without prompting: calibration has already happened by the time this is
    called, and the previous limits describe fingers that are no longer on.
    """
    payload = {
        "calibrated_at": datetime.now().isoformat(timespec="seconds"),
        "max_open": int(result.max_open),
        "min_open": int(result.min_open),
        "hard_close": int(result.hard_close),
        "backoff_ticks": int(result.backoff_ticks),
        "stop_current_raw": int(result.stop_current_raw),
        "travel_ticks": int(result.travel_ticks),
        "travel_deg": round(result.travel_ticks * position_unit_deg, 2),
    }
    with open(Path(path), "w", encoding="utf-8") as handle:
        handle.write(LIMITS_HEADER)
        yaml.safe_dump(payload, handle, sort_keys=False)
    return payload
