"""
Analysis and reporting: one statistics table plus five figures.

pandas and matplotlib are imported inside the functions so that importing
this module (and therefore the measurement UI) stays cheap.
"""

from pathlib import Path

import gripper_settings as settings

from .matrix import FINGER_MATERIALS, PADDINGS, TEST_CURRENTS, REPEATS

CURRENT_UNIT_MA = settings.CURRENT_UNIT_MA


# ----------------------------------------------------------------------------
# Data
# ----------------------------------------------------------------------------
def load_dataframe(csv_path):
    import pandas as pd

    if not Path(csv_path).exists():
        raise FileNotFoundError(f"No results file at {csv_path}")

    df = pd.read_csv(csv_path)
    if df.empty:
        raise ValueError("Results file is empty - nothing to report on.")

    df["goal_current_raw"] = df["goal_current_raw"].astype(int)
    df["weight_g"] = df["weight_g"].astype(float)

    # Keep the test matrix in a fixed, meaningful order rather than alphabetical.
    df["finger_material"] = pd.Categorical(
        df["finger_material"], categories=FINGER_MATERIALS, ordered=True
    )
    df["padding"] = pd.Categorical(df["padding"], categories=PADDINGS, ordered=True)
    return df


def build_stats_table(df):
    """One table of descriptive statistics per finger / padding / current cell."""
    stats = (
        df.groupby(["finger_material", "padding", "goal_current_raw"], observed=True)["weight_g"]
        .agg(n="count", mean="mean", std="std", min="min", median="median", max="max")
        .reset_index()
    )
    stats["range"] = stats["max"] - stats["min"]
    # Coefficient of variation: how repeatable the grip is at this setting.
    stats["cv_percent"] = (stats["std"] / stats["mean"]) * 100
    stats["goal_current_ma"] = (stats["goal_current_raw"] * CURRENT_UNIT_MA).round(0)

    stats = stats[[
        "finger_material", "padding", "goal_current_raw", "goal_current_ma",
        "n", "mean", "std", "min", "median", "max", "range", "cv_percent",
    ]]
    numeric = ["mean", "std", "min", "median", "max", "range", "cv_percent"]
    stats[numeric] = stats[numeric].round(2)
    stats = stats.rename(columns={
        "finger_material": "Finger",
        "padding": "Padding",
        "goal_current_raw": "Current (raw)",
        "goal_current_ma": "Current (mA)",
        "n": "N",
        "mean": "Mean (g)",
        "std": "SD (g)",
        "min": "Min (g)",
        "median": "Median (g)",
        "max": "Max (g)",
        "range": "Range (g)",
        "cv_percent": "CV (%)",
    })
    return stats.sort_values(["Finger", "Padding", "Current (raw)"]).reset_index(drop=True)


# ----------------------------------------------------------------------------
# Figures
# ----------------------------------------------------------------------------
def _current_tick_labels():
    return [f"{c}\n({c * CURRENT_UNIT_MA:.0f} mA)" for c in TEST_CURRENTS]


def _isnan(array):
    return array != array


