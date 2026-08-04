"""
CSV persistence for benchmark readings.

Readings are appended one at a time as they are captured, so an aborted run
never loses what was already measured and can be resumed later.
"""

import csv
from pathlib import Path

from .matrix import CSV_FIELDS


def read_rows(csv_path):
    """Return all recorded rows as dicts (empty list if nothing recorded yet)."""
    path = Path(csv_path)
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def append_row(row, csv_path):
    """Append a single reading, writing the header if the file is new."""
    path = Path(csv_path)
    is_new = not path.exists() or path.stat().st_size == 0
    with open(path, "a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow(row)
