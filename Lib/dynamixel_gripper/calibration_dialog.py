"""
The calibration wizard: an operator front end for GripperAPI.calibrate().

Run whenever fingers are loaded or unloaded. Step 1 lives on the Tk main loop -
torque is off, so all it does is poll the position the operator is moving by
hand. Step 2 hands that position to calibrate(), which owns the closing probe,
on a worker thread because it blocks for seconds at a time.

Nothing is written to disk until the operator accepts the result: the probe runs
with save=False, so a probe that finds the wrong stop can be discarded and
repeated. The measured limits are live on the API either way - calibrate()
applies them as soon as it has them, which is what a caller wants even if the
operator decides not to keep them for next session.

Tkinter is imported here rather than in api.py, so the package still imports on
a machine without it.
"""

import threading
import tkinter as tk
from tkinter import messagebox, ttk

from .config import save_limits

__all__ = ["CalibrationDialog"]

# How often step 1 refreshes the by-hand position readout. A UI refresh rate,
# not an experiment parameter, so it stays a local constant.
MANUAL_POLL_MS = 200


class CalibrationDialog:
    """Modal two-step wizard. Calls on_saved(limits_dict) if limits are kept."""

    def __init__(self, master, api, on_saved=None):
        self.master = master
        self.api = api
        self.on_saved = on_saved or (lambda limits: None)

        self.stage = "manual"
        self.result = None
        self._poll_job = None

        self._build()
        self._begin_manual_step()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build(self):
        self.window = tk.Toplevel(self.master)
        self.window.title("Calibrate travel limits")
        self.window.transient(self.master)
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
                f"The gripper closes at {self._probe_current_text()} until the\n"
                "current draw shows it has met the stop, then backs off "
                f"{self.api.calibration_config['backoff_ticks']} ticks so\n"
                "the finger joints' safety snap is not held under tension.\n"
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

    def _probe_current_text(self):
        raw = self.api.calibration_config["probe_current"]
        if self.api.current_unit_ma is None:
            return f"{raw} raw"
        return f"{raw} raw ({raw * self.api.current_unit_ma:.0f} mA)"

    # ------------------------------------------------------------------
    # Step 1: by hand
    # ------------------------------------------------------------------
    def _begin_manual_step(self):
        self.stage = "manual"
        self.api.abort_event.clear()
        try:
            # Drops torque and stops the monitor: the operator is about to move
            # the fingers, and nothing else should be driving or polling them.
            self.api.enable(False)
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
            position = self.api.gripper.read_present_position()
            deg = position * self.api.position_unit_deg
            self.manual_var.set(f"Present position: {position} ticks ({deg:.1f} deg)")
        except Exception as exc:
            self.manual_var.set(f"Present position: read failed ({exc})")
        self._poll_job = self.window.after(MANUAL_POLL_MS, self._poll_manual_position)

    def _capture_max_open(self):
        try:
            max_open = self.api.gripper.read_present_position()
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
        threading.Thread(
            target=self._probe_worker, args=(max_open,), daemon=True
        ).start()

    # ------------------------------------------------------------------
    # Step 2: worker thread
    # ------------------------------------------------------------------
    def _probe_worker(self, max_open):
        # calibrate() reports progress through the API's own callbacks, so the
        # wizard borrows them for the duration and hands them back after.
        borrowed = (self.api.on_status, self.api.on_readout)
        self.api.on_status, self.api.on_readout = self._set_status, self._set_readout
        try:
            # save=False: the limits go live on the API immediately either way,
            # but nothing reaches disk until the operator accepts them.
            result = self.api.calibrate(max_open=max_open, save=False)
        except Exception as exc:
            self._finish_probe(None, str(exc))
            return
        finally:
            self.api.on_status, self.api.on_readout = borrowed
        self._finish_probe(result, None)

    def _finish_probe(self, result, error):
        self.master.after(0, self._show_probe_outcome, result, error)

    def _show_probe_outcome(self, result, error):
        self.stage = "reviewing"
        self.retry_button.config(state="normal")

        if result is None:
            self.result_var.set(f"Calibration failed:\n{error}")
            self.status_var.set("Failed. Fix the cause and retry.")
            self.save_button.config(state="disabled")
            return

        self.result = result
        travel_deg = result.travel_ticks * self.api.position_unit_deg
        stop_current = f"{result.stop_current_raw} raw"
        if self.api.current_unit_ma is not None:
            stop_current += f", {result.stop_current_raw * self.api.current_unit_ma:.0f} mA"
        self.result_var.set(
            f"MAX OPEN      {result.max_open} ticks\n"
            f"Hard stop     {result.hard_close} ticks (at {stop_current})\n"
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
        if self.api.limits_path is None:
            messagebox.showerror(
                "Save error",
                "This gripper was built without a limits path, so there is "
                "nowhere to write to. The measured limits are live on the API "
                "regardless, but they will not survive the session.",
                parent=self.window,
            )
            return
        try:
            limits = save_limits(
                self.api.limits_path, self.result, self.api.position_unit_deg
            )
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
            self.api.abort_event.set()
            self.status_var.set("Abort requested - finishing current step...")
            return
        self._close()

    def _close(self):
        self._cancel_poll()
        self.stage = "closed"
        try:
            self.api.gripper.disable_torque()
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
    # Probe callbacks (worker thread)
    # ------------------------------------------------------------------
    def _set_status(self, text):
        self.master.after(0, self.status_var.set, text)

    def _set_readout(self, position, current):
        deg = position * self.api.position_unit_deg
        reading = f"Present position: {position} ({deg:.1f} deg)  |  Present current: {current}"
        if self.api.current_unit_ma is not None:
            reading += f" ({current * self.api.current_unit_ma:.0f} mA)"
        self.master.after(0, self.readout_var.set, reading)
