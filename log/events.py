"""Append-only meeting event log (events.jsonl). log.md is built from it after the meeting.

Event kinds:
  meeting_start {mode, source, paper, brief, config}
  answer        {id, t, question, q_start, q_end, text, latency_s, first_token_s, cited, sources}
  trigger       {id, t, trigger, confidence, reason, outcome}
                 outcome: raised | below_threshold | cooldown | duplicate | ungrounded | stale |
                          already_said | invalid
  hand          {id, t, trigger_id, trigger, confidence, reason, text, status, status_t}
                 status: revealed | dismissed | ignored | expired (set when it changes)
  hand_text     {id, t, text, cited, prep_s}    the hand's text, prepared after it was raised
  trigger_skip  {t, why}
  setting       {t, key, value}             changed from the control page
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
    """Append-only JSONL; reopens per write (see TranscriptStore for why) and rewrites on close."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.events: list[dict] = []

    def write(self, kind: str, **data) -> dict:
        ev = {"kind": kind, "wall": time.time(), **data}
        self.events.append(ev)
        with open(self.path, "a") as f:
            f.write(json.dumps(ev) + "\n")
        return ev

    def close(self) -> None:
        with open(self.path, "w") as f:
            f.writelines(json.dumps(ev) + "\n" for ev in self.events)


ICLOUD_DIRS = ("Desktop", "Documents")


def icloud_synced(path: str | Path) -> bool:
    """True if `path` is under ~/Desktop or ~/Documents while iCloud Desktop & Documents sync is on."""
    p = Path(path).resolve()
    home = Path.home()
    cloud = home / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
    for d in ICLOUD_DIRS:
        if (cloud / d).exists() and (p == home / d or (home / d) in p.parents):
            return True
    return False


def read_events(path: str | Path) -> list[dict]:
    out = []
    with open(path) as f:
        for line in f:
            if line.strip():
                out.append(json.loads(line))
    return out
