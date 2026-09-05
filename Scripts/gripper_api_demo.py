"""
Drive the gripper from a script instead of a GUI.

A worked example of dynamixel_gripper.GripperAPI, and the end-to-end check for
it: open, close, adjust the grip strength while holding, and watch the status go
from "ok" to "slip" when the object is pulled free.

Note what is NOT here - no gripper_settings, no gripper_benchmark, no Tk. The
only project-specific thing this script knows is where the three YAML files live.
Anyone who wants the same control from their own code needs the Lib/
dynamixel_gripper/ folder and nothing else.

Usage:
    python Scripts/gripper_api_demo.py             # grip test, needs an object
    python Scripts/gripper_api_demo.py --miss      # close on nothing, expect "miss"
    python Scripts/gripper_api_demo.py --calibrate # find this set of fingers'
                                                   # travel limits and save them
"""

import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "Lib"))

# Imported only after Lib/ is on the path above.
from dynamixel_gripper import GripperAPI

CONFIG_DIR = PROJECT_ROOT / "Config"


def calibrate(api):
    """Find and save this set of fingers' travel limits, then stop."""
    print("\nCALIBRATION")
    print("  Open the fingers by hand NOW if you have not already - wherever")
    print("  they are when torque drops becomes MAX OPEN.")
    input("  Press Enter when the fingers are open... ")

    result = api.calibrate()

    print(f"\n  MAX OPEN    {result.max_open} ticks")
    print(f"  Hard stop   {result.hard_close} ticks "
          f"(at {result.stop_current_raw} raw)")
    print(f"  MIN OPEN    {result.min_open} ticks "
          f"(backed off {result.backoff_ticks})")
    print(f"  Travel      {result.travel_ticks} ticks")
    print("\n  Saved. Re-run without --calibrate to use these limits.")


def main():
    miss_test = "--miss" in sys.argv
    calibrate_only = "--calibrate" in sys.argv

    api = GripperAPI.from_config(
        CONFIG_DIR / "gripper_config.yaml",
        CONFIG_DIR / "xm430_control_table.yaml",
        CONFIG_DIR / "gripper_limits.yaml",
        on_status=lambda text: print(f"  [status] {text}"),
        on_status_change=lambda old, new: print(f"  [{old} -> {new}]"),
    )

    with api:
        if calibrate_only:
            calibrate(api)
            return

        if not api.calibrated:
            print(
                "WARNING: no calibrated limits found, using the nominal travel from "
                "gripper_config.yaml. Run this script with --calibrate, or use the "
                "benchmark GUI's calibration wizard."
            )
        print(f"Travel: {api.min_open_position} .. {api.max_open_position} ticks")

        api.enable(True)
        api.set_grip_strength(0.5)
        print(f"Grip strength 0.5 = {api.grip_current_raw} raw")

        print("\nOpening...")
        print(f"  -> {api.open()}")

        if miss_test:
            print("\nClosing on nothing...")
            print(f"  -> {api.close()}        (expected: miss)")
            api.open()
            api.enable(False)
            return

        input("\nPlace an object between the fingers, then press Enter...")

        print("Closing...")
        verdict = api.close()
        print(f"  -> {verdict}")

        if verdict != "ok":
            print("Nothing gripped, stopping here.")
            api.open()
            api.enable(False)
            return

        if not api.slip_detectable:
            print("Grip too weak for slip detection - see the warning above.")

        print(f"\nHolding. Baseline {api.baseline_current_raw:.0f} raw.")
        print("Tightening to 0.9...")
        api.set_grip_strength(0.9)
        time.sleep(2.0)

        print("\nPULL THE OBJECT until it slips (25 s)...")
        result = api.wait_for_slip(timeout=25.0)
        print(f"  -> {result}")

        if result == "slip":
            event = api.last_slip
            print(
                f"  detected after {event.detection_time_s:.3f} s, "
                f"{event.baseline_raw:.0f} -> {event.slip_raw:.0f} raw "
                f"(threshold {event.threshold_raw:.0f})"
            )
            # The fingers have carried on closing by now; the latch means the
            # status is still "slip" rather than "miss".
            time.sleep(1.0)
            print(f"  status one second later: {api.status}")

        print("\nOpening and releasing torque.")
        api.open()
        api.enable(False)


if __name__ == "__main__":
    main()
