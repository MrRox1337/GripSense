"""
Open/close motion between two calibrated travel limits.

Every sequence that drives the fingers - the benchmark runners, the calibration
wizard's callers, the high-level API - needs the same two things: command a
position, then wait until the servo has actually stopped moving. In
current-based position control "stopped" is not the same as "arrived", because
the fingers stall wherever the object is, so the wait is a settle rule rather
than a comparison against the goal.

That common part lives here. Limits come from calibration, never from a
hardcoded tick value, and can be replaced with set_limits() if the fingers are
recalibrated mid-session.
"""

import threading
import time

# Motion timing
SETTLE_POLL = 0.1              # seconds between position samples while moving
SETTLE_TOLERANCE_TICKS = 3     # movement below this counts as stopped
SETTLE_STABLE_SAMPLES = 5      # consecutive stable samples before "settled"
SETTLE_TIMEOUT = 8.0           # give up waiting after this long
MOVE_MIN_DWELL = 0.4           # let the move actually start before sampling

__all__ = [
    "AbortedError",
    "MotionBase",
    "MOVE_MIN_DWELL",
    "SETTLE_POLL",
    "SETTLE_STABLE_SAMPLES",
    "SETTLE_TIMEOUT",
    "SETTLE_TOLERANCE_TICKS",
]


class AbortedError(Exception):
    """Raised inside a run when the operator aborts."""


class MotionBase:
    """
    Common hardware plumbing for a test sequence.

    Callbacks:
      on_status(text)                            progress message
      on_readout(position_ticks, current_raw)    live servo state
    """

    def __init__(self, gripper, max_open, min_open, profile_velocity,
                 on_status=None, on_readout=None, abort_event=None):
        self.gripper = gripper
        self.max_open_position = max_open
        self.min_open_position = min_open
        self.profile_velocity = profile_velocity

        # Instance-level so a subclass can take it from configuration; defaults
        # to the module constant, which is what every existing caller gets.
        self.settle_timeout = SETTLE_TIMEOUT

        self.on_status = on_status or (lambda text: None)
        self.on_readout = on_readout or (lambda position, current: None)
        self.abort_event = abort_event if abort_event is not None else threading.Event()

    # ------------------------------------------------------------------
    # Limits
    # ------------------------------------------------------------------
    def set_limits(self, max_open, min_open):
        self.max_open_position = max_open
        self.min_open_position = min_open

    @property
    def has_limits(self):
        return self.max_open_position is not None and self.min_open_position is not None

    def require_limits(self):
        if not self.has_limits:
            raise RuntimeError(
                "Travel limits are not set. Calibrate the fingers before running a test."
            )

    # ------------------------------------------------------------------
    # Motion
    # ------------------------------------------------------------------
    def prepare(self, goal_current):
        """Set the constant run parameters and prime Goal Position."""
        self.require_limits()
        self.gripper.set_profile_velocity(self.profile_velocity)
        self.gripper.set_goal_current(goal_current)
        # Prime Goal Position so enabling torque does not jerk the fingers
        # toward a stale goal left over from a previous session.
        self.gripper.set_goal_position(self.max_open_position)

    def check_abort(self):
        if self.abort_event.is_set():
            raise AbortedError()

    def move_and_settle(self, target_ticks):
        """Command a move and wait until the servo stops changing position."""
        self.check_abort()
        self.gripper.set_goal_position(target_ticks)
        time.sleep(MOVE_MIN_DWELL)

        deadline = time.monotonic() + self.settle_timeout
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

    def open_fully(self):
        return self.move_and_settle(self.max_open_position)

    def close_fully(self):
        return self.move_and_settle(self.min_open_position)
