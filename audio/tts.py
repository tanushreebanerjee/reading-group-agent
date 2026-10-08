"""Text-to-speech backends and a Speaker that plays answers into an output device.

Backends share one call, synth(text) -> (float32 mono samples, sample_rate):
  kokoro  local neural voice (kokoro-onnx); free, private; model files auto-download
  say     macOS built-in voices; instant, robotic
  groq    Orpheus on Groq's API (free tier; accept the model terms in the Groq console first)

The Speaker plays into `voice.output_device` (Zoom's microphone, e.g. "BlackHole 16ch"),
synthesizing sentence by sentence so speech starts after the first sentence is ready.
"""
from __future__ import annotations

import io
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import urllib.request
import wave
from pathlib import Path
from typing import Callable

import numpy as np

KOKORO_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"


def read_wav(data: bytes) -> tuple[np.ndarray, int]:
    with wave.open(io.BytesIO(data)) as w:
        sr, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError(f"expected 16-bit WAV, got {8 * width}-bit")
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768
    return (x.reshape(-1, ch).mean(axis=1) if ch > 1 else x), sr


# ---------- making answers speakable ----------

SPOKEN = [
    (r"§\s*", "section "), (r"\bSec\.\s*", "section "), (r"\bFigs?\.\s*", "figure "),
    (r"\bEqs?\.\s*", "equation "), (r"\bApp\.\s*", "appendix "), (r"\bTab\.\s*", "table "),
    (r"\bpp?\.\s*(\d)", r"page \1"), (r"\be\.g\.,?", "for example,"), (r"\bi\.e\.,?", "that is,"),
    (r"\bvs\.?", "versus"), (r"×\s*10\^?\s*([−-]?\d+)", r" times ten to the \1"), (r"[−]", "-"),
    (r"→", " to "), (r"≈", "about "), (r"\s*[\[\]]\s*", " "),
]


def speakable(text: str) -> str:
    """Expand citation shorthand so TTS reads 'section 4.3, page 7' instead of symbols."""
    for pat, rep in SPOKEN:
        text = re.sub(pat, rep, text)
    return re.sub(r"\s+", " ", text).strip()


def sentences(text: str, first_max_words: int = 10) -> list[str]:
    """Split for incremental synthesis; keeps decimals like 16.362 and 'Table 3.' intact.
    A long first sentence is split at its first comma after a few words, so speech starts
    after a short chunk is synthesized rather than a whole sentence."""
    parts = [p for p in re.split(r"(?<=[.!?])\s+(?=[A-Z\"'(])", text.strip()) if p.strip()]
    if parts and len(parts[0].split()) > first_max_words:
        n = len(parts[0].split())
        for m in re.finditer(r",\s+", parts[0]):
            if 3 <= len(parts[0][:m.start()].split()) < n - 2:
                parts[:1] = [parts[0][:m.end()].strip(), parts[0][m.end():].strip()]
                break
    return parts


# ---------- backends ----------

class TTS:
    name = "base"

    def __init__(self, cfg: dict):
        self.cfg = cfg

    def synth(self, text: str) -> tuple[np.ndarray, int]:
        raise NotImplementedError

    def warmup(self) -> None:
        self.synth("Ready.")


class KokoroTTS(TTS):
    name = "kokoro"

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self._k = None
        self.voice = cfg.get("voice", "af_heart")
        self.speed = float(cfg.get("speed", 1.0))
        self.lang = cfg.get("lang", "en-us")

    def _paths(self) -> tuple[Path, Path]:
        d = Path(os.path.expanduser(self.cfg.get("kokoro_dir", "~/.cache/kokoro-onnx")))
        model = d / self.cfg.get("kokoro_model", "kokoro-v1.0.onnx")
        voices = d / "voices-v1.0.bin"
        d.mkdir(parents=True, exist_ok=True)
        for p in (model, voices):
            if not p.exists() or p.stat().st_size == 0:
                print(f"[voice] downloading {p.name} to {d} (one time)", file=sys.stderr, flush=True)
                tmp = p.with_suffix(p.suffix + ".part")
                urllib.request.urlretrieve(KOKORO_URL + p.name, tmp)
                tmp.rename(p)
        return model, voices

    def load(self):
        if self._k is None:
            from kokoro_onnx import Kokoro

            model, voices = self._paths()
            self._k = Kokoro(str(model), str(voices))
        return self._k

    def synth(self, text):
        x, sr = self.load().create(text, voice=self.voice, speed=self.speed, lang=self.lang)
        return np.asarray(x, dtype=np.float32), int(sr)


