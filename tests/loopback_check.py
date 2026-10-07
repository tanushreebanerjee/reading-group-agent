"""Live-path test without Zoom: play the fixture WAV into BlackHole while the app listens live.

python tests/loopback_check.py [--mode ask|engaged]

BlackHole loops its output back to its input, so this exercises the real live
path: sounddevice capture -> resampling -> energy VAD chunking -> per-chunk
Whisper -> name detection -> answers -> display -> log. Nothing plays on the
speakers. Run it with nothing else sending audio to BlackHole.
"""
from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import load_config  # noqa: E402
from core.transcript import read_jsonl  # noqa: E402
from log.events import read_events  # noqa: E402
from tests.fixtures.make_synthetic import parse_script  # noqa: E402
from tests.stt_bench import wer, words  # noqa: E402

FIX = ROOT / "tests" / "fixtures"


def play_to_device(wav: Path, device_name: str) -> None:
    import sounddevice as sd

    with wave.open(str(wav)) as w:
        sr = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    dev = next(i for i, d in enumerate(sd.query_devices())
               if device_name.lower() in d["name"].lower() and d["max_output_channels"] > 0)
    out_sr = int(sd.query_devices(dev)["default_samplerate"])
    n = int(len(x) * out_sr / sr)
    y = np.interp(np.arange(n) * sr / out_sr, np.arange(len(x)), x).astype(np.float32)
    sd.play(np.stack([y, y], axis=1), samplerate=out_sr, device=dev, blocking=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="ask")
    ap.add_argument("--paper", default="papers/test.pdf")
    ap.add_argument("--port", type=int, default=8798)
    args = ap.parse_args()
    cfg = load_config()
    meeting = ROOT / "meetings" / f"loopback_{time.strftime('%Y%m%d_%H%M%S')}"
    meeting.mkdir(parents=True)
    cmd = [sys.executable, "-m", "assistant", "--paper", args.paper, "--mode", args.mode,
           "--meeting-dir", str(meeting), "--port", str(args.port)]
    print("$", " ".join(cmd), flush=True)
    log = open(meeting / "app.log", "w")
    app = subprocess.Popen(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    # wait until the app is listening (model loaded, stream open)
    for _ in range(240):
        if "listening on" in (meeting / "app.log").read_text():
            break
        time.sleep(0.5)
    else:
        app.kill()
        sys.exit("app never started listening; see " + str(meeting / "app.log"))
    time.sleep(1.0)
    print("playing fixture into", cfg["audio_device"], flush=True)
    t0 = time.time()
    play_to_device(FIX / "synthetic.wav", cfg["audio_device"])
    print(f"playback done ({time.time() - t0:.0f}s); waiting for in-flight answers", flush=True)
    time.sleep(40)
    app.send_signal(signal.SIGINT)
    app.wait(timeout=180)

    meta, turns = parse_script(FIX / "synthetic_script.md")
    segs = [s for s in read_jsonl(meeting / "transcript.jsonl") if not s.speaker]
    ref = words(" ".join(t["text"] for t in turns))
    hyp_text = " ".join(s.text for s in segs)
    events = read_events(meeting / "events.jsonl")
    answers = [e for e in events if e["kind"] == "answer"]
    name = meta["assistant_name"].lower()
    print(f"\nlive transcript: {len(segs)} segments, WER {wer(ref, words(hyp_text)):.1%}, "
          f"'{name}' heard {hyp_text.lower().count(name)}/2")
    for a in answers:
        print(f"{a['id']}: first words {a.get('first_words_s')}s / complete {a['latency_s']}s after question end"
              f"\n  Q: {a['question']}\n  A: {a['text']}")
    ok = len(answers) == 2 and all(a["cited"] for a in answers)
    print("RESULT:", "PASS" if ok else "FAIL", f"({len(answers)} answers)")
    print("meeting dir:", meeting)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
