"""
Manual control console: three sliders and a live readout, over a live API.

This is the servo at its rawest - Goal Position in ticks, Goal Current and
Profile Velocity in raw units - which is the point. The API's normalised
commands are what a program should use; this is what a person uses when they
want to see what the fingers do at 104 raw of current, or park them somewhere
to fit a part.

The window never opens or closes a port. It is handed a connected GripperAPI,
borrows the servo for as long as it is up, drops torque on the way out, and
leaves the port to whoever opened it. So this is safe to open in the middle of
a session and carry on afterwards:

    with GripperAPI.from_config(...) as api:
        api.teleop()          # blocks until the window is closed
        api.enable(True)      # and the API is still usable after it
        api.close()

Tkinter is imported here rather than in api.py, so the package still imports on
a machine without it.
"""

import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from .calibration_dialog import CalibrationDialog

__all__ = ["TeleopWindow", "run"]

DEFAULT_TITLE = "Slip-Aware Gripper Control"

# Slider drag -> serial write throttle interval (seconds)
DRAG_SEND_INTERVAL = 0.05
# Present position/current poll interval (seconds)
POLL_INTERVAL = 0.1


def run(api, title=DEFAULT_TITLE):
    """Open the console on its own Tk root and block until it is closed."""
    root = tk.Tk()
    TeleopWindow(root, api, title=title)
    root.mainloop()


