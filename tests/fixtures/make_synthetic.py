"""Synthesize tests/fixtures/synthetic.wav from synthetic_script.md with macOS `say`.

Usage: python tests/fixtures/make_synthetic.py [--rate 165]
Writes synthetic.wav (16 kHz mono) and synthetic_turns.json (turn start/end times).
"""
from __future__ import annotations

import argparse
import json
import random
import re
import subprocess
import tempfile
import wave
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
TURN_RE = re.compile(r"^(\d+)\.\s+\*\*(\w+):\*\*\s+(.*)$")
SR = 16000


def parse_script(path: Path) -> tuple[dict, list[dict]]:
    text = path.read_text()
    _, front, body = text.split("---", 2)
    meta = yaml.safe_load(front)
    turns = []
    for line in body.splitlines():
        m = TURN_RE.match(line.strip())
        if m:
            turns.append({"turn": int(m.group(1)), "speaker": m.group(2), "text": m.group(3)})
    return meta, turns


def say_to_pcm(text: str, voice: str, rate: int, tmp: Path) -> bytes:
    aiff = tmp / "t.aiff"
    raw = tmp / "t.raw"
    subprocess.run(["say", "-v", voice, "-r", str(rate), "-o", str(aiff), text], check=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(aiff), "-ac", "1", "-ar", str(SR),
                    "-f", "s16le", str(raw)], check=True)
    return raw.read_bytes()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", default=str(HERE / "synthetic_script.md"))
    ap.add_argument("--out", default=str(HERE / "synthetic.wav"))
    ap.add_argument("--rate", type=int, default=165, help="words per minute for say")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    meta, turns = parse_script(Path(args.script))
    voices = meta["voices"]
    rng = random.Random(args.seed)
    pcm = bytearray()
    timeline = []
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for t in turns:
            gap = rng.uniform(1.2, 2.0)
            # the planted stall after the unanswered question gets a longer silence
            if any(e["kind"] == "gap" and e["turn"] + 2 == t["turn"] for e in meta["events"]):
                gap = 3.0
            # leave a clear pause after questions addressed to the assistant
            if any(e["kind"] == "ask" and e["turn"] + 1 == t["turn"] for e in meta["events"]):
                gap = 2.5
            pcm += b"\x00\x00" * int(gap * SR)
            start = len(pcm) / 2 / SR
            pcm += say_to_pcm(t["text"], voices[t["speaker"]], args.rate, tmp)
            timeline.append({**t, "start": round(start, 2), "end": round(len(pcm) / 2 / SR, 2)})
            print(f"{t['turn']:>2} {t['speaker']:<6} {start:7.1f}s")
    pcm += b"\x00\x00" * int(2.0 * SR)

    with wave.open(args.out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(bytes(pcm))
    Path(args.out).with_name("synthetic_turns.json").write_text(json.dumps(timeline, indent=1))
    print(f"wrote {args.out}: {len(pcm) / 2 / SR / 60:.2f} min")


if __name__ == "__main__":
    main()
