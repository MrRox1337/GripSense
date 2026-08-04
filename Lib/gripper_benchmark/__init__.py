"""
Grip quality benchmark for the slip-aware gripper.

Modules:
  matrix   - the test grid (materials, paddings, currents) and what counts
             as a completed combination
  storage  - append/read the results CSV
  runner   - the open/close/measure sequence against real hardware, no UI
  ui       - Tk configuration panel and scale-reading popup
  report   - statistics table and figures

ui is not re-exported here: importing it pulls in tkinter, which a headless
report run does not need. Import it explicitly as
`from gripper_benchmark.ui import BenchmarkApp`.
"""

from .matrix import (
    FINGER_MATERIALS,
    PADDINGS,
    TEST_CURRENTS,
    REPEATS,
    CSV_FIELDS,
    READINGS_PER_COMBO,
    TOTAL_COMBOS,
    all_combos,
    combo_is_complete,
    completed_combos,
    recorded_counts,
)
from .report import build_stats_table, generate_report, load_dataframe
from .runner import AbortedError, BenchmarkRunner
from .storage import append_row, read_rows

__all__ = [
    "FINGER_MATERIALS",
    "PADDINGS",
    "TEST_CURRENTS",
    "REPEATS",
    "CSV_FIELDS",
    "READINGS_PER_COMBO",
    "TOTAL_COMBOS",
    "all_combos",
    "combo_is_complete",
    "completed_combos",
    "recorded_counts",
    "append_row",
    "read_rows",
    "AbortedError",
    "BenchmarkRunner",
    "build_stats_table",
    "generate_report",
    "load_dataframe",
]
