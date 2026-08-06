"""
Two-step discovery of the gripper's travel limits.

Finger geometry changes every time a set of printed fingers is swapped, so the
open and closed positions are measured on the hardware rather than stored as
fixed tick values:

  Step 1  Torque is released and the operator opens the fingers by hand.
          Wherever they are left becomes MAX OPEN.
  Step 2  The gripper drives closed under a fixed current limit until present
          current shows it has run into the mechanical stop. That stop is then
          backed off by a few ticks, and the backed-off position becomes
          MIN OPEN, so the finger joints' safety snap is not parked against
          its end stop under constant tension.

This is the procedure used when loading and unloading fingers.

Like BenchmarkRunner, this module knows nothing about UI or about where the
project keeps its configuration: tuning values are passed in and progress is
reported through callbacks.
"""

import time
from dataclasses import dataclass

# Let the close probe actually get moving before its position samples are
# trusted. Without this the servo is still stationary at the first sample
# while inrush current is high, which looks exactly like a stall.
PROBE_START_DWELL = 0.5

# Bound on the back-off and reopen moves, which are ordinary short travels and
# should never take anywhere near this long.
MOVE_TIMEOUT = 10.0


class CalibrationError(Exception):
    """The limits could not be established, so none should be trusted."""


class CalibrationAborted(Exception):
    """Raised inside a calibration when the operator aborts."""


@dataclass
class CalibrationResult:
    """The limits a single calibration established, in raw position ticks."""

    max_open: int          # set by hand in step 1
    hard_close: int        # where the fingers physically stopped in step 2
    min_open: int          # hard_close backed off, the working closed limit
    backoff_ticks: int
    stop_current_raw: int  # present current when the stop was called

    @property
    def travel_ticks(self):
        return self.max_open - self.min_open


