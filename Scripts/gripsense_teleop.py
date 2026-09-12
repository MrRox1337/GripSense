"""
Manual control GUI for a Dynamixel XM430-W210-T gripper.

Protocol 2.0, current-based position control (Operating Mode 5). Three sliders
straight onto the servo:
  - Position: max-open -> min-open (raw Goal Position ticks)
  - Current:  goal current limit (raw units)
  - Speed:    profile velocity (raw units)

plus a live readout and the travel-limit calibration wizard.

The console itself lives in the driver package as GripperAPI.teleop(), so it
comes with the package rather than with this repository. All this script does is
say where the three YAML files are.

Usage:
    python Scripts/gripsense_teleop.py
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "Lib"))

# Imported only after Lib/ is on the path above.
from dynamixel_gripper import GripperAPI

CONFIG_DIR = PROJECT_ROOT / "Config"


def main():
    with GripperAPI.from_config(
        CONFIG_DIR / "gripper_config.yaml",
        CONFIG_DIR / "xm430_control_table.yaml",
        CONFIG_DIR / "gripper_limits.yaml",
    ) as api:
        api.teleop()


if __name__ == "__main__":
    main()
