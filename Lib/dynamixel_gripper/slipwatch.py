"""
The slip rule, as arithmetic over a stream of current samples.

While the fingers are stalled against a gripped object the servo holds close to
its goal current. If the object is loaded until it escapes, the fingers are
suddenly free to move, the torque they were producing is no longer opposed, and
present current falls sharply. That fall is the slip signal.

Nothing in here reads a servo or sleeps. The caller does the I/O and hands the
numbers over, which is what makes the detector testable on a list of samples
rather than only against hardware - and it is the same rule the benchmark's
SlipRunner applies, driven from the same `slip:` config block, so a result
observed on the rig means the same thing in a script.
"""

import statistics
import time

from .status import SlipEvent

__all__ = ["SlipWatch"]


class SlipWatch:
    """
    Baseline, threshold, and the confirmation run that turns a dip into a slip.

    Lifecycle: arm() once the grip has settled, then feed() every sample until
    it returns a SlipEvent. reset() abandons the watch, which is what a new
    command or a change of grip strength does.
    """

    def __init__(self, poll_interval=0.005, baseline_settle=1.0,
                 baseline_samples=20, drop_fraction=0.35, min_drop_raw=15,
                 confirm_samples=3, timeout=25.0):
        # Collection parameters. Not used here - the caller samples - but kept
        # with the rest of the slip tuning so there is one place to read it off.
        self.poll_interval = float(poll_interval)
        self.baseline_settle = float(baseline_settle)
        self.baseline_samples = int(baseline_samples)
        self.timeout = float(timeout)

        # Decision parameters.
        self.drop_fraction = float(drop_fraction)
        self.min_drop_raw = float(min_drop_raw)
        self.confirm_samples = int(confirm_samples)

        self.baseline = None
        self.threshold = None
        self.reset()

    @classmethod
    def from_config(cls, slip_config):
        """Build from a `slip:` block already merged over its defaults."""
        return cls(**{k: slip_config[k] for k in (
            "poll_interval", "baseline_settle", "baseline_samples",
            "drop_fraction", "min_drop_raw", "confirm_samples", "timeout",
        ) if k in slip_config})

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------
    def reset(self):
        """Forget the baseline and any run in progress."""
        self.baseline = None
        self.threshold = None
        self._run = 0
        self._first_drop_time = None
        self._first_drop_current = None
        self._watch_start = None

    @property
    def armed(self):
        """True once a baseline has been taken and a threshold derived."""
        return self.threshold is not None

    @property
    def detectable(self):
        """
        False when the hold is too weak for a drop to be distinguishable.

        If the holding current is at or below the absolute drop margin, the
        threshold works out at zero or less and no sample can ever fall under
        it - so a watch would time out looking exactly like a grip that never
        slipped. Worth saying out loud rather than letting it read as a
        genuine failure to slip.
        """
        return self.threshold is not None and self.threshold > 0

    # ------------------------------------------------------------------
    # The rule
    # ------------------------------------------------------------------
    def drop_threshold(self, baseline):
        """
        The current a sample must fall below to count as dropped.

        Both a proportional and an absolute margin apply, so a firm grip needs
        a proportionally large fall while a weak one still needs a real one.
        """
        return baseline - max(self.min_drop_raw, self.drop_fraction * baseline)

    def arm(self, samples, now=None):
        """
        Take the holding current from collected samples and derive a threshold.

        `samples` are current magnitudes; the median is used so a single spike
        during collection cannot set the baseline. Returns the threshold.
        """
        self.baseline = statistics.median(samples)
        self.threshold = self.drop_threshold(self.baseline)
        self._run = 0
        self._first_drop_time = None
        self._first_drop_current = None
        self._watch_start = time.monotonic() if now is None else now
        return self.threshold

    def feed(self, current, now=None):
        """
        One current magnitude. Returns a SlipEvent once the drop is confirmed,
        otherwise None.

        The event's detection time is measured to the FIRST dropped sample, not
        the one that confirmed the run: the confirmation delay is a property of
        this detector, not of the gripper, and should not be charged to the
        measurement. A single sample back above the threshold abandons the run,
        so a momentary dip is not a slip.
        """
        now = time.monotonic() if now is None else now
        if self._watch_start is None:
            self._watch_start = now

        if not self.detectable or current >= self.threshold:
            self._run = 0
            self._first_drop_time = None
            self._first_drop_current = None
            return None

        if self._run == 0:
            self._first_drop_time = now
            self._first_drop_current = current
        self._run += 1

        if self._run < self.confirm_samples:
            return None

        return SlipEvent(
            detection_time_s=self._first_drop_time - self._watch_start,
            baseline_raw=self.baseline,
            slip_raw=self._first_drop_current,
            threshold_raw=self.threshold,
        )
