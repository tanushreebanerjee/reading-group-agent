"""Grounded answers: brief + retrieved paper excerpts + recent transcript -> streamed answer."""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from core.llm import LLM
from core.paper import Paper, Retriever
from core.prompts import load_prompt

CITATION_RE = re.compile(r"§\s*[A-Z]?\d|\b(?:Sec(?:tion)?\.?|Table|Tab\.|Fig(?:ure)?\.?|Eq(?:uation)?\.?|"
                         r"App(?:endix)?\.?)\s*\(?[A-Z]?\d", re.I)


def has_citation(text: str) -> bool:
    return bool(CITATION_RE.search(text))


def trim_sentences(text: str, n: int) -> str:
    """Hard cap on sentence count (models occasionally ignore the instruction)."""
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z\"'(])", text.strip())
    return " ".join(parts[:n]).strip()


@dataclass
class AnswerResult:
    text: str
    latency_s: float          # wall-clock from request to last token
    first_token_s: float | None
    cited: bool
    sources: list[str]


class Answerer:
    def __init__(self, cfg: dict, llm: LLM, brief: str, paper: Paper | None):
        self.cfg = cfg
        self.llm = llm
        self.brief = brief or "(no brief available)"
        self.paper = paper
        self.retriever = Retriever(paper) if paper else None
        a = cfg.get("answer", {})
        self.k = int(a.get("retrieval_k", 4))
        self.mode = a.get("context", "retrieval")
        self.max_sentences = int(a.get("max_sentences", 3))

    def excerpts(self, query: str) -> tuple[str, list[str]]:
        if not self.paper:
            return "(paper text not loaded)", []
        if self.mode == "full_paper":
            return self.paper.full_text, ["full paper"]
        chunks = self.retriever.search(query, self.k)
        return Retriever.format(chunks), [c.label for c in chunks]

    def build(self, prompt_name: str, question: str, transcript: str, **extra) -> tuple[str, str, list[str]]:
        excerpts, sources = self.excerpts(f"{question} {extra.get('reason', '')}")
        system = load_prompt(self.cfg, prompt_name, name=self.cfg.get("assistant_name", "Sherlock"),
                             max_sentences=self.max_sentences, brief=self.brief, excerpts=excerpts)
        user = load_prompt(self.cfg, f"{prompt_name}_user", transcript=transcript or "(none)",
                           question=question, **extra)
        return system, user, sources

    async def run(self, prompt_name: str, question: str, transcript: str,
                  on_delta: Callable[[str], Awaitable[None]] | None = None,
                  max_sentences: int | None = None, **extra) -> AnswerResult:
        system, user, sources = self.build(prompt_name, question, transcript, **extra)
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()
        t0 = time.monotonic()

        def worker():
            try:
                for piece in self.llm.stream(system, user):
                    loop.call_soon_threadsafe(q.put_nowait, piece)
            except Exception as e:  # surface backend errors as the answer text
                loop.call_soon_threadsafe(q.put_nowait, e)
            loop.call_soon_threadsafe(q.put_nowait, None)

        fut = loop.run_in_executor(None, worker)
        pieces, first = [], None
        while True:
            item = await q.get()
            if item is None:
                break
            if isinstance(item, Exception):
                pieces = [f"(answer failed: {item})"]
                break
            if first is None:
                first = time.monotonic() - t0
            pieces.append(item)
            if on_delta:
                await on_delta(item)
        await fut
        text = trim_sentences("".join(pieces), max_sentences or self.max_sentences)
        return AnswerResult(text, time.monotonic() - t0, first, has_citation(text), sources)

    async def answer(self, question: str, transcript: str, on_delta=None) -> AnswerResult:
        return await self.run("answer", question, transcript, on_delta)
