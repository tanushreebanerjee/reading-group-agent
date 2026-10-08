"""Compare answer models on questions whose answers are in the paper but not the brief.

python tests/qa_eval.py                       # default model set
python tests/qa_eval.py --models nexus groq   # a subset

Models (each without fallback, so a failure shows as a failure):
  nexus           Nexus qwen3.8:27b, whole paper in context   (needs scripts/nexus_up.sh)
  nexus-excerpts  same model, 3 retrieved excerpts            (isolates the effect of context)
  groq            Groq qwen/qwen3.8-27b, 3 retrieved excerpts (free tier; paced for rate limits)
  local           local Ollama qwen2.5:7b, 3 retrieved excerpts
Scores: an answer is correct if every expect_terms group appears in it.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from assistant.answer import Answerer, has_citation, plain_text, trim_sentences  # noqa: E402
from core.config import load_config  # noqa: E402
from core.llm import LLMError, make_llm  # noqa: E402
from core.paper import load_paper  # noqa: E402

NEXUS = {"backend": "ollama", "model": "qwen3.8:27b", "host": "http://127.0.0.1:11435", "num_ctx": 32768,
         "think": False, "keep_alive": "24h", "timeout_s": 60, "max_tokens": 200, "temperature": 0.2}
MODELS = {
    "nexus": {**NEXUS, "context": "full_paper"},
    "nexus-excerpts": {**NEXUS, "context": "retrieval"},
    "groq": {"backend": "groq", "model": "qwen/qwen3.8-27b", "extra": {"reasoning_effort": "none"},
             "max_tokens": 200, "temperature": 0.2, "timeout_s": 30, "max_retry_wait_s": 0},
    "local": {"backend": "ollama", "model": "qwen2.5:7b", "num_ctx": 8192, "keep_alive": "30m",
              "max_tokens": 200, "temperature": 0.2, "context": "retrieval"},
}


def matches(answer: str, groups: list[str]) -> list[bool]:
    a = answer.lower()
    return [any(re.search(alt.lower(), a) for alt in g.split("|")) for g in groups]


def ask(answerer: Answerer, q: str, max_wait: float = 90) -> tuple[str, float | None, float]:
    """Stream one answer; on a rate limit wait as asked (up to max_wait) and retry."""
    while True:
        system, user, _ = answerer.build("answer", q, "(none)")
        t0, first, out = time.monotonic(), None, ""
        try:
            for piece in answerer.llm.stream(system, user):
                first = first if first is not None else time.monotonic() - t0
                out += piece
            return trim_sentences(plain_text(out), 3), first, time.monotonic() - t0
        except LLMError as e:
            wait = getattr(e, "retry_after", None)
            if wait is None or wait > max_wait:
                return f"(failed: {str(e)[:120]})", None, time.monotonic() - t0
            print(f"    rate limited, waiting {wait:.0f}s", flush=True)
            time.sleep(wait + 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", default=list(MODELS), choices=list(MODELS))
    ap.add_argument("--questions", default=str(ROOT / "tests/fixtures/qa_out_of_brief.yaml"))
    ap.add_argument("--groq-gap", type=float, default=25, help="seconds between Groq calls (8k input tokens/min)")
    args = ap.parse_args()
    spec = yaml.safe_load(open(args.questions))
    cfg = load_config()
    a = cfg["answer"]
    paper = load_paper(ROOT / spec["paper"], int(a["chunk_chars"]), cfg["paths"]["cache_dir"], int(a["chunk_overlap"]))
    brief = (ROOT / spec["brief"]).read_text()
    qs = spec["questions"]
    summary = {}
    for name in args.models:
        llm = make_llm(MODELS[name])
        ans = Answerer(cfg, llm, brief, paper)
        print(f"\n## {name}: {llm!r}, context={ans.context_mode()}", flush=True)
        try:
            t0 = time.monotonic(); ans.warmup(); print(f"   warmup {time.monotonic() - t0:.1f}s", flush=True)
        except Exception as e:
            print(f"   unavailable: {e}"); continue
        right, firsts, cited = 0, [], 0
        for i, item in enumerate(qs, 1):
            text, first, total = ask(ans, item["q"])
            ok = matches(text, item["expect_terms"])
            right += all(ok); cited += has_citation(text)
            if first is not None:
                firsts.append(first)
            print(f"{'✓' if all(ok) else '✗'} Q{i} ({first or 0:.1f}s): {item['q']}\n    A: {text}"
                  + ("" if all(ok) else f"\n    expected: {item['truth']}"), flush=True)
            if name == "groq" and i < len(qs):
                time.sleep(args.groq_gap)
        firsts.sort()
        med = firsts[len(firsts) // 2] if firsts else float("nan")
        summary[name] = (right, len(qs), cited, med)
    print("\n| model | correct | cited | median first words |\n|---|---|---|---|")
    for name, (r, n, c, med) in summary.items():
        print(f"| {name} | {r}/{n} | {c}/{n} | {med:.1f} s |")


if __name__ == "__main__":
    main()
