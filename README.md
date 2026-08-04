# Slip-Aware Gripper Control & Grip Quality Benchmark

Control software and an experimental benchmarking rig for a slip-aware robotic
gripper actuated by a **Dynamixel XM430-W210-T** servo, built for the PDE4445
module at Middlesex University.

**Author:** Aman Mishra
**Module:** PDE4445 · Middlesex University
**Platform:** Windows 11 · Python 3.13

---

## Description

The gripper runs in **current-based position control** (Operating Mode 5),
where the servo drives toward a commanded position but never exceeds a
commanded current. Because current is proportional to torque, that current
ceiling becomes a grip-force ceiling: the fingers close until they stall
against an object rather than crushing it. This is what makes the gripper
"slip-aware" — grip force is a tunable parameter rather than a consequence of
how far the fingers were told to travel.

The repository contains two applications and the shared libraries behind them:

| Application                | Purpose                                                                                                                                                                 |
| -------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Manual control GUI**     | Three sliders (position, goal current, profile velocity) with a live servo readout. Used for setup, calibration and ad-hoc testing.                                     |
| **Grip quality benchmark** | A guided experiment that measures how much grip force each finger material and padding combination produces at each current limit, then generates a statistical report. |

Both are plain Python with Tkinter — **no ROS2**.

### The experiment

Grip force is measured by closing the gripper onto a kitchen scale and
recording the reading. The full matrix is:

| Variable           | Levels                                         |
| ------------------ | ---------------------------------------------- |
| Finger material    | PETG, PLA, ABS, TPU                            |
| Padding            | No padding, Rubber band, Eraser, Sponge        |
| Goal current limit | 100, 105, 110, 115, 120 raw units (269–323 mA) |
| Repeats            | 3 per cell                                     |

**4 × 4 × 5 × 3 = 240 readings.**

---

## Repository layout

```
pickerbot_gripper/
├── Config/
│   ├── gripper_config.yaml         Port, servo ID, travel and limit settings
│   └── xm430_control_table.yaml    Register addresses + unit conversion scales
├── Lib/
│   ├── gripper_settings.py         Resolves config paths; the only module that
│   │                               knows the repository layout
│   ├── gripper_control_ui.py       Tk app for the manual control GUI
│   ├── dynamixel_gripper/          Reusable servo driver (layout-agnostic)
│   │   ├── __init__.py
│   │   └── gripper.py
│   └── gripper_benchmark/
│       ├── __init__.py
│       ├── matrix.py               Test grid + "what counts as complete"
│       ├── storage.py              Results CSV read/append
│       ├── runner.py               Open/close/measure sequence (no UI code)
│       ├── ui.py                   Tk configuration panel + scale popup
│       └── report.py               Statistics table and figures
├── Scripts/
│   ├── gripper_control_gui.py      Entry point: manual control
│   └── gripper_benchmark.py        Entry point: benchmark + report
├── requirements.txt
└── README.md
```

Generated at runtime (not present on a fresh clone):

```
benchmark_results.csv       One row per reading, appended as captured
benchmark_report/           Statistics table + five figures
```

> **Note on directory names:** `Lib/` and `Scripts/` are also the directory
> names used inside a Python virtual environment. The `.gitignore` therefore
> excludes the environment as `.venv/` and never as bare `Lib/` or `Scripts/`,
> which would silently exclude the source code.

---

## Hardware requirements

- Dynamixel **XM430-W210-T** servo
- U2D2 (or equivalent) USB-to-TTL interface
- 12 V power supply for the servo
- Kitchen scale (grams) for the benchmark
- 3D-printed fingers in PETG, PLA, ABS and TPU
- Padding samples: rubber band, eraser, sponge

Default communication settings — change in `Config/gripper_config.yaml`:

| Setting   | Value     |
| --------- | --------- |
| Protocol  | 2.0       |
| Port      | `COM5`    |
| Baud rate | 1,000,000 |
| Servo ID  | 1         |

---

## Setup instructions

**1. Clone the repository**

```powershell
git clone <repository-url>
cd pickerbot_gripper
```

**2. Create and activate a virtual environment**

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

**3. Install dependencies**

```powershell
pip install -r requirements.txt
```

Tkinter ships with the standard library and needs no installation. `pandas`
and `matplotlib` are used only for report generation — the gripper itself
runs without them.

**4. Confirm your COM port**

Find the U2D2's port in Device Manager (Ports → USB Serial Port) and update
`Config/gripper_config.yaml` if it is not `COM5`:

```yaml
port:
  device: COM5
  baudrate: 1000000
  protocol_version: 2.0
  dxl_id: 1
```

**5. Verify the installation**

```powershell
python Scripts\gripper_control_gui.py
```

