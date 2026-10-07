"""Transcript sources. Live and replay both yield Segments in meeting time.

Each source exposes:
    clock            RealClock / SimClock
    segments()       async iterator of Segment
    pending_start()  meeting time where not-yet-transcribed speech begins, or None
"""
from __future__ import annotations

import asyncio
import hashlib
import sys
import time
from pathlib import Path

import numpy as np

from core.clock import RealClock, SimClock
from core.transcript import Segment, read_jsonl, write_jsonl


def stt_prompt(cfg: dict, paper_title: str | None = None, terms: list[str] | None = None) -> str:
    """Whisper initial prompt: the assistant's name, paper title, and the paper's jargon."""
    name = cfg.get("assistant_name", "Sherlock")
    prompt = f"Reading group discussion with {name}."
    if paper_title:
        prompt += f" Paper: {paper_title}."
    if cfg.get("group_members"):
        prompt += " Attendees: " + ", ".join(cfg["group_members"]) + "."
    if terms:
        prompt += " Terms: " + ", ".join(terms) + "."
    return prompt


class ReplaySource:
    """Transcribe a WAV once (cached), then emit segments at their end time on a SimClock."""

    def __init__(self, cfg: dict, path: str, speed: float = 1.0, prompt: str | None = None):
        self.cfg = cfg
        self.path = Path(path)
        self.clock = SimClock(speed)
        self.prompt = prompt or stt_prompt(cfg)
        self.done = False

    def cache_path(self) -> Path:
        h = hashlib.sha1(self.path.read_bytes() + self.prompt.encode()).hexdigest()[:12]
        model = self.cfg["stt"].get("model", "model").replace("/", "_")
        return Path(self.cfg["paths"]["cache_dir"]) / f"{self.path.stem}.{h}.{model}.jsonl"

    def load_segments(self) -> list[Segment]:
        cache = self.cache_path()
        if cache.exists():
            return read_jsonl(cache)
        from audio.stt import make_stt

        print(f"[audio] transcribing {self.path} with {self.cfg['stt']['model']} (cached afterwards)...",
              file=sys.stderr)
        t0 = time.time()
        stt = make_stt(self.cfg, hotwords=self.cfg.get("assistant_name"), initial_prompt=self.prompt)
        segs = stt.transcribe_file(str(self.path))
        cache.parent.mkdir(parents=True, exist_ok=True)
        write_jsonl(cache, segs)
        print(f"[audio] transcribed in {time.time() - t0:.1f}s -> {cache}", file=sys.stderr)
        return segs

    def pending_start(self) -> float | None:
        return None  # replay emits whole segments; nothing is ever half-transcribed

    async def segments(self):
        segs = await asyncio.to_thread(self.load_segments)
        self.clock = SimClock(self.clock.speed)  # start the meeting clock after transcription
        for seg in segs:
            await self.clock.sleep_until(seg.end)
            yield seg
        self.done = True


