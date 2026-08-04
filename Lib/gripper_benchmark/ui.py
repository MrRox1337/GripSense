"""
Tk front end for the benchmark: the configuration matrix, the run controls
and the scale-reading popup.

The measurement sequence itself lives in runner.py; this module only wires
that sequence to widgets and marshals worker-thread events back onto the Tk
main loop.
"""

import threading
import tkinter as tk
from tkinter import ttk, messagebox

import gripper_settings as settings

from .matrix import (
    FINGER_MATERIALS,
    PADDINGS,
    TEST_CURRENTS,
    REPEATS,
    READINGS_PER_COMBO,
    TOTAL_COMBOS,
    combo_is_complete,
    completed_combos,
    recorded_counts,
)
from .report import generate_report
from .runner import AbortedError, BenchmarkRunner
from .storage import append_row, read_rows


class BenchmarkApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Gripper Grip-Quality Benchmark (XM430-W210-T)")

        self.gripper = None
        self.runner = None

        self.selected = None                 # (finger, padding)
        self.cell_buttons = {}               # (finger, padding) -> ttk.Button
        self.rows = read_rows(settings.RESULTS_CSV)
        self.done = completed_combos(self.rows)

        self.run_thread = None
        # Tracked separately from run_thread.is_alive(): _finish_run schedules
        # the UI refresh from inside the worker, so the thread is still alive
        # when that refresh would run.
        self.running = False

        # Worker <-> UI handshake for the weight popup.
        self._prompt_event = threading.Event()
        self._prompt_result = {}
        self._weight_dialog = None

        self._build_ui()
        self._connect_hardware()
        self._refresh_matrix()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self):
        style = ttk.Style()
        style.configure("Cell.TButton", width=14)
        style.configure("Selected.TButton", width=14, font=("TkDefaultFont", 9, "bold"))

        frame = ttk.Frame(self.root, padding=10)
        frame.grid(row=0, column=0, sticky="nsew")

        # --- Configuration matrix ---
        config_frame = ttk.LabelFrame(
            frame, text="1. Configuration - select finger material + padding"
        )
        config_frame.grid(row=0, column=0, sticky="ew", pady=(0, 10))

        ttk.Label(config_frame, text="").grid(row=0, column=0, padx=6, pady=4)
        for col, padding_name in enumerate(PADDINGS, start=1):
            ttk.Label(config_frame, text=padding_name, anchor="center").grid(
                row=0, column=col, padx=4, pady=4, sticky="ew"
            )

        for row_index, finger in enumerate(FINGER_MATERIALS, start=1):
            ttk.Label(config_frame, text=finger, width=6, anchor="w").grid(
                row=row_index, column=0, padx=6, pady=3, sticky="w"
            )
            for col, padding_name in enumerate(PADDINGS, start=1):
                button = ttk.Button(
                    config_frame,
                    text="select",
                    style="Cell.TButton",
                    command=lambda f=finger, p=padding_name: self._select_combo(f, p),
                )
                button.grid(row=row_index, column=col, padx=4, pady=3, sticky="ew")
                self.cell_buttons[(finger, padding_name)] = button

        self.selection_var = tk.StringVar(value="Selected: (none)")
        ttk.Label(config_frame, textvariable=self.selection_var).grid(
            row=len(FINGER_MATERIALS) + 1, column=0, columnspan=3,
            padx=6, pady=(8, 6), sticky="w",
        )

        self.progress_var = tk.StringVar()
        ttk.Label(config_frame, textvariable=self.progress_var).grid(
            row=len(FINGER_MATERIALS) + 1, column=3, columnspan=2,
            padx=6, pady=(8, 6), sticky="e",
        )

        # --- Run controls ---
        run_frame = ttk.LabelFrame(frame, text="2. Run")
        run_frame.grid(row=1, column=0, sticky="ew", pady=(0, 10))

        self.start_button = ttk.Button(
            run_frame,
            text=f"Start Run (all {len(TEST_CURRENTS)} currents x {REPEATS} repeats)",
            command=self._start_run, state="disabled",
        )
        self.start_button.grid(row=0, column=0, padx=8, pady=8, sticky="w")

        self.abort_button = ttk.Button(
            run_frame, text="Abort Run", command=self._abort_run, state="disabled"
        )
        self.abort_button.grid(row=0, column=1, padx=8, pady=8, sticky="w")

        self.status_var = tk.StringVar(value="Idle.")
        ttk.Label(run_frame, textvariable=self.status_var, wraplength=520,
                  justify="left").grid(row=1, column=0, columnspan=3,
                                       padx=8, pady=(0, 4), sticky="w")

        self.readout_var = tk.StringVar(value="Present position: --  |  Present current: --")
        ttk.Label(run_frame, textvariable=self.readout_var).grid(
            row=2, column=0, columnspan=3, padx=8, pady=(0, 8), sticky="w"
        )

        # --- Hardware / safety ---
        hw_frame = ttk.LabelFrame(frame, text="Hardware")
        hw_frame.grid(row=2, column=0, sticky="ew", pady=(0, 10))

        self.limits_var = tk.StringVar(value="Limits: not initialised")
        ttk.Label(hw_frame, textvariable=self.limits_var).grid(
            row=0, column=0, columnspan=2, padx=8, pady=(6, 4), sticky="w"
        )

        self.rezero_button = ttk.Button(
            hw_frame, text="Re-zero (treat present position as MAX OPEN)",
            command=self._rezero,
        )
        self.rezero_button.grid(row=1, column=0, padx=8, pady=(0, 8), sticky="w")

        ttk.Button(
            hw_frame, text="EMERGENCY STOP (disable torque)", command=self._emergency_stop
        ).grid(row=1, column=1, padx=8, pady=(0, 8), sticky="e")

        # --- Report ---
        report_frame = ttk.LabelFrame(frame, text="3. Report")
        report_frame.grid(row=3, column=0, sticky="ew")

        self.report_button = ttk.Button(
            report_frame, text="Generate Report (graphs + statistics table)",
            command=self._generate_report_clicked,
        )
        self.report_button.grid(row=0, column=0, padx=8, pady=8, sticky="w")

        self.report_status_var = tk.StringVar(value="")
        ttk.Label(report_frame, textvariable=self.report_status_var, wraplength=520,
                  justify="left").grid(row=1, column=0, padx=8, pady=(0, 8), sticky="w")

    # ------------------------------------------------------------------
    # Hardware
    # ------------------------------------------------------------------
    def _connect_hardware(self):
        try:
            self.gripper = settings.connect()
            self.runner = BenchmarkRunner(
                self.gripper,
                travel_ticks=settings.TRAVEL_TICKS,
                profile_velocity=settings.BENCHMARK_PROFILE_VELOCITY,
                on_status=self._set_status,
                on_readout=self._set_readout,
                on_reading=self._record_reading,
                ask_weight=self._prompt_weight,
            )
            self._rezero(announce=False)
            self.runner.prepare()
        except Exception as exc:
            messagebox.showerror("Connection error", str(exc))
            self.root.destroy()
            raise

    def _rezero(self, announce=True):
        if self.running:
            messagebox.showwarning("Re-zero", "Cannot re-zero while a run is in progress.")
            return
        try:
            max_open, min_open = self.runner.rezero()
        except Exception as exc:
            messagebox.showerror("Re-zero error", str(exc))
            return

        self.limits_var.set(
            f"Limits: MAX OPEN = {max_open} ticks  ->  MIN OPEN = {min_open} ticks  "
            f"({settings.TRAVEL_DEG:.0f} deg travel)"
        )
        if announce:
            self._set_status("Re-zeroed. Present position is now treated as MAX OPEN.")

    def _emergency_stop(self):
        self.runner.abort_event.set()
        try:
            self.gripper.disable_torque()
            self._set_status("EMERGENCY STOP: torque disabled.")
        except Exception as exc:
            messagebox.showerror("Emergency stop error", str(exc))

    # ------------------------------------------------------------------
    # Selection / matrix state
    # ------------------------------------------------------------------
    def _select_combo(self, finger, padding):
        if (finger, padding) in self.done:
            return
        self.selected = (finger, padding)
        self.selection_var.set(f"Selected: {finger}  /  {padding}")
        self._refresh_matrix()

    def _refresh_matrix(self):
        running = self.running
        for combo, button in self.cell_buttons.items():
            if combo in self.done:
                button.config(text="done", style="Cell.TButton", state="disabled")
            elif running:
                button.config(state="disabled")
            elif combo == self.selected:
                button.config(text="SELECTED", style="Selected.TButton", state="normal")
            else:
                button.config(text="select", style="Cell.TButton", state="normal")

        self.progress_var.set(
            f"Progress: {len(self.done)} / {TOTAL_COMBOS} combinations complete"
        )

        can_start = self.selected is not None and not running
        self.start_button.config(state="normal" if can_start else "disabled")
        self.abort_button.config(state="normal" if running else "disabled")
        self.rezero_button.config(state="disabled" if running else "normal")
        self.report_button.config(state="disabled" if running else "normal")

    # ------------------------------------------------------------------
    # Run control
    # ------------------------------------------------------------------
    def _start_run(self):
        if self.selected is None:
            return
        finger, padding = self.selected

        counts = recorded_counts(self.rows, finger, padding)
        already = sum(min(count, REPEATS) for count in counts.values())
        if already:
            resume = messagebox.askyesno(
                "Resume run",
                f"{finger} / {padding} already has {already} of "
                f"{READINGS_PER_COMBO} readings.\n\n"
                "Continue from where it stopped? (No = cancel)",
            )
            if not resume:
                return

        confirm = messagebox.askokcancel(
            "Start run",
            f"Finger: {finger}\nPadding: {padding}\n\n"
            "Torque will be ENABLED and the gripper will close onto the scale.\n"
            "Make sure the scale is in position and clear of obstructions.",
        )
        if not confirm:
            return

        self.runner.abort_event.clear()
        self.running = True
        self.run_thread = threading.Thread(
            target=self._run_worker, args=(finger, padding, counts), daemon=True
        )
        self.run_thread.start()
        self._refresh_matrix()

    def _abort_run(self):
        self.runner.abort_event.set()
        self._set_status("Abort requested - finishing current step...")

    def _run_worker(self, finger, padding, counts):
        try:
            self.runner.run_combo(finger, padding, counts)
            self._finish_run(finger, padding, aborted=False)
        except AbortedError:
            self._finish_run(finger, padding, aborted=True)
        except Exception as exc:
            self._set_status(f"Run failed: {exc}")
            self.root.after(0, messagebox.showerror, "Run error", str(exc))
            self._finish_run(finger, padding, aborted=True)

    def _finish_run(self, finger, padding, aborted):
        self.running = False
        try:
            self.gripper.disable_torque()
        except Exception:
            pass

        complete = combo_is_complete(self.rows, finger, padding)
        if complete:
            self.done.add((finger, padding))
            self.selected = None
            self.root.after(0, self.selection_var.set, "Selected: (none)")

        if aborted:
            self._set_status("Run aborted. Torque disabled. Partial readings were saved.")
        elif complete:
            self._set_status(f"{finger} / {padding} complete. Torque disabled.")
        else:
            self._set_status("Run ended early. Torque disabled. Partial readings were saved.")

        self.root.after(0, self._refresh_matrix)

        if not aborted and len(self.done) == TOTAL_COMBOS:
            self.root.after(0, self._auto_generate_report)

    # ------------------------------------------------------------------
    # Runner callbacks (worker thread)
    # ------------------------------------------------------------------
    def _record_reading(self, row):
        append_row(row, settings.RESULTS_CSV)
        self.rows.append({key: str(value) for key, value in row.items()})

    def _prompt_weight(self, finger, padding, goal_current, repeat):
        """Ask the UI thread for a scale reading and block until it arrives."""
        self._prompt_event.clear()
        self._prompt_result.clear()
        self.root.after(0, self._show_weight_dialog, finger, padding, goal_current, repeat)

        while not self._prompt_event.wait(timeout=0.2):
            if self.runner.abort_event.is_set():
                # Abort raised elsewhere (e.g. emergency stop) while waiting.
                self.root.after(0, self._close_weight_dialog)
                raise AbortedError()

        if self._prompt_result.get("aborted"):
            raise AbortedError()
        return self._prompt_result["weight"]

    # ------------------------------------------------------------------
    # Weight dialog (UI thread only)
    # ------------------------------------------------------------------
    def _show_weight_dialog(self, finger, padding, goal_current, repeat):
        dialog = tk.Toplevel(self.root)
        self._weight_dialog = dialog
        dialog.title("Scale reading")
        dialog.transient(self.root)
        dialog.resizable(False, False)

        ttk.Label(
            dialog,
            text=(
                f"{finger}  /  {padding}\n"
                f"Goal current: {goal_current} raw "
                f"({goal_current * settings.CURRENT_UNIT_MA:.0f} mA)\n"
                f"Repeat {repeat} of {REPEATS}"
            ),
            justify="left",
        ).grid(row=0, column=0, columnspan=2, padx=14, pady=(14, 8), sticky="w")

        ttk.Label(dialog, text="Weight observed on scale (g):").grid(
            row=1, column=0, padx=14, pady=4, sticky="w"
        )

        entry_var = tk.StringVar()
        entry = ttk.Entry(dialog, textvariable=entry_var, width=12)
        entry.grid(row=1, column=1, padx=14, pady=4, sticky="w")
        entry.focus_set()

        error_var = tk.StringVar()
        ttk.Label(dialog, textvariable=error_var, foreground="red").grid(
            row=2, column=0, columnspan=2, padx=14, sticky="w"
        )

        def submit(_event=None):
            try:
                weight = float(entry_var.get().strip())
            except ValueError:
                error_var.set("Enter a number, e.g. 412.5")
                return
            self._prompt_result["weight"] = weight
            self._prompt_result["aborted"] = False
            self._close_weight_dialog()
            self._prompt_event.set()

        def abort():
            self._prompt_result["aborted"] = True
            self.runner.abort_event.set()
            self._close_weight_dialog()
            self._prompt_event.set()

        button_frame = ttk.Frame(dialog)
        button_frame.grid(row=3, column=0, columnspan=2, padx=14, pady=(8, 14), sticky="ew")
        ttk.Button(button_frame, text="Submit", command=submit).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(button_frame, text="Abort run", command=abort).grid(row=0, column=1)

        entry.bind("<Return>", submit)
        dialog.protocol("WM_DELETE_WINDOW", abort)
        dialog.grab_set()

    def _close_weight_dialog(self):
        dialog = self._weight_dialog
        if dialog is not None:
            try:
                dialog.grab_release()
                dialog.destroy()
            except tk.TclError:
                pass
            self._weight_dialog = None

    # ------------------------------------------------------------------
    # Thread-safe UI updates
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

    # ------------------------------------------------------------------
    # Report
    # ------------------------------------------------------------------
    def _generate_report_clicked(self):
        if len(self.done) < TOTAL_COMBOS:
            proceed = messagebox.askyesno(
                "Incomplete data",
                f"Only {len(self.done)} of {TOTAL_COMBOS} combinations are complete.\n\n"
                "Generate a report from the partial data anyway?",
            )
            if not proceed:
                return
        self._auto_generate_report()

    def _auto_generate_report(self):
        try:
            outputs = generate_report(settings.RESULTS_CSV, settings.REPORT_DIR)
        except Exception as exc:
            messagebox.showerror("Report error", str(exc))
            self.report_status_var.set(f"Report failed: {exc}")
            return
        self.report_status_var.set(
            "Report written to benchmark_report/:\n  "
            + "\n  ".join(path.name for path in outputs)
        )
        messagebox.showinfo(
            "Report generated",
            f"{len(outputs)} files written to:\n{settings.REPORT_DIR}",
        )

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------
    def _on_close(self):
        if self.run_thread is not None and self.run_thread.is_alive():
            if not messagebox.askokcancel("Quit", "A run is in progress. Abort it and quit?"):
                return
            self.runner.abort_event.set()
            self._prompt_event.set()
            self.run_thread.join(timeout=3.0)

        if self.gripper is not None:
            try:
                self.gripper.close()
            except Exception:
                pass
        self.root.destroy()
