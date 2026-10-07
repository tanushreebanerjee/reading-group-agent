"""Fill `helpful:` labels in a synthetic-run log.md from the fixture's ground truth.

python tests/label_synthetic.py meetings/e2e_phase5_.../log.md

A raised hand or suppressed trigger is labelled `yes` if its type matches a
planted event (contradiction / gap) and it fired within 90 s after that event
started; otherwise `no`. Answers are labelled `yes` if they contain the
expected terms. This stands in for the group's manual labels so the tuning
report can be exercised end to end.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from log.events import read_events  # noqa: E402
from tests.fixtures.make_synthetic import parse_script  # noqa: E402

FIX = ROOT / "tests" / "fixtures"
MARK_RE = re.compile(r"<!--\s*(.*?)\s*-->")


def labels_for(meeting_dir: Path) -> dict[str, str]:
    meta, _ = parse_script(FIX / "synthetic_script.md")
    turns = {t["turn"]: t for t in json.loads((FIX / "synthetic_turns.json").read_text())}
    planted = [(e["kind"], turns[e["turn"]]["start"], [t.split("|") for t in e.get("match_terms", [])])
               for e in meta["events"] if e["kind"] in ("contradiction", "gap")]
    asks = [(turns[e["turn"]], e["expect_terms"]) for e in meta["events"] if e["kind"] == "ask"]
    out = {}
    for ev in read_events(meeting_dir / "events.jsonl"):
        if ev["kind"] in ("hand", "trigger"):
            good = any(ev["trigger"] == k and t0 <= ev["t"] <= t0 + 90 and
                       all(any(a.lower() in (ev.get("quote", "") + " " + ev["reason"]).lower() for a in g) for g in terms)
                       for k, t0, terms in planted)
            out[ev["id"]] = "yes" if good else "no"
        elif ev["kind"] == "answer":
            good = any(t["start"] - 3 <= ev["q_start"] <= t["end"] + 3 and
                       all(any(alt.lower() in ev["text"].lower() for alt in term.split("|")) for term in terms) for t, terms in asks)
            out[ev["id"]] = "yes" if good else "no"
    return out


def apply_labels(log_md: Path, labels: dict[str, str]) -> int:
    lines = log_md.read_text().splitlines()
    current, n = None, 0
    for i, line in enumerate(lines):
        m = MARK_RE.search(line)
        if m:
            attrs = dict(kv.split("=", 1) for kv in m.group(1).split() if "=" in kv)
            current = attrs.get("id")
        elif line.strip().lower().startswith("helpful:") and current in labels:
            lines[i] = f"helpful: {labels[current]}"
            n += 1
            current = None
    log_md.write_text("\n".join(lines) + "\n")
    return n


def main():
    log_md = Path(sys.argv[1])
    n = apply_labels(log_md, labels_for(log_md.parent))
    print(f"labelled {n} entries in {log_md}")


if __name__ == "__main__":
    main()