class GripperCalibrator:
    """
    Runs the two calibration steps against real hardware.

    Callbacks:
      on_status(text)                            progress message
      on_readout(position_ticks, current_raw)    live servo state

    `abort_event` is a threading.Event the caller may set to interrupt the
    close probe; the probe checks it between samples.
    """

    def __init__(self, gripper, probe_current, probe_velocity, backoff_ticks,
                 return_velocity=None, stall_current_fraction=0.85,
                 stall_stable_samples=5, stall_position_tolerance=3,
                 max_probe_ticks=4096, probe_timeout=30.0, poll_interval=0.05,
                 on_status=None, on_readout=None, abort_event=None):
        self.gripper = gripper
        self.probe_current = probe_current
        self.probe_velocity = probe_velocity
        self.return_velocity = return_velocity or probe_velocity
        self.backoff_ticks = backoff_ticks
        self.stall_current_fraction = stall_current_fraction
        self.stall_stable_samples = stall_stable_samples
        self.stall_position_tolerance = stall_position_tolerance
        self.max_probe_ticks = max_probe_ticks
        self.probe_timeout = probe_timeout
        self.poll_interval = poll_interval

        self.on_status = on_status or (lambda text: None)
        self.on_readout = on_readout or (lambda position, current: None)
        self.abort_event = abort_event

        self.max_open = None

    # ------------------------------------------------------------------
    # Step 1: max open, set by hand
    # ------------------------------------------------------------------
    def release_for_manual_positioning(self):
        """Drop torque so the operator can move the fingers freely."""
        self.gripper.disable_torque()
        self.on_status(
            "Torque released. Open the fingers fully by hand, then confirm."
        )

    def capture_max_open(self):
        """Whatever position the fingers were left at becomes MAX OPEN."""
        self.max_open = self.gripper.read_present_position()
        self.on_status(f"MAX OPEN captured at {self.max_open} ticks.")
        return self.max_open

    # ------------------------------------------------------------------
    # Step 2: closed limit, found by current draw
    # ------------------------------------------------------------------
    def probe_close_limit(self):
        """
        Drive closed until the current draw says the stop has been reached,
        then back off. Returns a CalibrationResult.
        """
        if self.max_open is None:
            raise CalibrationError("Capture MAX OPEN before probing the close limit.")

        self.gripper.set_profile_velocity(self.probe_velocity)
        self.gripper.set_goal_current(self.probe_current)
        # Prime Goal Position at the current location so enabling torque does
        # not snap the fingers toward a stale goal from a previous session.
        self.gripper.set_goal_position(self.max_open)
        self.gripper.enable_torque()

        # Closing is the decreasing-tick direction. Command a target beyond
        # any plausible stop and let the current tell us where the real one is.
        floor_target = self.max_open - self.max_probe_ticks
        self.on_status("Closing under current limit to find the mechanical stop...")
        self.gripper.set_goal_position(floor_target)

        hard_close, stop_current = self._watch_for_stall(floor_target)

        min_open = hard_close + self.backoff_ticks
        self.on_status(f"Stop found at {hard_close} ticks. Backing off to {min_open}.")
        self.gripper.set_goal_position(min_open)
        self._wait_until_near(min_open, timeout=MOVE_TIMEOUT)

        self.on_status("Reopening to MAX OPEN.")
        self.gripper.set_profile_velocity(self.return_velocity)
        self.gripper.set_goal_position(self.max_open)
        # Wait for the reopen to finish before returning: the caller drops
        # torque as soon as this returns, which would otherwise abandon the
        # fingers mid-travel, still close to the stop.
        self._wait_until_near(self.max_open, timeout=MOVE_TIMEOUT)

        return CalibrationResult(
            max_open=int(self.max_open),
            hard_close=int(hard_close),
            min_open=int(min_open),
            backoff_ticks=int(self.backoff_ticks),
            stop_current_raw=int(stop_current),
        )

    def _watch_for_stall(self, floor_target):
        """Sample until position stops changing while current stays high."""
        stall_current = self.probe_current * self.stall_current_fraction
        deadline = time.monotonic() + self.probe_timeout
        last_position = None
        stalled_samples = 0

        time.sleep(PROBE_START_DWELL)

        while time.monotonic() < deadline:
            self._check_abort()
            position = self.gripper.read_present_position()
            # Closing draws negative current; only the magnitude matters here.
            current = abs(self.gripper.read_present_current())
            self.on_readout(position, current)

            moved = (
                last_position is None
                or abs(position - last_position) > self.stall_position_tolerance
            )
            if current >= stall_current and not moved:
                stalled_samples += 1
                if stalled_samples >= self.stall_stable_samples:
                    return position, current
            else:
                stalled_samples = 0

            if position <= floor_target + self.stall_position_tolerance:
                self._retreat()
                raise CalibrationError(
                    f"Travelled the full {self.max_probe_ticks}-tick probe range "
                    "without meeting a stop. The fingers are probably not "
                    "mounted, or MAX OPEN was captured while already closed."
                )

            last_position = position
            time.sleep(self.poll_interval)

        self._retreat()
        raise CalibrationError(
            f"No mechanical stop detected within {self.probe_timeout:.0f} s. "
            "Check that the fingers can move and that probe_current is high "
            "enough to drive them closed."
        )

    def _wait_until_near(self, target, tolerance=None, timeout=None):
        """Block until the fingers reach `target`, or give up quietly."""
        tolerance = tolerance if tolerance is not None else self.stall_position_tolerance
        deadline = time.monotonic() + (timeout if timeout is not None else self.probe_timeout)
        while time.monotonic() < deadline:
            self._check_abort()
            position = self.gripper.read_present_position()
            self.on_readout(position, abs(self.gripper.read_present_current()))
            if abs(position - target) <= tolerance:
                return position
            time.sleep(self.poll_interval)
        return None

    def _retreat(self):
        """Give up on the probe without leaving the fingers loaded."""
        try:
            self.gripper.set_profile_velocity(self.return_velocity)
            self.gripper.set_goal_position(self.max_open)
        except Exception:
            # Already failing; a failed retreat must not mask the real cause.
            pass

    def _check_abort(self):
        if self.abort_event is not None and self.abort_event.is_set():
            self._retreat()
            raise CalibrationAborted()
