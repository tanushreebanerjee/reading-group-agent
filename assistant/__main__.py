"""Run the assistant: listen, answer when addressed, raise hands in engaged mode, serve the display.

  python -m assistant --paper papers/x.pdf [--mode ask|engaged]
  python -m assistant --paper papers/x.pdf --replay meeting.wav --speed 4
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from core.config import load_config
from core.paper import load_paper


def parse_args(argv=None):
    ap = argparse.ArgumentParser(prog="python -m assistant")
    ap.add_argument("--config", default=None)
    ap.add_argument("--paper", required=True, help="paper PDF")
    ap.add_argument("--brief", default=None, help="default: briefs/<paper stem>/brief.md")
    ap.add_argument("--mode", choices=["ask", "engaged"], default=None)
    ap.add_argument("--replay", metavar="WAV", default=None)
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--record", action="store_true", help="save live audio to WAV in the meeting folder")
    ap.add_argument("--meeting-dir", default=None, help="default: meetings/<date>[_n]")
    ap.add_argument("--hold", type=float, default=None,
                    help="replay: seconds to keep running after the recording ends (default 10)")
    ap.add_argument("--port", type=int, default=None)
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    overrides = {"mode": args.mode}
    if args.port:
        overrides["display"] = {"port": args.port}
    cfg = load_config(args.config, overrides)

    from assistant.app import App, log
    from audio.sources import make_source
    from log.events import new_meeting_dir

    paper_path = Path(args.paper)
    paper = load_paper(paper_path, int(cfg["answer"].get("chunk_chars", 600)), cfg["paths"]["cache_dir"])
    brief_path = Path(args.brief) if args.brief else Path(cfg["paths"]["briefs_dir"]) / paper_path.stem / "brief.md"
    if brief_path.exists():
        brief = brief_path.read_text()
    else:
        log(f"[assistant] WARNING: no brief at {brief_path}; run `python -m prep {paper_path}` first. "
            "Answering from the paper text only.")
        brief = ""

    meeting_dir = Path(args.meeting_dir) if args.meeting_dir else new_meeting_dir(cfg["paths"]["meetings_dir"])
    meeting_dir.mkdir(parents=True, exist_ok=True)
    source = make_source(cfg, replay=args.replay, speed=args.speed, record=args.record,
                         record_dir=meeting_dir, paper_title=paper.title)
    hold = args.hold if args.hold is not None else (10.0 if args.replay else 0.0)
    app = App(cfg, source, paper, brief, meeting_dir, hold_s=hold)
    try:
        asyncio.run(app.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