def resample(x: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    if sr_in == sr_out:
        return x
    ratio = sr_in / sr_out
    if ratio > 1:  # crude anti-alias: moving average over the decimation factor
        k = int(round(ratio))
        x = np.convolve(x, np.ones(k) / k, mode="same")
    n_out = int(len(x) / ratio)
    return np.interp(np.arange(n_out) * ratio, np.arange(len(x)), x).astype(np.float32)


class Chunker:
    """Energy-based VAD: emits a chunk after `silence_s` of silence following speech, or at `max_s`."""

    def __init__(self, sr: int, silence_s: float, max_s: float, threshold: float, block_s: float = 0.03):
        self.sr, self.silence_s, self.max_s, self.threshold = sr, silence_s, max_s, threshold
        self.block = int(sr * block_s)
        self.buf: list[np.ndarray] = []
        self.buf_start = 0.0          # meeting time of buf[0]
        self.t = 0.0                  # meeting time of next incoming sample
        self.silence = 0.0
        self.has_speech = False
        self._pending = np.zeros(0, dtype=np.float32)

    @property
    def buffered_s(self) -> float:
        return sum(len(b) for b in self.buf) / self.sr

    def push(self, x: np.ndarray) -> list[tuple[float, np.ndarray]]:
        """Feed 16 kHz mono audio; return finished (start_time, audio) chunks."""
        out = []
        x = np.concatenate([self._pending, x])
        n = len(x) // self.block * self.block
        self._pending = x[n:]
        for i in range(0, n, self.block):
            blk = x[i:i + self.block]
            loud = float(np.sqrt(np.mean(blk ** 2))) > self.threshold
            if not self.buf:
                self.buf_start = self.t
            self.t += len(blk) / self.sr
            if loud:
                self.has_speech = True
                self.silence = 0.0
            else:
                self.silence += len(blk) / self.sr
            if not self.has_speech:
                # keep only a short pre-roll of silence before speech
                self.buf.append(blk)
                while self.buffered_s > 0.3:
                    self.buf_start += len(self.buf.pop(0)) / self.sr
                continue
            self.buf.append(blk)
            if self.silence >= self.silence_s or self.buffered_s >= self.max_s:
                out.append((self.buf_start, np.concatenate(self.buf)))
                self.buf, self.has_speech, self.silence = [], False, 0.0
        return out


class LiveSource:
    def __init__(self, cfg: dict, record_path: Path | None = None, prompt: str | None = None):
        from audio.devices import find_input

        self.cfg = cfg
        s = cfg["stt"]
        self.dev = find_input(cfg["audio_device"])
        self.sr_in = self.dev["samplerate"]
        self.sr = int(s.get("sample_rate", 16000))
        self.channels = min(2, self.dev["channels"])
        self.clock = RealClock()
        self.chunker = Chunker(self.sr, float(s.get("silence_s", 0.6)), float(s.get("chunk_max_s", 10)),
                               float(s.get("energy_threshold", 0.008)))
        self.record_path = record_path
        self.prompt = prompt or stt_prompt(cfg)
        self._inflight: list[float] = []   # start times of chunks being transcribed
        self.done = False

    def pending_start(self) -> float | None:
        """Meeting time where not-yet-transcribed speech begins (buffered or in flight), if any."""
        starts = list(self._inflight)
        if self.chunker.has_speech:
            starts.append(self.chunker.buf_start)
        return min(starts) if starts else None

    async def segments(self):
        import sounddevice as sd

        from audio.recorder import WavRecorder
        from audio.stt import make_stt

        stt = await asyncio.to_thread(make_stt, self.cfg, self.cfg.get("assistant_name"), self.prompt)
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()
        rec = WavRecorder(self.record_path, self.sr_in) if self.record_path else None

        def callback(indata, frames, t, status):
            if status:
                print(f"[audio] {status}", file=sys.stderr)
            mono = indata.mean(axis=1).astype(np.float32)
            loop.call_soon_threadsafe(q.put_nowait, mono)

        out_q: asyncio.Queue = asyncio.Queue()
        sem = asyncio.Semaphore(1)  # transcribe chunks in order, one at a time

        async def transcribe(start, audio):
            async with sem:
                try:
                    segs = await asyncio.to_thread(stt.transcribe, audio, start)
                    for s in segs:
                        await out_q.put(s)
                finally:
                    self._inflight.remove(start)

        async def pump():
            tasks = []
            while True:
                mono = await q.get()
                if rec:
                    rec.write(mono)
                for start, audio in self.chunker.push(resample(mono, self.sr_in, self.sr)):
                    self._inflight.append(start)
                    tasks.append(asyncio.create_task(transcribe(start, audio)))

        self.clock = RealClock()
        stream = sd.InputStream(device=self.dev["index"], channels=self.channels, samplerate=self.sr_in,
                                dtype="float32", blocksize=int(self.sr_in * 0.1), callback=callback)
        print(f"[audio] listening on {self.dev['name']} ({self.sr_in} Hz)"
              + (f", recording to {self.record_path}" if rec else ""), file=sys.stderr)
        pump_task = asyncio.create_task(pump())
        try:
            with stream:
                while True:
                    yield await out_q.get()
        finally:
            pump_task.cancel()
            if rec:
                rec.close()
            self.done = True


def make_source(cfg: dict, replay: str | None = None, speed: float = 1.0, record: bool = False,
                record_dir: Path | None = None, prompt: str | None = None):
    if replay:
        return ReplaySource(cfg, replay, speed, prompt=prompt)
    record_path = None
    if record:
        record_dir = Path(record_dir or cfg["paths"]["meetings_dir"])
        record_dir.mkdir(parents=True, exist_ok=True)
        record_path = record_dir / f"recording_{time.strftime('%H%M%S')}.wav"
    return LiveSource(cfg, record_path=record_path, prompt=prompt)
