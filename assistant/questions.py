"""Collect the question that follows the assistant's name until a pause."""
from __future__ import annotations

from dataclasses import dataclass, field

from core.transcript import Segment


@dataclass
class Question:
    start: float                    # meeting time the name was heard
    end: float                      # meeting time of the last word of the question
    parts: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(p for p in self.parts if p).strip()


class QuestionCollector:
    """State machine: idle -> collecting (after a name hit) -> ready (after `pause_s` of silence).

    Feed segments with add(); call poll(now, busy) periodically. poll returns a finished
    Question once `now >= last_end + pause_s` and the audio source isn't mid-utterance.
    """

    def __init__(self, pause_s: float = 1.5, max_s: float = 30.0):
        self.pause_s = pause_s
        self.max_s = max_s
        self.current: Question | None = None

    @property
    def collecting(self) -> bool:
        return self.current is not None

    def start(self, seg: Segment, rest: str) -> None:
        self.current = Question(start=seg.start, end=seg.end, parts=[rest])

    def add(self, seg: Segment) -> None:
        if self.current:
            self.current.parts.append(seg.text.strip())
            self.current.end = seg.end

    def poll(self, now: float, busy: bool = False) -> Question | None:
        q = self.current
        if not q:
            return None
        paused = now >= q.end + self.pause_s and not busy
        too_long = now - q.start >= self.max_s
        if paused or too_long:
            self.current = None
            return q
        return None
