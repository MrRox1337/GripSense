"""
Drive the gripper through dynamixel_gripper.GripperAPI, from a GUI.

A worked example of the API and the end-to-end check for it: connect, enable,
grip, move and squeeze in normalised units, and measure how often the reported
status matches a grip staged to produce it.

Note what is NOT here - no gripper_settings, nothing from the rest of this
repository except the demo window itself. The only project-specific thing this
script knows is where the three YAML files live. Anyone who wants the same
control from their own code needs the Lib/dynamixel_gripper/ folder and nothing
else.

Runs are written to Demo/ as a CSV of every trial and a JPEG accuracy matrix.

Usage:
    python Scripts/gripper_api_demo.py
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "Lib"))

# Everything below is imported only after Lib/ is on the path above.
import tkinter as tk

from gripper_demo_ui import DemoApp

CONFIG_DIR = PROJECT_ROOT / "Config"


def main():
    root = tk.Tk()
    DemoApp(
        root,
        config_path=CONFIG_DIR / "gripper_config.yaml",
        control_table_path=CONFIG_DIR / "xm430_control_table.yaml",
        limits_path=CONFIG_DIR / "gripper_limits.yaml",
        output_dir=PROJECT_ROOT / "Demo",
    )
    root.mainloop()


if __name__ == "__main__":
    main()
