"""
The settle rule, as arithmetic over a stream of position samples.

In current-based position control "stopped" is not "arrived": the fingers stall
wherever the object is, so a move is finished when its position stops changing
rather than when it matches the goal. That is the whole rule, and it is applied
in two places - the blocking move in motion.py, and the monitor thread in api.py
resolving a move that was issued with wait=False.

Nothing here reads a servo or sleeps. The caller does the I/O and hands the
numbers over, which is what makes the rule testable on a list of positions
rather than only against hardware - the same arrangement as slipwatch.py.

Only the decision lives here. How often to sample, how long to wait before
giving up and how long to let a move start are the caller's, and genuinely
differ between the two: see motion.py.
"""

SETTLE_TOLERANCE_TICKS = 3     # movement below this counts as stopped
SETTLE_STABLE_SAMPLES = 5      # consecutive stable samples before "settled"

__all__ = ["SETTLE_STABLE_SAMPLES", "SETTLE_TOLERANCE_TICKS", "SettleTracker"]


class SettleTracker:
    """
    Counts consecutive position samples that barely moved.

    Lifecycle: reset() before a move, then feed() every sample until it returns
    True. One tracker belongs to one move - watching two at once needs two.
    """

    def __init__(self, tolerance_ticks=SETTLE_TOLERANCE_TICKS,
                 stable_samples=SETTLE_STABLE_SAMPLES):
        self.tolerance_ticks = int(tolerance_ticks)
        self.stable_samples = int(stable_samples)
        self.reset()

    def reset(self):
        """Forget the run in progress. A new command does this."""
        self.last_position = None
        self.stable = 0

    @property
    def settled(self):
        """True once enough consecutive samples have been close together."""
        return self.stable >= self.stable_samples

    def feed(self, position):
        """
        One position sample, in raw ticks. Returns True once settled.

        The first sample after a reset only establishes the baseline - there is
        nothing to compare it against - so a move needs stable_samples + 1
        samples at the very earliest.

        Feed only samples that were actually read: a failed read is not a
        sample, and must not be allowed to abandon the run in progress.
        """
        if (self.last_position is not None
                and abs(position - self.last_position) <= self.tolerance_ticks):
            self.stable += 1
        else:
            self.stable = 0
        self.last_position = position
        return self.settled
