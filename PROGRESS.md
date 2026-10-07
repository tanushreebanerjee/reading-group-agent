# Progress

Re-read this when resuming work. Plan: Phases 0–5 from `CLAUDE.md`.

## Status

| Phase | Status | Acceptance |
|---|---|---|
| 0 Skeleton | done | `python -m audio --list-devices` shows BlackHole 2ch; `pytest` green; `synthetic.wav` 5.0 min |
| 1 Prep brief | done | `python -m prep papers/test.pdf` -> `briefs/test/brief.md` (935 words); all 8 key-number rows verified on cited page by `prep.verify`, Tables 2/3 hand-checked |
| 2 Transcript | done | `python -m audio --replay tests/fixtures/synthetic.wav --speed 4 --paper papers/test.pdf` streams a readable transcript; `tests/transcript_wer.py`: WER 7.5%, name heard 2/2 |
| 3 Ask + display | done, latency target missed locally | `tests/e2e_check.py --phase 3`: both questions answered correctly with citations, streamed to the display, no spurious answers. First words 8–13 s after the question locally; the ~5 s target needs a GPU backend (see Known issues) |
| 4 Meeting log | done | after each run `meetings/<dir>/log.md` has the summary, every answer, every hand (revealed/dismissed/ignored), and every suppressed trigger, each with ±45 s transcript context, a machine marker, and a blank `helpful:` line; `tests/test_log.py` round-trips labels through `log.tune` |
| 5 Engaged mode | done | `tests/e2e_check.py --phase 5` (real time): contradiction hand raised 28 s after the wrong claim with the exact quote and a correct §4.3/Table 2 correction; the stalled source-views gap raised with the right answer (N=2); 0 false hands; reveal/dismiss/ignored all logged; `tests/label_synthetic.py` + `python -m log.tune` print precision per type/threshold. Answer latency still over target (see Known issues) |

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

- **Live answer prompt layout (latency):** every live prompt (answer,
  interjection, trigger) starts with the same prefix (identity + brief) and
  puts per-question content (excerpts, transcript) in the user turn. Ollama
  reuses the cached prefix: a new question costs ~0.4 s for the prefix instead
  of ~20 s. The app prefills the prefix at startup. Roles sharing a model use
  the same `num_ctx`; a mismatch makes Ollama reload the model.
- **Measured on the M4 Air (qwen2.5:7b, 100% GPU):** generation runs at ~8
  tokens/s, and new prompt tokens are read at ~80–200 tokens/s. With 3 chunks
  of 450 chars and a 45 s transcript window, first words appear 6–13 s after
  the question and the full answer at 11–19 s. qwen2.5:3b is about 2× faster
  but got 1 of 4 test answers wrong and cited nothing, so it is not used for
  answers.
- **Answer latency** is logged two ways: first words on screen (answers
  stream) and complete answer, both measured from the end of the question
  audio.
- **Display:** FastAPI + websocket on 127.0.0.1:8765. The hub replays current
  state to a reloaded page. Raised hands send only the trigger type; content
  is sent on reveal. Keys: R reveal, D dismiss.
- Model output is cleaned of LaTeX/markdown before display.

- **Meeting log:** `events.jsonl` is append-only during the meeting.
  `log.md` is built at shutdown (`python -m log.build <dir>` rebuilds it into
  `log.rebuilt.md` without overwriting labels). Raised triggers appear once,
  as hands. Below-threshold, cooldown, and duplicate triggers are listed
  separately so they can be labelled too.
- **Summary:** 5 bullets max, from the summary LLM with the brief as context,
  so claims from the meeting are reported as discussion, not paper facts.
  Long meetings are summarized in chunks, then merged.
- **Local overrides:** `config.local.yaml` (gitignored) is merged over
  `config.yaml`. It holds `group_members` (lab names), which prime Whisper.

- **Trigger grounding:** the checker must return the speaker's exact words
  (`quote`). The gate rejects a trigger as `ungrounded` unless the quote
  fuzzy-matches (partial_ratio ≥ 80) a human line in the window. Without this,
  qwen2.5:7b raised hands for "open questions" copied from the brief that
  nobody said, at confidence 1.0. The prompt also has a confidence rubric and
  one example per trigger type.
