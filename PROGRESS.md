# Progress

Re-read this when resuming work. Plan: Phases 0–5 from `CLAUDE.md`.

## Status

| Phase | Status | Acceptance |
|---|---|---|
| 0 Skeleton | done | `python -m audio --list-devices` shows BlackHole 2ch; `pytest` green; `synthetic.wav` 5.0 min |
| 1 Prep brief | done | `python -m prep papers/test.pdf` -> `briefs/test/brief.md` (935 words); all 8 key-number rows verified on cited page by `prep.verify`, Tables 2/3 hand-checked |
| 2 Transcript | done | `python -m audio --replay tests/fixtures/synthetic.wav --speed 4 --paper papers/test.pdf` streams a readable transcript; `tests/transcript_wer.py`: WER 7.5%, name heard 2/2 |
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
  after questions to Sherlock and 3.0 s after the planted stall. Turn timings
  are in `tests/fixtures/synthetic_turns.json`. `synthetic.wav` is committed
  (gitignore exception).

- **Prep pipeline:** related work comes from Semantic Scholar (no key; backs
  off on 429), with a fallback that parses the bibliography, ranks entries by
  in-text citation count, and looks them up on arXiv. Four `claude -p` calls
  (advocate → skeptic → checker → editor) take about 90 s total, plus up to
  two "condense" passes when the editor overshoots `prep.max_words` (900).
  Each role's output is cached in `briefs/<stem>/`. `--fresh` reruns.
  `prep.verify` checks every Key-numbers row against the PDF text of the cited
  page and appends a warning to the brief if any row fails.

- **Prep model:** `claude -p --model opus` (switched from sonnet at the user's
  request for best quality; prep runs once per paper, about 5 min). The Opus
  brief is 1,028 words after two condense passes, slightly over the 900 target
  but within the ~2-page limit.
- **Assistant name:** **Sherlock** (user's choice). Aliases include sherlok,
  shirlock, and "sure lock". Two-word aliases only match near-exactly and never
  after a determiner ("the lock-step" must not match). Single-word matches are
  ignored after a determiner.
- **STT model:** benchmarked on the fixture (`tests/stt_bench.py`, M4 CPU,
  int8, 4 threads):

  | model | file RTF | 10 s chunk | WER | name |
  |---|---|---|---|---|
  | small.en | 0.15 | 2.8 s | 7.5% | 2/2 |
  | medium.en | 0.43 | 8.7 s | 6.6% | 2/2 |
  | distil-large-v3 | 0.44 | 11.6 s | 6.7% | 2/2 |
  | large-v3-turbo | > 3 (stopped) | – | – | – |

  small.en is the default: bigger models gain about 1 point of WER at roughly
  3× the latency. faster-whisper has no Apple GPU support. If live accuracy
  is a problem, add an `mlx-whisper` backend.
- **STT priming:** the Whisper `initial_prompt` is the name, the paper title,
  and about 25 acronyms/jargon terms pulled from the paper (`key_terms`).
  Replay transcripts are cached in `meetings/.cache/` keyed by WAV, prompt,
  and model.
- **Replay timing:** segments are emitted at their `end` time on a SimClock,
  and every timer (question pause, trigger interval, cooldown) runs in
  meeting time, so `--speed N` scales everything consistently.

## How to run

See README. Phase 0: `python -m audio --list-devices`, `pytest -q`,
`python tests/fixtures/make_synthetic.py`.

## Known issues

- The synthetic `say` voice pronounces "VAE" so Whisper writes "V"; this is a
  TTS artifact, and real speech should be fine (vocabulary priming is on).
- Live chunking uses a simple energy VAD (`stt.energy_threshold`). It is
  untested on real room audio and may need tuning for Zoom levels.

## Needs a human to test live

- Zoom speaker output → BlackHole routing in the meeting room.
