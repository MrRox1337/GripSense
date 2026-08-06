"""
The benchmark test matrix and the bookkeeping that decides what is done.

Two tests share the same matrix, and each is tracked separately:

  grip force      how hard the gripper squeezes, read off a kitchen scale
  slip detection  whether a loaded grip is detected slipping, and how quickly

  4 finger materials x 4 paddings x 5 currents x 3 repeats = 240 readings each

Nothing here touches hardware, files or UI, so the completion rules can be
exercised directly in tests.
"""

FINGER_MATERIALS = ["PETG", "PLA", "ABS", "TPU"]
PADDINGS = ["No padding", "Rubber band", "Eraser", "Sponge"]
TEST_CURRENTS = [100, 105, 110, 115, 120]
REPEATS = 3

# The two tests, used as dict keys throughout the UI and storage.
GRIP_FORCE = "grip_force"
SLIP_DETECTION = "slip_detection"
TEST_KINDS = [GRIP_FORCE, SLIP_DETECTION]
TEST_KIND_LABELS = {
    GRIP_FORCE: "Grip force (kitchen scale)",
    SLIP_DETECTION: "Slip detection (present current)",
}

CSV_FIELDS = [
    "timestamp",
    "finger_material",
    "padding",
    "goal_current_raw",
    "goal_current_ma",
    "repeat",
    "weight_g",
    "present_position_ticks",
    "present_current_raw",
    "present_current_ma",
]

SLIP_CSV_FIELDS = [
    "timestamp",
    "finger_material",
    "padding",
    "goal_current_raw",
    "goal_current_ma",
    "repeat",
    # Whether the current collapse was seen at all before the watch timed out.
    "slip_detected",
    # Seconds from the start of the watch to the first sample of the drop --
    # not to the sample that confirmed it, so the confirmation delay does not
    # inflate the measurement.
    "detection_time_s",
    "baseline_current_raw",
    "baseline_current_ma",
    "slip_current_raw",
    "slip_current_ma",
    "drop_raw",
    "drop_percent",
    "position_at_slip_ticks",
    "position_shift_ticks",
    "watch_duration_s",
    "samples",
]

CSV_FIELDS_BY_KIND = {
    GRIP_FORCE: CSV_FIELDS,
    SLIP_DETECTION: SLIP_CSV_FIELDS,
}

READINGS_PER_COMBO = len(TEST_CURRENTS) * REPEATS
TOTAL_COMBOS = len(FINGER_MATERIALS) * len(PADDINGS)


def all_combos():
    """Every (finger material, padding) pairing under test."""
    return [(finger, padding) for finger in FINGER_MATERIALS for padding in PADDINGS]


def recorded_counts(rows, finger, padding):
    """
    Map goal current -> number of readings already captured for a combo.

    Both tests key their rows on finger material, padding and goal current, so
    this and the completion helpers below work on either dataset - pass the
    rows of whichever CSV is being tracked.
    """
    counts = {current: 0 for current in TEST_CURRENTS}
    for row in rows:
        if row["finger_material"] != finger or row["padding"] != padding:
            continue
        try:
            current = int(row["goal_current_raw"])
        except (TypeError, ValueError):
            continue
        if current in counts:
            counts[current] += 1
    return counts


def combo_is_complete(rows, finger, padding):
    counts = recorded_counts(rows, finger, padding)
    return all(counts[current] >= REPEATS for current in TEST_CURRENTS)


def completed_combos(rows):
    return {
        combo for combo in all_combos()
        if combo_is_complete(rows, *combo)
    }
