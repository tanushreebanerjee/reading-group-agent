"""Precision per trigger type and threshold, from labelled log.md files.

python -m log.tune meetings/            # all meetings
python -m log.tune meetings/2026-10-07  # one meeting

Reads every raised hand and every suppressed trigger with a `helpful: yes|no`
label. For each trigger type and candidate threshold t, counts entries with
confidence >= t ("would have fired"), and precision = helpful / labelled.
"""
from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

MARK_RE = re.compile(r"<!--\s*(.*?)\s*-->")
HELP_RE = re.compile(r"^helpful:\s*(\S*)", re.I | re.M)


@dataclass
class Labelled:
    meeting: str
    id: str
    kind: str
    type: str
    conf: float
    status: str
    helpful: bool | None


def parse_log(path: Path) -> list[Labelled]:
    text = path.read_text()
    marks = list(MARK_RE.finditer(text))
    out = []
    for i, m in enumerate(marks):
        attrs = dict(kv.split("=", 1) for kv in m.group(1).split() if "=" in kv)
        if attrs.get("kind") not in ("hand", "trigger"):
            continue
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        h = HELP_RE.search(text, m.end(), end)
        val = (h.group(1).lower() if h else "")
        helpful = True if val in ("yes", "y") else False if val in ("no", "n") else None
        out.append(Labelled(path.parent.name, attrs["id"], attrs["kind"], attrs.get("type", "?"),
                            float(attrs.get("conf", 0)), attrs.get("status") or attrs.get("outcome", ""), helpful))
    return out


def find_logs(target: Path) -> list[Path]:
    if target.is_file():
        return [target]
    return sorted(p for p in target.rglob("log.md") if not p.parent.name.startswith("."))


# Suppressed for reasons a threshold change would not affect: excluded from precision.
NOT_THRESHOLD = {"duplicate", "ungrounded", "invalid", "stale", "already_said"}


def report(entries: list[Labelled], thresholds: list[float], current: dict | None = None) -> str:
    lines = []
    other = [e for e in entries if e.status in NOT_THRESHOLD]
    labelled = [e for e in entries if e.helpful is not None and e.status not in NOT_THRESHOLD]
    lines.append(f"{len(entries)} trigger entries, {len(labelled)} labelled "
                 f"({sum(e.kind == 'hand' for e in entries)} raised hands, "
                 f"{sum(e.kind == 'trigger' for e in entries)} suppressed triggers)")
    for typ in sorted({e.type for e in entries}):
        es = [e for e in labelled if e.type == typ]
        cur = (current or {}).get(typ)
        lines.append("")
        lines.append(f"## {typ}" + (f"  (current threshold {cur})" if cur is not None else ""))
        lines.append(f"{'threshold':>10} {'fired':>6} {'helpful':>8} {'precision':>10}")
        for t in thresholds:
            fired = [e for e in es if e.conf >= t]
            good = sum(e.helpful for e in fired)
            prec = f"{good / len(fired):.0%}" if fired else "–"
            mark = "  <-" if cur is not None and abs(t - float(cur)) < 1e-9 else ""
            lines.append(f"{t:>10.2f} {len(fired):>6} {good:>8} {prec:>10}{mark}")
        unl = sum(1 for e in entries if e.type == typ and e.helpful is None and e.status not in NOT_THRESHOLD)
        if unl:
            lines.append(f"({unl} unlabelled {typ} entries ignored)")
        skipped = [e for e in other if e.type == typ]
        if skipped:
            by = {}
            for e in skipped:
                by[e.status] = by.get(e.status, 0) + 1
            lines.append("(not counted, suppressed regardless of threshold: "
                         + ", ".join(f"{n} {s}" for s, n in sorted(by.items())) + ")")
    return "\n".join(lines)


def main(argv=None):
    from core.config import load_config

    ap = argparse.ArgumentParser(prog="python -m log.tune")
    ap.add_argument("target", nargs="?", default=None, help="meetings dir, one meeting dir, or a log.md")
    ap.add_argument("--thresholds", default="0.5,0.6,0.7,0.75,0.8,0.85,0.9,0.95")
    args = ap.parse_args(argv)
    cfg = load_config()
    target = Path(args.target or cfg["paths"]["meetings_dir"])
    logs = find_logs(target)
    entries = [e for p in logs for e in parse_log(p)]
    print(f"{len(logs)} log file(s) under {target}")
    print(report(entries, [float(x) for x in args.thresholds.split(",")],
                 cfg.get("trigger", {}).get("thresholds")))


if __name__ == "__main__":
    main()
