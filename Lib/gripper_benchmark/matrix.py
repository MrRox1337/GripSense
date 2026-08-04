"""
The benchmark test matrix and the bookkeeping that decides what is done.

  4 finger materials x 4 paddings x 5 currents x 3 repeats = 240 readings

Nothing here touches hardware, files or UI, so the completion rules can be
exercised directly in tests.
"""

FINGER_MATERIALS = ["PETG", "PLA", "ABS", "TPU"]
PADDINGS = ["No padding", "Rubber band", "Eraser", "Sponge"]
TEST_CURRENTS = [100, 105, 110, 115, 120]
REPEATS = 3

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

READINGS_PER_COMBO = len(TEST_CURRENTS) * REPEATS
TOTAL_COMBOS = len(FINGER_MATERIALS) * len(PADDINGS)


def all_combos():
    """Every (finger material, padding) pairing under test."""
    return [(finger, padding) for finger in FINGER_MATERIALS for padding in PADDINGS]


def recorded_counts(rows, finger, padding):
    """Map goal current -> number of readings already captured for a combo."""
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