class TeleopWindow:
    """
    The console, built into `master` - a Tk root or a Toplevel.

    Construct it directly to put the console inside an application that already
    has a main loop; use run() (or GripperAPI.teleop()) for a standalone window.
    """

    def __init__(self, master, api, title=DEFAULT_TITLE):
        self.root = master
        self.api = api
        self.gripper = api.gripper
        self.root.title(title)

        self.torque_enabled = False
        self._last_send_time = 0.0
        self._poll_thread_stop = threading.Event()

        # Slider bounds come from the last calibration, so the position slider
        # spans the travel of the fingers actually fitted. Falls back to the
        # nominal values in the config if they have never been calibrated,
        # which the UI says out loud rather than pretending the range is
        # trustworthy. Instance state, not read once, because a calibration run
        # from this window replaces it without a restart.
        self.max_open_position = api.max_open_position
        self.min_open_position = api.min_open_position
        self.limits_calibrated = api.calibrated

        self._build_ui()
        self._prime_servo()
        self._start_polling()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self):
        pad = {"padx": 10, "pady": 8}
        frame = ttk.Frame(self.root)
        frame.grid(row=0, column=0, sticky="nsew")

        # --- Torque control ---
        torque_frame = ttk.LabelFrame(frame, text="Torque")
        torque_frame.grid(row=0, column=0, columnspan=2, sticky="ew", **pad)

        self.torque_button = ttk.Button(
            torque_frame, text="Enable Torque", command=self._toggle_torque
        )
        self.torque_button.grid(row=0, column=0, padx=8, pady=6)

        self.torque_status_var = tk.StringVar(value="Torque: DISABLED")
        ttk.Label(torque_frame, textvariable=self.torque_status_var).grid(
            row=0, column=1, padx=8
        )

        # --- Position slider ---
        self.pos_frame = ttk.LabelFrame(frame, text=self._position_frame_title())
        self.pos_frame.grid(row=1, column=0, columnspan=2, sticky="ew", **pad)

        self.limits_warning_label = None
        if not self.limits_calibrated:
            self.limits_warning_label = ttk.Label(
                self.pos_frame,
                text=(
                    "These limits are the nominal travel from the config, not a\n"
                    "measurement of the fingers now fitted. Calibrate below before\n"
                    "trusting these ends."
                ),
                foreground="red",
                justify="left",
            )
            self.limits_warning_label.grid(
                row=2, column=0, columnspan=3, padx=8, pady=(0, 6), sticky="w"
            )

        self.position_var = tk.IntVar(value=self.max_open_position)
        self.position_slider = ttk.Scale(
            self.pos_frame,
            from_=self.max_open_position,
            to=self.min_open_position,
            orient="horizontal",
            length=400,
            variable=self.position_var,
            command=self._on_position_slider,
        )
        self.position_slider.grid(row=0, column=0, columnspan=3, sticky="ew", padx=8, pady=4)

        self.position_label_var = tk.StringVar()
        ttk.Label(self.pos_frame, textvariable=self.position_label_var).grid(
            row=1, column=0, columnspan=3, padx=8
        )
        self._update_position_label(self.max_open_position)

        self.calibrate_button = ttk.Button(
            self.pos_frame, text="Calibrate...", command=self._open_calibration_dialog
        )
        self.calibrate_button.grid(row=3, column=0, padx=8, pady=(0, 8), sticky="w")

        # --- Current slider ---
        cur_frame = ttk.LabelFrame(
            frame,
            text=f"Goal Current ({self.api.current_min}-{self.api.current_max} raw units)",
        )
        cur_frame.grid(row=2, column=0, columnspan=2, sticky="ew", **pad)

        self.current_var = tk.IntVar(value=self.api.current_min)
        self.current_slider = ttk.Scale(
            cur_frame,
            from_=self.api.current_min,
            to=self.api.current_max,
            orient="horizontal",
            length=400,
            variable=self.current_var,
            command=self._on_current_slider,
        )
        self.current_slider.grid(row=0, column=0, columnspan=3, sticky="ew", padx=8, pady=4)

        self.current_label_var = tk.StringVar()
        ttk.Label(cur_frame, textvariable=self.current_label_var).grid(
            row=1, column=0, columnspan=3, padx=8
        )
        self._update_current_label(self.api.current_min)

        # --- Speed (Profile Velocity) slider ---
        vel_frame = ttk.LabelFrame(
            frame,
            text=(
                f"Speed / Profile Velocity "
                f"({self.api.velocity_min}-{self.api.velocity_max} raw units)"
            ),
        )
        vel_frame.grid(row=3, column=0, columnspan=2, sticky="ew", **pad)

        self.velocity_var = tk.IntVar(value=self.api.velocity_max)
        self.velocity_slider = ttk.Scale(
            vel_frame,
            from_=self.api.velocity_min,
            to=self.api.velocity_max,
            orient="horizontal",
            length=400,
            variable=self.velocity_var,
            command=self._on_velocity_slider,
        )
        self.velocity_slider.grid(row=0, column=0, columnspan=3, sticky="ew", padx=8, pady=4)

        self.velocity_label_var = tk.StringVar()
        ttk.Label(vel_frame, textvariable=self.velocity_label_var).grid(
            row=1, column=0, columnspan=3, padx=8
        )
        self._update_velocity_label(self.api.velocity_max)

        # --- Live readout ---
        readout_frame = ttk.LabelFrame(frame, text="Live Readout")
        readout_frame.grid(row=4, column=0, columnspan=2, sticky="ew", **pad)

        self.readout_var = tk.StringVar(value="Present position: --  |  Present current: --")
        ttk.Label(readout_frame, textvariable=self.readout_var).grid(
            row=0, column=0, padx=8, pady=6
        )

        self.comm_status_var = tk.StringVar(value="Comm status: OK")
        ttk.Label(readout_frame, textvariable=self.comm_status_var).grid(
            row=1, column=0, padx=8, pady=(0, 6)
        )

        # --- Emergency stop ---
        stop_button = ttk.Button(
            frame, text="EMERGENCY STOP (Disable Torque)", command=self._emergency_stop
        )
        stop_button.grid(row=5, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 10))

    # ------------------------------------------------------------------
    # Taking over the servo
    # ------------------------------------------------------------------
    def _prime_servo(self):
        try:
            # Whatever the API was doing with the servo, this window is driving
            # it now: torque off, monitor stopped, sliders in charge.
            self.api.enable(False)
            self.gripper.set_goal_current(self.api.current_min)
            self.gripper.set_profile_velocity(self.api.velocity_max)
            # Prime Goal Position so the first torque-enable drives to max-open
            # rather than to whatever stale goal the servo held.
            self.gripper.set_goal_position(self.max_open_position)
        except Exception as exc:
            messagebox.showerror("Gripper error", str(exc))
            self.root.destroy()
            raise

    # ------------------------------------------------------------------
    # Torque handling
    # ------------------------------------------------------------------
    def _toggle_torque(self):
        try:
            if self.torque_enabled:
                self.gripper.disable_torque()
                self._set_torque_ui(False)
            else:
                self.gripper.enable_torque()
                self._set_torque_ui(True)
        except Exception as exc:
            messagebox.showerror("Torque error", str(exc))

    def _emergency_stop(self):
        try:
            self.gripper.disable_torque()
        except Exception as exc:
            messagebox.showerror("Emergency stop error", str(exc))
        finally:
            self._set_torque_ui(False)

    def _set_torque_ui(self, enabled):
        self.torque_enabled = enabled
        self.torque_button.config(text="Disable Torque" if enabled else "Enable Torque")
        self.torque_status_var.set(f"Torque: {'ENABLED' if enabled else 'DISABLED'}")

    # ------------------------------------------------------------------
    # Calibration
    # ------------------------------------------------------------------
    def _position_frame_title(self):
        source = "calibrated" if self.limits_calibrated else "NOT CALIBRATED - nominal fallback"
        return (
            f"Position: {self.max_open_position} (max open) -> "
            f"{self.min_open_position} (min open)  [{source}]"
        )

    def _open_calibration_dialog(self):
        # The dialog drops torque itself, so the window's torque state is
        # brought in line immediately rather than going stale while the modal
        # wizard is up.
        self._set_torque_ui(False)
        CalibrationDialog(self.root, self.api, on_saved=self._apply_new_limits)

    def _apply_new_limits(self, _limits):
        """Called once the wizard's Save button has written new limits."""
        # calibrate() already applied them to the API; this window just catches up.
        self.max_open_position = self.api.max_open_position
        self.min_open_position = self.api.min_open_position
        self.limits_calibrated = True

        self.pos_frame.config(text=self._position_frame_title())
        if self.limits_warning_label is not None:
            self.limits_warning_label.grid_remove()
            self.limits_warning_label = None

        self.position_slider.config(from_=self.max_open_position, to=self.min_open_position)
        self.position_var.set(self.max_open_position)
        self._update_position_label(self.max_open_position)

        # The probe leaves the fingers physically at MAX OPEN with torque off.
        # Priming Goal Position to match now means the next Enable Torque
        # drives to where the fingers already are rather than snapping toward
        # whatever goal was set before calibration.
        try:
            self.gripper.set_goal_position(self.max_open_position)
        except Exception as exc:
            messagebox.showerror("Post-calibration error", str(exc))

    # ------------------------------------------------------------------
    # Slider callbacks
    # ------------------------------------------------------------------
    def _on_position_slider(self, _value):
        position_ticks = int(round(self.position_var.get()))
        self._update_position_label(position_ticks)
        self._throttled_send(lambda: self.gripper.set_goal_position(position_ticks))

    def _on_current_slider(self, _value):
        current_units = int(round(self.current_var.get()))
        self._update_current_label(current_units)
        self._throttled_send(lambda: self.gripper.set_goal_current(current_units))

    def _on_velocity_slider(self, _value):
        velocity_units = int(round(self.velocity_var.get()))
        self._update_velocity_label(velocity_units)
        self._throttled_send(lambda: self.gripper.set_profile_velocity(velocity_units))

    def _throttled_send(self, send_fn):
        now = time.monotonic()
        if now - self._last_send_time < DRAG_SEND_INTERVAL:
            return
        self._last_send_time = now
        try:
            send_fn()
            self.comm_status_var.set("Comm status: OK")
        except Exception as exc:
            # Non-blocking: a transient busy/timeout during a drag should not
            # pop a modal dialog (that stalls the Tk loop and causes writes to
            # pile up, producing more errors). Just surface it.
            self.comm_status_var.set(f"Comm status: {exc}")

    # ------------------------------------------------------------------
    # Label formatting
    # ------------------------------------------------------------------
    def _update_position_label(self, position_ticks):
        deg = position_ticks * self.api.position_unit_deg
        self.position_label_var.set(f"Raw: {position_ticks}  |  {deg:.2f} deg")

    def _update_current_label(self, current_units):
        self.current_label_var.set(
            f"Raw: {current_units}{self._scaled(current_units, self.api.current_unit_ma, 'mA')}"
        )

    def _update_velocity_label(self, velocity_units):
        self.velocity_label_var.set(
            f"Raw: {velocity_units}"
            f"{self._scaled(velocity_units, self.api.velocity_unit_rev, 'rev/min')}"
        )

    @staticmethod
    def _scaled(raw, unit, suffix):
        """The engineering-unit half of a readout, or nothing if unscaled."""
        return "" if unit is None else f"  |  {raw * unit:.2f} {suffix}"

    # ------------------------------------------------------------------
    # Live polling of present position / current
    # ------------------------------------------------------------------
    def _start_polling(self):
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

    def _poll_loop(self):
        while not self._poll_thread_stop.is_set():
            try:
                position_ticks = self.gripper.read_present_position()
                current_units = self.gripper.read_present_current()
                text = (
                    f"Present position: {position_ticks}"
                    f"{self._scaled(position_ticks, self.api.position_unit_deg, 'deg')}"
                    f"  ||  Present current: {current_units}"
                    f"{self._scaled(current_units, self.api.current_unit_ma, 'mA')}"
                )
                self.root.after(0, self.readout_var.set, text)
            except Exception:
                # Skip a bad read; keep polling.
                pass
            time.sleep(POLL_INTERVAL)

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------
    def _on_close(self):
        self._poll_thread_stop.set()
        try:
            # Torque off, but the port stays open - it belongs to whoever
            # opened the API, not to this window.
            self.gripper.disable_torque()
        except Exception:
            pass
        self.root.destroy()
