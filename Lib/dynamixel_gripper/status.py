"""
What the gripper reports about itself.

Three plain value types with no behaviour and no dependencies, kept apart from
the API that produces them so a caller can import the vocabulary without
importing the machinery - and so the machinery reads as one thing rather than
one thing preceded by ninety lines of declarations.
"""

from dataclasses import dataclass
from enum import StrEnum

__all__ = ["GripStatus", "GripperState", "SlipEvent"]


class GripStatus(StrEnum):
    """
    What the gripper is doing, and how the last grasp turned out.

    A StrEnum, so `api.status == "ok"` works for callers who never import this.
    """

    IDLE = "idle"      # torque off, or a move that finished holding nothing
    MOVING = "moving"  # a commanded move is in flight
    OK = "ok"          # holding an object
    SLIP = "slip"      # was holding; present current collapsed. Latched.
    MISS = "miss"      # closed all the way to MIN OPEN, nothing caught


@dataclass(frozen=True)
class GripperState:
    """A snapshot of everything the caller might want in one read."""

    status: str
    position_ticks: int | None
    position: float | None          # normalised: 0.0 closed, 1.0 open
    current_raw: int | None
    current_ma: float | None
    grip_strength: float            # normalised: 0.0 weakest, 1.0 firmest
    grip_current_raw: int
    enabled: bool


@dataclass(frozen=True)
class SlipEvent:
    """
    What the current did when a grip was lost.

    SlipWatch fills in everything except the position fields, which cost a
    round trip it deliberately does not make; the API adds those.
    """

    detection_time_s: float         # from the start of the watch to the FIRST
                                    # dropped sample, not the confirming one
    baseline_raw: float
    slip_raw: float
    threshold_raw: float
    position_ticks: int | None = None
    position_shift: int | None = None   # ticks the fingers closed as it escaped