class SayTTS(TTS):
    name = "say"

    def synth(self, text):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "say.wav"
            cmd = ["say", "-o", str(out), "--file-format=WAVE", "--data-format=LEI16@22050"]
            if self.cfg.get("say_voice"):
                cmd += ["-v", self.cfg["say_voice"]]
            rate = self.cfg.get("say_rate")
            if rate:
                cmd += ["-r", str(rate)]
            subprocess.run(cmd + [text], check=True, capture_output=True, timeout=30)
            return read_wav(out.read_bytes())

    def warmup(self) -> None:
        pass


class GroqTTS(TTS):
    name = "groq"

    def synth(self, text):
        import requests

        key = os.environ.get("GROQ_API_KEY")
        if not key:
            raise RuntimeError("GROQ_API_KEY is not set (add it to .env)")
        r = requests.post("https://api.groq.com/openai/v1/audio/speech",
                          headers={"Authorization": f"Bearer {key}"}, timeout=30,
                          json={"model": self.cfg.get("groq_model", "canopylabs/orpheus-v1-english"),
                                "voice": self.cfg.get("groq_voice", "troy"), "input": text,
                                "response_format": "wav"})
        if r.status_code != 200:
            raise RuntimeError(f"groq tts {r.status_code}: {r.text[:300]}")
        return read_wav(r.content)


BACKENDS = {"kokoro": KokoroTTS, "say": SayTTS, "groq": GroqTTS}


def make_tts(cfg: dict) -> TTS:
    b = cfg.get("backend", "kokoro")
    if b not in BACKENDS:
        raise ValueError(f"unknown voice backend {b!r}; choose from {sorted(BACKENDS)}")
    return BACKENDS[b](cfg)


# ---------- playback ----------

def find_output(name: str | None) -> tuple[int | None, str]:
    """Output device index by (partial) name; None = system default. Falls back to the default
    output with a warning if the named device is missing (e.g. BlackHole 16ch not installed)."""
    import sounddevice as sd

    if not name or name == "default":
        return None, sd.query_devices(kind="output")["name"]
    for i, d in enumerate(sd.query_devices()):
        if name.lower() in d["name"].lower() and d["max_output_channels"] > 0:
            return i, d["name"]
    default = sd.query_devices(kind="output")["name"]
    print(f"[voice] WARNING: output device {name!r} not found; speaking through {default!r}. "
          f"Install it with: brew install blackhole-16ch", file=sys.stderr, flush=True)
    return None, default


class Speaker:
    """Speaks one utterance at a time on a background thread. Synthesis of the next sentence
    overlaps playback of the current one. stop() cuts speech off within ~0.1 s.

    on_start/on_end are called from the worker thread (wrap them for asyncio)."""

    def __init__(self, tts: TTS, device: str | None, on_start: Callable[[], None] | None = None,
                 on_end: Callable[[], None] | None = None, play=None):
        self.tts = tts
        self.device, self.device_name = find_output(device) if play is None else (None, "test")
        self.on_start, self.on_end = on_start, on_end
        self.gain = 1.0
        self._play = play or self._play_sd
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.thread: threading.Thread | None = None

    @property
    def speaking(self) -> bool:
        return bool(self.thread and self.thread.is_alive())

    def say(self, text: str) -> None:
        """Interrupts anything already being said, then speaks `text`."""
        self.stop()
        self._stop.clear()
        self.thread = threading.Thread(target=self._run, args=(speakable(text),), daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self._stop.set()
        t = self.thread
        if t and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=2)

    def _run(self, text: str) -> None:
        q: queue.Queue = queue.Queue(maxsize=2)

        def produce():
            for s in sentences(text):
                if self._stop.is_set():
                    break
                try:
                    q.put(self.tts.synth(s))
                except Exception as e:
                    print(f"[voice] synthesis failed: {e}", file=sys.stderr, flush=True)
                    break
            q.put(None)

        threading.Thread(target=produce, daemon=True).start()
        started = False
        try:
            while not self._stop.is_set():
                item = q.get()
                if item is None:
                    break
                if not started and self.on_start:
                    self.on_start()
                started = True
                self._play(*item)
        finally:
            if started and self.on_end:
                self.on_end()

    def _play_sd(self, x: np.ndarray, sr: int) -> None:
        import sounddevice as sd

        info = sd.query_devices(self.device if self.device is not None else sd.default.device[1])
        out_sr, ch = int(info["default_samplerate"]), min(2, int(info["max_output_channels"]))
        if out_sr != sr:  # virtual devices run at a fixed rate; resample rather than fail
            n = int(len(x) * out_sr / sr)
            x = np.interp(np.arange(n) * sr / out_sr, np.arange(len(x)), x).astype(np.float32)
            sr = out_sr
        x = x * float(self.gain)
        block = int(sr * 0.1)
        with sd.OutputStream(samplerate=sr, channels=ch, dtype="float32", device=self.device) as out:
            for i in range(0, len(x), block):
                if self._stop.is_set():
                    break
                out.write(np.repeat(x[i:i + block].reshape(-1, 1), ch, axis=1))
