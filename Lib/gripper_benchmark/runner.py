"""
The grip-force measurement sequence for one finger/padding combination.

Deliberately free of any UI code: the caller supplies callbacks for status
text, live readouts, asking the operator for a scale reading, and persisting
a captured row. That keeps the open/close/measure logic testable.

Motion between the calibrated limits comes from MotionBase; this module only
adds what happens once the fingers have closed.
"""

import time
from datetime import datetime

from .matrix import TEST_CURRENTS, REPEATS
from .motion import AbortedError, MotionBase

SCALE_DWELL = 1.0              # let the scale reading stabilise before asking

__all__ = ["AbortedError", "BenchmarkRunner", "SCALE_DWELL"]


class BenchmarkRunner(MotionBase):
    """
    Drives the gripper through one combination's full current sweep.

    Callbacks beyond MotionBase's:
      on_reading(row_dict)                             a captured measurement
      ask_weight(finger, padding, current, repeat)     -> float, may raise
                                                          AbortedError
    """

    def __init__(self, gripper, max_open, min_open, profile_velocity,
                 on_status=None, on_readout=None, on_reading=None,
                 ask_weight=None, abort_event=None):
        super().__init__(gripper, max_open, min_open, profile_velocity,
                         on_status=on_status, on_readout=on_readout,
                         abort_event=abort_event)
        self.on_reading = on_reading or (lambda row: None)
        self.ask_weight = ask_weight

    def run_combo(self, finger, padding, counts):
        """
        Sweep every current limit for one combination.

        `counts` maps goal current -> readings already on file, so a resumed
        run picks up where it stopped instead of duplicating rows. Raises
        AbortedError if the operator aborts.
        """
        self.require_limits()
        current_unit_ma = self.gripper.current_unit_ma

        self.on_status("Enabling torque...")
        self.prepare(TEST_CURRENTS[0])
        self.gripper.enable_torque()

        self.on_status("Opening fully before first measurement...")
        self.open_fully()

        for goal_current in TEST_CURRENTS:
            start_repeat = min(counts.get(goal_current, 0), REPEATS)
            if start_repeat >= REPEATS:
                self.on_status(f"{goal_current} raw: already has {REPEATS} readings, skipping.")
                continue

            self.gripper.set_goal_current(goal_current)

            for repeat in range(start_repeat + 1, REPEATS + 1):
                self.check_abort()
                label = (
                    f"{finger} / {padding}  |  current {goal_current} raw "
                    f"({goal_current * current_unit_ma:.0f} mA)  |  repeat {repeat}/{REPEATS}"
                )

                self.on_status(f"{label}\nOpening...")
                self.open_fully()

                self.on_status(f"{label}\nClosing onto scale...")
                position, current = self.close_fully()

                time.sleep(SCALE_DWELL)
                self.check_abort()

                self.on_status(f"{label}\nWaiting for scale reading...")
                weight = self.ask_weight(finger, padding, goal_current, repeat)

                self.on_reading({
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                    "finger_material": finger,
                    "padding": padding,
                    "goal_current_raw": goal_current,
                    "goal_current_ma": round(goal_current * current_unit_ma, 2),
                    "repeat": repeat,
                    "weight_g": weight,
                    "present_position_ticks": position,
                    "present_current_raw": current,
                    "present_current_ma": round(current * current_unit_ma, 2),
                })
                counts[goal_current] = counts.get(goal_current, 0) + 1

        self.on_status("Run complete. Opening and disabling torque...")
        self.open_fully()
