"""
Tk front end for the benchmark: calibration, the configuration matrix, the run
controls and the report button.

Two tests share this window and the same 4x4 matrix - grip force and slip
detection - tracked independently, each with its own CSV and its own set of
completed combinations.

The measurement sequences live in runner.py and slip.py, the modal prompts in
dialogs.py and the calibration wizard in calibration_ui.py; this module only
wires them to widgets and marshals worker-thread events back onto the Tk main
loop.
"""

import threading
import tkinter as tk
from tkinter import ttk, messagebox

import gripper_settings as settings

from .calibration_ui import CalibrationDialog
from .dialogs import WorkerPrompt, ready_dialog, weight_dialog
from .matrix import (
    CSV_FIELDS_BY_KIND,
    FINGER_MATERIALS,
    GRIP_FORCE,
    PADDINGS,
    REPEATS,
    READINGS_PER_COMBO,
    SLIP_DETECTION,
    TEST_CURRENTS,
    TEST_KINDS,
    TEST_KIND_LABELS,
    TOTAL_COMBOS,
    combo_is_complete,
    completed_combos,
    recorded_counts,
)
from .motion import AbortedError
from .report import generate_report
from .runner import BenchmarkRunner
from .slip import SlipRunner
from .storage import append_row, read_rows

CSV_BY_KIND = {
    GRIP_FORCE: settings.RESULTS_CSV,
    SLIP_DETECTION: settings.SLIP_CSV,
}


class BenchmarkApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Gripper Benchmark - grip force and slip detection (XM430-W210-T)")

        self.gripper = None

        # Travel limits come from the last calibration, never from a constant.
        self.max_open, self.min_open, self.calibrated = settings.travel_limits()

        self.selected = None                 # (finger, padding)
        self.cell_buttons = {}               # (finger, padding) -> ttk.Button
        self.rows = {kind: read_rows(CSV_BY_KIND[kind]) for kind in TEST_KINDS}
        self.done = {kind: completed_combos(self.rows[kind]) for kind in TEST_KINDS}

        self.run_thread = None
        # Tracked separately from run_thread.is_alive(): _finish_run schedules
        # the UI refresh from inside the worker, so the thread is still alive
        # when that refresh would run.
        self.running = False

        # One abort event for every runner, the prompts and the emergency stop,
        # so any of them can stop the others.
        self.abort_event = threading.Event()
        self.prompt = WorkerPrompt(self.root, self.abort_event)

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

        self._build_calibration_frame(frame, row=0)
        self._build_test_frame(frame, row=1)
        self._build_config_frame(frame, row=2)
        self._build_run_frame(frame, row=3)
        self._build_report_frame(frame, row=4)

    def _build_calibration_frame(self, parent, row):
        calib_frame = ttk.LabelFrame(parent, text="1. Calibration - run whenever fingers change")
        calib_frame.grid(row=row, column=0, sticky="ew", pady=(0, 10))

        self.limits_var = tk.StringVar()
        ttk.Label(calib_frame, textvariable=self.limits_var, wraplength=560,
                  justify="left").grid(row=0, column=0, columnspan=2,
                                       padx=8, pady=(6, 4), sticky="w")

        self.calibrate_button = ttk.Button(
            calib_frame, text="Calibrate travel limits...", command=self._calibrate
        )
        self.calibrate_button.grid(row=1, column=0, padx=8, pady=(0, 8), sticky="w")

        ttk.Button(
            calib_frame, text="EMERGENCY STOP (disable torque)", command=self._emergency_stop
        ).grid(row=1, column=1, padx=8, pady=(0, 8), sticky="e")

        self._refresh_limits_label()

    def _build_test_frame(self, parent, row):
        test_frame = ttk.LabelFrame(parent, text="2. Test to run")
        test_frame.grid(row=row, column=0, sticky="ew", pady=(0, 10))

        self.kind_var = tk.StringVar(value=GRIP_FORCE)
        for column, kind in enumerate(TEST_KINDS):
            ttk.Radiobutton(
                test_frame, text=TEST_KIND_LABELS[kind], value=kind,
                variable=self.kind_var, command=self._on_kind_changed,
            ).grid(row=0, column=column, padx=8, pady=6, sticky="w")

        self.kind_help_var = tk.StringVar()
        ttk.Label(test_frame, textvariable=self.kind_help_var, wraplength=560,
                  justify="left").grid(row=1, column=0, columnspan=2,
                                       padx=8, pady=(0, 8), sticky="w")

    def _build_config_frame(self, parent, row):
        config_frame = ttk.LabelFrame(
            parent, text="3. Configuration - select finger material + padding"
        )
        config_frame.grid(row=row, column=0, sticky="ew", pady=(0, 10))

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

    def _build_run_frame(self, parent, row):
        run_frame = ttk.LabelFrame(parent, text="4. Run")
        run_frame.grid(row=row, column=0, sticky="ew", pady=(0, 10))

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
        ttk.Label(run_frame, textvariable=self.status_var, wraplength=560,
                  justify="left").grid(row=1, column=0, columnspan=3,
                                       padx=8, pady=(0, 4), sticky="w")

        self.readout_var = tk.StringVar(value="Present position: --  |  Present current: --")
        ttk.Label(run_frame, textvariable=self.readout_var).grid(
            row=2, column=0, columnspan=3, padx=8, pady=(0, 8), sticky="w"
        )

    def _build_report_frame(self, parent, row):
        report_frame = ttk.LabelFrame(parent, text="5. Report")
        report_frame.grid(row=row, column=0, sticky="ew")

        self.report_button = ttk.Button(
            report_frame, text="Generate Report (graphs + statistics tables)",
            command=self._generate_report_clicked,
        )
        self.report_button.grid(row=0, column=0, padx=8, pady=8, sticky="w")

        self.report_status_var = tk.StringVar(value="")
        ttk.Label(report_frame, textvariable=self.report_status_var, wraplength=560,
                  justify="left").grid(row=1, column=0, padx=8, pady=(0, 8), sticky="w")

    # ------------------------------------------------------------------
    # Hardware
    # ------------------------------------------------------------------
    def _connect_hardware(self):
        try:
            self.gripper = settings.connect()
        except Exception as exc:
            messagebox.showerror("Connection error", str(exc))
            self.root.destroy()
            raise

    def _calibrate(self):
        if self.running:
            messagebox.showwarning("Calibration", "Cannot calibrate while a run is in progress.")
            return
        CalibrationDialog(self.root, self.gripper, on_saved=self._on_calibrated)

    def _on_calibrated(self, limits):
        self.max_open = int(limits["max_open"])
        self.min_open = int(limits["min_open"])
        self.calibrated = True
        self._refresh_limits_label()
        self._refresh_matrix()
        self._set_status(
            f"Calibrated: MAX OPEN {self.max_open} -> MIN OPEN {self.min_open} ticks."
        )

    def _refresh_limits_label(self):
        travel = self.max_open - self.min_open
        deg = travel * settings.POSITION_UNIT_DEG
        if self.calibrated:
            self.limits_var.set(
                f"Limits: MAX OPEN = {self.max_open} ticks  ->  MIN OPEN = {self.min_open} "
                f"ticks  ({travel} ticks, {deg:.1f} deg)"
            )
        else:
            self.limits_var.set(
                "NOT CALIBRATED. Showing the nominal fallback limits "
                f"({self.max_open} -> {self.min_open} ticks), which belong to whichever "
                "fingers were fitted when they were written down. Calibrate before running "
                "a test."
            )

    def _emergency_stop(self):
        self.abort_event.set()
        try:
            self.gripper.disable_torque()
            self._set_status("EMERGENCY STOP: torque disabled.")
        except Exception as exc:
            messagebox.showerror("Emergency stop error", str(exc))

    # ------------------------------------------------------------------
    # Selection / matrix state
    # ------------------------------------------------------------------
    @property
    def kind(self):
        return self.kind_var.get()

    def _on_kind_changed(self):
        # Completion is tracked per test, so a combo finished for grip force
        # may still be outstanding for slip detection.
        self.selected = None
        self.selection_var.set("Selected: (none)")
        self._refresh_matrix()

    def _select_combo(self, finger, padding):
        if (finger, padding) in self.done[self.kind]:
            return
        self.selected = (finger, padding)
        self.selection_var.set(f"Selected: {finger}  /  {padding}")
        self._refresh_matrix()

    def _refresh_matrix(self):
        running = self.running
        done = self.done[self.kind]

        for combo, button in self.cell_buttons.items():
            if combo in done:
                button.config(text="done", style="Cell.TButton", state="disabled")
            elif running:
                button.config(state="disabled")
            elif combo == self.selected:
                button.config(text="SELECTED", style="Selected.TButton", state="normal")
            else:
                button.config(text="select", style="Cell.TButton", state="normal")

        self.progress_var.set(
            f"{TEST_KIND_LABELS[self.kind]}: {len(done)} / {TOTAL_COMBOS} complete"
        )
        self.kind_help_var.set(
            "Closes onto a kitchen scale and asks for the weight after each grip."
            if self.kind == GRIP_FORCE else
            "Holds the object and watches present current. Pull the object until it "
            "slips; the collapse in current is timed automatically."
        )

        can_start = self.selected is not None and not running and self.calibrated
        self.start_button.config(state="normal" if can_start else "disabled")
        self.abort_button.config(state="normal" if running else "disabled")
        self.calibrate_button.config(state="disabled" if running else "normal")
        self.report_button.config(state="disabled" if running else "normal")

    # ------------------------------------------------------------------
    # Run control
    # ------------------------------------------------------------------
    def _build_runner(self, kind):
        common = {
            "max_open": self.max_open,
            "min_open": self.min_open,
            "profile_velocity": settings.BENCHMARK_PROFILE_VELOCITY,
            "on_status": self._set_status,
            "on_readout": self._set_readout,
            "on_reading": lambda row: self._record_reading(kind, row),
            "abort_event": self.abort_event,
        }
        if kind == GRIP_FORCE:
            return BenchmarkRunner(self.gripper, ask_weight=self._ask_weight, **common)

        config = settings.SLIP
        return SlipRunner(
            self.gripper,
            poll_interval=config["poll_interval"],
            baseline_settle=config["baseline_settle"],
            baseline_samples=config["baseline_samples"],
            drop_fraction=config["drop_fraction"],
            min_drop_raw=config["min_drop_raw"],
            confirm_samples=config["confirm_samples"],
            timeout=config["timeout"],
            ask_ready=self._ask_ready,
            **common,
        )

    def _start_run(self):
        if self.selected is None or not self.calibrated:
            return
        kind = self.kind
        finger, padding = self.selected

        counts = recorded_counts(self.rows[kind], finger, padding)
        already = sum(min(count, REPEATS) for count in counts.values())
        if already:
            resume = messagebox.askyesno(
                "Resume run",
                f"{finger} / {padding} already has {already} of "
                f"{READINGS_PER_COMBO} {TEST_KIND_LABELS[kind]} readings.\n\n"
                "Continue from where it stopped? (No = cancel)",
            )
            if not resume:
                return

        blurb = (
            "Torque will be ENABLED and the gripper will close onto the scale.\n"
            "Make sure the scale is in position and clear of obstructions."
            if kind == GRIP_FORCE else
            "Torque will be ENABLED and the gripper will grip the test object.\n"
            "You will be prompted before each grip, then asked to pull the\n"
            "object until it slips."
        )
        if not messagebox.askokcancel(
            "Start run",
            f"Test: {TEST_KIND_LABELS[kind]}\nFinger: {finger}\nPadding: {padding}\n\n{blurb}",
        ):
            return

        self.abort_event.clear()
        self.running = True
        runner = self._build_runner(kind)
        self.run_thread = threading.Thread(
            target=self._run_worker, args=(runner, kind, finger, padding, counts), daemon=True
        )
        self.run_thread.start()
        self._refresh_matrix()

    def _abort_run(self):
        self.abort_event.set()
        self._set_status("Abort requested - finishing current step...")

    def _run_worker(self, runner, kind, finger, padding, counts):
        try:
            runner.run_combo(finger, padding, counts)
            self._finish_run(kind, finger, padding, aborted=False)
        except AbortedError:
            self._finish_run(kind, finger, padding, aborted=True)
        except Exception as exc:
            self._set_status(f"Run failed: {exc}")
            self.root.after(0, messagebox.showerror, "Run error", str(exc))
            self._finish_run(kind, finger, padding, aborted=True)

    def _finish_run(self, kind, finger, padding, aborted):
        self.running = False
        try:
            self.gripper.disable_torque()
        except Exception:
            pass

        complete = combo_is_complete(self.rows[kind], finger, padding)
        if complete:
            self.done[kind].add((finger, padding))
            self.selected = None
            self.root.after(0, self.selection_var.set, "Selected: (none)")

        if aborted:
            self._set_status("Run aborted. Torque disabled. Partial readings were saved.")
        elif complete:
            self._set_status(
                f"{finger} / {padding} complete for {TEST_KIND_LABELS[kind]}. Torque disabled."
            )
        else:
            self._set_status("Run ended early. Torque disabled. Partial readings were saved.")

        self.root.after(0, self._refresh_matrix)

        everything_done = all(len(self.done[k]) == TOTAL_COMBOS for k in TEST_KINDS)
        if not aborted and everything_done:
            self.root.after(0, self._auto_generate_report)

    # ------------------------------------------------------------------
    # Runner callbacks (worker thread)
    # ------------------------------------------------------------------
    def _record_reading(self, kind, row):
        append_row(row, CSV_BY_KIND[kind], CSV_FIELDS_BY_KIND[kind])
        self.rows[kind].append({key: str(value) for key, value in row.items()})

    def _ask_weight(self, finger, padding, goal_current, repeat):
        return self.prompt.ask(
            weight_dialog, finger, padding, goal_current, repeat,
            settings.CURRENT_UNIT_MA, REPEATS,
        )

    def _ask_ready(self, finger, padding, goal_current, repeat):
        return self.prompt.ask(
            ready_dialog, finger, padding, goal_current, repeat,
            settings.CURRENT_UNIT_MA, REPEATS,
        )

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
        outstanding = {
            TEST_KIND_LABELS[kind]: TOTAL_COMBOS - len(self.done[kind])
            for kind in TEST_KINDS if len(self.done[kind]) < TOTAL_COMBOS
        }
        if outstanding:
            detail = "\n".join(
                f"  {label}: {count} combination(s) outstanding"
                for label, count in outstanding.items()
            )
            if not messagebox.askyesno(
                "Incomplete data",
                f"Not every combination is complete:\n\n{detail}\n\n"
                "Generate a report from the partial data anyway?",
            ):
                return
        self._auto_generate_report()

    def _auto_generate_report(self):
        try:
            outputs = generate_report(
                settings.RESULTS_CSV, settings.REPORT_DIR, settings.SLIP_CSV
            )
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
            self.abort_event.set()
            self.prompt.release()
            self.run_thread.join(timeout=3.0)

        if self.gripper is not None:
            try:
                self.gripper.close()
            except Exception:
                pass
        self.root.destroy()
