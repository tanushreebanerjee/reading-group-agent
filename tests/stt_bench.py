"""Benchmark Whisper model sizes on the synthetic fixture.

python tests/stt_bench.py [small.en medium.en distil-large-v3 large-v3-turbo]

Reports per model: load time, full-file real-time factor, latency to transcribe a
10 s chunk (what live mode does after each utterance), WER against the script,
and how many times the assistant's name was transcribed correctly.
"""
from __future__ import annotations

import re
import sys
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import load_config  # noqa: E402
from tests.fixtures.make_synthetic import parse_script  # noqa: E402

FIX = ROOT / "tests" / "fixtures"


def words(text: str) -> list[str]:
    text = text.lower().replace("-", " ")
    return re.findall(r"[a-z0-9']+", text)


def wer(ref: list[str], hyp: list[str]) -> float:
    d = list(range(len(hyp) + 1))
    for i in range(1, len(ref) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(hyp) + 1):
            cur = min(d[j] + 1, d[j - 1] + 1, prev + (ref[i - 1] != hyp[j - 1]))
            prev, d[j] = d[j], cur
    return d[len(hyp)] / max(1, len(ref))


def load_wav(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768


def main():
    models = sys.argv[1:] or ["small.en", "medium.en", "distil-large-v3", "large-v3-turbo"]
    meta, turns = parse_script(FIX / "synthetic_script.md")
    ref = words(" ".join(t["text"] for t in turns))
    audio = load_wav(FIX / "synthetic.wav")
    dur = len(audio) / 16000
    chunk = audio[int(30 * 16000):int(40 * 16000)]
    from audio.stt import FasterWhisperSTT

    print(f"{'model':<18}{'load s':>8}{'file RTF':>10}{'10s chunk s':>13}{'WER':>8}{'Name':>7}")
    for m in models:
        cfg = load_config(overrides={"stt": {"model": m}})
        t0 = time.time()
        stt = FasterWhisperSTT(cfg, hotwords=cfg["assistant_name"], initial_prompt=f"Reading group discussion with {cfg['assistant_name']}.")
        load = time.time() - t0
        t0 = time.time()
        segs = stt.transcribe(audio)
        rtf = (time.time() - t0) / dur
        t0 = time.time()
        stt.transcribe(chunk)
        lat = time.time() - t0
        hyp_text = " ".join(s.text for s in segs)
        hyp = words(hyp_text)
        n_atlas = len(re.findall(rf"\b{cfg['assistant_name'].lower()}\b", hyp_text.lower()))
        print(f"{m:<18}{load:>8.1f}{rtf:>10.3f}{lat:>13.2f}{wer(ref, hyp):>8.1%}{n_atlas:>5}/2", flush=True)
        (FIX.parent.parent / "meetings" / ".cache").mkdir(parents=True, exist_ok=True)
        (ROOT / "meetings" / ".cache" / f"bench_{m}.txt").write_text(hyp_text)


if __name__ == "__main__":
    main()
