"""
Project glue: where the config files live and what they resolve to.

This is the one module that knows the repository layout. dynamixel_gripper
stays layout-agnostic, and scripts get their constants from here instead of
each re-deriving them from YAML.
"""

from datetime import datetime
from pathlib import Path

import yaml

from dynamixel_gripper import DynamixelGripper, load_control_table

# ----------------------------------------------------------------------------
# Layout
# ----------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "Config"

APP_CONFIG_PATH = CONFIG_DIR / "gripper_config.yaml"
CONTROL_TABLE_PATH = CONFIG_DIR / "xm430_control_table.yaml"
# Written by the calibration wizard, not by hand. Absent until the first
# calibration on a given set of fingers.
LIMITS_PATH = CONFIG_DIR / "gripper_limits.yaml"

RESULTS_CSV = PROJECT_ROOT / "benchmark_results.csv"
SLIP_CSV = PROJECT_ROOT / "slip_results.csv"
REPORT_DIR = PROJECT_ROOT / "benchmark_report"

# ----------------------------------------------------------------------------
# Loaded configuration
# ----------------------------------------------------------------------------
with open(APP_CONFIG_PATH, "r", encoding="utf-8") as _handle:
    APP_CONFIG = yaml.safe_load(_handle)

CONTROL_TABLE = load_control_table(CONTROL_TABLE_PATH)

DEVICE_PORT = APP_CONFIG["port"]["device"]
BAUDRATE = APP_CONFIG["port"]["baudrate"]
PROTOCOL_VERSION = APP_CONFIG["port"]["protocol_version"]
DXL_ID = APP_CONFIG["port"]["dxl_id"]

# Unit scales come from the control table rather than being hardcoded, so they
# stay in sync with whichever servo definition is loaded.
POSITION_UNIT_DEG = CONTROL_TABLE["units"]["position_deg_per_tick"]
CURRENT_UNIT_MA = CONTROL_TABLE["units"]["current_ma_per_tick"]
VELOCITY_UNIT_REV = CONTROL_TABLE["units"]["velocity_rev_per_min_per_tick"]

# Nominal travel, used only until the fingers have been calibrated. See
# travel_limits() for the values anything should actually drive to.
NOMINAL_MAX_OPEN = APP_CONFIG["position"]["max_open"]
TRAVEL_DEG = APP_CONFIG["position"]["travel_deg"]
NOMINAL_TRAVEL_TICKS = round(TRAVEL_DEG / POSITION_UNIT_DEG)
NOMINAL_MIN_OPEN = NOMINAL_MAX_OPEN - NOMINAL_TRAVEL_TICKS

CURRENT_MIN = APP_CONFIG["current"]["min"]
CURRENT_MAX = APP_CONFIG["current"]["max"]

VELOCITY_MIN = APP_CONFIG["velocity"]["min"]
VELOCITY_MAX = APP_CONFIG["velocity"]["max"]

BENCHMARK_PROFILE_VELOCITY = APP_CONFIG["benchmark"]["profile_velocity"]

CALIBRATION = APP_CONFIG["calibration"]
SLIP = APP_CONFIG["slip"]


# ----------------------------------------------------------------------------
# Calibrated travel limits
#
# Written by the calibration wizard whenever fingers are swapped, and read back
# by every script, so no position is hardcoded anywhere once a set of fingers
# has been calibrated.
# ----------------------------------------------------------------------------
LIMITS_HEADER = (
    "# Gripper travel limits in raw position ticks.\n"
    "#\n"
    "# GENERATED FILE - written by the calibration wizard in the benchmark GUI\n"
    "# (Scripts/gripper_benchmark.py). Do not edit by hand: re-run calibration\n"
    "# instead, which is required anyway whenever fingers are swapped.\n"
)


def load_limits():
    """The last calibration's limits, or None if never calibrated."""
    if not LIMITS_PATH.exists():
        return None
    with open(LIMITS_PATH, "r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not data or "max_open" not in data or "min_open" not in data:
        return None
    return data


def save_limits(result):
    """Persist a CalibrationResult so later sessions inherit these limits."""
    payload = {
        "calibrated_at": datetime.now().isoformat(timespec="seconds"),
        "max_open": int(result.max_open),
        "min_open": int(result.min_open),
        "hard_close": int(result.hard_close),
        "backoff_ticks": int(result.backoff_ticks),
        "stop_current_raw": int(result.stop_current_raw),
        "travel_ticks": int(result.travel_ticks),
        "travel_deg": round(result.travel_ticks * POSITION_UNIT_DEG, 2),
    }
    with open(LIMITS_PATH, "w", encoding="utf-8") as handle:
        handle.write(LIMITS_HEADER)
        yaml.safe_dump(payload, handle, sort_keys=False)
    return payload


def travel_limits():
    """
    (max_open, min_open, calibrated) for whoever needs to command a position.

    `calibrated` is False when this is falling back to the nominal values in
    gripper_config.yaml, which callers should surface rather than silently
    drive to limits that belong to a different set of fingers.
    """
    limits = load_limits()
    if limits is None:
        return NOMINAL_MAX_OPEN, NOMINAL_MIN_OPEN, False
    return int(limits["max_open"]), int(limits["min_open"]), True


def connect():
    """Open the servo port and put it in current-based position control."""
    gripper = DynamixelGripper(
        DEVICE_PORT, BAUDRATE, PROTOCOL_VERSION, DXL_ID, CONTROL_TABLE_PATH
    )
    gripper.set_operating_mode_current_based_position()
    return gripper
