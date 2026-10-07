"""Transcript segments, JSONL storage, and rolling-window helpers."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


@dataclass
class Segment:
    start: float
    end: float
    text: str
    speaker: str | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_dict(cls, d: dict) -> "Segment":
        return cls(start=float(d["start"]), end=float(d["end"]), text=d["text"], speaker=d.get("speaker"))


def fmt_ts(t: float) -> str:
    t = max(0, int(t))
    return f"{t // 60:02d}:{t % 60:02d}"


def format_segments(segs: Iterable[Segment]) -> str:
    lines = []
    for s in segs:
        who = f"{s.speaker}: " if s.speaker else ""
        lines.append(f"[{fmt_ts(s.start)}] {who}{s.text.strip()}")
    return "\n".join(lines)


def read_jsonl(path: str | Path) -> list[Segment]:
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(Segment.from_dict(json.loads(line)))
    return out


def write_jsonl(path: str | Path, segs: Iterable[Segment]) -> None:
    with open(path, "w") as f:
        for s in segs:
            f.write(s.to_json() + "\n")


class TranscriptStore:
    """Rolling transcript kept in memory and appended to a JSONL file."""

    def __init__(self, path: str | Path | None = None):
        self.segments: list[Segment] = []
        self.path = Path(path) if path else None
        self._fh = open(self.path, "a") if self.path else None

    def add(self, seg: Segment) -> None:
        self.segments.append(seg)
        if self._fh:
            self._fh.write(seg.to_json() + "\n")
            self._fh.flush()

    def window(self, now: float, seconds: float) -> list[Segment]:
        return [s for s in self.segments if s.end >= now - seconds]

    def between(self, t0: float, t1: float) -> list[Segment]:
        return [s for s in self.segments if s.end >= t0 and s.start <= t1]

    def close(self) -> None:
        if self._fh:
            self._fh.close()
            self._fh = None
