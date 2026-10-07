"""Queue of raised hands. Content is hidden until someone reveals it."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Hand:
    id: str
    t: float                  # meeting time raised
    trigger_id: str
    trigger: str
    confidence: float
    reason: str
    text: str                 # the prepared interjection (not shown until revealed)
    status: str = "pending"   # pending | revealed | dismissed | ignored
    status_t: float | None = None


class HandQueue:
    def __init__(self):
        self.hands: list[Hand] = []

    @property
    def pending(self) -> list[Hand]:
        return [h for h in self.hands if h.status == "pending"]

    def add(self, hand: Hand) -> None:
        self.hands.append(hand)

    def head(self) -> Hand | None:
        """Oldest pending hand: the one Reveal/Dismiss acts on."""
        p = self.pending
        return p[0] if p else None

    def resolve(self, status: str, now: float) -> Hand | None:
        h = self.head()
        if h:
            h.status, h.status_t = status, now
        return h

    def close(self, now: float) -> list[Hand]:
        """Meeting over: anything still pending was ignored."""
        left = self.pending
        for h in left:
            h.status, h.status_t = "ignored", now
        return left
