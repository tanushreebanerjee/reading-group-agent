"""Audio input and transcription.

  python -m audio --list-devices
  python -m audio --replay tests/fixtures/synthetic.wav [--speed 4]
  python -m audio [--record]            # live from config audio_device
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import sys
from pathlib import Path

from core.config import load_config
from core.transcript import TranscriptStore, fmt_ts


def parse_args(argv=None):
    ap = argparse.ArgumentParser(prog="python -m audio")
    ap.add_argument("--config", default=None)
    ap.add_argument("--list-devices", action="store_true")
    ap.add_argument("--replay", metavar="WAV")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--record", action="store_true", help="save live audio to WAV under meetings/")
    ap.add_argument("--device", default=None)
    ap.add_argument("--stt-model", default=None)
    ap.add_argument("--out", default=None, help="transcript JSONL path")
    return ap.parse_args(argv)


async def run(args, cfg):
    from audio.sources import make_source

    out = Path(args.out) if args.out else (
        Path(cfg["paths"]["meetings_dir"]) / dt.date.today().isoformat() / "transcript_audio_only.jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    store = TranscriptStore(out)
    source = make_source(cfg, replay=args.replay, speed=args.speed, record=args.record,
                         record_dir=out.parent)
    try:
        async for seg in source.segments():
            store.add(seg)
            print(f"[{fmt_ts(seg.start)}] {seg.text}", flush=True)
    finally:
        store.close()
        print(f"\ntranscript: {out}", file=sys.stderr)


def main(argv=None):
    args = parse_args(argv)
    overrides = {}
    if args.device:
        overrides["audio_device"] = args.device
    if args.stt_model:
        overrides["stt"] = {"model": args.stt_model}
    cfg = load_config(args.config, overrides)
    if args.list_devices:
        from audio.devices import list_devices

        print(list_devices(cfg.get("audio_device")))
        return
    try:
        asyncio.run(run(args, cfg))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