The window should open and the live readout should show a changing present
position when you move the fingers by hand.

---

## Definitions

| Term                               | Meaning                                                                                                                                                                                                                       |
| ---------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Tick**                           | The servo's raw position unit. 1 tick = 0.087891°, so a full 360° turn is 4096 ticks.                                                                                                                                         |
| **Raw current unit**               | The servo's current unit. 1 unit ≈ 2.69 mA. The benchmark's 100–120 range is therefore ≈ 269–323 mA.                                                                                                                          |
| **Goal Position**                  | Where the servo is told to move (register 116).                                                                                                                                                                               |
| **Present Position**               | Where the servo actually is (register 132).                                                                                                                                                                                   |
| **Goal Current**                   | The current ceiling the servo will not exceed while moving (register 102). In current-based position control this is the grip-force limit.                                                                                    |
| **Profile Velocity**               | How fast the servo travels toward its goal (register 112). 1 unit ≈ 0.229 rev/min. Held constant during benchmarking so force is the only variable.                                                                           |
| **Current-based position control** | Operating Mode 5: position control with a hard current ceiling. The fingers stall against an object instead of stripping the gearbox.                                                                                         |
| **Torque enable**                  | Register 64. With torque off the servo is back-drivable and ignores position commands.                                                                                                                                        |
| **Max open**                       | The fully open finger position, used as the travel reference point.                                                                                                                                                           |
| **Min open**                       | The closed limit, 270° of travel below max open (3072 ticks).                                                                                                                                                                 |
| **Re-zero**                        | Taking the _current_ present position to be max open and re-deriving min open from it. The servo's multi-turn position is whatever the previous session left behind, so the travel window is re-anchored rather than trusted. |
| **Finger material**                | The filament a finger pair is printed in: PETG, PLA, ABS or TPU.                                                                                                                                                              |
| **Padding**                        | The compliant layer bonded to the finger face: none, rubber band, eraser or sponge.                                                                                                                                           |
| **Combination (combo)**            | One finger material paired with one padding — 16 in total. A benchmark run covers one combination across all five currents.                                                                                                   |
| **Repeat**                         | One of the three measurements taken at a given combination and current.                                                                                                                                                       |
| **CV (%)**                         | Coefficient of variation, `SD ÷ mean × 100`. A repeatability measure: lower means the grip is more consistent between repeats.                                                                                                |

---

## Usage examples

### Manual control GUI

```powershell
python Scripts\gripper_control_gui.py
```

Torque starts **disabled** — nothing moves until you press **Enable Torque**.
The Goal Position register is primed to max open at startup, so the first
torque-enable opens the gripper rather than jumping to a stale target.

Set the current slider _before_ enabling torque when handling anything
fragile. **EMERGENCY STOP** disables torque immediately and makes the fingers
back-drivable.

### Running the benchmark

```powershell
python Scripts\gripper_benchmark.py
```

1. **Check the derived limits** shown in the Hardware panel. On startup the
   present position is taken to _be_ max open. If the gripper was left closed,
   open it by hand and press **Re-zero**.
2. **Select a combination** from the 4 × 4 grid. Completed cells read `done`
   and cannot be selected again.
3. **Start Run.** Torque is enabled and the gripper sweeps all five current
   limits, three closes each.
4. **After each close, enter the scale reading** in the popup and press
   Submit (or Enter). The gripper reopens and continues.
5. When the combination finishes, torque is disabled automatically. Swap the
   fingers or padding and select the next cell.

Each reading is written to `benchmark_results.csv` the moment you submit it.
Aborting mid-run keeps everything already captured, and re-selecting that
combination resumes from the exact repeat where it stopped — no duplicates.

Once all 16 combinations are complete, the report is generated automatically.

### Generating the report manually

```powershell
python Scripts\gripper_benchmark.py --report
```

Writes to `benchmark_report/`:

| File                                    | Contents                                                       |
| --------------------------------------- | -------------------------------------------------------------- |
| `summary_statistics.csv` / `.png`       | N, mean, SD, min, median, max, range and CV% for all 80 cells  |
| `fig1_weight_vs_current_by_finger.png`  | Force vs current, one panel per material, one line per padding |
| `fig2_weight_vs_current_by_padding.png` | The same data transposed                                       |
| `fig3_mean_grip_force_heatmap.png`      | Mean force across the full matrix                              |
| `fig4_repeatability_cv_heatmap.png`     | CV% — which settings grip most consistently                    |
| `fig5_combination_ranking.png`          | Combinations ranked by mean grip force                         |

### Results CSV schema

