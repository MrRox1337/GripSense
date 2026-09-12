"""
A high-level dynamixel gripper driver that you can import in your own projects.
Refer to the documentation to see how to use it.

The register-level driver is deliberately dumb: it writes Goal Position and
reads Present Current and has no idea what either means. This module is the
opposite end - it knows that the servo runs in current-based position control,
that the current ceiling is the grip-force ceiling, and that a grip which stops
drawing current has lost whatever it was holding.

    with GripperAPI.from_config("gripper_config.yaml",
                                "xm430_control_table.yaml",
                                "gripper_limits.yaml") as api:
        api.enable(True)
        api.set_grip_strength(0.6)
        if api.close() == "ok":
            while api.status == "ok":
                do_something_useful()
            print(api.status)          # "slip" - the object got away

Positions and grip strengths are normalised 0.0-1.0, so nothing a caller writes
has to change when the fingers are swapped and recalibrated: 0.0 is always as
closed as this pair of fingers goes and 1.0 is always as open.

Status is one of five values (see status.py), updated by a background monitor
thread so reading it never blocks and never costs a round trip. `slip` latches
until the next command - without that, the fingers would carry on closing after
the object escaped, reach MIN OPEN, and the status would settle on `miss`, so a
caller polling at 10 Hz could watch an object slip away and never see it happen.

What this module owns is the facade and the thread that keeps it current. The
pieces that stand alone live next door: the reported types in status.py, the
tuning defaults and limits resolution in config.py, and the slip arithmetic in
slipwatch.py.

NAMING: this class's close() closes the FINGERS. The driver's close() closes the
SERIAL PORT. Tear this object down with disconnect(), or use it as a context
manager.
"""

import dataclasses
import threading
import time
from pathlib import Path

from .calibration import GripperCalibrator
from .config import (
    DEFAULT_CALIBRATION,
    DEFAULT_CURRENT,
    DEFAULT_GRASP,
    DEFAULT_POSITION_UNIT_DEG,
    DEFAULT_SLIP,
    DEFAULT_VELOCITY,
    clamp01,
    load_yaml,
    resolve_limits,
    save_limits,
    section,
)
from .gripper import DynamixelGripper, load_control_table
from .motion import AbortedError, MotionBase
from .settle import SettleTracker
from .slipwatch import SlipWatch
from .status import GripperState, GripStatus, SlipEvent

# The slip watch samples as fast as the bus allows; live readouts are throttled
# to something a UI main loop can absorb.
READOUT_INTERVAL = 0.1

# How long the monitor stands aside for while a blocking move owns the bus.
BUS_YIELD = 0.02


