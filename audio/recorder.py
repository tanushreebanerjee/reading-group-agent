"""--record: write live audio to a 16-bit mono WAV. Only used when the flag is given."""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np


class WavRecorder:
    def __init__(self, path: Path, samplerate: int):
        self.w = wave.open(str(path), "wb")
        self.w.setnchannels(1)
        self.w.setsampwidth(2)
        self.w.setframerate(samplerate)

    def write(self, mono: np.ndarray) -> None:
        self.w.writeframes((np.clip(mono, -1, 1) * 32767).astype(np.int16).tobytes())

    def close(self) -> None:
        self.w.close()
