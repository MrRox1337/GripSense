"""
The measurement sequence for one finger/padding combination.

Deliberately free of any UI code: the caller supplies callbacks for status
text, live readouts, asking the operator for a scale reading, and persisting
a captured row. That keeps the open/close/measure logic testable and lets a
future headless or slip-detection script drive the same sequence.
"""

import threading
import time
from datetime import datetime

from .matrix import TEST_CURRENTS, REPEATS

# Motion timing
SETTLE_POLL = 0.1              # seconds between position samples while moving
SETTLE_TOLERANCE_TICKS = 3     # movement below this counts as stopped
SETTLE_STABLE_SAMPLES = 5      # consecutive stable samples before "settled"
SETTLE_TIMEOUT = 8.0           # give up waiting after this long
MOVE_MIN_DWELL = 0.4           # let the move actually start before sampling
SCALE_DWELL = 1.0              # let the scale reading stabilise before asking


class AbortedError(Exception):
    """Raised inside a run when the operator aborts."""


class BenchmarkRunner:
    """
    Drives the gripper through one combination's full current sweep.

    Callbacks:
      on_status(text)                                  progress message
      on_readout(position_ticks, current_raw)          live servo state
      on_reading(row_dict)                             a captured measurement
      ask_weight(finger, padding, current, repeat)     -> float, may raise
                                                          AbortedError
    """

    def __init__(self, gripper, travel_ticks, profile_velocity,
                 on_status=None, on_readout=None, on_reading=None, ask_weight=None):
        self.gripper = gripper
        self.travel_ticks = travel_ticks
        self.profile_velocity = profile_velocity

        self.on_status = on_status or (lambda text: None)
        self.on_readout = on_readout or (lambda position, current: None)
        self.on_reading = on_reading or (lambda row: None)
        self.ask_weight = ask_weight

        self.abort_event = threading.Event()
        self.max_open_position = None
        self.min_open_position = None

    # ------------------------------------------------------------------
    # Position limits
    # ------------------------------------------------------------------
    def rezero(self):
        """
        Take the present position to BE max-open and derive the closed limit
        from it. The gripper's multi-turn position is whatever the last
        session left behind, so the travel window is re-anchored each run
        rather than trusting a stored tick value.
        """
        present = self.gripper.read_present_position()
        self.max_open_position = present
        self.min_open_position = present - self.travel_ticks
        return self.max_open_position, self.min_open_position

    def prepare(self):
        """Set the constant run parameters and prime Goal Position."""
        self.gripper.set_profile_velocity(self.profile_velocity)
        self.gripper.set_goal_current(TEST_CURRENTS[0])
        # Prime Goal Position so enabling torque does not jerk the fingers
        # toward a stale goal left over from a previous session.
        self.gripper.set_goal_position(self.max_open_position)

    # ------------------------------------------------------------------
    # The run
    # ------------------------------------------------------------------
    def run_combo(self, finger, padding, counts):
        """
        Sweep every current limit for one combination.

        `counts` maps goal current -> readings already on file, so a resumed
        run picks up where it stopped instead of duplicating rows. Raises
        AbortedError if the operator aborts.
        """
        current_unit_ma = self.gripper.current_unit_ma

        self.on_status("Enabling torque...")
        self.prepare()
        self.gripper.enable_torque()

        self.on_status("Opening fully before first measurement...")
        self.move_and_settle(self.max_open_position)

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
                self.move_and_settle(self.max_open_position)

                self.on_status(f"{label}\nClosing onto scale...")
                position, current = self.move_and_settle(self.min_open_position)

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
        self.move_and_settle(self.max_open_position)

    # ------------------------------------------------------------------
    # Motion helpers
    # ------------------------------------------------------------------
    def check_abort(self):
        if self.abort_event.is_set():
            raise AbortedError()

    def move_and_settle(self, target_ticks):
        """Command a move and wait until the servo stops changing position."""
        self.check_abort()
        self.gripper.set_goal_position(target_ticks)
        time.sleep(MOVE_MIN_DWELL)

        deadline = time.monotonic() + SETTLE_TIMEOUT
        last_position = None
        stable_samples = 0
        position, current = None, None

        while time.monotonic() < deadline:
            self.check_abort()
            try:
                position = self.gripper.read_present_position()
                current = self.gripper.read_present_current()
            except Exception:
                time.sleep(SETTLE_POLL)
                continue

            self.on_readout(position, current)

            if last_position is not None and abs(position - last_position) <= SETTLE_TOLERANCE_TICKS:
                stable_samples += 1
                if stable_samples >= SETTLE_STABLE_SAMPLES:
                    break
            else:
                stable_samples = 0
            last_position = position
            time.sleep(SETTLE_POLL)

        if position is None:
            # Never got a clean read; fall back to one last attempt.
            position = self.gripper.read_present_position()
            current = self.gripper.read_present_current()
        return position, current
