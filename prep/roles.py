"""Advocate -> skeptic -> checker -> editor discussion that produces the brief."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from core.llm import LLM
from core.paper import Paper
from core.prompts import load_prompt


def _step(name: str, out_dir: Path, fn: Callable[[], str], log=print) -> str:
    """Run one role, caching its output so a failed later step doesn't redo earlier ones."""
    path = out_dir / f"{name}.md"
    if path.exists() and path.stat().st_size > 0:
        log(f"[prep] {name}: cached ({path})")
        return path.read_text()
    t0 = time.time()
    log(f"[prep] {name}: running...")
    text = fn()
    path.write_text(text)
    log(f"[prep] {name}: done in {time.time() - t0:.0f}s")
    return text


def run_roles(cfg: dict, llm: LLM, paper: Paper, related_text: str, out_dir: Path, log=print) -> str:
    paper_block = f"# Paper: {paper.title}\n\n{paper.full_text}"

    advocate = _step("advocate", out_dir, lambda: llm.complete(
        load_prompt(cfg, "prep_advocate"), paper_block, max_tokens=4000), log)

    skeptic = _step("skeptic", out_dir, lambda: llm.complete(
        load_prompt(cfg, "prep_skeptic"),
        f"{paper_block}\n\n# Advocate's notes\n\n{advocate}", max_tokens=4000), log)

    checker = _step("checker", out_dir, lambda: llm.complete(
        load_prompt(cfg, "prep_checker"),
        f"{paper_block}\n\n# Advocate's notes\n\n{advocate}\n\n# Skeptic's notes\n\n{skeptic}"
        f"\n\n# Related work\n\n{related_text}", max_tokens=4000), log)

    max_words = int(cfg.get("prep", {}).get("max_words", 900))
    brief = _step("brief_draft", out_dir, lambda: llm.complete(
        load_prompt(cfg, "prep_editor", max_words=max_words),
        f"Paper title: {paper.title}\n\n# Advocate's notes\n\n{advocate}\n\n# Skeptic's notes\n\n{skeptic}"
        f"\n\n# Checker's notes\n\n{checker}", max_tokens=3000), log)

    for attempt in range(2):  # models overshoot length limits; condense if needed
        words = len(brief.split())
        if words <= max_words * 1.1:
            break
        brief = _step(f"brief_condensed{attempt + 1}", out_dir, lambda: llm.complete(
            load_prompt(cfg, "prep_condense", words=words, max_words=max_words), brief, max_tokens=3000), log)
    return brief
