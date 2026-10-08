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
    status: str = "pending"   # pending | revealed | dismissed | ignored | expired
    status_t: float | None = None


# Reveal/Dismiss act on the most important pending hand: a correction outranks an
# unanswered question, which outranks an extra point.
PRIORITY = {"contradiction": 0, "gap": 1, "point": 2}


class HandQueue:
    def __init__(self):
        self.hands: list[Hand] = []

    @property
    def pending(self) -> list[Hand]:
        return [h for h in self.hands if h.status == "pending"]

    def add(self, hand: Hand) -> None:
        self.hands.append(hand)

    def head(self) -> Hand | None:
        """The pending hand Reveal/Dismiss acts on: highest priority, then oldest."""
        p = self.pending
        return min(p, key=lambda h: (PRIORITY.get(h.trigger, 9), h.t)) if p else None

    def expire(self, now: float, ttl: dict) -> list[Hand]:
        """Drop pending hands older than their type's ttl (seconds; missing or 0 = never).
        A point about a topic the group has left is noise, so it lowers its hand."""
        out = []
        for h in self.pending:
            limit = float(ttl.get(h.trigger) or 0)
            if limit and now - h.t >= limit:
                h.status, h.status_t = "expired", now
                out.append(h)
        return out

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
