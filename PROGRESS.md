# Progress

Re-read this when resuming work. Plan: Phases 0–5 from `CLAUDE.md`.

## Status

| Phase | Status | Acceptance |
|---|---|---|
| 0 Skeleton | done | `python -m audio --list-devices` shows BlackHole 2ch; `pytest` green; `synthetic.wav` 5.0 min |
| 1 Prep brief | todo | |
| 2 Transcript | todo | |
| 3 Ask + display | todo | |
| 4 Meeting log | todo | |
| 5 Engaged mode | todo | |

## Environment (dev machine)

- MacBook Air M4, 16 GB. Ollama with `qwen2.5:7b`. BlackHole 2ch installed.
- `papers/test.pdf` is a symlink to `papers/GLD.pdf` ("Repurposing Geometric
  Foundation Models for Multi-view Diffusion"). PDFs are gitignored.

## Design decisions

- **Layout:** top-level packages `core/` (config, LLM backends, paper
  retrieval, transcript, clock, prompts), `prep/`, `audio/`, `assistant/`,
  `display/`, `log/`. Prompts are files in `prompts/` with `$var` substitution.
- **LLM interface:** `complete(system, user, json_mode, max_tokens, temperature)`
  plus `stream(...)`. Backends: ollama, claude-cli, gemini, anthropic, openai,
  fake. `make_llm(cfg["llm"][role])`. Gemini, Anthropic, and OpenAI are
  untested (no keys).
- **Paper context for answers:** a local 7B model can't take the whole paper
  (about 18k tokens) quickly. So answers use the brief plus the top-4 BM25
  chunks (600 chars with 200-char overlap, labelled with §/page).
  `answer.context: full_paper` is for large cloud models.
- **PDF text:** pypdf glues words ("useN= 2source"). The tokenizer splits at
  case and digit boundaries. Figure-label chunks are dropped. Section labels
  for chunks are only approximate: a table can float onto a page before its
  section heading.
- **Synthetic fixture:** made with `say` voices Samantha (Priya), Daniel (Tom),
  and Karen (Kate) at 165 wpm. Pauses between turns are 1.2–2.0 s, with 2.5 s
  after questions to Atlas and 3.0 s after the planted stall. Turn timings
  are in `tests/fixtures/synthetic_turns.json`. `synthetic.wav` is committed
  (gitignore exception).

## How to run

See README. Phase 0: `python -m audio --list-devices`, `pytest -q`,
`python tests/fixtures/make_synthetic.py`.

## Known issues

- (none yet)

## Needs a human to test live

- Zoom speaker output → BlackHole routing in the meeting room.
