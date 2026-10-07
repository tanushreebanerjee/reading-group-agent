"""Append-only meeting event log (events.jsonl). log.md is built from it after the meeting.

Event kinds:
  meeting_start {mode, source, paper, brief, config}
  answer        {id, t, question, q_start, q_end, text, latency_s, first_token_s, cited, sources}
  trigger       {id, t, trigger, confidence, reason, outcome}
                 outcome: raised | below_threshold | cooldown | duplicate | invalid
  hand          {id, t, trigger_id, trigger, confidence, reason, text, status, status_t}
                 status: revealed | dismissed | ignored (set when it changes)
  trigger_skip  {t, why}
  meeting_end   {t}
"""
from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path


def new_meeting_dir(meetings_dir: str | Path, date: dt.date | None = None) -> Path:
    """meetings/<YYYY-MM-DD>, or <YYYY-MM-DD>_2, _3 ... if that day already has a meeting."""
    base = Path(meetings_dir)
    day = (date or dt.date.today()).isoformat()
    d, n = base / day, 1
    while d.exists() and any(d.iterdir()):
        n += 1
        d = base / f"{day}_{n}"
    d.mkdir(parents=True, exist_ok=True)
    return d


class EventLog:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._fh = open(self.path, "a")

    def write(self, kind: str, **data) -> dict:
        ev = {"kind": kind, "wall": time.time(), **data}
        self._fh.write(json.dumps(ev) + "\n")
        self._fh.flush()
        return ev

    def close(self) -> None:
        self._fh.close()


def read_events(path: str | Path) -> list[dict]:
    out = []
    with open(path) as f:
        for line in f:
            if line.strip():
                out.append(json.loads(line))
    return out
