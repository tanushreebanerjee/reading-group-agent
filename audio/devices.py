"""Audio device listing and lookup."""
from __future__ import annotations

import sounddevice as sd


def input_devices() -> list[dict]:
    out = []
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            out.append({"index": i, "name": d["name"], "channels": d["max_input_channels"],
                        "samplerate": int(d["default_samplerate"])})
    return out


def list_devices(target: str | None = None) -> str:
    lines = []
    for i, d in enumerate(sd.query_devices()):
        mark = "  <-- configured input" if target and target.lower() in d["name"].lower() else ""
        lines.append(f"{i:>3}  {d['name']:<32} in={d['max_input_channels']:<2} out={d['max_output_channels']:<2} "
                     f"{int(d['default_samplerate'])} Hz{mark}")
    if target and not any(target.lower() in d["name"].lower() for d in sd.query_devices()):
        lines.append(f"\nWARNING: configured audio_device {target!r} not found. Install BlackHole "
                     "(brew install blackhole-2ch) or change audio_device in config.yaml.")
    return "\n".join(lines)


def find_input(name: str) -> dict:
    for d in input_devices():
        if name.lower() in d["name"].lower():
            return d
    raise RuntimeError(f"input device {name!r} not found; run `python -m audio --list-devices`")
