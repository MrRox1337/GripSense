"""
Draw a recorded slip trace: the plateau, the threshold, and the collapse.

Reads a CSV written by SlipTrace.save_csv() and renders the figure the paper
calls for - present current against time, the median-of-20 holding baseline and
the drop threshold as horizontal references, and the confirming samples marked.

    python Scripts/plot_slip_trace.py slip_trace.csv
    python Scripts/plot_slip_trace.py slip_trace.csv -o img/slip.png

To produce the CSV in the first place, record a watch on the rig:

    api.record_slip_trace()
    api.close()                 # grip the object
    api.wait_for_slip()         # then pull it out by hand
    api.slip_trace.save_csv("slip_trace.csv")

Nothing here invents data: a CSV with no confirmed slip is drawn as such, with
the markers absent, rather than being dressed up as a detection.
"""

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "img" / "slip.png"

# Raw current units to mA, from the XM430 control table. Only used for the
# secondary axis, so a control table with no scale simply omits it.
CURRENT_MA_PER_RAW = 2.69


def read_trace(path):
    """Load the CSV into parallel lists, keeping the phase and marker columns."""
    rows = list(csv.DictReader(open(path, newline="", encoding="utf-8")))
    if not rows:
        raise SystemExit(f"{path}: no samples in file")

    def scalar(column):
        for row in rows:
            if row.get(column):
                return float(row[column])
        return None

    return {
        "t": [float(r["t_s"]) for r in rows],
        "current": [float(r["current_raw"]) for r in rows],
        "phase": [r["phase"] for r in rows],
        "confirming": [r.get("confirming") == "1" for r in rows],
        "baseline": scalar("baseline_raw"),
        "threshold": scalar("threshold_raw"),
        "detection_time_s": scalar("detection_time_s"),
    }


def watch_relative_times(trace):
    """
    Re-origin the trace so t=0 is the start of the watch, not of the baseline.

    SlipWatch times its detection from arm() - the moment the baseline window
    closes - while the trace timestamps run from its first baseline sample. Left
    alone the two disagree by the length of that window, and the figure would
    mark a sample at one time while labelling it with another. Anchoring on the
    reported detection time removes the discrepancy by construction: the first
    confirming sample then lands exactly where the event says it did, and the
    baseline window plots at negative time, which is what it is.
    """
    t = trace["t"]
    first = next((i for i, c in enumerate(trace["confirming"]) if c), None)
    if first is not None and trace["detection_time_s"] is not None:
        shift = t[first] - trace["detection_time_s"]
    else:
        # No confirmed slip: fall back to the baseline/watch boundary.
        split = next((i for i, p in enumerate(trace["phase"]) if p == "watch"),
                     len(t))
        shift = t[split] if split < len(t) else 0.0
    return [x - shift for x in t]


def plot(trace, out_path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    t = watch_relative_times(trace)
    current = trace["current"]
    baseline, threshold = trace["baseline"], trace["threshold"]

    fig, ax = plt.subplots(figsize=(7.0, 4.0))

    # The baseline window is a different kind of sample from the watch - it is
    # what the threshold is derived FROM - so it is drawn distinctly rather
    # than being run together with the samples the threshold is applied to.
    split = next((i for i, p in enumerate(trace["phase"]) if p == "watch"),
                 len(t))
    if split:
        ax.plot(t[:split], current[:split], color="#8a8a8a", linewidth=1.0,
                label=f"baseline window ({split} samples)")
    # Join the two phases so the line is continuous across the boundary.
    join = max(split - 1, 0)
    ax.plot(t[join:], current[join:], color="#1f4e79", linewidth=1.0,
            label="present current")

    if baseline is not None:
        ax.axhline(baseline, color="#2e7d32", linestyle="--", linewidth=1.1,
                   label=f"holding baseline, median of {split} = {baseline:.0f}")
    if threshold is not None:
        ax.axhline(threshold, color="#c62828", linestyle=":", linewidth=1.3,
                   label=f"drop threshold = {threshold:.0f}")

    marked = [(t[i], current[i]) for i, c in enumerate(trace["confirming"]) if c]
    if marked:
        ax.plot([x for x, _ in marked], [y for _, y in marked], "o",
                markersize=7, markerfacecolor="none", markeredgecolor="#c62828",
                markeredgewidth=1.6,
                label=f"confirming samples ({len(marked)})")
        # The reported detection time is that of the FIRST of the run; annotate
        # it where it actually falls rather than trusting the two clocks to
        # share an origin (SlipWatch times from arm(), the trace from its first
        # baseline sample).
        first_t, first_y = marked[0]
        reported = trace["detection_time_s"]
        label = "first dropped sample"
        if reported is not None:
            label += f"\nreported at {reported:.3f} s"
        # Placed to the lower right: below the threshold and after the collapse
        # is the one reliably empty region of this figure.
        ax.annotate(label, xy=(first_t, first_y),
                    xytext=(22, -26), textcoords="offset points",
                    fontsize=8, color="#c62828", ha="left", va="top",
                    arrowprops=dict(arrowstyle="->", color="#c62828", lw=1.0))
    else:
        ax.text(0.5, 0.06, "no slip confirmed in this trace",
                transform=ax.transAxes, ha="center", fontsize=9, color="#c62828")

    ax.axvline(0.0, color="#9e9e9e", linewidth=0.8, linestyle="-", zorder=0)

    # The rate is measured from the sample timestamps rather than quoted from
    # the configured poll interval, which is only the sleep between reads.
    xlabel = "time from start of watch (s); baseline window at $t<0$"
    watch_t = [x for x, p in zip(t, trace["phase"]) if p == "watch"]
    gaps = sorted(b - a for a, b in zip(watch_t, watch_t[1:]))
    if gaps:
        period = gaps[len(gaps) // 2]
        if period > 0:
            xlabel += (f"\nsampled at {1.0 / period:.1f} Hz "
                       f"(median period {period * 1e3:.1f} ms, measured)")
    ax.set_xlabel(xlabel)
    ax.set_ylabel("present current (raw units)")
    ax.set_xlim(min(t), max(t))
    ax.margins(y=0.16)
    ax.grid(True, linestyle="-", linewidth=0.4, color="#d8d8d8")
    ax.set_axisbelow(True)
    ax.legend(loc="lower left", fontsize=8, framealpha=0.95)

    secondary = ax.secondary_yaxis(
        "right",
        functions=(lambda v: v * CURRENT_MA_PER_RAW,
                   lambda v: v / CURRENT_MA_PER_RAW),
    )
    secondary.set_ylabel("present current (mA)")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_path, dpi=200)
    plt.close(fig)
    return out_path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("csv", type=Path,
                        help="a CSV written by SlipTrace.save_csv()")
    parser.add_argument("-o", "--out", type=Path, default=DEFAULT_OUT,
                        help=f"output image (default: {DEFAULT_OUT})")
    args = parser.parse_args(argv)

    if not args.csv.exists():
        raise SystemExit(f"{args.csv}: not found")

    trace = read_trace(args.csv)
    written = plot(trace, args.out)

    confirmed = sum(trace["confirming"])
    print(f"{len(trace['t'])} samples, baseline {trace['baseline']}, "
          f"threshold {trace['threshold']}, {confirmed} confirming")
    print(f"wrote {written}")
    if not confirmed:
        print("note: this trace records no confirmed slip", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
