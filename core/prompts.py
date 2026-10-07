"""Prompts live as files under prompts/ and use $var substitution."""
from __future__ import annotations

from pathlib import Path
from string import Template


def system_prompt(cfg: dict, brief: str, task: str, **values) -> str:
    """Shared prefix (identity + brief) followed by the task instructions.

    Every live prompt (answer, interjection, trigger) starts with the same prefix
    so local backends like Ollama can reuse its KV cache across calls.
    """
    name = cfg.get("assistant_name", "Sherlock")
    prefix = load_prompt(cfg, "context", name=name, brief=brief or "(no brief available)")
    return prefix.rstrip() + "\n\n" + load_prompt(cfg, task, name=name, **values)


def load_prompt(cfg: dict, prompt_file: str, /, **values) -> str:
    path = Path(cfg["paths"]["prompts_dir"]) / f"{prompt_file}.md"
    text = path.read_text()
    return Template(text).safe_substitute({k: str(v) for k, v in values.items()})
