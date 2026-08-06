"""
Benchmarks for the slip-aware gripper.

Two tests share one test matrix and one window:

  grip force      close onto a kitchen scale and record what it reads
  slip detection  hold a loaded object and time the collapse in present
                  current that means the grip was lost

Modules:
  matrix         - the test grid (materials, paddings, currents) and what
                   counts as a completed combination
  storage        - append/read the results CSVs
  motion         - open/close between the calibrated limits, shared by both
                   runners
  runner         - the grip-force measurement sequence, no UI
  slip           - the slip-detection sequence, no UI
  dialogs        - modal prompts a worker thread can raise on the main loop
  calibration_ui - the travel-limit calibration wizard
  ui             - Tk configuration panel and run controls
  report         - statistics tables and figures

ui and calibration_ui are not re-exported here: importing them pulls in
tkinter, which a headless report run does not need. Import them explicitly as
`from gripper_benchmark.ui import BenchmarkApp`.
"""

from .matrix import (
    CSV_FIELDS,
    CSV_FIELDS_BY_KIND,
    FINGER_MATERIALS,
    GRIP_FORCE,
    PADDINGS,
    READINGS_PER_COMBO,
    REPEATS,
    SLIP_CSV_FIELDS,
    SLIP_DETECTION,
    TEST_CURRENTS,
    TEST_KIND_LABELS,
    TEST_KINDS,
    TOTAL_COMBOS,
    all_combos,
    combo_is_complete,
    completed_combos,
    recorded_counts,
)
from .motion import AbortedError, MotionBase
from .report import (
    build_slip_stats_table,
    build_stats_table,
    generate_report,
    load_dataframe,
    load_slip_dataframe,
)
from .runner import BenchmarkRunner
from .slip import SlipRunner, SlipWatchResult
from .storage import append_row, read_rows

__all__ = [
    "FINGER_MATERIALS",
    "PADDINGS",
    "TEST_CURRENTS",
    "REPEATS",
    "CSV_FIELDS",
    "SLIP_CSV_FIELDS",
    "CSV_FIELDS_BY_KIND",
    "GRIP_FORCE",
    "SLIP_DETECTION",
    "TEST_KINDS",
    "TEST_KIND_LABELS",
    "READINGS_PER_COMBO",
    "TOTAL_COMBOS",
    "all_combos",
    "combo_is_complete",
    "completed_combos",
    "recorded_counts",
    "append_row",
    "read_rows",
    "AbortedError",
    "MotionBase",
    "BenchmarkRunner",
    "SlipRunner",
    "SlipWatchResult",
    "build_stats_table",
    "build_slip_stats_table",
    "generate_report",
    "load_dataframe",
    "load_slip_dataframe",
]