- **Trigger scheduling:** checks wait at least 1.5× the previous check's
  duration (each takes 10–20 s locally), retry 2 s after a busy skip, and
  never run while an answer or question is in progress. The assistant's own
  answers go into the transcript (speaker = its name), so answered questions
  aren't flagged as gaps.
- **De-duplication** compares quote + reason against earlier hands. Cooldown
  is global (3 min default). The e2e fixture uses `--set trigger.cooldown_s=30`
  because its two planted events are 48 s apart; the default is covered by
  unit tests.
- **Tuning report:** precision counts raised hands plus below-threshold and
  cooldown triggers. Duplicate, ungrounded, and invalid triggers are listed
  separately, since a threshold change wouldn't affect them.
- **Any config value** can be overridden per run: `--set key.sub=value`.

- **Live-path test without Zoom:** `tests/loopback_check.py` plays the fixture
  into BlackHole's output while the app runs in live mode on its input. Result:
  48 segments, WER 9.4%, name 2/2, both answers correct and cited, first words
  12–15 s after the question (live Whisper chunks included). This found two
  bugs replay could not:
  1. Live questions absorbed the next speaker's turn ("busy" held them open).
     The collector now only waits for speech that *started* before the pause
     ended (`pending_start()`).
  2. iCloud Desktop sync replaced a freshly created `transcript.jsonl`, so a
     held file handle wrote into an orphaned copy. Writers now reopen per
     append and rewrite the full file on close.

## How to run

See README. Phase 0: `python -m audio --list-devices`, `pytest -q`,
`python tests/fixtures/make_synthetic.py`.

## Known issues

- The synthetic `say` voice pronounces "VAE" so Whisper writes "V"; this is a
  TTS artifact, and real speech should be fine (vocabulary priming is on).
- Live chunking uses a simple energy VAD (`stt.energy_threshold`). It is
  untested on real room audio and may need tuning for Zoom levels.

- **Privacy: the repo is on an iCloud-synced Desktop.** Everything under
  `meetings/` (transcripts, logs, `--record` audio) is uploaded to iCloud,
  which contradicts "stays local". The app warns at startup. Fix (needs the
  user): move the repo outside ~/Desktop and ~/Documents, or set
  `paths.meetings_dir` to a non-synced folder.
- **Answer latency on the MacBook Air is 6–13 s to first words, not ~5 s.**
  The fix is to run the LLM on a GPU: the UMD Nexus cluster (Ampere). Plan:
  start an Ollama server in a SLURM GPU job, open an SSH tunnel to
  localhost:11434, and set `OLLAMA_HOST` in `.env`. Audio, Whisper, and the
  display stay on the Mac with Zoom; only LLM text goes over the tunnel. This
  also allows a larger model (e.g. qwen2.5:32b). Still to do: an sbatch
  script and automatic fallback to the local model if the tunnel drops.
  Needs the user's Nexus account, partition, and QOS.
- Memory: with qwen2.5:7b + 3b both loaded, free memory dropped to 18% and
  prefill slowed about 2×. Keep only one model resident on the 16 GB Air.

- The 7B summary sometimes mixes brief content into "what was discussed" and
  can misstate facts (on the fixture it said the paper doesn't give the number
  of source views; §5.1 says 2). A stronger model (GPU backend) should help.

- **Trigger confidence is coarse:** qwen2.5:7b mostly outputs 0.8 or 0.9, so
  threshold tuning has little resolution until we collect real labels or use
  a stronger model.
- **Gap hands are slow:** the source-views gap was raised 86 s after the
  question, since each check takes 10–20 s and waits behind answers.
- **Interjections can hedge or cite a weaker location:** the gap interjection
  said "2 source views" but cited App. A.3 instead of §5.1 and added a
  self-contradicting hedge. Earlier runs before the fixes had one invented
  number ("DINO 16.386").

## Needs a human to test live

- Zoom speaker output → BlackHole routing in the meeting room.
