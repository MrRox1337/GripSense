"""
Slip detection by present-current collapse.

While the fingers are stalled against a gripped object the servo holds close
to its goal current. If the operator loads the object until it escapes, the
fingers are suddenly free to move, the torque they were producing is no longer
opposed, and present current falls sharply. That fall is the slip signal.

Each repeat records whether a slip was seen and how long it took to see it,
measured from the moment the watch started to the FIRST sample of the drop -
not to the sample that confirmed it, so the confirmation delay does not
inflate the detection time.

As with the grip-force runner, there is no UI here: the caller supplies
callbacks and can drive the whole sequence against a fake gripper.
"""

import statistics
import time
from dataclasses import dataclass
from datetime import datetime

import gripper_settings as settings

from .matrix import TEST_CURRENTS, REPEATS
from .motion import AbortedError, MotionBase

# The watch loop reads present current as fast as the bus allows, so live UI
# updates are throttled to something a Tk main loop can absorb.
READOUT_INTERVAL = settings.READOUT_INTERVAL


@dataclass
class SlipWatchResult:
    """One watch window: what the current did while the object was loaded."""

    detected: bool
    detection_time_s: float | None   # None when nothing slipped before timeout
    baseline_raw: float
    slip_raw: float | None
    position_at_slip: int | None
    position_shift: int | None
    duration_s: float
    samples: int


