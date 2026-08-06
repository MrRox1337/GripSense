"""
The calibration wizard: a modal front end for GripperCalibrator.

Runs whenever fingers are loaded or unloaded. Step 1 lives on the Tk main
loop (torque is off, so all it does is poll the position the operator is
moving by hand); step 2 runs on a worker thread because the closing probe
blocks for seconds at a time.

Nothing is written to disk until the operator accepts the result, so a probe
that finds the wrong stop can simply be discarded and repeated.
"""

import threading
import tkinter as tk
from tkinter import ttk, messagebox

import gripper_settings as settings
from dynamixel_gripper import CalibrationAborted, GripperCalibrator

# How often step 1 refreshes the by-hand position readout.
MANUAL_POLL_MS = 200


class CalibrationDialog:
    """Modal two-step wizard. Calls on_saved(limits_dict) if limits are kept."""

    def __init__(self, root, gripper, on_saved=None):
        self.root = root
        self.gripper = gripper
        self.on_saved = on_saved or (lambda limits: None)

        self.abort_event = threading.Event()
        self.stage = "manual"
        self.result = None
        self._poll_job = None

        config = settings.CALIBRATION
        self.calibrator = GripperCalibrator(
            gripper,
            probe_current=config["probe_current"],
            probe_velocity=config["probe_velocity"],
            backoff_ticks=config["backoff_ticks"],
            return_velocity=config["return_velocity"],
            stall_current_fraction=config["stall_current_fraction"],
            stall_stable_samples=config["stall_stable_samples"],
            stall_position_tolerance=config["stall_position_tolerance"],
            max_probe_ticks=config["max_probe_ticks"],
            probe_timeout=config["probe_timeout"],
            poll_interval=config["poll_interval"],
            on_status=self._set_status,
            on_readout=self._set_readout,
            abort_event=self.abort_event,
        )

        self._build()
        self._begin_manual_step()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build(self):
        self.window = tk.Toplevel(self.root)
        self.window.title("Calibrate travel limits")
        self.window.transient(self.root)
        self.window.resizable(False, False)
        self.window.protocol("WM_DELETE_WINDOW", self._cancel)

        frame = ttk.Frame(self.window, padding=12)
        frame.grid(row=0, column=0, sticky="nsew")

        step1 = ttk.LabelFrame(frame, text="Step 1 - set MAX OPEN by hand")
        step1.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(
            step1,
            text=(
                "Torque is released. Open the fingers fully by hand, up to\n"
                "where you want the open limit to be, then confirm.\n"
                "Load or unload fingers now if you are swapping them."
            ),
            justify="left",
        ).grid(row=0, column=0, columnspan=2, padx=10, pady=(8, 6), sticky="w")

        self.manual_var = tk.StringVar(value="Present position: --")
        ttk.Label(step1, textvariable=self.manual_var).grid(
            row=1, column=0, padx=10, pady=(0, 6), sticky="w"
        )

        self.done_button = ttk.Button(
            step1, text="Done - this is MAX OPEN", command=self._capture_max_open
        )
        self.done_button.grid(row=2, column=0, padx=10, pady=(0, 10), sticky="w")

        step2 = ttk.LabelFrame(frame, text="Step 2 - find the closed limit automatically")
        step2.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(
            step2,
            text=(
                f"The gripper closes at {settings.CALIBRATION['probe_current']} raw "
                f"({settings.CALIBRATION['probe_current'] * settings.CURRENT_UNIT_MA:.0f} mA) "
                "until the current\n"
                "draw shows it has met the stop, then backs off "
                f"{settings.CALIBRATION['backoff_ticks']} ticks so the\n"
                "finger joints' safety snap is not held under tension.\n"
                "Keep hands clear once this starts."
            ),
            justify="left",
        ).grid(row=0, column=0, columnspan=2, padx=10, pady=(8, 6), sticky="w")

        self.status_var = tk.StringVar(value="Waiting for MAX OPEN.")
        ttk.Label(step2, textvariable=self.status_var, wraplength=430,
                  justify="left").grid(row=1, column=0, columnspan=2,
                                       padx=10, pady=(0, 4), sticky="w")

        self.readout_var = tk.StringVar(value="Present position: --  |  Present current: --")
        ttk.Label(step2, textvariable=self.readout_var).grid(
            row=2, column=0, columnspan=2, padx=10, pady=(0, 10), sticky="w"
        )

        result_frame = ttk.LabelFrame(frame, text="Result")
        result_frame.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        self.result_var = tk.StringVar(value="No limits established yet.")
        ttk.Label(result_frame, textvariable=self.result_var, justify="left",
                  wraplength=430).grid(row=0, column=0, padx=10, pady=8, sticky="w")

        buttons = ttk.Frame(frame)
        buttons.grid(row=3, column=0, sticky="ew")

        self.save_button = ttk.Button(
            buttons, text="Save limits", command=self._save, state="disabled"
        )
        self.save_button.grid(row=0, column=0, padx=(0, 8))

        self.retry_button = ttk.Button(
            buttons, text="Retry", command=self._retry, state="disabled"
        )
        self.retry_button.grid(row=0, column=1, padx=(0, 8))

        self.cancel_button = ttk.Button(buttons, text="Cancel", command=self._cancel)
        self.cancel_button.grid(row=0, column=2)

        self.window.grab_set()

    # ------------------------------------------------------------------
    # Step 1: by hand
    # ------------------------------------------------------------------
    def _begin_manual_step(self):
        self.stage = "manual"
        self.abort_event.clear()
        try:
            self.calibrator.release_for_manual_positioning()
        except Exception as exc:
            messagebox.showerror("Calibration error", str(exc), parent=self.window)
            self._close()
            return
        self.done_button.config(state="normal")
        self._poll_manual_position()

    def _poll_manual_position(self):
        if self.stage != "manual":
            return
        try:
            position = self.gripper.read_present_position()
            deg = position * settings.POSITION_UNIT_DEG
            self.manual_var.set(f"Present position: {position} ticks ({deg:.1f} deg)")
        except Exception as exc:
            self.manual_var.set(f"Present position: read failed ({exc})")
        self._poll_job = self.window.after(MANUAL_POLL_MS, self._poll_manual_position)

    def _capture_max_open(self):
        try:
            max_open = self.calibrator.capture_max_open()
        except Exception as exc:
            messagebox.showerror("Calibration error", str(exc), parent=self.window)
            return

        if not messagebox.askokcancel(
            "Start closing probe",
            f"MAX OPEN = {max_open} ticks.\n\n"
            "The gripper will now close under current limit until it meets "
            "the mechanical stop. Keep hands and the workpiece clear.",
            parent=self.window,
        ):
            return

        self.stage = "probing"
        self._cancel_poll()
        self.done_button.config(state="disabled")
        self.retry_button.config(state="disabled")
        self.save_button.config(state="disabled")
        threading.Thread(target=self._probe_worker, daemon=True).start()

    # ------------------------------------------------------------------
    # Step 2: worker thread
    # ------------------------------------------------------------------
    def _probe_worker(self):
        try:
            result = self.calibrator.probe_close_limit()
        except CalibrationAborted:
            self._finish_probe(None, "Calibration aborted. No limits were saved.")
            return
        except Exception as exc:
            self._finish_probe(None, str(exc))
            return
        finally:
            # The probe leaves the fingers back at MAX OPEN before returning,
            # so it is safe to release them here.
            try:
                self.gripper.disable_torque()
            except Exception:
                pass
        self._finish_probe(result, None)

    def _finish_probe(self, result, error):
        self.root.after(0, self._show_probe_outcome, result, error)

    def _show_probe_outcome(self, result, error):
        self.stage = "reviewing"
        self.retry_button.config(state="normal")

        if result is None:
            self.result_var.set(f"Calibration failed:\n{error}")
            self.status_var.set("Failed. Fix the cause and retry.")
            self.save_button.config(state="disabled")
            return

        self.result = result
        travel_deg = result.travel_ticks * settings.POSITION_UNIT_DEG
        self.result_var.set(
            f"MAX OPEN      {result.max_open} ticks\n"
            f"Hard stop     {result.hard_close} ticks "
            f"(at {result.stop_current_raw} raw, "
            f"{result.stop_current_raw * settings.CURRENT_UNIT_MA:.0f} mA)\n"
            f"MIN OPEN      {result.min_open} ticks "
            f"(backed off {result.backoff_ticks})\n"
            f"Travel        {result.travel_ticks} ticks ({travel_deg:.1f} deg)"
        )
        self.status_var.set("Closed limit found. Save these limits, or retry.")
        self.save_button.config(state="normal")

    # ------------------------------------------------------------------
    # Outcome
    # ------------------------------------------------------------------
    def _save(self):
        try:
            limits = settings.save_limits(self.result)
        except Exception as exc:
            messagebox.showerror("Save error", str(exc), parent=self.window)
            return
        self.on_saved(limits)
        self._close()

    def _retry(self):
        self.result = None
        self.save_button.config(state="disabled")
        self.result_var.set("No limits established yet.")
        self.status_var.set("Waiting for MAX OPEN.")
        self._begin_manual_step()

    def _cancel(self):
        if self.stage == "probing":
            if not messagebox.askokcancel(
                "Abort calibration",
                "A closing probe is running. Abort it?",
                parent=self.window,
            ):
                return
            self.abort_event.set()
            self.status_var.set("Abort requested - finishing current step...")
            return
        self._close()

    def _close(self):
        self._cancel_poll()
        self.stage = "closed"
        try:
            self.gripper.disable_torque()
        except Exception:
            pass
        try:
            self.window.grab_release()
            self.window.destroy()
        except tk.TclError:
            pass

    def _cancel_poll(self):
        if self._poll_job is not None:
            try:
                self.window.after_cancel(self._poll_job)
            except tk.TclError:
                pass
            self._poll_job = None

    # ------------------------------------------------------------------
    # Calibrator callbacks (worker thread)
    # ------------------------------------------------------------------
    def _set_status(self, text):
        self.root.after(0, self.status_var.set, text)

    def _set_readout(self, position, current):
        deg = position * settings.POSITION_UNIT_DEG
        ma = current * settings.CURRENT_UNIT_MA
        self.root.after(
            0,
            self.readout_var.set,
            f"Present position: {position} ({deg:.1f} deg)  |  "
            f"Present current: {current} ({ma:.0f} mA)",
        )
