"""
Open/close motion shared by both test sequences.

The implementation moved to dynamixel_gripper.motion so the driver package
ships standalone: the high-level API needs the same settle rule, and a third
party who downloads only dynamixel_gripper/ must get it without pulling in the
benchmark. Nothing about the behaviour changed, and every existing import site
(`from .motion import AbortedError, MotionBase`) keeps working unaltered.
"""

from dynamixel_gripper.motion import (
    MOVE_MIN_DWELL,
    SETTLE_POLL,
    SETTLE_STABLE_SAMPLES,
    SETTLE_TIMEOUT,
    SETTLE_TOLERANCE_TICKS,
    AbortedError,
    MotionBase,
)

__all__ = [
    "AbortedError",
    "MotionBase",
    "MOVE_MIN_DWELL",
    "SETTLE_POLL",
    "SETTLE_STABLE_SAMPLES",
    "SETTLE_TIMEOUT",
    "SETTLE_TOLERANCE_TICKS",
]
