# Reading Group Assistant

An AI participant for our research group's in-person paper reading group.
It acts like a well-prepared extra member: it answers when asked and, in
"engaged" mode, quietly raises a hand when it has something worth saying.
It never makes decisions and never interrupts on its own.

## How it is deployed

- The meeting room has a video-conferencing system running a Zoom call.
- A laptop joins that same Zoom call as a participant named
  "Reading Group Assistant", with its mic muted and video off.
- Zoom's speaker output is routed into a virtual audio device
  (BlackHole 2ch on macOS, VB-Cable on Windows). This app records from
  that device, so it hears the room mics plus any remote attendees.
- The app serves a local web page (localhost) that is screen-shared into
  the Zoom call and shown on the room display.
- Everything runs on the laptop. Speech-to-text and LLMs are pluggable
  backends (see Config). The first pass must run at zero cost: local STT,
  and LLMs that are local, free-tier, or the developer's Claude Code
  login. Paid APIs (Anthropic, Deepgram) are optional backends for later.

## Components

1. `prep/`: the pre-meeting brief
   - Input: paper PDF.
   - Fetch titles and abstracts of key references and citing papers
     (Semantic Scholar API; fall back to arXiv).
   - Run a structured discussion with three roles: advocate (contributions),
     skeptic (assumptions, baselines, evaluation), and checker (claims
     against related work). Each role uses a configurable LLM backend
     (default: `claude-cli`, i.e. `claude -p` headless mode on the
     developer's Claude Code login; latency doesn't matter here).
   - Output `brief.md` with key numbers and where they appear (section,
     table, figure, equation), likely confusions, weak points, and open
     questions. Max ~2 pages.

2. `audio/`: input and transcription
   - Live: stream from the configured input device to the STT backend.
     Default `faster-whisper`, run locally in short chunks (near real time).
     Speaker labels are optional in the first pass (speaker may be null).
     Optional backend: Deepgram streaming with diarization.
   - Replay: `--replay meeting.wav`. On first run, transcribe the file with
     the STT backend and cache the result. Then simulate the
     live stream from timestamps, with `--speed N` to run faster than real
     time. This is the primary development loop.
   - `--record` saves the live audio to WAV. With no flag, no audio is
     stored.
   - `--list-devices` prints available audio devices.
   - Rolling transcript stored as JSONL: start, end, speaker, text.

3. `assistant/`: deciding when and what to say
   - Modes chosen at startup: `ask` or `engaged`.
   - Ask mode: detect the assistant's name in the transcript. Use fuzzy
     matching because STT mangles names. Collect the question until a pause
     of about 1.5 s, then answer.
   - Engaged mode adds a trigger checker. Every ~15 s it runs a cheap model
     over the last ~2 min of transcript plus the brief and returns JSON:
     `{trigger: "contradiction" | "gap" | "none", confidence, reason}`.
     - contradiction: a claim that conflicts with the paper or brief
     - gap: an unanswered factual question the group stalls on
     - Apply a confidence threshold, a cooldown (default 3 min), and
       de-duplication of repeated points.
     - A trigger above threshold creates a queued "raised hand". It is
       never spoken or shown automatically.
   - Answers use the stronger model with the paper PDF and brief in context
     (prompt caching when the backend supports it) plus recent transcript. Max 3 sentences,
     always cite section, figure, table, or equation. Say "the paper doesn't
     say" rather than guess. Stream the text to the display.

4. `display/`: FastAPI + websocket page for the room screen
   - Latest answer, large readable font, dark background.
   - Raised-hand indicator showing only the trigger type, not the content.
   - "Reveal" button or keyboard shortcut to show the queued
     interjection, and "Dismiss" to drop it.
   - Small status line: mode, listening or not, latency of last answer.

5. `log/`: after the meeting
   - `meetings/<date>/log.md`: short summary, every answer given, every
     raised hand (revealed, dismissed, or ignored), and every trigger that
     fired below threshold, each with transcript context and a blank
     `helpful: yes/no` field for group members to fill in.
   - A script that reads labeled logs and reports precision per trigger
     type and threshold, to tune engaged mode.

## Config

All tunables live in `config.yaml`. Nothing below is hardcoded.

- `assistant_name` (default "Atlas"; pick something STT transcribes reliably)
- `audio_device` (default "BlackHole 2ch")
- `mode`: ask | engaged
- `stt_backend`: faster-whisper (default) | deepgram; whisper model size
- LLM backends, set separately for `prep`, `answer`, and `trigger`:
  - `ollama`: local open model; default for `answer` and `trigger`
  - `claude-cli`: shells out to `claude -p`; default for `prep`. Too slow
    for live triggers; acceptable for occasional live answers.
  - `gemini`: free tier via Google AI Studio key
  - `anthropic`, `openai`: paid APIs, for later
  - each with its own model id
- All backends share one small interface so switching is a config change.
- trigger interval, context window length, threshold per trigger type,
  cooldown
- prompts, stored as separate files under `prompts/`

API keys come from environment variables via a `.env` file. Never commit
`.env`. All keys are optional: the default config must run end to end
with no paid keys (faster-whisper + Ollama + claude-cli).

## Engineering conventions

- Python 3.11+, managed with uv. Pipecat for the live audio pipeline if it
  fits cleanly; otherwise use sounddevice plus the Deepgram SDK directly.
  Keep it simple.
- Every component must run against replay files without live audio.
- pytest for non-audio logic: name detection, trigger parsing, cooldown,
  logging. Keep a fixture transcript in `tests/fixtures/`.
- A clear README covering setup (BlackHole, Zoom settings, keys) and
  run commands.
- First pass builds Phases 0–5 in one go. After each phase, run its
  acceptance check against the replay fixture, fix failures, and commit
  before starting the next phase.
- Keep `PROGRESS.md` updated after every phase: status, design decisions,
  how to run it, known issues, and anything that needs a human to test
  live. Re-read it when resuming work.
- Don't stop to ask questions unless blocked (e.g. Ollama not installed,
  missing audio device). Make a reasonable choice, note it in
  `PROGRESS.md`, and keep going. Never require a paid API key.

## Build phases

Each phase ends with a demo command and acceptance check.

0. Skeleton: repo layout, config loading, `.env`, `--list-devices`, README.
   Done when the device list shows BlackHole.
1. Prep brief: `uv run prep papers/x.pdf` writes `brief.md`. Done when the
   brief for a real paper has correct key numbers with locations.
2. Transcript: live and replay modes produce JSONL transcripts. Done when
   replaying the test WAV yields a readable transcript at `--speed 4`.
3. Ask mode and display: name detection, grounded answers, web page.
   Done when, in replay, a question addressed to the assistant gets a cited
   answer on screen within ~5 s of the question ending.
4. Meeting log: post-meeting `log.md` with labelable entries.
5. Engaged mode: trigger checker, raised hand, reveal and dismiss, tuning
   script. Done when it runs on replay with a tunable precision report.
6. Later: voice output. TTS plays into a second virtual device
   ("BlackHole 16ch") that Zoom uses as its microphone. Also cross-session
   memory of past meetings.

## Privacy

- Everyone in the group consents before recording.
- Audio is only saved with `--record`.
- Transcripts and logs stay local under `meetings/`, which is gitignored.