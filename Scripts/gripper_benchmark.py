"""
Grip quality benchmark for the slip-aware Dynamixel XM430-W210-T gripper.

For every (finger material x padding) combination the gripper closes onto a
kitchen scale at each goal-current limit, three times. After each close a
popup asks for the weight the scale showed; submitting it reopens the gripper
and starts the next repeat. Readings are appended to benchmark_results.csv as
they are captured, and the report is generated automatically once all 16
combinations are complete.

Usage:
    python Scripts/gripper_benchmark.py            # measurement GUI
    python Scripts/gripper_benchmark.py --report   # rebuild report from CSV
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
        outputs = generate_report(settings.RESULTS_CSV, settings.REPORT_DIR)
        print(f"Report written to {settings.REPORT_DIR}:")
        for path in outputs:
            print(f"  {path.name}")
        return

    root = tk.Tk()
    BenchmarkApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
