# Reading Group Assistant

An AI participant for an in-person paper reading group. It listens to the
meeting through the room's Zoom call and shows answers on a local web page
that is screen-shared to the room display. In `ask` mode it answers when
addressed by name ("Sherlock, ..."). In `engaged` mode it also quietly raises a
hand when it spots a contradiction with the paper or a factual question the
group can't resolve. Nothing is shown until someone presses Reveal.

The default setup costs nothing: local speech-to-text (faster-whisper), a
local LLM (Ollama), and `claude -p` (your Claude Code login) for the
pre-meeting brief. Paid backends are optional config changes.

See `PROGRESS.md` for build status and known issues.

## Setup (macOS)

1. **Conda env** (Python 3.11):
   ```bash
   conda env create -f environment.yml    # first time
   conda env update -f environment.yml --prune   # after dependency changes
   conda activate rga
   ```
2. **Ollama** (https://ollama.com), then pull the default model:
   ```bash
   ollama pull qwen2.5:7b
   ```
3. **Claude Code** logged in (`claude` on PATH). Used only by `prep`.
4. **BlackHole 2ch** virtual audio device:
   ```bash
   brew install blackhole-2ch
   python -m audio --list-devices      # should show "BlackHole 2ch  <-- configured input"
   ```
5. **Keys (optional).** Copy any keys into `.env` (never commit it). The
   default config needs none of them: `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`,
   `OPENAI_API_KEY`, `DEEPGRAM_API_KEY`, `OLLAMA_HOST`.

The first speech-to-text run downloads the Whisper model (`small.en`,
about 500 MB) into the Hugging Face cache.

## Zoom settings on the assistant laptop

The laptop joins the room's Zoom call as a separate participant.

1. Rename yourself in Zoom to **Reading Group Assistant**. Mic muted, video off.
2. Zoom → Settings → Audio → **Speaker: BlackHole 2ch**. The app records
   from BlackHole, so it hears the room mics plus any remote attendees.
   - To also hear the call on the laptop, open Audio MIDI Setup, create a
     **Multi-Output Device** with BlackHole 2ch + your headphones, and pick
     that as Zoom's speaker instead.
   - Turn off Zoom's "Automatically adjust volume" if levels pump.
3. Start the app (below), open `http://127.0.0.1:8765` in a browser, and
   **Share Screen → that browser window**. Keep the window focused on the
   laptop so the keyboard shortcuts work: **R** reveals a raised hand,
   **D** dismisses it.

## Running

```bash
# 0. devices
python -m audio --list-devices

# 1. pre-meeting brief (a few minutes; uses claude -p)
python -m prep papers/paper.pdf                # -> briefs/paper/brief.md

# 2. transcript only (live, or replay a recording)
python -m audio                                # live from BlackHole, Ctrl-C to stop
python -m audio --replay meeting.wav --speed 4

# 3. the assistant (display at http://127.0.0.1:8765)
python -m assistant --paper papers/paper.pdf --mode ask
python -m assistant --paper papers/paper.pdf --mode engaged
python -m assistant --paper papers/paper.pdf --replay tests/fixtures/synthetic.wav --speed 4

# 4. after the meeting: fill in the helpful: yes/no fields in meetings/<date>/log.md, then
python -m log.tune meetings/
```

Any config value can be overridden for one run with `--set`, e.g.
`--set trigger.cooldown_s=120 --set trigger.thresholds.gap=0.8`.

Add `--record` to save live audio as WAV. Without it, no audio is stored.

### Engaged mode: raised hands

Every 15 s a trigger check reads the last 2 minutes of transcript plus the
brief. It decides whether someone stated something that contradicts the paper
(`contradiction`), or the group stalled on a factual question the paper
answers (`gap`). If the confidence clears the per-type threshold, and the
point is not a repeat or inside the 3-minute cooldown, the screen shows
**✋ CONTRADICTION** or **✋ GAP**, never the content. Press **R** (or the
Reveal button) to show what the assistant wants to say, or **D** to dismiss
it. Hands nobody acts on are logged as ignored.

### After the meeting: label and tune

`meetings/<date>/log.md` lists the summary, every answer, every raised hand,
and every trigger that fired but stayed hidden (below threshold, cooldown, or
duplicate). Each entry has its transcript context. Group members fill in
`helpful: yes` or `helpful: no`, then:

```bash
python -m log.tune meetings/        # precision per trigger type at each threshold
```

Raise a threshold in `config.yaml` if precision is low at the current value.
Lower it if many below-threshold triggers were labelled helpful.
Transcripts and logs stay under `meetings/`, which is gitignored.

## Tests

```bash
pytest -q                                   # unit tests, no audio or LLM needed
python tests/fixtures/make_synthetic.py     # regenerate the synthetic meeting (macOS say)
```

`tests/fixtures/synthetic_script.md` is the ground truth for
`tests/fixtures/synthetic.wav`. It is a 5-minute, 3-voice discussion of
`papers/test.pdf` with two questions addressed to Sherlock, one wrong claim,
and one unanswered factual question.

## Configuration

Every tunable is in `config.yaml`. Machine- or group-specific overrides go in
`config.local.yaml` (gitignored). For example, `group_members: [...]` primes
speech recognition with attendees' names. Prompts live in `prompts/*.md`. LLM
backends are set separately for `prep`, `answer`, `trigger`, and `summary`:

| backend | notes |
|---|---|
| `ollama` | local; default for answer/trigger/summary |
| `claude-cli` | `claude -p` on your Claude Code login; default for prep; too slow for triggers |
| `gemini` | free tier, needs `GEMINI_API_KEY` (untested) |
| `anthropic`, `openai` | paid, for later (untested) |
| `fake` | canned responses for tests |

To use a large cloud model for answers, also set `answer.context: full_paper`.
This sends the whole paper instead of retrieved snippets.
