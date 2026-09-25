"""
An optional recording of one slip watch, for plotting what the rule saw.

The slip verdict is a single event, but the evidence for it is a curve: a
holding plateau, a threshold drawn below it, a collapse when the object leaves,
and the confirmation run that turns that collapse into a verdict. SlipWatch
keeps none of this - it consumes each sample and forgets it, which is what makes
it testable on a list of numbers. This module is the opposite trade, made
explicitly and off by default: hold the samples so a figure can be drawn from
them, at the cost of memory proportional to how long the watch runs.

Nothing here reads a servo or sleeps either. api.py does the I/O and hands the
numbers over, exactly as it does for slipwatch and settle.

Recording is per-watch. A new baseline starts a new trace, so the object you
hold after a slip fires is the trace of the watch that fired, until the next
one arms.
"""

import csv
import statistics
import threading

__all__ = ["BASELINE", "WATCH", "SlipTrace"]

BASELINE = "baseline"   # the samples the holding median is taken from
WATCH = "watch"         # the samples the threshold is applied to

# 25 s of timeout at a 5 ms poll is 5000 samples; this leaves generous room
# above that for a slower bus or a longer configured timeout.
DEFAULT_MAX_SAMPLES = 20000


class SlipTrace:
    """
    Timestamped current samples for one watch, plus what the rule made of them.

    Lifecycle: start() when a baseline begins, add() per sample, arm() once the
    threshold is known, finish() when a verdict lands. Full traces stop growing
    rather than evicting, so the plateau at the start is never the part lost.
    """

    def __init__(self, max_samples=DEFAULT_MAX_SAMPLES):
        self.max_samples = int(max_samples)
        self._lock = threading.Lock()
        self.start()

    def start(self):
        """Begin a new recording, discarding any previous one."""
        with self._lock:
            self._samples = []      # (t_seconds_from_start, current_raw, phase)
            self._t0 = None
            self.baseline = None
            self.threshold = None
            self.confirm_samples = None
            self.event = None
            self.truncated = False

    def add(self, now, current_raw, phase=WATCH):
        """Record one sample. `now` is any monotonic clock; t0 is the first."""
        with self._lock:
            if len(self._samples) >= self.max_samples:
                self.truncated = True
                return
            if self._t0 is None:
                self._t0 = now
            self._samples.append((now - self._t0, float(current_raw), phase))

    def arm(self, baseline, threshold, confirm_samples=None):
        """Record the holding median, the drop threshold, and the run length."""
        with self._lock:
            self.baseline = None if baseline is None else float(baseline)
            self.threshold = None if threshold is None else float(threshold)
            if confirm_samples is not None:
                self.confirm_samples = int(confirm_samples)

    def finish(self, event):
        """Record the verdict. None means the watch ended without a slip."""
        with self._lock:
            self.event = event

    # ------------------------------------------------------------------
    # Reading back
    # ------------------------------------------------------------------
    @property
    def samples(self):
        """A copy, so a caller can iterate while the monitor thread records."""
        with self._lock:
            return list(self._samples)

    def __len__(self):
        with self._lock:
            return len(self._samples)

    def periods(self, phase=WATCH):
        """
        Intervals between consecutive samples, in seconds.

        This is how the sampling rate is measured, and it needs no timer or
        counter of its own: each sample is already stamped with the clock the
        loop read, so the intervals between them are the loop's actual period.
        A counter reset once a second would answer the same question one
        integer at a time, and would hide the outlier that matters - a single
        stalled read shows up here and cannot show up there.

        Pass phase=None for every sample; the default is the watch, since that
        is the rate a detection time is resolved at.
        """
        with self._lock:
            times = [t for t, _value, ph in self._samples
                     if phase is None or ph == phase]
        return [b - a for a, b in zip(times, times[1:])]

    @property
    def rate_hz(self):
        """
        Measured sampling rate, from the median interval between samples.

        Median rather than mean: one slow read should not be allowed to drag
        the reported rate down, and one fast one should not flatter it.
        """
        intervals = self.periods(WATCH) or self.periods(None)
        if not intervals:
            return None
        median = statistics.median(intervals)
        return None if median <= 0 else 1.0 / median

    def timing_summary(self):
        """Period and rate as a dict, for logging or a figure annotation."""
        intervals = self.periods(WATCH) or self.periods(None)
        if not intervals:
            return None
        return {
            "samples": len(intervals) + 1,
            "median_period_s": statistics.median(intervals),
            "min_period_s": min(intervals),
            "max_period_s": max(intervals),
            "rate_hz": self.rate_hz,
        }

    def confirming_indices(self):
        """
        Indices of the run of dropped samples that declared the slip.

        Recovered from the trace alone, by the rule SlipWatch applies: the first
        run of `confirm_samples` consecutive watch samples below the threshold.
        Deliberately not anchored to `event.detection_time_s` - that is measured
        from arm(), while trace time starts at the first baseline sample, so the
        two origins differ by the length of the baseline window.

        Returns an empty list when no slip was recorded, the threshold was never
        usable, or no run reached the required length - an isolated dip is not
        a confirmation and is not reported as one.
        """
        with self._lock:
            if self.event is None or not self.threshold or self.threshold <= 0:
                return []
            needed = self.confirm_samples or 1
            run = []
            for i, (_t, value, phase) in enumerate(self._samples):
                if phase != WATCH:
                    continue
                if value < self.threshold:
                    run.append(i)
                    if len(run) >= needed:
                        return run
                else:
                    run = []
            return []

    def save_csv(self, path):
        """
        Write the trace as one row per sample, with the constants repeated.

        Repeating baseline and threshold on every row keeps the file readable by
        anything that plots a CSV without needing a second header to be parsed.
        """
        rows = self.samples
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow([
                "t_s", "current_raw", "phase", "baseline_raw", "threshold_raw",
                "detection_time_s", "confirming",
            ])
            confirming = set(self.confirming_indices())
            detection = getattr(self.event, "detection_time_s", "")
            for i, (t, value, phase) in enumerate(rows):
                writer.writerow([
                    f"{t:.6f}", f"{value:.1f}", phase,
                    "" if self.baseline is None else f"{self.baseline:.1f}",
                    "" if self.threshold is None else f"{self.threshold:.1f}",
                    detection, int(i in confirming),
                ])
        return path
