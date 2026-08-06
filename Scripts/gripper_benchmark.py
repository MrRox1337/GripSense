"""
Benchmarks for the slip-aware Dynamixel XM430-W210-T gripper.

Start by calibrating the travel limits: torque is released so the fingers can
be opened by hand to set MAX OPEN, then the gripper closes under a current
limit until it meets its mechanical stop and backs off a few ticks to set
MIN OPEN. Run this whenever fingers are loaded or unloaded - no position is
hardcoded.

Two tests then run over the same (finger material x padding) matrix, each at
every goal-current limit, three times:

  Grip force       the gripper closes onto a kitchen scale and a popup asks
                   for the weight it showed. Recorded to benchmark_results.csv.
  Slip detection   the gripper holds the object while you pull it. The sudden
                   fall in present current when the object escapes is the
                   slip, and the time to see it is recorded to
                   slip_results.csv.

Readings are appended as they are captured, so an aborted run resumes rather
than restarts. The report is generated automatically once every combination of
both tests is complete.

Usage:
    python Scripts/gripper_benchmark.py            # measurement GUI
    python Scripts/gripper_benchmark.py --report   # rebuild report from CSVs
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "Lib"))

# Everything below is imported only after Lib/ is on the path above.
import tkinter as tk

import gripper_settings as settings
from gripper_benchmark.report import generate_report
from gripper_benchmark.ui import BenchmarkApp


def main():
    if "--report" in sys.argv:
        outputs = generate_report(
            settings.RESULTS_CSV, settings.REPORT_DIR, settings.SLIP_CSV
        )
        print(f"Report written to {settings.REPORT_DIR}:")
        for path in outputs:
            print(f"  {path.name}")
        return

    root = tk.Tk()
    BenchmarkApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