def _plot_grouped_lines(df, plt, group_col, line_col, group_order, line_order,
                        title, out_path):
    """2x2 grid of mean-weight-vs-current plots, one panel per group value."""
    fig, axes = plt.subplots(2, 2, figsize=(13, 9), sharex=True, sharey=True)
    colors = plt.get_cmap("tab10")

    summary = (
        df.groupby([group_col, line_col, "goal_current_raw"], observed=True)["weight_g"]
        .agg(["mean", "std"])
        .reset_index()
    )

    for ax, group_value in zip(axes.flat, group_order):
        panel = summary[summary[group_col] == group_value]
        for index, line_value in enumerate(line_order):
            series = panel[panel[line_col] == line_value].sort_values("goal_current_raw")
            if series.empty:
                continue
            ax.errorbar(
                series["goal_current_raw"], series["mean"],
                yerr=series["std"].fillna(0),
                marker="o", capsize=4, linewidth=1.8, markersize=6,
                color=colors(index), label=str(line_value),
            )
        ax.set_title(str(group_value), fontsize=12, fontweight="bold")
        ax.grid(True, linestyle=":", alpha=0.6)
        ax.set_xticks(TEST_CURRENTS)
        ax.set_xticklabels(_current_tick_labels(), fontsize=8)

    for ax in axes[-1]:
        ax.set_xlabel("Goal current limit", fontsize=10)
    for ax in axes[:, 0]:
        ax.set_ylabel("Grip force measured on scale (g)", fontsize=10)

    handles, labels = axes.flat[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=len(line_order),
               frameon=False, fontsize=10)
    fig.suptitle(title, fontsize=15, fontweight="bold")
    fig.text(0.5, 0.055, f"Error bars = 1 SD over {REPEATS} repeats",
             ha="center", fontsize=9, style="italic")
    fig.tight_layout(rect=[0, 0.09, 1, 0.96])
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def _plot_heatmap(pivot, plt, title, cbar_label, out_path, cmap, value_fmt="{:.0f}"):
    fig, ax = plt.subplots(figsize=(9, 9))
    data = pivot.to_numpy(dtype=float)
    image = ax.imshow(data, aspect="auto", cmap=cmap)

    ax.set_xticks(range(len(pivot.columns)))
    ax.set_xticklabels(_current_tick_labels(), fontsize=9)
    ax.set_yticks(range(len(pivot.index)))
    ax.set_yticklabels([f"{finger} / {pad}" for finger, pad in pivot.index], fontsize=9)
    ax.set_xlabel("Goal current limit", fontsize=11)
    ax.set_ylabel("Finger material / padding", fontsize=11)
    ax.set_title(title, fontsize=14, fontweight="bold", pad=14)

    finite = data[~_isnan(data)]
    threshold = (finite.max() + finite.min()) / 2 if finite.size else 0
    for row in range(data.shape[0]):
        for col in range(data.shape[1]):
            value = data[row, col]
            if value != value:  # NaN
                ax.text(col, row, "-", ha="center", va="center", fontsize=8, color="grey")
                continue
            ax.text(
                col, row, value_fmt.format(value), ha="center", va="center", fontsize=8,
                color="white" if value > threshold else "black",
            )

    bar = fig.colorbar(image, ax=ax, shrink=0.8)
    bar.set_label(cbar_label, fontsize=10)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def _plot_combo_ranking(df, plt, out_path):
    """Which finger/padding pairing grips hardest, averaged over all currents."""
    summary = (
        df.groupby(["finger_material", "padding"], observed=True)["weight_g"]
        .agg(["mean", "std"])
        .reset_index()
        .sort_values("mean", ascending=True)
    )
    labels = [f"{row.finger_material} / {row.padding}" for row in summary.itertuples()]

    fig, ax = plt.subplots(figsize=(11, 8))
    colors = plt.get_cmap("viridis")
    positions = range(len(summary))
    maximum = summary["mean"].max() or 1
    bars = ax.barh(
        list(positions), summary["mean"],
        xerr=summary["std"].fillna(0), capsize=4,
        color=[colors(value / maximum) for value in summary["mean"]],
    )
    ax.set_yticks(list(positions))
    ax.set_yticklabels(labels, fontsize=10)
    ax.set_xlabel("Mean grip force across all current limits (g)", fontsize=11)
    ax.set_title("Grip strength ranking by finger material and padding",
                 fontsize=14, fontweight="bold", pad=14)
    ax.grid(True, axis="x", linestyle=":", alpha=0.6)

    for bar, value in zip(bars, summary["mean"]):
        ax.text(bar.get_width() + maximum * 0.01, bar.get_y() + bar.get_height() / 2,
                f"{value:.0f} g", va="center", fontsize=9)

    fig.text(0.5, 0.02, "Error bars = 1 SD across all currents and repeats",
             ha="center", fontsize=9, style="italic")
    fig.tight_layout(rect=[0, 0.04, 1, 1])
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def _render_stats_png(stats, plt, out_path):
    row_height = 0.28
    fig_height = max(4.0, row_height * (len(stats) + 4))
    fig, ax = plt.subplots(figsize=(14, fig_height))
    ax.axis("off")

    table = ax.table(
        cellText=stats.astype(str).values,
        colLabels=stats.columns,
        cellLoc="center",
        loc="upper center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(7.5)
    table.scale(1, 1.15)

    for col in range(len(stats.columns)):
        header = table[0, col]
        header.set_facecolor("#37474f")
        header.set_text_props(color="white", fontweight="bold")

    # Band each finger material so the blocks are easy to scan.
    band_colors = {finger: shade for finger, shade in
                   zip(FINGER_MATERIALS, ["#ffffff", "#eef3f7", "#ffffff", "#eef3f7"])}
    for row_index, finger in enumerate(stats["Finger"], start=1):
        for col in range(len(stats.columns)):
            table[row_index, col].set_facecolor(band_colors.get(finger, "#ffffff"))

    ax.set_title(
        "Grip quality statistics by finger material, padding and current limit",
        fontsize=14, fontweight="bold", pad=18,
    )
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


# ----------------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------------
def generate_report(csv_path=None, out_dir=None):
    """Build the graphs and statistics table. Returns the written file paths."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    csv_path = Path(csv_path) if csv_path is not None else settings.RESULTS_CSV
    out_dir = Path(out_dir) if out_dir is not None else settings.REPORT_DIR

    df = load_dataframe(csv_path)
    stats = build_stats_table(df)

    out_dir.mkdir(exist_ok=True)
    outputs = []

    # --- Statistics table (the single summary table) ---
    stats_csv = out_dir / "summary_statistics.csv"
    stats.to_csv(stats_csv, index=False)
    outputs.append(stats_csv)

    stats_png = out_dir / "summary_statistics.png"
    _render_stats_png(stats, plt, stats_png)
    outputs.append(stats_png)

    # --- Fig 1: per finger material, one line per padding ---
    path = out_dir / "fig1_weight_vs_current_by_finger.png"
    _plot_grouped_lines(
        df, plt, "finger_material", "padding", FINGER_MATERIALS, PADDINGS,
        "Grip force vs current limit - one panel per finger material", path,
    )
    outputs.append(path)

    # --- Fig 2: per padding, one line per finger material ---
    path = out_dir / "fig2_weight_vs_current_by_padding.png"
    _plot_grouped_lines(
        df, plt, "padding", "finger_material", PADDINGS, FINGER_MATERIALS,
        "Grip force vs current limit - one panel per padding type", path,
    )
    outputs.append(path)

    # --- Fig 3: mean grip force heatmap ---
    mean_pivot = df.pivot_table(
        index=["finger_material", "padding"], columns="goal_current_raw",
        values="weight_g", aggfunc="mean", observed=True,
    ).reindex(columns=TEST_CURRENTS)
    path = out_dir / "fig3_mean_grip_force_heatmap.png"
    _plot_heatmap(mean_pivot, plt, "Mean grip force (g) across the full test matrix",
                  "Mean grip force (g)", path, cmap="viridis")
    outputs.append(path)

    # --- Fig 4: repeatability heatmap ---
    cv_pivot = df.pivot_table(
        index=["finger_material", "padding"], columns="goal_current_raw",
        values="weight_g",
        aggfunc=lambda values: (values.std() / values.mean() * 100) if values.mean() else float("nan"),
        observed=True,
    ).reindex(columns=TEST_CURRENTS)
    path = out_dir / "fig4_repeatability_cv_heatmap.png"
    _plot_heatmap(cv_pivot, plt,
                  f"Grip repeatability - coefficient of variation over {REPEATS} repeats\n"
                  "(lower = more consistent)",
                  "CV (%)", path, cmap="magma_r", value_fmt="{:.1f}")
    outputs.append(path)

    # --- Fig 5: combo ranking ---
    path = out_dir / "fig5_combination_ranking.png"
    _plot_combo_ranking(df, plt, path)
    outputs.append(path)

    return outputs
