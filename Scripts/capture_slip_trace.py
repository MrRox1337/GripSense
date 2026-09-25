"""
Capture one slip trace on the rig and render it, in a single command.

    python Scripts/capture_slip_trace.py

Grip an object, pull it free when prompted, and this writes the sample CSV and
the figure beside it. Retries until a slip is actually confirmed, so a mistimed
pull costs nothing but another go.

    python Scripts/capture_slip_trace.py --out Data/img/slip.png
    python Scripts/capture_slip_trace.py --keep 3   # keep going, pick the best

The gripper must already be calibrated for this set of fingers; run the
calibration wizard first if `calibrated` reports False.
"""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "Lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Imported only after the paths above are set.
from dynamixel_gripper import GripperAPI
from plot_slip_trace import plot, read_trace

CONFIG_DIR = PROJECT_ROOT / "Config"
DEFAULT_OUT = PROJECT_ROOT / "Data" / "img" / "slip.png"


def capture_one(api, attempt):
    """
    One staged grasp-and-pull. Returns True once a slip has been confirmed.

    Mirrors the slip trial the demo console stages, with recording switched on:
    open, close on the object, then wait while it is pulled free.
    """
    print(f"\n--- attempt {attempt} ---")
    api.open()
    input("Place an object between the fingers, then press Enter...")

    verdict = api.close()
    if verdict != "ok":
        print(f"  nothing caught (reported {verdict}) - reposition and retry")
        return False

    print("  holding. Now pull the object free.")
    verdict = api.wait_for_slip()
    if verdict != "slip":
        print(f"  no slip confirmed (ended {verdict}) - retry")
        return False

    trace = api.slip_trace
    confirming = trace.confirming_indices()
    print(f"  slip confirmed: {len(trace)} samples, baseline "
          f"{trace.baseline:.0f}, threshold {trace.threshold:.0f}, "
          f"{len(confirming)} confirming")

    timing = trace.timing_summary()
    if timing:
        # Measured, not configured: quote this rate rather than the poll
        # interval, which is only the sleep between reads.
        print(f"  sampling: {timing['rate_hz']:.1f} Hz "
              f"(median period {timing['median_period_s'] * 1e3:.2f} ms, "
              f"range {timing['min_period_s'] * 1e3:.2f}"
              f"-{timing['max_period_s'] * 1e3:.2f} ms)")
        if len(confirming) >= 2:
            span = 2 * timing["median_period_s"] * 1e3
            print(f"  confirmation delay: {span:.0f} ms "
                  f"(2 periods, first dropped sample to verdict)")
    if trace.truncated:
        print("  NOTE: the trace hit its sample cap and stops early")
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("-o", "--out", type=Path, default=DEFAULT_OUT,
                        help=f"output image (default: {DEFAULT_OUT})")
    parser.add_argument("--keep", type=int, default=1,
                        help="how many good traces to capture (default: 1); "
                             "extras are numbered so you can pick the clearest")
    args = parser.parse_args(argv)

    with GripperAPI.from_config(
        CONFIG_DIR / "gripper_config.yaml",
        CONFIG_DIR / "xm430_control_table.yaml",
        CONFIG_DIR / "gripper_limits.yaml",
    ) as api:
        if not api.calibrated:
            print("WARNING: these limits are nominal, not calibrated for these "
                  "fingers. Run the calibration wizard for a trustworthy trace.")

        api.enable(True)
        api.record_slip_trace()
        print(f"grip strength {api.grip_strength:.2f} "
              f"({api.grip_current_raw} raw)")

        captured, attempt = 0, 0
        while captured < args.keep:
            attempt += 1
            try:
                if not capture_one(api, attempt):
                    continue
            except KeyboardInterrupt:
                print("\nstopped.")
                break

            # Save before the next grasp: arming a new watch starts a new trace.
            suffix = "" if args.keep == 1 else f"_{captured + 1}"
            out_img = args.out.with_name(
                f"{args.out.stem}{suffix}{args.out.suffix}")
            out_csv = out_img.with_suffix(".csv")
            out_csv.parent.mkdir(parents=True, exist_ok=True)

            api.slip_trace.save_csv(out_csv)
            plot(read_trace(out_csv), out_img)
            print(f"  wrote {out_csv}")
            print(f"  wrote {out_img}")
            captured += 1

        api.open()

    return 0 if captured else 1


if __name__ == "__main__":
    raise SystemExit(main())
