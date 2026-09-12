"""
A Tk front end for GripperAPI: what it was handed, what it reports, and how
often it is right.

This is the worked example for the driver package, so it imports
dynamixel_gripper and nothing else from this repository - no gripper_settings,
no shared UI helpers. Anyone wanting the same control needs
Lib/dynamixel_gripper/, their own three YAML files, and this file to read.

Three panels, three jobs:

  initialisation  the paths handed to from_config(), the port they name, and
                  the calibrated limits that came back - including when they
                  were measured, since limits belong to the fingers that were
                  fitted at the time
  control         torque, a one-shot grip, and the two normalised sliders, with
                  the API's status mirrored as a colour
  status tests    close on an object, on nothing, and on an object that is then
                  pulled free, N times each, writing a CSV of every trial and a
                  matrix of how often the verdict matched the setup

Threading: close(), wait_for_slip() and a whole test run block for seconds, so
they run on a worker thread. Widgets are touched from the Tk thread only -
anything arriving from a worker or from the API's monitor thread is marshalled
back with root.after.
"""

import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

from dynamixel_gripper import AbortedError, GripperAPI
from dynamixel_gripper.config import load_yaml
from gripper_demo_report import Trial, write_accuracy_matrix, write_csv

STATUS_COLOURS = {
    "idle": "#202020",
    "moving": "#1f6fd0",
    "ok": "#1e9e4a",
    "slip": "#e0b400",
    "miss": "#cc2b2b",
}
OFF_COLOUR = "#202020"

# What the operator has to do before each kind of trial. A miss needs the
# fingers clear; the other two need the object back between them.
SETUP_PROMPTS = {
    "ok": "Place an object between the fingers.",
    "miss": "Clear the fingers - nothing between them.",
    "slip": "Place an object between the fingers. Pull it free once the light turns green.",
}

TICK_MS = 100            # UI refresh; status is a plain attribute read
SLIDER_INTERVAL = 0.05   # s between slider-driven register writes
GRIP_HOLD_S = 2.0        # how long the one-shot grip test holds before reopening


