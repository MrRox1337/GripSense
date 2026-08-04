"""
Slip-aware gripper control GUI for a Dynamixel XM430-W210-T.

Protocol 2.0, current-based position control (Operating Mode 5).
Provides three sliders:
  - Position: max-open -> min-open (raw Goal Position ticks)
  - Current:  goal current limit (raw units)
  - Speed:    profile velocity (raw units)

Plain Python + Tkinter (stdlib) + dynamixel-sdk. No ROS2.

Usage:
    python Scripts/gripper_control_gui.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "Lib"))

# Everything below is imported only after Lib/ is on the path above.
import tkinter as tk

from gripper_control_ui import GripperApp


def main():
    root = tk.Tk()
    GripperApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
