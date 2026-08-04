"""
Project glue: where the config files live and what they resolve to.

This is the one module that knows the repository layout. dynamixel_gripper
stays layout-agnostic, and scripts get their constants from here instead of
each re-deriving them from YAML.
"""

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

RESULTS_CSV = PROJECT_ROOT / "benchmark_results.csv"
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

MAX_OPEN_POSITION = APP_CONFIG["position"]["max_open"]
TRAVEL_DEG = APP_CONFIG["position"]["travel_deg"]
TRAVEL_TICKS = round(TRAVEL_DEG / POSITION_UNIT_DEG)
MIN_OPEN_POSITION = MAX_OPEN_POSITION - TRAVEL_TICKS

CURRENT_MIN = APP_CONFIG["current"]["min"]
CURRENT_MAX = APP_CONFIG["current"]["max"]

VELOCITY_MIN = APP_CONFIG["velocity"]["min"]
VELOCITY_MAX = APP_CONFIG["velocity"]["max"]

BENCHMARK_PROFILE_VELOCITY = APP_CONFIG["benchmark"]["profile_velocity"]


def connect():
    """Open the servo port and put it in current-based position control."""
    gripper = DynamixelGripper(
        DEVICE_PORT, BAUDRATE, PROTOCOL_VERSION, DXL_ID, CONTROL_TABLE_PATH
    )
    gripper.set_operating_mode_current_based_position()
    return gripper
