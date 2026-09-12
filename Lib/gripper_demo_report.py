"""
What a demo run leaves behind.

Two datasets, each a CSV plus a figure:

  status tests     every staged grip, and a matrix of how often the status
                   GripperAPI reported was the one the trial was set up to
                   produce
  opening sweeps   every commanded opening against the caliper reading taken
                   at it, and a scatter of measured against expected

Data in, files out. No Tk and no servo here, so a run can be re-plotted from its
own numbers without the rig present.
"""

import csv
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "EXPECTED_STATUSES",
    "OpeningSample",
    "Trial",
    "write_accuracy_matrix",
    "write_csv",
    "write_opening_csv",
    "write_opening_plot",
]

# The three statuses a test can be staged to produce: close on an object, close
# on nothing, close on an object that is then pulled free. `idle` and `moving`
# are not outcomes anyone sets up, so they are only ever reported, never expected.
EXPECTED_STATUSES = ["ok", "miss", "slip"]


@dataclass(frozen=True)
class Trial:
    """One staged grip: what it was set up to produce, and what came back."""

    expected: str
    observed: str


def write_csv(path, trials):
    """Write every trial so far to `path`, newest run last, and return the path."""
    with open(Path(path), "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["grip", "expected", "observed"])
        for number, trial in enumerate(trials, start=1):
            writer.writerow([number, trial.expected, trial.observed])
    return Path(path)


def _matrix(trials):
    """(columns, counts) - counts[expected][observed], rows in EXPECTED order."""
    reported = {trial.observed for trial in trials}
    # `idle` after a close means the fingers arrived without stalling, which is
    # a real outcome worth a column of its own rather than a bin marked "other".
    columns = EXPECTED_STATUSES + sorted(reported - set(EXPECTED_STATUSES))

    counts = [[0] * len(columns) for _ in EXPECTED_STATUSES]
    for trial in trials:
        row = EXPECTED_STATUSES.index(trial.expected)
        counts[row][columns.index(trial.observed)] += 1
    return columns, counts


def write_accuracy_matrix(path, trials, title=None):
    """
    Render expected-vs-reported to `path` as a JPEG, and return the path.

    Rows are what each test stages, columns what the API reported. The diagonal
    is the detector being right; everything off it is a particular way of being
    wrong, which is the part worth looking at.
    """
    # Imported here rather than at module scope: matplotlib costs about a second
    # to import and the GUI should not pay that before its first window.
    import matplotlib

    matplotlib.use("Agg")  # a second Tk backend behind the running GUI is trouble
    import matplotlib.pyplot as plt

    columns, counts = _matrix(trials)
    totals = [sum(row) for row in counts]
    fractions = [
        [count / total if total else 0.0 for count in row]
        for row, total in zip(counts, totals)
    ]

    fig, ax = plt.subplots(figsize=(1.5 * len(columns) + 3.0, 4.2))
    ax.imshow(fractions, cmap="Blues", vmin=0.0, vmax=1.0, aspect="auto")

    row_labels = []
    for row, (expected, total) in enumerate(zip(EXPECTED_STATUSES, totals)):
        if not total:
            row_labels.append(f"{expected}\nnot run")
            continue
        correct = counts[row][columns.index(expected)]
        row_labels.append(f"{expected}\nn={total}, {100 * correct / total:.0f}% correct")

    ax.set_xticks(range(len(columns)), columns)
    ax.set_yticks(range(len(EXPECTED_STATUSES)), row_labels)
    ax.set_xlabel("status reported by GripperAPI")
    ax.set_ylabel("status the trial was staged to produce")
    ax.set_title(title or "GripperAPI status accuracy")

    for row in range(len(EXPECTED_STATUSES)):
        for column in range(len(columns)):
            if not totals[row]:
                label = "-"
            else:
                label = f"{counts[row][column]}\n{100 * fractions[row][column]:.0f}%"
            ax.text(
                column,
                row,
                label,
                ha="center",
                va="center",
                color="white" if fractions[row][column] > 0.5 else "#202020",
            )

    fig.tight_layout()
    fig.savefig(Path(path), format="jpg", dpi=150)
    plt.close(fig)
    return Path(path)


@dataclass(frozen=True)
class OpeningSample:
    """One commanded opening and the caliper reading taken at it."""

    sweep: int
    set_percent: float       # what set_position() was asked for
    expected_mm: float       # that fraction of this sweep's full-open span
    actual_mm: float         # what the caliper read
    actual_percent: float    # actual_mm as a fraction of the full-open span


def write_opening_csv(path, samples):
    """Write every sweep reading to `path`, and return the path."""
    with open(Path(path), "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["sweep", "set opening %", "expected opening mm",
             "actual opening mm", "actual opening %"]
        )
        for sample in samples:
            writer.writerow([
                sample.sweep,
                f"{sample.set_percent:g}",
                f"{sample.expected_mm:.2f}",
                f"{sample.actual_mm:.2f}",
                f"{sample.actual_percent:.2f}",
            ])
    return Path(path)


def _reference_mm(samples):
    """
    The full-open span each sweep was measured against, averaged.

    Not stored on the sample - expected_mm is that fraction of it, so it comes
    back out by division and the CSV keeps the columns that were asked for.
    """
    spans = [
        sample.expected_mm * 100.0 / sample.set_percent
        for sample in samples
        if sample.set_percent
    ]
    return sum(spans) / len(spans) if spans else 0.0


def write_opening_plot(path, samples, title=None):
    """
    Scatter every reading against what was commanded, over the ideal line.

    Both axes are percentages of the same full-open span, so sweeps measured
    against slightly different references still sit on one plot and the dotted
    line is exactly where a perfectly linear jaw would put every point.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    commanded = [sample.set_percent for sample in samples]
    measured = [sample.actual_percent for sample in samples]
    sweeps = {sample.sweep for sample in samples}

    fig, ax = plt.subplots(figsize=(7.5, 5.2))
    ax.plot([0, 100], [0, 100], linestyle=":", color="#c03020", linewidth=1.8,
            label="expected (linear)", zorder=1)
    ax.scatter(commanded, measured, s=30, color="#1f6fd0", alpha=0.7,
               edgecolor="none", label="measured", zorder=2)

    ax.set_xlabel("commanded opening (% of calibrated travel)")
    ax.set_ylabel("measured opening (% of full-open span)")
    ax.set_xlim(0, 105)
    ax.set_ylim(0, 105)
    ax.grid(True, linestyle="-", linewidth=0.4, color="#d8d8d8")
    ax.set_axisbelow(True)
    ax.legend(loc="upper left")
    ax.set_title(
        title
        or f"Jaw opening linearity - {len(sweeps)} sweeps, "
           f"full-open reference {_reference_mm(samples):.1f} mm"
    )

    fig.tight_layout()
    fig.savefig(Path(path), format="jpg", dpi=150)
    plt.close(fig)
    return Path(path)