| Column                                      | Description                                |
| ------------------------------------------- | ------------------------------------------ |
| `timestamp`                                 | ISO timestamp of the reading               |
| `finger_material`                           | PETG / PLA / ABS / TPU                     |
| `padding`                                   | No padding / Rubber band / Eraser / Sponge |
| `goal_current_raw`, `goal_current_ma`       | Commanded current limit                    |
| `repeat`                                    | 1–3                                        |
| `weight_g`                                  | Scale reading entered by the operator      |
| `present_position_ticks`                    | Where the fingers stalled                  |
| `present_current_raw`, `present_current_ma` | Actual current drawn at stall              |

### Driving the gripper from your own script

The driver is importable, so a slip-detection or pick-and-place script can
reuse it without any UI:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "Lib"))

import gripper_settings as settings

gripper = settings.connect()          # opens the port, sets Operating Mode 5
try:
    gripper.set_goal_current(110)     # ~296 mA grip ceiling
    gripper.set_profile_velocity(settings.VELOCITY_MAX)
    gripper.set_goal_position(settings.MIN_OPEN_POSITION)
    gripper.enable_torque()

    print(gripper.read_present_position(), gripper.read_present_current())
finally:
    gripper.close()                   # disables torque, closes the port
```

`DynamixelGripper` serialises every read and write behind a lock, so a polling
thread and a command thread can share one instance safely.

### Running the benchmark sequence headlessly

`BenchmarkRunner` contains no UI code — supply callbacks and it will drive the
sequence from anywhere:

```python
from gripper_benchmark import BenchmarkRunner, append_row, recorded_counts

runner = BenchmarkRunner(
    gripper,
    travel_ticks=settings.TRAVEL_TICKS,
    profile_velocity=settings.BENCHMARK_PROFILE_VELOCITY,
    on_status=print,
    on_reading=lambda row: append_row(row, settings.RESULTS_CSV),
    ask_weight=lambda finger, pad, current, rep: float(input("Scale (g): ")),
)
runner.rezero()
runner.run_combo("PETG", "Sponge", recorded_counts([], "PETG", "Sponge"))
```

---

## FAQ

**The GUI opens but the gripper does not move.**
Torque starts disabled by design, so an accidental launch cannot slam the
fingers shut. Press **Enable Torque**.

**"Failed to open port COM5".**
The port name is wrong, the U2D2 is unplugged, or another program is holding
the port — including a second copy of this application. Check Device Manager
and update `Config/gripper_config.yaml`.

**The gripper closes too far and jams.**
The benchmark assumes the present position at startup _is_ max open, and
derives the closed limit 270° below it. Launching with the gripper already
closed puts that limit past the mechanical stops. Open the fingers by hand and
press **Re-zero**, then check the limits shown in the Hardware panel.

**Can I resume an aborted benchmark run?**
Yes. Readings are written to the CSV as they are captured. Re-select the
combination, confirm the resume prompt, and it continues from the repeat where
it stopped without duplicating rows.

**Can I change the materials, paddings or current levels?**
Edit `Lib/gripper_benchmark/matrix.py`. Everything else — the UI grid, the
completion logic, the figures — derives from those lists. Existing CSV rows for
removed levels are simply ignored.

**Can I use a different Dynamixel servo?**
Copy `Config/xm430_control_table.yaml`, edit the register addresses and unit
scales for your model, and point `CONTROL_TABLE_PATH` in
`Lib/gripper_settings.py` at the copy. The driver reads everything from that
file, so no code changes are needed for another X-series model.

**Pylance reports "import could not be resolved".**
The scripts add `Lib/` to `sys.path` at runtime, which a static analyser never
executes. `.vscode/settings.json` tells Pylance where to look — reload the
window (`Ctrl+Shift+P` → Developer: Reload Window) after cloning.

**Why a kitchen scale rather than a load cell?**
It measures the quantity of interest — the force the fingers apply — with
equipment available for the module, at a resolution well below the differences
between materials. The operator-entered reading is the reason each close pauses
for a popup.

**Why not ROS2?**
Plain Python was a requirement of the brief. The driver has no framework
dependencies, so a ROS2 node could wrap it unchanged.

**Why is `benchmark_results.csv` committed rather than ignored?**
It holds 240 hand-recorded measurements that cannot be regenerated without
repeating the entire experiment. The report is derived from it and is cheap to
rebuild, but the raw data is not.

---

## Acknowledgements

- **ROBOTIS** for the [Dynamixel SDK](https://github.com/ROBOTIS-GIT/DynamixelSDK)
  and the XM430-W210-T control table documentation.
- **Middlesex University**, module **PDE4445**, for the project brief and
  laboratory hardware.
- The [pandas](https://pandas.pydata.org/),
  [Matplotlib](https://matplotlib.org/) and [PyYAML](https://pyyaml.org/)
  projects, which the analysis and configuration layers are built on.

---

Copyright © 2026 `Aman Mishra`. All rights reserved.