class GripperAPI(MotionBase):
    """
    Open, close, grip and watch, in normalised units.

    Subclasses MotionBase for the limits handling and the settle rule, which is
    the same one the benchmark runners use, so a move here stops exactly where a
    benchmark move would.

    Callbacks:
      on_status(text)                            progress and warnings
      on_readout(position_ticks, current_raw)    live servo state
      on_status_change(old_status, new_status)   fired from the MONITOR THREAD;
                                                 must not block
    """

    def __init__(self, gripper, config=None, limits=None, control_table=None,
                 grip_strength=0.5, on_status=None, on_readout=None,
                 on_status_change=None, abort_event=None):
        grasp = section(config, "grasp", DEFAULT_GRASP)
        current_cfg = section(config, "current", DEFAULT_CURRENT)

        # The driver has already loaded the control table; reuse it rather than
        # making the caller pass the same dict twice.
        self.control_table = control_table or getattr(gripper, "ct", None) or {}
        units = self.control_table.get("units") or {}
        self.position_unit_deg = units.get(
            "position_deg_per_tick", DEFAULT_POSITION_UNIT_DEG
        )
        self.current_unit_ma = units.get("current_ma_per_tick")
        self.velocity_unit_rev = units.get("velocity_rev_per_min_per_tick")

        max_open, min_open, calibrated = resolve_limits(
            config, limits, self.position_unit_deg
        )
        self.calibrated = calibrated

        # Kept for calibrate(), which is the only thing that needs it. Where
        # from_config() loaded the limits from is remembered too, so a headless
        # recalibration writes back to the file it read.
        self.calibration_config = section(config, "calibration", DEFAULT_CALIBRATION)
        self.limits_path = None

        super().__init__(gripper, max_open, min_open,
                         grasp["profile_velocity"],
                         on_status=on_status, on_readout=on_readout,
                         abort_event=abort_event)
        self.settle_timeout = float(grasp["settle_timeout"])

        # Grasp classification
        self.miss_tolerance_ticks = int(grasp["miss_tolerance_ticks"])
        self.hold_current_fraction = float(grasp["hold_current_fraction"])
        self.idle_poll_interval = float(grasp["idle_poll_interval"])

        # Slip detection - built from the same `slip:` block the benchmark uses,
        # so both judge a slip by the same rule.
        self.slip = SlipWatch.from_config(section(config, "slip", DEFAULT_SLIP))

        self.current_min = int(current_cfg["min"])
        self.current_max = int(current_cfg["max"])

        velocity_cfg = section(config, "velocity", DEFAULT_VELOCITY)
        self.velocity_min = int(velocity_cfg["min"])
        self.velocity_max = int(velocity_cfg["max"])
        self._grip_strength = clamp01(grip_strength)
        self._grip_current_raw = self._strength_to_raw(self._grip_strength)

        self._on_status_change = on_status_change
        self._status = GripStatus.IDLE
        self._status_lock = threading.RLock()

        self._enabled = False
        self._owns_gripper = False

        # Monitor thread and the state it owns
        self._monitor = None
        self._stop = threading.Event()
        self._moving = threading.Event()   # set while a blocking move owns the bus
        self._armed = threading.Event()    # slip baseline taken, watch is live
        self._sample_lock = threading.Lock()
        self._last_position = None
        self._last_current = None

        self._grasp_position = None        # where the fingers stopped when gripping
        self._target_ticks = None
        # Named _settle_tracker, not _settle: _settle() is a method.
        self._settle_tracker = SettleTracker()
        self.last_slip = None
        self._reset_grasp()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    @classmethod
    def from_config(cls, config_path, control_table_path, limits_path=None,
                    calibrate_if_missing=False, **kwargs):
        """
        Open the port, select current-based position control, return an API.

        The only place this package touches the filesystem, apart from
        calibrate() writing back to `limits_path`. That path may be omitted or
        point at a file that does not exist yet, in which case the nominal
        travel from the config is used until the fingers are calibrated; check
        `.calibrated` to find out which you got, and call calibrate() to
        establish real ones.

        Pass calibrate_if_missing=True to do that immediately instead: if no
        usable limits were found, this calibrates headlessly before returning -
        which means dropping torque and taking WHATEVER POSITION THE FINGERS
        ARE ALREADY AT as MAX OPEN, exactly as calibrate() always has. There is
        no prompt here for the same reason calibrate() has none: make sure the
        fingers are open by hand before construction, or this will read
        wherever they happen to be as fully open. A failed probe raises
        CalibrationError/CalibrationAborted straight through and the port is
        closed rather than left connected but uncalibrated.
        """
        config = load_yaml(config_path)
        control_table = load_control_table(control_table_path)

        limits = None
        if limits_path is not None and Path(limits_path).exists():
            limits = load_yaml(limits_path)

        port = config.get("port") or {}
        gripper = DynamixelGripper(
            port["device"], port["baudrate"], port["protocol_version"],
            port["dxl_id"], control_table_path,
        )
        try:
            gripper.set_operating_mode_current_based_position()
            api = cls(gripper, config=config, limits=limits,
                      control_table=control_table, **kwargs)
            api._owns_gripper = True
            # Remembered even when the file does not exist yet: that is
            # exactly the uncalibrated case, and calibrate() should create it.
            api.limits_path = limits_path
            if calibrate_if_missing and not api.calibrated:
                api.calibrate()
        except Exception:
            gripper.close()
            raise

        return api

    # ------------------------------------------------------------------
    # Unit conversion
    # ------------------------------------------------------------------
    def _strength_to_raw(self, fraction):
        span = self.current_max - self.current_min
        return int(round(self.current_min + clamp01(fraction) * span))

    def _fraction_to_ticks(self, fraction):
        self.require_limits()
        span = self.max_open_position - self.min_open_position
        return int(round(self.min_open_position + clamp01(fraction) * span))

    def _ticks_to_fraction(self, ticks):
        if not self.has_limits:
            return None
        span = self.max_open_position - self.min_open_position
        if not span:
            return 0.0
        return clamp01((ticks - self.min_open_position) / span)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------
    @property
    def status(self):
        """The current verdict. A plain attribute read - never touches the bus."""
        return self._status

    @property
    def enabled(self):
        return self._enabled

    @property
    def baseline_current_raw(self):
        """Median holding current of the live grip, or None if not armed yet."""
        return self.slip.baseline

    @property
    def slip_threshold_raw(self):
        """The current a sample must fall below to count as a slip."""
        return self.slip.threshold

    @property
    def slip_detectable(self):
        """
        False when the hold is too weak for a drop to be distinguishable, so a
        long run of `ok` would prove nothing. See SlipWatch.detectable.
        """
        return self.slip.detectable

    def _set_status(self, new_status):
        with self._status_lock:
            old_status = self._status
            if old_status == new_status:
                return
            self._status = new_status
        if self._on_status_change is not None:
            try:
                self._on_status_change(old_status, new_status)
            except Exception as exc:
                self._report(f"on_status_change callback raised: {exc}")

    def _report(self, text):
        try:
            self.on_status(text)
        except Exception:
            # A failing status callback must not take the monitor thread down.
            pass

    # ------------------------------------------------------------------
    # Torque
    # ------------------------------------------------------------------
    def enable(self, on=True):
        """
        Turn torque on or off. Returns the resulting status.

        Enabling primes Goal Position at wherever the fingers currently are, so
        torque coming on does not snap them toward a goal left over from a
        previous session, and starts the monitor thread.
        """
        if on:
            self.require_limits()
            present = self.gripper.read_present_position()
            self.gripper.set_profile_velocity(self.profile_velocity)
            self.gripper.set_goal_current(self._grip_current_raw)
            self.gripper.set_goal_position(present)
            self.gripper.enable_torque()
            self._enabled = True
            self._reset_grasp()
            self._set_status(GripStatus.IDLE)
            self._start_monitor()
        else:
            self._stop_monitor()
            self.gripper.disable_torque()
            self._enabled = False
            self._reset_grasp()
            self._set_status(GripStatus.IDLE)
        return self.status

    def _require_enabled(self):
        if not self._enabled:
            raise RuntimeError(
                "Torque is off, so the fingers will not move. Call enable(True) first."
            )

    # ------------------------------------------------------------------
    # Calibration
    # ------------------------------------------------------------------
    def calibrate(self, limits_path=None, max_open=None, save=True):
        """
        Establish this set of fingers' travel limits, headlessly.

        Torque is dropped and WHEREVER THE FINGERS ALREADY ARE becomes MAX OPEN,
        so open them by hand before calling - nothing here waits for an operator.
        The gripper then closes under a current limit until it meets its
        mechanical stop, backs off, and reopens.

        On success the new limits are applied to this object immediately, so
        open()/close()/set_position() use them without a reconnect, and are
        written to `limits_path` (defaulting to the path from_config() read,
        created if absent) unless save=False.

        Pass `max_open` to skip the manual step entirely and declare it instead.

        Raises CalibrationError if no stop was found - the fingers are left
        reopened and the old limits untouched - or CalibrationAborted if the
        abort event was set.
        """
        # The monitor reads the bus, and torque is about to go away underneath
        # it; the probe owns the servo for the duration.
        self._stop_monitor()
        self._enabled = False
        self._reset_grasp()
        self._set_status(GripStatus.IDLE)

        cfg = self.calibration_config
        calibrator = GripperCalibrator(
            self.gripper,
            probe_current=cfg["probe_current"],
            probe_velocity=cfg["probe_velocity"],
            backoff_ticks=cfg["backoff_ticks"],
            return_velocity=cfg.get("return_velocity"),
            stall_current_fraction=cfg["stall_current_fraction"],
            stall_stable_samples=cfg["stall_stable_samples"],
            stall_position_tolerance=cfg["stall_position_tolerance"],
            max_probe_ticks=cfg["max_probe_ticks"],
            probe_timeout=cfg["probe_timeout"],
            poll_interval=cfg["poll_interval"],
            on_status=self._report,
            on_readout=self.on_readout,
            abort_event=self.abort_event,
        )

        if max_open is None:
            calibrator.release_for_manual_positioning()
            max_open = calibrator.capture_max_open()
        else:
            self.gripper.disable_torque()
            calibrator.max_open = int(max_open)
            self._report(f"MAX OPEN declared at {calibrator.max_open} ticks.")

        try:
            result = calibrator.probe_close_limit()
        finally:
            # The probe leaves the fingers reopened but still holding torque.
            # Calibration is not a grip, so it should not end holding one.
            try:
                self.gripper.disable_torque()
            except Exception:
                pass
            self._enabled = False

        self.set_limits(result.max_open, result.min_open)
        self.calibrated = True

        path = limits_path if limits_path is not None else self.limits_path
        if save and path is not None:
            save_limits(path, result, self.position_unit_deg)
            self._report(f"Limits written to {path}.")
        elif save:
            self._report(
                "Calibrated, but no limits path is known, so nothing was "
                "written. Pass limits_path= to save these limits."
            )

        self._report(
            f"Calibrated: MAX OPEN {result.max_open}, MIN OPEN {result.min_open}, "
            f"travel {result.travel_ticks} ticks."
        )
        return result

    # ------------------------------------------------------------------
    # Motion
    # ------------------------------------------------------------------
    def open(self, wait=True):
        """Open to MAX OPEN. Returns the resulting status."""
        return self._move(self.max_open_position, wait=wait)

    def close(self, wait=True):
        """
        Close to MIN OPEN under the current ceiling. Returns the status:
        "ok" if the fingers stalled on something, "miss" if they shut fully.

        Closes the FINGERS, not the port - see disconnect().
        """
        return self._move(self.min_open_position, wait=wait)

    def set_position(self, fraction, wait=True):
        """
        Drive to a normalised position: 0.0 fully closed, 1.0 fully open.

        Classified like close(), so stalling on an object part way through
        still reports "ok".
        """
        return self._move(self._fraction_to_ticks(fraction), wait=wait)

    def _move(self, target_ticks, wait=True):
        self.require_limits()
        self._require_enabled()

        # A new command clears the slip latch and the old baseline.
        self._reset_grasp()
        self._target_ticks = int(target_ticks)
        self._set_status(GripStatus.MOVING)

        if not wait:
            # The monitor owns the settle detection and the classification.
            self._pending_classify = True
            self.gripper.set_goal_position(self._target_ticks)
            return self.status

        # Blocking: this thread owns the bus for the duration, so the monitor
        # stands down rather than competing for it.
        self._moving.set()
        try:
            position, current = self.move_and_settle(self._target_ticks)
        finally:
            self._moving.clear()

        self._settle(position, current)

        if self.status is GripStatus.OK:
            # The caller is already blocking, so wait for the monitor to take a
            # baseline too: when close() returns "ok" the slip watch is live,
            # rather than arming itself a second later.
            self._wait_until_armed()
        return self.status

    def _settle(self, position, current):
        """Record where a move ended and publish what it meant."""
        self._record_sample(position, current)
        verdict = self._classify(position, current, self._target_ticks)
        if verdict is GripStatus.OK:
            self._grasp_position = position
        self._set_status(verdict)
        return verdict

    def _classify(self, position, current, target_ticks):
        """
        What a settled move means.

        Reaching MIN OPEN after being told to go there means nothing was in the
        way - a miss. Stopping short of the target while still drawing near the
        goal current means something stopped the fingers, which is the whole
        premise of current-based position control - a grip. Anything else is the
        fingers simply arriving where they were sent.
        """
        tolerance = self.miss_tolerance_ticks
        commanded_full_close = target_ticks <= self.min_open_position + tolerance
        reached_min_open = abs(position - self.min_open_position) <= tolerance
        if commanded_full_close and reached_min_open:
            return GripStatus.MISS

        stopped_short = abs(position - target_ticks) > tolerance
        holding = abs(current) >= self.hold_current_fraction * self._grip_current_raw
        if stopped_short and holding:
            return GripStatus.OK
        return GripStatus.IDLE

    # ------------------------------------------------------------------
    # Grip strength
    # ------------------------------------------------------------------
    @property
    def grip_strength(self):
        """Normalised 0.0-1.0 between the config's minimum and maximum current."""
        return self._grip_strength

    @property
    def grip_current_raw(self):
        return self._grip_current_raw

    def set_grip_strength(self, fraction):
        """
        Set how hard the fingers squeeze, live, with torque on.

        0.0 is the config's minimum current, 1.0 its maximum. Since current is
        proportional to torque, this is the force ceiling the fingers stall
        against - raising it mid-grip tightens on whatever is already held.

        Changing it invalidates the slip baseline, which was measured at the old
        holding current, so the monitor re-measures before watching again: a
        threshold derived from the old hold level would either fire immediately
        on a weakened grip or never fire on a tightened one.
        """
        self._grip_strength = clamp01(fraction)
        self._grip_current_raw = self._strength_to_raw(self._grip_strength)
        self.gripper.set_goal_current(self._grip_current_raw)

        self._armed.clear()
        self.slip.reset()
        return self._grip_current_raw

    # ------------------------------------------------------------------
    # Reading back
    # ------------------------------------------------------------------
    def state(self):
        """
        A snapshot of status plus the latest servo readings.

        Served from the monitor's last sample when it is running. During a slip
        watch only current is sampled - position costs a second round trip and
        would halve the rate the watch runs at - so position may be up to one
        grasp old there.
        """
        with self._sample_lock:
            position = self._last_position
            current = self._last_current

        if position is None or current is None:
            position = self.gripper.read_present_position()
            current = self.gripper.read_present_current()
            self._record_sample(position, current)

        return GripperState(
            status=self.status,
            position_ticks=position,
            position=self._ticks_to_fraction(position),
            current_raw=current,
            current_ma=(
                round(current * self.current_unit_ma, 2)
                if self.current_unit_ma else None
            ),
            grip_strength=self._grip_strength,
            grip_current_raw=self._grip_current_raw,
            enabled=self._enabled,
        )

    def wait_for_slip(self, timeout=None):
        """
        Block until the grip stops being `ok`, and return whatever it became.

        Returns "slip" if the object escaped, or the status it ended on if the
        timeout expires first (still "ok" for a grip that held).
        """
        deadline = time.monotonic() + (
            self.slip.timeout if timeout is None else timeout
        )
        while self.status is GripStatus.OK and time.monotonic() < deadline:
            self.check_abort()
            time.sleep(0.01)
        return self.status

    # ------------------------------------------------------------------
    # Monitor thread
    #
    # Owns every read that a command did not ask for. It stands down while a
    # blocking move is in flight, samples current alone at the slip rate while a
    # grip is held, and otherwise ticks over slowly just to keep state() fresh.
    # ------------------------------------------------------------------
    def _start_monitor(self):
        if self._monitor is not None and self._monitor.is_alive():
            return
        self._stop.clear()
        self._monitor = threading.Thread(
            target=self._monitor_loop, name="gripper-monitor", daemon=True
        )
        self._monitor.start()

    def _stop_monitor(self):
        self._stop.set()
        monitor, self._monitor = self._monitor, None
        if monitor is not None and monitor.is_alive():
            monitor.join(timeout=2.0)

    def _reset_grasp(self):
        """Clear the slip latch and everything derived from the last grasp."""
        self._armed.clear()
        self.slip.reset()
        self._grasp_position = None
        self._pending_classify = False
        self._settle_tracker.reset()

    def _record_sample(self, position, current):
        with self._sample_lock:
            if position is not None:
                self._last_position = position
            if current is not None:
                self._last_current = current

    def _monitor_loop(self):
        last_readout = 0.0
        while not self._stop.is_set():
            if self._moving.is_set():
                # A blocking move owns the bus; do not compete for it.
                self._stop.wait(BUS_YIELD)
                continue
            try:
                if self.status is GripStatus.OK:
                    if not self._armed.is_set():
                        self._arm_slip_watch()
                        continue
                    last_readout = self._watch_step(last_readout)
                else:
                    last_readout = self._idle_step(last_readout)
            except Exception as exc:
                # A transient bus error must not kill the monitor: report it and
                # carry on, exactly as the manual GUI's poll loop does.
                self._report(f"Monitor read failed: {exc}")
                self._stop.wait(self.idle_poll_interval)

    def _wait_until_armed(self):
        """Give the monitor time to take a baseline before returning "ok"."""
        budget = (
            self.slip.baseline_settle
            + self.slip.baseline_samples * self.slip.poll_interval
            + 2.0
        )
        self._armed.wait(budget)

    def _arm_slip_watch(self):
        """Collect the holding current, then hand it to SlipWatch for a threshold."""
        if self._stop.wait(self.slip.baseline_settle):
            return

        samples = []
        while len(samples) < self.slip.baseline_samples:
            if self._stop.is_set() or self._moving.is_set():
                return
            if self.status is not GripStatus.OK:
                return
            # Closing draws negative current; only the magnitude matters.
            samples.append(abs(self.gripper.read_present_current()))
            self._stop.wait(self.slip.poll_interval)

        threshold = self.slip.arm(samples)
        self._armed.set()

        if threshold <= 0:
            self._report(
                f"WARNING: holding current is only {self.slip.baseline:.0f} raw, too "
                "low for a drop to be distinguishable. A slip cannot be detected at "
                "this grip strength - grip something firmer, raise set_grip_strength, "
                "or lower slip.min_drop_raw."
            )

    def _watch_step(self, last_readout):
        """One sample of the slip watch. Current only, as fast as the bus allows."""
        now = time.monotonic()
        signed = self.gripper.read_present_current()
        # Closing draws negative current; only the magnitude matters to the
        # threshold, but the snapshot keeps the sign the driver reported so
        # state() reads the same whether or not a grip is being held.
        self._record_sample(None, signed)

        if now - last_readout >= READOUT_INTERVAL:
            self.on_readout(self._last_position, signed)
            last_readout = now

        event = self.slip.feed(abs(signed), now=now)
        if event is not None:
            self._fire_slip(event)
            return last_readout

        self._stop.wait(self.slip.poll_interval)
        return last_readout

    def _fire_slip(self, event):
        """Latch the slip, and add the position SlipWatch deliberately skipped."""
        position = None
        try:
            position = self.gripper.read_present_position()
            self._record_sample(position, None)
        except Exception:
            # The verdict matters more than the position it happened at.
            pass

        shift = (
            position - self._grasp_position
            if position is not None and self._grasp_position is not None
            else None
        )
        self.last_slip = dataclasses.replace(
            event, position_ticks=position, position_shift=shift
        )
        # Latched: the fingers will now carry on closing to MIN OPEN, and
        # without this the next classification would call that a miss.
        self._set_status(GripStatus.SLIP)

    def _idle_step(self, last_readout):
        """
        One slow sample while not holding.

        Keeps state() fresh, and resolves a wait=False move: the same settle
        rule the blocking path uses, applied at the idle rate.
        """
        position = self.gripper.read_present_position()
        current = self.gripper.read_present_current()
        self._record_sample(position, current)

        now = time.monotonic()
        if now - last_readout >= READOUT_INTERVAL:
            self.on_readout(position, current)
            last_readout = now

        if self._pending_classify and self.status is GripStatus.MOVING:
            if self._settle_tracker.feed(position):
                self._pending_classify = False
                self._settle(position, current)

        self._stop.wait(self.idle_poll_interval)
        return last_readout

    # ------------------------------------------------------------------
    # Manual control
    # ------------------------------------------------------------------
    def teleop(self, title=None):
        """
        Open the manual control console, and block until it is closed.

        Three sliders straight onto the servo - Goal Position in ticks, Goal
        Current and Profile Velocity in raw units - plus a live readout and the
        calibration wizard. For driving the fingers by hand: normalised
        open()/close()/set_position() are what a program should use.

        The console takes the servo over while it is up, so this drops torque
        and stops the monitor on the way in and leaves torque off on the way
        out. It does not touch the port: this object is still connected and
        usable afterwards, and still needs disconnecting.

        Tk is imported here, not at module scope, so a machine with no tkinter
        can still use everything else in this package.
        """
        from .teleop import DEFAULT_TITLE, run

        run(self, title=title or DEFAULT_TITLE)

    # ------------------------------------------------------------------
    # Teardown
    # ------------------------------------------------------------------
    def disconnect(self):
        """
        Stop the monitor, drop torque, and release the port.

        The port is only closed if this object opened it - a gripper handed in
        by the caller stays theirs to close.
        """
        try:
            self._stop_monitor()
        finally:
            self._enabled = False
            self._set_status(GripStatus.IDLE)
            if self._owns_gripper:
                self.gripper.close()
            else:
                try:
                    self.gripper.disable_torque()
                except Exception:
                    pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.disconnect()
        return False


__all__ = [
    "AbortedError",
    "GripStatus",
    "GripperAPI",
    "GripperState",
    "SlipEvent",
    "SlipWatch",
]
