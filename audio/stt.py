"""Speech-to-text backends behind one interface.

    transcribe(audio: float32 mono 16 kHz ndarray, offset: float) -> list[Segment]
    transcribe_file(path) -> list[Segment]
"""
from __future__ import annotations

import os

import numpy as np

from core.transcript import Segment


class STT:
    def transcribe(self, audio: np.ndarray, offset: float = 0.0) -> list[Segment]:
        raise NotImplementedError

    def transcribe_file(self, path: str) -> list[Segment]:
        raise NotImplementedError


class FasterWhisperSTT(STT):
    def __init__(self, cfg: dict, hotwords: str | None = None, initial_prompt: str | None = None):
        from faster_whisper import WhisperModel

        s = cfg["stt"]
        self.cfg = s
        self.model_name = s.get("model", "small.en")
        self.model = WhisperModel(self.model_name, device="cpu", compute_type=s.get("compute_type", "int8"),
                                  cpu_threads=int(s.get("cpu_threads", 4)))
        self.hotwords = hotwords
        self.initial_prompt = initial_prompt

    def _run(self, audio_or_path, offset: float = 0.0) -> list[Segment]:
        segs, _info = self.model.transcribe(
            audio_or_path,
            language=self.cfg.get("language", "en") if not self.model_name.endswith(".en") else None,
            beam_size=int(self.cfg.get("beam_size", 1)),
            vad_filter=True,
            hotwords=self.hotwords,
            initial_prompt=self.initial_prompt,
            condition_on_previous_text=False,
        )
        out = []
        for s in segs:
            text = s.text.strip()
            if text:
                out.append(Segment(round(offset + s.start, 2), round(offset + s.end, 2), text))
        return out

    def transcribe(self, audio, offset=0.0):
        return self._run(audio.astype(np.float32), offset)

    def transcribe_file(self, path):
        return self._run(str(path))


class ParakeetMLXSTT(STT):
    """NVIDIA Parakeet on the Apple GPU (mlx-audio). On the M4 it transcribes an utterance in
    ~0.9 s versus ~2.8 s for Whisper small.en on the CPU, at slightly lower word error on the
    test meeting. English only; it takes no prompt, so names and terms can't be primed.
    All MLX calls run on one dedicated thread (MLX streams are per thread)."""

    def __init__(self, cfg: dict, **_):
        from concurrent.futures import ThreadPoolExecutor

        self.cfg = cfg["stt"]
        self.model_name = self.cfg.get("parakeet_model", "mlx-community/parakeet-tdt-0.6b-v2")
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="parakeet-mlx")
        self.model = self.pool.submit(self._load).result()

    def _load(self):
        from mlx_audio.stt.utils import load_model

        return load_model(self.model_name)

    def _run(self, audio, offset: float, **kw) -> list[Segment]:
        import mlx.core as mx

        result = self.model.generate(audio if isinstance(audio, str) else mx.array(audio), **kw)
        out = []
        for s in getattr(result, "sentences", None) or []:
            text = s.text.strip()
            if text:
                out.append(Segment(round(offset + s.start, 2), round(offset + s.end, 2), text))
        if not out and getattr(result, "text", "").strip():  # no timestamps: one segment for the chunk
            dur = len(audio) / 16000 if not isinstance(audio, str) else 0.0
            out.append(Segment(round(offset, 2), round(offset + dur, 2), result.text.strip()))
        return out

    def transcribe(self, audio, offset=0.0):
        return self.pool.submit(self._run, audio.astype(np.float32), offset).result()

    def transcribe_file(self, path):
        # long files in overlapping chunks (live mode never needs this)
        return self.pool.submit(self._run, str(path), 0.0, chunk_duration=60.0, overlap_duration=5.0).result()


class DeepgramSTT(STT):
    """Optional paid backend with diarization. NOT IMPLEMENTED in the first pass beyond file mode stub."""

    def __init__(self, cfg: dict, **_):
        if not os.environ.get("DEEPGRAM_API_KEY"):
            raise RuntimeError("DEEPGRAM_API_KEY not set; use stt.backend: faster-whisper")
        raise NotImplementedError("Deepgram backend is planned for later (see PROGRESS.md)")


def make_stt(cfg: dict, hotwords: str | None = None, initial_prompt: str | None = None) -> STT:
    backend = cfg["stt"].get("backend", "faster-whisper")
    if backend == "faster-whisper":
        return FasterWhisperSTT(cfg, hotwords=hotwords, initial_prompt=initial_prompt)
    if backend == "parakeet-mlx":
        return ParakeetMLXSTT(cfg)
    if backend == "deepgram":
        return DeepgramSTT(cfg)
    raise ValueError(f"unknown stt backend {backend!r}")
