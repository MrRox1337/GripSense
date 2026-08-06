"""
Tk front end for manual gripper control.

Three sliders (position, goal current, profile velocity) plus a live readout
polled from the servo on a background thread. All hardware access goes
through the DynamixelGripper returned by gripper_settings.connect().
"""

import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

import gripper_settings as settings

POSITION_UNIT_DEG = settings.POSITION_UNIT_DEG
CURRENT_UNIT_MA = settings.CURRENT_UNIT_MA
VELOCITY_UNIT_REV = settings.VELOCITY_UNIT_REV

# Slider bounds come from the last calibration, so the position slider spans
# the travel of the fingers actually fitted. Falls back to the nominal values
# in gripper_config.yaml if the fingers have never been calibrated, which the
# UI says out loud rather than pretending the range is trustworthy.
MAX_OPEN_POSITION, MIN_OPEN_POSITION, LIMITS_CALIBRATED = settings.travel_limits()

CURRENT_MIN = settings.CURRENT_MIN
CURRENT_MAX = settings.CURRENT_MAX

VELOCITY_MIN = settings.VELOCITY_MIN
VELOCITY_MAX = settings.VELOCITY_MAX

# Slider drag -> serial write throttle interval (seconds)
DRAG_SEND_INTERVAL = 0.05
# Present position/current poll interval (seconds)
POLL_INTERVAL = 0.1


class GripperApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Slip-Aware Gripper Control (XM430-W210-T)")

        self.gripper = None
        self.torque_enabled = False
        self._last_send_time = 0.0
        self._poll_thread_stop = threading.Event()

        self._build_ui()
        self._connect_hardware()
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
        source = "calibrated" if LIMITS_CALIBRATED else "NOT CALIBRATED - nominal fallback"
        pos_frame = ttk.LabelFrame(
            frame,
            text=(
                f"Position: {MAX_OPEN_POSITION} (max open) -> {MIN_OPEN_POSITION} "
                f"(min open)  [{source}]"
            ),
        )
        pos_frame.grid(row=1, column=0, columnspan=2, sticky="ew", **pad)

        if not LIMITS_CALIBRATED:
            ttk.Label(
                pos_frame,
                text=(
                    "These limits belong to whichever fingers were fitted when they were\n"
                    "written into gripper_config.yaml. Run the calibration wizard in\n"
                    "Scripts/gripper_benchmark.py before trusting the ends of this slider."
                ),
                foreground="red",
                justify="left",
            ).grid(row=2, column=0, columnspan=3, padx=8, pady=(0, 6), sticky="w")

        self.position_var = tk.IntVar(value=MAX_OPEN_POSITION)
        self.position_slider = ttk.Scale(
            pos_frame,
            from_=MAX_OPEN_POSITION,
            to=MIN_OPEN_POSITION,
            orient="horizontal",
            length=400,
            variable=self.position_var,
            command=self._on_position_slider,
        )
        self.position_slider.grid(row=0, column=0, columnspan=3, sticky="ew", padx=8, pady=4)

        self.position_label_var = tk.StringVar()
        ttk.Label(pos_frame, textvariable=self.position_label_var).grid(
            row=1, column=0, columnspan=3, padx=8
        )
        self._update_position_label(MAX_OPEN_POSITION)

        # --- Current slider ---
        cur_frame = ttk.LabelFrame(
            frame, text=f"Goal Current ({CURRENT_MIN}-{CURRENT_MAX} raw units)"
        )
        cur_frame.grid(row=2, column=0, columnspan=2, sticky="ew", **pad)

        self.current_var = tk.IntVar(value=CURRENT_MIN)
        self.current_slider = ttk.Scale(
            cur_frame,
            from_=CURRENT_MIN,
            to=CURRENT_MAX,
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
        self._update_current_label(CURRENT_MIN)

        # --- Speed (Profile Velocity) slider ---
        vel_frame = ttk.LabelFrame(
            frame, text=f"Speed / Profile Velocity ({VELOCITY_MIN}-{VELOCITY_MAX} raw units)"
        )
        vel_frame.grid(row=3, column=0, columnspan=2, sticky="ew", **pad)

        self.velocity_var = tk.IntVar(value=VELOCITY_MAX)
        self.velocity_slider = ttk.Scale(
            vel_frame,
            from_=VELOCITY_MIN,
            to=VELOCITY_MAX,
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
        self._update_velocity_label(VELOCITY_MAX)

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
    # Hardware connection
    # ------------------------------------------------------------------
    def _connect_hardware(self):
        try:
            self.gripper = settings.connect()
            self.gripper.set_goal_current(CURRENT_MIN)
            self.gripper.set_profile_velocity(VELOCITY_MAX)
            # Prime Goal Position so the first torque-enable drives to
            # max-open rather than to whatever stale goal the servo held.
            self.gripper.set_goal_position(MAX_OPEN_POSITION)
        except Exception as exc:
            messagebox.showerror("Connection error", str(exc))
            self.root.destroy()
            raise

    # ------------------------------------------------------------------
    # Torque handling
    # ------------------------------------------------------------------
    def _toggle_torque(self):
        try:
            if self.torque_enabled:
                self.gripper.disable_torque()
                self.torque_enabled = False
                self.torque_button.config(text="Enable Torque")
                self.torque_status_var.set("Torque: DISABLED")
            else:
                self.gripper.enable_torque()
                self.torque_enabled = True
                self.torque_button.config(text="Disable Torque")
                self.torque_status_var.set("Torque: ENABLED")
        except Exception as exc:
            messagebox.showerror("Torque error", str(exc))

    def _emergency_stop(self):
        try:
            self.gripper.disable_torque()
        except Exception as exc:
            messagebox.showerror("Emergency stop error", str(exc))
        finally:
            self.torque_enabled = False
            self.torque_button.config(text="Enable Torque")
            self.torque_status_var.set("Torque: DISABLED")

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
            # Non-blocking: a transient busy/timeout during a drag should
            # not pop a modal dialog (that stalls the Tk loop and causes
            # writes to pile up, producing more errors). Just surface it.
            self.comm_status_var.set(f"Comm status: {exc}")

    # ------------------------------------------------------------------
    # Label formatting
    # ------------------------------------------------------------------
    def _update_position_label(self, position_ticks):
        deg = position_ticks * POSITION_UNIT_DEG
        self.position_label_var.set(f"Raw: {position_ticks}  |  {deg:.2f} deg")

    def _update_current_label(self, current_units):
        ma = current_units * CURRENT_UNIT_MA
        self.current_label_var.set(f"Raw: {current_units}  |  {ma:.1f} mA")

    def _update_velocity_label(self, velocity_units):
        rev_per_min = velocity_units * VELOCITY_UNIT_REV
        self.velocity_label_var.set(f"Raw: {velocity_units}  |  {rev_per_min:.2f} rev/min")

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
                deg = position_ticks * POSITION_UNIT_DEG
                ma = current_units * CURRENT_UNIT_MA
                text = (
                    f"Present position: {position_ticks} ({deg:.2f} deg)  |  "
                    f"Present current: {current_units} ({ma:.1f} mA)"
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
        if self.gripper is not None:
            try:
                self.gripper.close()
            except Exception:
                pass
        self.root.destroy()