class SlipRunner(MotionBase):
    """
    Drives one combination's slip test across every current limit.

    Callbacks beyond MotionBase's:
      on_reading(row_dict)                          a captured measurement
      ask_ready(finger, padding, current, repeat)   block until the operator
                                                    has placed the object; may
                                                    raise AbortedError
    """

    def __init__(self, gripper, max_open, min_open, profile_velocity,
                 poll_interval=0.005, baseline_settle=1.0, baseline_samples=20,
                 drop_fraction=0.35, min_drop_raw=15, confirm_samples=3,
                 timeout=25.0, on_status=None, on_readout=None,
                 on_reading=None, ask_ready=None, abort_event=None):
        super().__init__(gripper, max_open, min_open, profile_velocity,
                         on_status=on_status, on_readout=on_readout,
                         abort_event=abort_event)
        self.poll_interval = poll_interval
        self.baseline_settle = baseline_settle
        self.baseline_samples = baseline_samples
        self.drop_fraction = drop_fraction
        self.min_drop_raw = min_drop_raw
        self.confirm_samples = confirm_samples
        self.timeout = timeout

        self.on_reading = on_reading or (lambda row: None)
        self.ask_ready = ask_ready

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------
    def drop_threshold(self, baseline):
        """
        The current a sample must fall below to count as dropped.

        Both a proportional and an absolute margin apply, so a firm grip needs
        a proportionally large fall while a weak one still needs a real one.
        """
        return baseline - max(self.min_drop_raw, self.drop_fraction * baseline)

    def measure_baseline(self):
        """Median holding current once the grip has stabilised."""
        time.sleep(self.baseline_settle)
        samples = []
        while len(samples) < self.baseline_samples:
            self.check_abort()
            # Closing draws negative current; only the magnitude matters.
            samples.append(abs(self.gripper.read_present_current()))
            time.sleep(self.poll_interval)
        return statistics.median(samples)

    def watch_for_slip(self, baseline, start_position):
        """
        Sample present current until it collapses or the window expires.

        Only current is read in this loop. Position costs a second round trip
        per sample and would halve the sampling rate, which is exactly the
        resolution of the number this test exists to measure, so position is
        read once at the end instead.
        """
        threshold = self.drop_threshold(baseline)
        watch_start = time.monotonic()
        deadline = watch_start + self.timeout
        last_readout = 0.0

        dropped_run = 0
        first_drop_time = None
        first_drop_current = None
        samples = 0

        while time.monotonic() < deadline:
            self.check_abort()
            now = time.monotonic()
            current = abs(self.gripper.read_present_current())
            samples += 1

            if now - last_readout >= READOUT_INTERVAL:
                self.on_readout(start_position, current)
                last_readout = now

            if current < threshold:
                if dropped_run == 0:
                    first_drop_time = now
                    first_drop_current = current
                dropped_run += 1
                if dropped_run >= self.confirm_samples:
                    end_position = self.gripper.read_present_position()
                    return SlipWatchResult(
                        detected=True,
                        detection_time_s=first_drop_time - watch_start,
                        baseline_raw=baseline,
                        slip_raw=first_drop_current,
                        position_at_slip=end_position,
                        position_shift=end_position - start_position,
                        duration_s=now - watch_start,
                        samples=samples,
                    )
            else:
                dropped_run = 0
                first_drop_time = None
                first_drop_current = None

            time.sleep(self.poll_interval)

        return SlipWatchResult(
            detected=False,
            detection_time_s=None,
            baseline_raw=baseline,
            slip_raw=None,
            position_at_slip=None,
            position_shift=None,
            duration_s=time.monotonic() - watch_start,
            samples=samples,
        )

    # ------------------------------------------------------------------
    # The run
    # ------------------------------------------------------------------
    def run_combo(self, finger, padding, counts):
        """
        Sweep every current limit for one combination, three slips each.

        `counts` maps goal current -> readings already on file so an aborted
        run resumes without duplicating rows, exactly as the grip-force test
        does. Raises AbortedError if the operator aborts.
        """
        self.require_limits()
        current_unit_ma = self.gripper.current_unit_ma

        self.on_status("Enabling torque...")
        self.prepare(TEST_CURRENTS[0])
        self.gripper.enable_torque()

        self.on_status("Opening fully before first grip...")
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

                self.on_status(f"{label}\nPlace the object between the fingers.")
                self.ask_ready(finger, padding, goal_current, repeat)

                self.on_status(f"{label}\nGripping...")
                position, _ = self.close_fully()

                self.on_status(f"{label}\nMeasuring holding current...")
                baseline = self.measure_baseline()

                threshold = self.drop_threshold(baseline)
                if threshold <= 0:
                    # No current can fall below zero, so this watch cannot
                    # detect anything. It still runs, so watch_duration_s stays
                    # comparable across rows, but say why the result will read
                    # as "not detected" rather than letting it look like a
                    # genuine failure to slip.
                    self.on_status(
                        f"{label}\nWARNING: holding current is only {baseline:.0f} raw, "
                        "too low for a drop to be distinguishable. This repeat will "
                        "record as not detected whatever you do - abort and grip "
                        "something firmer, or lower min_drop_raw."
                    )
                else:
                    self.on_status(
                        f"{label}\nHolding at {baseline:.0f} raw. PULL THE OBJECT until it slips."
                    )

                result = self.watch_for_slip(baseline, position)
                self.on_reading(
                    self._row(finger, padding, goal_current, repeat, result, current_unit_ma)
                )
                counts[goal_current] = counts.get(goal_current, 0) + 1

                if result.detected:
                    self.on_status(
                        f"{label}\nSlip detected after {result.detection_time_s:.3f} s."
                    )
                else:
                    self.on_status(
                        f"{label}\nNo slip within {self.timeout:.0f} s - recorded as not detected."
                    )

        self.on_status("Run complete. Opening and disabling torque...")
        self.open_fully()

    def _row(self, finger, padding, goal_current, repeat, result, current_unit_ma):
        drop_raw = (
            result.baseline_raw - result.slip_raw if result.detected else None
        )
        drop_percent = (
            round(drop_raw / result.baseline_raw * 100, 1)
            if drop_raw is not None and result.baseline_raw else None
        )
        return {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "finger_material": finger,
            "padding": padding,
            "goal_current_raw": goal_current,
            "goal_current_ma": round(goal_current * current_unit_ma, 2),
            "repeat": repeat,
            "slip_detected": int(result.detected),
            "detection_time_s": (
                round(result.detection_time_s, 4) if result.detected else ""
            ),
            "baseline_current_raw": round(result.baseline_raw, 1),
            "baseline_current_ma": round(result.baseline_raw * current_unit_ma, 2),
            "slip_current_raw": round(result.slip_raw, 1) if result.detected else "",
            "slip_current_ma": (
                round(result.slip_raw * current_unit_ma, 2) if result.detected else ""
            ),
            "drop_raw": round(drop_raw, 1) if drop_raw is not None else "",
            "drop_percent": drop_percent if drop_percent is not None else "",
            "position_at_slip_ticks": (
                result.position_at_slip if result.position_at_slip is not None else ""
            ),
            "position_shift_ticks": (
                result.position_shift if result.position_shift is not None else ""
            ),
            "watch_duration_s": round(result.duration_s, 3),
            "samples": result.samples,
        }


__all__ = ["AbortedError", "SlipRunner", "SlipWatchResult"]
