"""
Reusable driver package for a Dynamixel X-series gripper servo.

Self-contained: nothing here imports anything from outside this package, so the
folder can be lifted out of this repository, dropped next to your own three YAML
files (application config, control table, calibrated limits) and used on its own.

  gripper      register-level driver - addresses, lengths and unit scales all
               come from a YAML control table, so a different X-series model
               needs a different YAML rather than different code
  motion       open/close between two calibrated limits, and the settle rule
               that decides when a move has finished
  calibration  two-step discovery of those limits, run whenever fingers change
  api          GripperAPI - open/close/set_position/set_grip_strength in
               normalised units, plus a live ok/slip/miss status
  status       the reported types: GripStatus, GripperState, SlipEvent
  config       tuning defaults, and how limits and units are resolved
  settle       the rule that decides a move has finished, as arithmetic over
               position samples
  slipwatch    the slip rule as arithmetic over current samples - like settle,
               no I/O, so it can be exercised on a list of numbers
  teleop       the manual control console, and the calibration wizard beside
               it in calibration_dialog. Tkinter, and the only part of this
               package that needs it - imported on use, not on import, so a
               machine without tkinter still gets everything above

Most callers want the API:

    from dynamixel_gripper import GripperAPI
    api = GripperAPI.from_config("gripper_config.yaml",
                                 "xm430_control_table.yaml",
                                 "gripper_limits.yaml")

and the same object opens the manual console when a person needs the sliders:

    api.teleop()
"""

from .api import GripperAPI
from .calibration import (
    CalibrationAborted,
    CalibrationError,
    CalibrationResult,
    GripperCalibrator,
)
from .gripper import DynamixelGripper, load_control_table, to_signed16, to_signed32
from .motion import AbortedError, MotionBase
from .settle import SettleTracker
from .slipwatch import SlipWatch
from .status import GripperState, GripStatus, SlipEvent

__all__ = [
    "DynamixelGripper",
    "load_control_table",
    "to_signed16",
    "to_signed32",
    "AbortedError",
    "MotionBase",
    "CalibrationAborted",
    "CalibrationError",
    "CalibrationResult",
    "GripperCalibrator",
    "GripperAPI",
    "GripperState",
    "GripStatus",
    "SettleTracker",
    "SlipEvent",
    "SlipWatch",
]