class DemoApp:
    """The demo window. Owns the API object and the worker thread that drives it."""

    def __init__(self, root, config_path, control_table_path, limits_path, output_dir):
        self.root = root
        self.root.title("GripSense - GripperAPI demo")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.config_path = Path(config_path)
        self.control_table_path = Path(control_table_path)
        self.limits_path = Path(limits_path)
        self.output_dir = Path(output_dir)

        self.api = None
        self.trials = []

        self.abort_event = threading.Event()
        self.ready_event = threading.Event()
        self.worker = None
        self.busy = False
        self.waiting_ready = False

        self._last_position_send = 0.0
        self._last_strength_send = 0.0
        self._tick_job = None

        # One stamp per session: the CSV grows across runs and the matrix is
        # redrawn from all of it, so the two always describe the same trials.
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.csv_path = self.output_dir / f"grip_status_{stamp}.csv"
        self.matrix_path = self.output_dir / f"grip_status_{stamp}.jpg"

        self._build_ui()
        self._refresh_controls()
        self._tick()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}
        frame = ttk.Frame(self.root, padding=10)
        frame.grid(row=0, column=0, sticky="nsew")

        self._build_init_panel(frame, pad)
        self._build_control_panel(frame, pad)
        self._build_test_panel(frame, pad)
        self._build_log(frame, pad)

    def _build_init_panel(self, parent, pad):
        panel = ttk.LabelFrame(parent, text="Initialisation")
        panel.grid(row=0, column=0, sticky="ew", **pad)

        paths = [self.config_path, self.control_table_path, self.limits_path]
        labels = ["config", "control table", "limits file"]

        # Three absolute paths are wider than the window. When they share a
        # directory - which they do in this repository - name it once and list
        # the files under it; otherwise show each in full and let it wrap.
        parents = {path.parent for path in paths}
        shared = parents.pop() if len(parents) == 1 else None
        if shared is not None:
            ttk.Label(panel, text="directory:").grid(
                row=0, column=0, sticky="w", padx=8, pady=1
            )
            ttk.Label(panel, text=str(shared)).grid(
                row=0, column=1, sticky="w", padx=8, pady=1
            )

        row = 1 if shared is not None else 0
        for label, path in zip(labels, paths):
            ttk.Label(panel, text=f"{label}:").grid(
                row=row, column=0, sticky="w", padx=8, pady=1
            )
            shown = path.name if shared is not None else str(path)
            found = "" if path.exists() else "   [MISSING]"
            ttk.Label(
                panel,
                text=f"{shown}{found}",
                wraplength=700,
                justify="left",
                foreground="black" if path.exists() else "red",
            ).grid(row=row, column=1, sticky="w", padx=8, pady=1)
            row += 1

        ttk.Label(panel, text="port:").grid(row=row, column=0, sticky="w", padx=8, pady=1)
        self.port_var = tk.StringVar(value=self._port_summary())
        ttk.Label(panel, textvariable=self.port_var).grid(
            row=row, column=1, sticky="w", padx=8, pady=1
        )
        row += 1

        ttk.Label(panel, text="travel:").grid(row=row, column=0, sticky="nw", padx=8, pady=1)
        self.limits_var = tk.StringVar(value="Not connected.")
        ttk.Label(panel, textvariable=self.limits_var, justify="left").grid(
            row=row, column=1, sticky="w", padx=8, pady=1
        )
        row += 1

        self.connect_button = ttk.Button(panel, text="Connect", command=self._toggle_connection)
        self.connect_button.grid(row=row, column=0, sticky="w", padx=8, pady=(6, 8))

        self.connection_var = tk.StringVar(value="Disconnected.")
        ttk.Label(panel, textvariable=self.connection_var).grid(
            row=row, column=1, sticky="w", padx=8, pady=(6, 8)
        )

    def _build_control_panel(self, parent, pad):
        panel = ttk.LabelFrame(parent, text="Control")
        panel.grid(row=1, column=0, sticky="ew", **pad)

        self.status_canvas = tk.Canvas(panel, width=28, height=28, highlightthickness=0)
        self.status_dot = self.status_canvas.create_oval(
            3, 3, 25, 25, fill=OFF_COLOUR, outline="#808080"
        )
        self.status_canvas.grid(row=0, column=0, padx=(8, 4), pady=6)

        self.status_var = tk.StringVar(value="off")
        ttk.Label(panel, textvariable=self.status_var, width=10).grid(
            row=0, column=1, sticky="w", pady=6
        )

        self.readout_var = tk.StringVar(value="position --  |  current --")
        ttk.Label(panel, textvariable=self.readout_var).grid(
            row=0, column=2, sticky="w", padx=8, pady=6
        )

        buttons = ttk.Frame(panel)
        buttons.grid(row=1, column=0, columnspan=3, sticky="w", padx=8, pady=(0, 6))

        self.enable_button = ttk.Button(buttons, text="Enable torque", command=self._toggle_torque)
        self.enable_button.grid(row=0, column=0, padx=(0, 8))

        self.grip_button = ttk.Button(
            buttons, text=f"Test grip (close, hold {GRIP_HOLD_S:.0f}s, open)",
            command=self._start_grip_test,
        )
        self.grip_button.grid(row=0, column=1)

        self.position_var = tk.DoubleVar(value=1.0)
        self.position_label = tk.StringVar(value="Position 1.00")
        self.position_scale = self._slider(
            panel, row=2, variable=self.position_var,
            label=self.position_label, command=self._on_position,
        )

        self.strength_var = tk.DoubleVar(value=0.5)
        self.strength_label = tk.StringVar(value="Grip strength 0.50")
        self.strength_scale = self._slider(
            panel, row=3, variable=self.strength_var,
            label=self.strength_label, command=self._on_strength,
        )

    def _slider(self, panel, row, variable, label, command):
        ttk.Label(panel, textvariable=label, width=30).grid(
            row=row, column=0, columnspan=2, sticky="w", padx=8, pady=2
        )
        scale = ttk.Scale(
            panel, from_=0.0, to=1.0, orient="horizontal", length=360,
            variable=variable, command=command,
        )
        scale.grid(row=row, column=2, sticky="ew", padx=8, pady=2)
        return scale

    def _build_test_panel(self, parent, pad):
        panel = ttk.LabelFrame(parent, text="Status tests")
        panel.grid(row=2, column=0, sticky="ew", **pad)

        # Each row is its own frame: the long output path below would otherwise
        # stretch the shared grid columns and push the buttons off the window.
        setup = ttk.Frame(panel)
        setup.grid(row=0, column=0, sticky="w", padx=8, pady=(6, 2))

        ttk.Label(setup, text="Trials per run:").grid(row=0, column=0, sticky="w")
        self.trials_var = tk.StringVar(value="10")
        ttk.Spinbox(setup, from_=1, to=500, width=6, textvariable=self.trials_var).grid(
            row=0, column=1, sticky="w", padx=(6, 20)
        )

        self.pause_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            setup, text="Pause for setup before each trial", variable=self.pause_var
        ).grid(row=0, column=2, sticky="w")

        buttons = ttk.Frame(panel)
        buttons.grid(row=1, column=0, sticky="w", padx=8, pady=2)

        self.test_buttons = {}
        for column, expected in enumerate(("ok", "miss", "slip")):
            button = ttk.Button(
                buttons, text=f"Run {expected} test",
                command=lambda e=expected: self._start_status_test(e),
            )
            button.grid(row=0, column=column, sticky="w", padx=(0, 6))
            self.test_buttons[expected] = button

        self.ready_button = ttk.Button(buttons, text="Ready", command=self._ready)
        self.ready_button.grid(row=0, column=3, sticky="w", padx=(20, 6))

        self.abort_button = ttk.Button(buttons, text="Abort", command=self._abort)
        self.abort_button.grid(row=0, column=4, sticky="w")

        self.progress_var = tk.StringVar(value="")
        ttk.Label(panel, textvariable=self.progress_var, wraplength=720,
                  justify="left").grid(row=2, column=0, sticky="w", padx=8, pady=2)

        self.output_var = tk.StringVar(value=f"Output: {self.output_dir}")
        ttk.Label(panel, textvariable=self.output_var, wraplength=720,
                  justify="left", foreground="#505050").grid(
            row=3, column=0, sticky="w", padx=8, pady=(2, 8)
        )

    def _build_log(self, parent, pad):
        panel = ttk.LabelFrame(parent, text="Log")
        panel.grid(row=3, column=0, sticky="nsew", **pad)

        self.log_text = tk.Text(panel, height=12, width=96, wrap="word", state="disabled")
        self.log_text.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=8)

        scrollbar = ttk.Scrollbar(panel, orient="vertical", command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns", padx=(0, 8), pady=8)
        self.log_text.config(yscrollcommand=scrollbar.set)

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------
    def _port_summary(self):
        """The port the config names, readable before anything is opened."""
        try:
            port = (load_yaml(self.config_path) or {}).get("port") or {}
        except Exception as exc:
            return f"unreadable ({exc})"
        return (
            f"{port.get('device', '?')} @ {port.get('baudrate', '?')} baud, "
            f"protocol {port.get('protocol_version', '?')}, id {port.get('dxl_id', '?')}"
        )

    def _toggle_connection(self):
        if self.api is None:
            self._connect()
        else:
            self._disconnect()
        self._refresh_controls()

    def _connect(self):
        try:
            self.api = GripperAPI.from_config(
                self.config_path,
                self.control_table_path,
                self.limits_path,
                on_status=self._log_threadsafe,
                on_status_change=lambda old, new: self._log_threadsafe(f"status {old} -> {new}"),
                abort_event=self.abort_event,
            )
        except Exception as exc:
            messagebox.showerror("Connection error", str(exc))
            self.api = None
            return

        self.connection_var.set("Connected.")
        self._show_limits()
        self.strength_var.set(self.api.grip_strength)
        self.strength_label.set(f"Grip strength {self.api.grip_strength:.2f}")
        self._log(f"Connected on {self._port_summary()}")

    def _disconnect(self):
        try:
            self.api.disconnect()
        except Exception as exc:
            self._log(f"Disconnect error: {exc}")
        self.api = None
        self.connection_var.set("Disconnected.")
        self.limits_var.set("Not connected.")
        self._paint_status(None)
        self._log("Disconnected.")

    def _show_limits(self):
        """What the API resolved the travel to, and when it was measured."""
        if not self.api.calibrated:
            self.limits_var.set(
                f"NOT CALIBRATED - nominal fallback from {self.config_path.name}: "
                f"{self.api.min_open_position} .. {self.api.max_open_position} ticks"
            )
            return

        calibrated_at = "unknown"
        if self.limits_path.exists():
            calibrated_at = str(
                (load_yaml(self.limits_path) or {}).get("calibrated_at", "unknown")
            ).replace("T", " ")
        travel = self.api.max_open_position - self.api.min_open_position
        self.limits_var.set(
            f"calibrated {calibrated_at}\n"
            f"max open {self.api.max_open_position} ticks, "
            f"min open {self.api.min_open_position} ticks "
            f"({travel} ticks of travel)"
        )

    # ------------------------------------------------------------------
    # Torque and sliders
    # ------------------------------------------------------------------
    def _toggle_torque(self):
        try:
            if self.api.enabled:
                self.api.enable(False)
            else:
                self.api.enable(True)
                state = self.api.state()
                if state.position is not None:
                    self.position_var.set(state.position)
                    self.position_label.set(f"Position {state.position:.2f}")
        except Exception as exc:
            messagebox.showerror("Torque error", str(exc))
        self._refresh_controls()

    def _on_position(self, raw):
        fraction = float(raw)
        self.position_label.set(f"Position {fraction:.2f}")
        if not self._commandable():
            return
        now = time.monotonic()
        if now - self._last_position_send < SLIDER_INTERVAL:
            return
        self._last_position_send = now
        try:
            # wait=False: the monitor thread settles the move and classifies it,
            # so dragging the slider never blocks the UI.
            self.api.set_position(fraction, wait=False)
        except Exception as exc:
            self._log(f"set_position failed: {exc}")

    def _on_strength(self, raw):
        fraction = float(raw)
        self.strength_label.set(f"Grip strength {fraction:.2f}")
        if self.api is None or self.busy:
            return
        now = time.monotonic()
        if now - self._last_strength_send < SLIDER_INTERVAL:
            return
        self._last_strength_send = now
        try:
            raw_current = self.api.set_grip_strength(fraction)
            self.strength_label.set(f"Grip strength {fraction:.2f} ({raw_current} raw)")
        except Exception as exc:
            self._log(f"set_grip_strength failed: {exc}")

    def _commandable(self):
        return self.api is not None and self.api.enabled and not self.busy

    # ------------------------------------------------------------------
    # Live readout
    # ------------------------------------------------------------------
    def _tick(self):
        if self.api is not None:
            try:
                state = self.api.state()
            except Exception as exc:
                self.readout_var.set(f"read failed: {exc}")
            else:
                self._paint_status(state.status)
                position = "--" if state.position is None else f"{state.position:.2f}"
                current = (
                    f"{state.current_raw} raw"
                    if state.current_ma is None
                    else f"{state.current_raw} raw ({state.current_ma:.0f} mA)"
                )
                frozen = "" if state.enabled else "   [torque off - readout frozen]"
                self.readout_var.set(
                    f"position {state.position_ticks} ticks ({position})  |  "
                    f"current {current}{frozen}"
                )
        self._tick_job = self.root.after(TICK_MS, self._tick)

    def _paint_status(self, status):
        colour = OFF_COLOUR if status is None else STATUS_COLOURS.get(status, OFF_COLOUR)
        self.status_canvas.itemconfig(self.status_dot, fill=colour)
        self.status_var.set("off" if status is None else str(status))

    # ------------------------------------------------------------------
    # Worker plumbing
    # ------------------------------------------------------------------
    def _start_worker(self, target):
        self.abort_event.clear()
        self.ready_event.clear()
        self.busy = True
        self._refresh_controls()

        def run():
            try:
                target()
            except AbortedError:
                self._log_threadsafe("Aborted.")
            except Exception as exc:
                self._log_threadsafe(f"ERROR: {exc}")
            finally:
                self.root.after(0, self._worker_done)

        self.worker = threading.Thread(target=run, name="demo-worker", daemon=True)
        self.worker.start()

    def _worker_done(self):
        self.busy = False
        self.waiting_ready = False
        self.progress_var.set("")
        self._refresh_controls()

    def _abort(self):
        self.abort_event.set()
        self.ready_event.set()   # release a trial waiting on the operator
        self._log("Abort requested.")

    def _ready(self):
        self.ready_event.set()

    def _await_ready(self, text):
        """Block the worker until the operator says the fixture is set."""
        self.ready_event.clear()
        self.root.after(0, self._set_waiting, True, text)
        while not self.ready_event.wait(0.1):
            if self.abort_event.is_set():
                break
        self.root.after(0, self._set_waiting, False, "")
        return not self.abort_event.is_set()

    def _set_waiting(self, waiting, text):
        self.waiting_ready = waiting
        if text:
            self.progress_var.set(text)
        self._refresh_controls()

    def _refresh_controls(self):
        connected = self.api is not None
        enabled = connected and self.api.enabled
        idle = connected and not self.busy

        self.connect_button.config(
            text="Disconnect" if connected else "Connect",
            state="normal" if not self.busy else "disabled",
        )
        self.enable_button.config(
            text="Disable torque" if enabled else "Enable torque",
            state="normal" if idle else "disabled",
        )
        self.grip_button.config(state="normal" if idle and enabled else "disabled")
        self.position_scale.config(state="normal" if idle and enabled else "disabled")
        self.strength_scale.config(state="normal" if idle else "disabled")

        for button in self.test_buttons.values():
            button.config(state="normal" if idle and enabled else "disabled")
        self.abort_button.config(state="normal" if self.busy else "disabled")
        self.ready_button.config(state="normal" if self.waiting_ready else "disabled")

    # ------------------------------------------------------------------
    # The one-shot grip test
    # ------------------------------------------------------------------
    def _start_grip_test(self):
        self._start_worker(self._grip_test_worker)

    def _grip_test_worker(self):
        self._log_threadsafe("Closing...")
        verdict = self.api.close()
        self._log_threadsafe(f"close() -> {verdict}")
        time.sleep(GRIP_HOLD_S)
        self._log_threadsafe("Reopening...")
        self._log_threadsafe(f"open() -> {self.api.open()}")

    # ------------------------------------------------------------------
    # Status test runs
    # ------------------------------------------------------------------
    def _start_status_test(self, expected):
        try:
            count = int(self.trials_var.get())
        except ValueError:
            messagebox.showerror("Trials", "Number of trials must be a whole number.")
            return
        if count < 1:
            messagebox.showerror("Trials", "Run at least one trial.")
            return
        self._start_worker(lambda: self._status_test_worker(expected, count))

    def _status_test_worker(self, expected, count):
        self._log_threadsafe(f"--- {expected} test, {count} trials ---")
        completed = 0

        for number in range(1, count + 1):
            if self.abort_event.is_set():
                break
            label = f"{expected} trial {number} of {count}"
            self.root.after(0, self.progress_var.set, label)

            if self.pause_var.get():
                if not self._await_ready(f"{label}: {SETUP_PROMPTS[expected]} Press Ready."):
                    break

            observed = self._one_trial(expected, label)
            self.trials.append(Trial(expected, observed))
            completed += 1
            self._log_threadsafe(f"  {label}: expected {expected}, got {observed}")

        if completed:
            self.root.after(0, self._write_outputs)
        else:
            self._log_threadsafe("No trials completed, nothing written.")

    def _one_trial(self, expected, label):
        """One staged grip. Returns whatever the API ended up reporting."""
        self.api.open()
        verdict = self.api.close()

        if expected == "slip" and verdict == "ok":
            self.root.after(
                0, self.progress_var.set, f"{label}: holding - now pull the object free"
            )
            verdict = self.api.wait_for_slip()

        self.api.open()
        return str(verdict)

    def _write_outputs(self):
        """CSV of every trial this session, and the matrix redrawn from all of it."""
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            write_csv(self.csv_path, self.trials)
            write_accuracy_matrix(
                self.matrix_path,
                self.trials,
                title=f"GripperAPI status accuracy - {len(self.trials)} trials",
            )
        except Exception as exc:
            self._log(f"Could not write results: {exc}")
            return
        self.output_var.set(f"Output: {self.csv_path}  |  {self.matrix_path}")
        self._log(f"Wrote {self.csv_path.name} and {self.matrix_path.name}")

    # ------------------------------------------------------------------
    # Log
    # ------------------------------------------------------------------
    def _log(self, text):
        self.log_text.config(state="normal")
        self.log_text.insert("end", f"{text}\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def _log_threadsafe(self, text):
        """Callable from the worker and from the API's monitor thread."""
        self.root.after(0, self._log, text)

    # ------------------------------------------------------------------
    # Teardown
    # ------------------------------------------------------------------
    def _on_close(self):
        self.abort_event.set()
        self.ready_event.set()
        if self._tick_job is not None:
            # Otherwise the refresh already queued fires into a destroyed
            # interpreter and Tk complains on the way out.
            self.root.after_cancel(self._tick_job)
            self._tick_job = None
        if self.worker is not None and self.worker.is_alive():
            self.worker.join(timeout=3.0)
        if self.api is not None:
            try:
                self.api.disconnect()
            except Exception:
                pass
            self.api = None
        self.root.destroy()
