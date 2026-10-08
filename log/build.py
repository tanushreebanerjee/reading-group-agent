"""Build meetings/<date>/log.md from events.jsonl + transcript.jsonl.

python -m log.build meetings/2026-10-07 [--no-summary]

Every answer, raised hand, and suppressed trigger gets transcript context, a
machine-readable marker, and a blank `helpful:` line for the group to fill in
(yes/no). log.tune reads those labels.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from core.transcript import Segment, fmt_ts, read_jsonl
from log.events import read_events

HELPFUL = "helpful: "


def _context(segs: list[Segment], t0: float, t1: float) -> str:
    segs = sorted(segs, key=lambda s: s.start)
    lines = [f"> [{fmt_ts(s.start)}] {s.speaker + ': ' if s.speaker else ''}{s.text.strip()}"
             for s in segs if s.end >= t0 and s.start <= t1]
    return "\n".join(lines) or "> (no transcript in this window)"


def collect(events: list[dict]) -> dict:
    out = {"start": None, "end": None, "answers": [], "hands": {}, "suppressed": [], "skips": 0, "settings": []}
    for ev in events:
        k = ev["kind"]
        if k == "meeting_start":
            out["start"] = ev
        elif k == "meeting_end":
            out["end"] = ev
        elif k == "answer":
            out["answers"].append(ev)
        elif k == "hand":
            out["hands"][ev["id"]] = dict(ev)
        elif k == "hand_status" and ev["id"] in out["hands"]:
            out["hands"][ev["id"]].update(status=ev["status"], status_t=ev["t"])
        elif k == "trigger" and ev["outcome"] != "raised":
            out["suppressed"].append(ev)
        elif k == "trigger_skip":
            out["skips"] += 1
        elif k == "setting":
            out["settings"].append(ev)
    return out


def _summarize_with(cfg: dict, llm, system: str, text: str, paper: str | None) -> str:
    from core.prompts import load_prompt

    limit = int(llm.cfg.get("summary_chars", 12000))
    parts = [text[i:i + limit] for i in range(0, len(text), limit)]
    if len(parts) > 1:  # map-reduce when the meeting is longer than this model's context
        partial = [llm.complete(system, load_prompt(cfg, "summary_user", paper=paper or "unknown", transcript=p))
                   for p in parts]
        text = "\n".join(partial)
    return llm.complete(system, load_prompt(cfg, "summary_user", paper=paper or "unknown", transcript=text)).strip()


def summarize(cfg: dict, segs: list[Segment], paper: str | None, brief: str = "") -> str:
    from core.llm import make_llm
    from core.prompts import load_prompt, system_prompt
    from core.transcript import format_segments

    if not segs:
        return "_No transcript._"
    from core.llm import LLMError, chain

    system = system_prompt(cfg, brief, "summary")
    transcript = format_segments(segs)
    # Try each backend of the summary chain on its own, so each splits the transcript to fit
    # its context (summary_chars: a long-context GPU model takes a whole meeting in one pass).
    errors = []
    for llm in chain(make_llm(cfg["llm"].get("summary") or cfg["llm"]["answer"])):
        try:
            out = _summarize_with(cfg, llm, system, transcript, paper)
            break
        except LLMError as e:
            errors.append(str(e)[:120])
    else:
        return "_Summary unavailable: " + "; ".join(errors) + "_"
    bullets = [ln for ln in out.splitlines() if ln.strip().startswith(("-", "*"))]
    return "\n".join(bullets[:5]) if bullets else out


def render(cfg: dict, meeting_dir: Path, info: dict, segs: list[Segment], summary: str) -> str:
    ctx_s = float(cfg.get("trigger", {}).get("context_s", 45))
    start = info["start"] or {}
    dur = info["end"]["t"] if info["end"] else (segs[-1].end if segs else 0)
    L = [f"# Reading group log: {meeting_dir.name}", ""]
    L.append(f"- Paper: {start.get('paper') or 'unknown'}")
    L.append(f"- Mode: {start.get('mode', '?')} · duration {fmt_ts(dur)} · source: {start.get('source', '?')}")
    L.append(f"- STT: {start.get('stt_model', '?')} · answer LLM: {start.get('answer_llm', '?')} · "
             f"trigger LLM: {start.get('trigger_llm', '?')}")
    for ev in info.get("settings", []):
        L.append(f"- [{fmt_ts(ev['t'])}] setting changed: `{ev['key']}` = `{ev['value']}`")
    L += ["", "Fill in each `helpful:` line with `yes` or `no`. Leave it blank if unsure.", ""]
    L += ["## Summary", "", summary or "_Summary unavailable._", ""]

    L += ["## Answers", ""]
    if not info["answers"]:
        L += ["_None._", ""]
    for a in info["answers"]:
        L.append(f"### {a['id']} · {fmt_ts(a['q_start'])} · answered {a['latency_s']:.1f} s after the question")
        L.append(f"<!-- id={a['id']} kind=answer cited={str(a.get('cited')).lower()} -->")
        L += [f"**Question:** {a['question']}", "", f"**Answer:** {a['text']}", ""]
        if not a.get("cited"):
            L += ["_No section/table/figure citation detected in this answer._", ""]
        L += ["**Context:**", _context(segs, a["q_start"] - ctx_s, a["q_end"]), "", HELPFUL, ""]

    L += ["## Raised hands", ""]
    if not info["hands"]:
        L += ["_None._", ""]
    for h in info["hands"].values():
        status = h.get("status", "pending")
        L.append(f"### {h['id']} · {fmt_ts(h['t'])} · {h['trigger']} · confidence {h['confidence']:.2f} · {status}")
        L.append(f"<!-- id={h['id']} kind=hand type={h['trigger']} conf={h['confidence']:.3f} status={status} -->")
        L += [f"**Heard:** \"{h.get('quote', '')}\"", "", f"**Why the hand went up:** {h['reason']}", "",
              f"**Prepared interjection:** {h['text']}", ""]
        L += ["**Context:**", _context(segs, h["t"] - ctx_s, h["t"]), "", HELPFUL, ""]

    L += ["## Triggers that did not raise a hand", "",
          "These fired, but were below the threshold, in cooldown, or duplicates. They were never shown. "
          "Label them anyway: they tell us whether the threshold is too strict.", ""]
    if not info["suppressed"]:
        L += ["_None._", ""]
    for t in info["suppressed"]:
        L.append(f"### {t['id']} · {fmt_ts(t['t'])} · {t['trigger']} · confidence {t['confidence']:.2f} · {t['outcome']}")
        L.append(f"<!-- id={t['id']} kind=trigger type={t['trigger']} conf={t['confidence']:.3f} "
                 f"outcome={t['outcome']} -->")
        L += [f"**Heard:** \"{t.get('quote', '')}\"", "", f"**Reason:** {t['reason']}", ""]
        L += ["**Context:**", _context(segs, t["t"] - ctx_s, t["t"]), "", HELPFUL, ""]
    if info["skips"]:
        L += [f"_{info['skips']} trigger checks were skipped because an answer was in progress._", ""]
    return "\n".join(L)


def build_log(cfg: dict, meeting_dir: str | Path, with_summary: bool = True, out_name: str = "log.md") -> Path:
    meeting_dir = Path(meeting_dir)
    events = read_events(meeting_dir / "events.jsonl")
    tpath = meeting_dir / "transcript.jsonl"
    segs = read_jsonl(tpath) if tpath.exists() else []
    info = collect(events)
    summary = ""
    if with_summary:
        try:
            start = info["start"] or {}
            bp = Path(start["brief"]) if start.get("brief") else None
            brief = bp.read_text() if bp and bp.exists() else ""
            summary = summarize(cfg, segs, start.get("paper"), brief)
        except Exception as e:
            print(f"[log] summary failed: {e}", file=sys.stderr)
    out = meeting_dir / out_name
    out.write_text(render(cfg, meeting_dir, info, segs, summary))
    return out


def main(argv=None):
    from core.config import load_config

    ap = argparse.ArgumentParser(prog="python -m log.build")
    ap.add_argument("meeting_dir")
    ap.add_argument("--no-summary", action="store_true")
    ap.add_argument("--config", default=None)
    ap.add_argument("--profile", default=None, help="e.g. nexus: summarize with the Nexus model")
    args = ap.parse_args(argv)
    name = "log.md"
    if (Path(args.meeting_dir) / name).exists():
        name = "log.rebuilt.md"  # never overwrite a log that may already contain labels
        print(f"log.md exists (it may contain labels); writing {name} instead", file=sys.stderr)
    print(build_log(load_config(args.config, profile=args.profile), args.meeting_dir, not args.no_summary, name))


if __name__ == "__main__":
    main()
