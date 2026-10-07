"""Prompts live as files under prompts/ and use $var substitution."""
from __future__ import annotations

from pathlib import Path
from string import Template


def load_prompt(cfg: dict, name: str, **values) -> str:
    path = Path(cfg["paths"]["prompts_dir"]) / f"{name}.md"
    text = path.read_text()
    return Template(text).safe_substitute({k: str(v) for k, v in values.items()})
