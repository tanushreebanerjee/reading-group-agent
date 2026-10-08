# Reading Group Assistant

An AI participant for an in-person paper reading group. It listens to the
meeting through the room's Zoom call and shows answers on a local web page
that is screen-shared to the room display. In `ask` mode it answers when
addressed by name ("Sherlock, ..."). In `engaged` mode it also quietly raises a
hand when it spots a contradiction with the paper or a factual question the
group can't resolve. Nothing is shown until someone presses Reveal.

> **Privacy:** keep this repo outside iCloud-synced folders (`~/Desktop`,
> `~/Documents` when "Desktop & Documents" sync is on). Otherwise meeting
> transcripts and recordings under `meetings/` are uploaded to iCloud. The app
> warns at startup if this is the case.

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
   default config needs none of them: `GROQ_API_KEY`, `GEMINI_API_KEY`,
   `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `DEEPGRAM_API_KEY`, `OLLAMA_HOST`.

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

## Meeting day: recommended setup

Discuss mode (answers when asked, plus raised hands for corrections, unanswered
questions and points that add to the current topic), the best models we can run
(Qwen3.8 27B on a Nexus GPU with the whole paper in context), and Sherlock speaking
its answers and any hand you reveal. All of it comes from one flag, `--profile nexus`
(`profiles/nexus.yaml`). If Nexus is unreachable, each model falls back to Groq, then
the local one, automatically.

One-time: BlackHole 2ch and 16ch (`brew install blackhole-2ch blackhole-16ch`, or the
installers from https://existential.audio/blackhole/, then `sudo killall coreaudiod`), `GROQ_API_KEY` in `.env`, and one run of `scripts/nexus_up.sh`
(installs Ollama and the model in your Nexus scratch).

**Before the meeting (about 10 minutes)**

```bash
cd ~/Desktop/reading-group-agent && conda activate rga
python -m prep papers/<paper>.pdf          # the day before: briefs/<paper>/brief.md
# connect the UMD VPN, then:
scripts/nexus_up.sh                        # one Duo approval; wait for "ready"
```

In Zoom on the laptop (joined as "Reading Group Assistant"), Settings → Audio:
**Speaker: BlackHole 2ch** (or a Multi-Output Device with your headphones),
**Microphone: BlackHole 16ch**, unmuted; turn off "Automatically adjust microphone
volume" and set background noise suppression to Low.

```bash
python -m assistant --paper papers/<paper>.pdf --profile nexus
```

Wait for "model warm" (×3), "[voice] ready" and "listening on BlackHole 2ch". Screen-share
the display at http://127.0.0.1:8765; keep http://127.0.0.1:8765/control open on the
laptop only (it should show `qwen3.8:27b (nexus)` for all three models).

**Instead of screen-sharing**, add `--share`: Sherlock prints a link (also shown on /control
with a Copy button) to paste in the Zoom chat. Everyone, in the room or remote, opens the live
display in their own browser and can Reveal or Dismiss hands. The link has a secret key (without
it the page is refused), it can't open /control, and it stops working when Sherlock stops. It
goes through a free Cloudflare tunnel (cloudflared is downloaded to `~/.cache/rga/` the first
time) and can take about a minute to start working. Anyone with the link sees Sherlock's
answers and revealed hands (not the transcript).

**During:** "Sherlock, ..." then pause: the answer is shown and spoken. A ✋ shows only
its type (contradiction, gap, point); **Reveal** (R, or the button on /control) shows and
speaks it; the screen also shows the words it responds to and when they were said
(*Re: "..." (2:05)*). If you reveal it before its text is ready, the screen shows
"…preparing" for a few seconds. **D**
dismisses, **S** stops speech. Points nobody reveals lower themselves after 2 minutes.

**After:** Ctrl-C (writes `meetings/<date>/log.md`), then `scripts/nexus_down.sh` to free
the GPU. Fill in `helpful: yes/no` in the log and run `python -m log.tune meetings/`.

**Trying it on the laptop alone** (laptop mic and speakers, no Zoom):

```bash
python -m assistant --paper papers/GLD.pdf --profile nexus \
  --set 'audio_device="MacBook Air Microphone"' --set voice.output_device=default
```

Zoom never plays your own voice back to you, so to test the Zoom path alone, join the
meeting from your phone as well and talk into the phone.

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
python -m assistant --paper papers/paper.pdf --profile nexus   # recommended (above)
python -m assistant --paper papers/paper.pdf --mode ask        # zero-cost defaults: local + Groq
python -m assistant --paper papers/paper.pdf --mode engaged
python -m assistant --paper papers/paper.pdf --mode discuss
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

End-to-end checks against the synthetic meeting (need Ollama running):

```bash
python tests/e2e_check.py --phase 3        # ask mode on replay: answers, citations, display, log
python tests/e2e_check.py --phase 5        # engaged mode, real time: hands, reveal/dismiss, log
python tests/loopback_check.py             # live path: plays the WAV into BlackHole, app listens live
```

`tests/fixtures/synthetic_script.md` is the ground truth for
`tests/fixtures/synthetic.wav`. It is a 5-minute, 3-voice discussion of
`papers/test.pdf` with two questions addressed to Sherlock, one wrong claim,
and one unanswered factual question.

Model comparison on questions the brief doesn't cover (`tests/fixtures/qa_out_of_brief.yaml`):
`python tests/qa_eval.py [--models nexus nexus-excerpts groq local]`. Last run: Nexus with the
whole paper 10/11, Nexus with excerpts 8/11, local 7B 6/11.

## Live controls

Open `http://127.0.0.1:8765/control` on the laptop (don't screen-share it). It shows
what's listening and which models are in use, has Reveal/Dismiss buttons, and lets you
change the mode, the answer and hand-checker models, answer length, excerpts per answer,
trigger interval, cooldown and thresholds mid-meeting. Changes last for the session and are
listed in `log.md`. The model list comes from `model_presets` in `config.yaml` (cloud
presets appear only when their key is in `.env`) plus every model in local Ollama. The room
display's status line shows the models in use.

## Spoken answers (optional)

Sherlock can speak its answers into the Zoom call. It's off by default: turn it on with
`--set voice.mode=answers` (or `answers+reveal` to also speak a raised hand after Reveal),
or from the control page.

1. `brew install blackhole-16ch` (a second virtual device, separate from the 2ch one Zoom
   plays into), then restart the audio service as for BlackHole 2ch.
2. In Zoom on the assistant laptop: **Microphone → BlackHole 16ch**, and unmute.
3. The voice is Kokoro, running locally (free). On Apple Silicon it runs on the GPU via MLX
   (`voice.engine: auto`), about 1 s to first audio; elsewhere it uses the CPU (ONNX, ~3 s).
   Model files download on first use (Hugging Face cache for MLX, `~/.cache/kokoro-onnx` for ONNX). `voice.backend: say` uses macOS voices
   instead; `groq` uses Orpheus on Groq once you accept its terms in the Groq console.
4. To try it without Zoom, set `voice.output_device: default` to hear it on the laptop speakers.

Listening pauses while Sherlock speaks (plus `voice.echo_tail_s`), because the room mics
pick up its voice and send it back through Zoom; otherwise it would transcribe itself and
could answer its own answer. **S** on the room display, or **Stop speaking** on the control
page, cuts it off.

## Nexus GPU (optional, UMD)

Runs Qwen3.8 27B on one Nexus GPU (answers see the whole paper) for answers and raised-hand checks: no rate limits,
nothing leaves UMD, and hand checks take ~1–2 s instead of 10–20 s. Audio, Whisper, the
display and the voice stay on the Mac; only prompt text goes through an ssh tunnel.

Before the meeting (allow 5–10 minutes; the first run also downloads Ollama and the ~20 GB
model into `/fs/nexus-scratch/$USER/rga-ollama`):

```bash
# 1. connect the UMD VPN
scripts/nexus_up.sh        # one Duo prompt; submits the GPU job, waits, opens the tunnel
scripts/nexus_up.sh --check
```

Then start the assistant with `--profile nexus` (see Meeting day), or pick
**Nexus · Qwen3.8 27B, whole paper** for individual models on the control page.

```bash
scripts/nexus_down.sh
```

The job runs two model servers on its A6000 (two copies of the model, ~35 GB): one only for
answers, with the whole paper in context (tunnelled to port 11435), and one for hand checks and
hand text, which use short prompts (port 11436). Keeping answers on their own server keeps their
cached paper from being displaced; with one shared server, answers sometimes took 30–80 s.

Defaults: account `vulcan-zwicker`, partition `vulcan-ampere`, QOS `vulcan-default`, one RTX A6000 (48 GB), 4 h. For a 120B-class model use `RGA_NEXUS_GRES=gpu:rtxa6000:2` or the H200 node (`RGA_NEXUS_QOS=vulcan-default-h200 RGA_NEXUS_GRES=gpu:h200-sxm:1`) with `RGA_NEXUS_MODELS=...` and a matching preset.
Override with `RGA_NEXUS_ACCOUNT`, `RGA_NEXUS_PARTITION`, `RGA_NEXUS_QOS`, `RGA_NEXUS_GRES`
(e.g. `gpu:rtxa6000:1` to pin a 48 GB card), `RGA_NEXUS_TIME`, `RGA_NEXUS_HOST` (ssh alias,
default `umiacs`). Avoid scavenger partitions: their jobs can be preempted mid-meeting.

## Configuration

Every tunable is in `config.yaml`. Machine- or group-specific overrides go in
`config.local.yaml` (gitignored). For example, `group_members: [...]` primes
speech recognition with attendees' names. Prompts live in `prompts/*.md`. LLM
backends are set separately for `prep`, `answer`, `trigger`, and `summary`:

| backend | notes |
|---|---|
| `ollama` | local; default for answer/trigger/summary |
| `claude-cli` | `claude -p` on your Claude Code login; default for prep; too slow for triggers. `ANTHROPIC_API_KEY` is hidden from it so it never bills your API account (set `use_api_key: true` to allow) |
| `groq` | free tier, fast; needs `GROQ_API_KEY` (console.groq.com). Sends the transcript to Groq |
| `cerebras`, `openrouter` | free tiers, same OpenAI-style API; `CEREBRAS_API_KEY` / `OPENROUTER_API_KEY` (untested) |
| `gemini` | free tier, needs `GEMINI_API_KEY` (untested) |
| `anthropic`, `openai` | paid, for later (untested). `openai` also takes `base_url` and `api_key_env` for any OpenAI-compatible server |
| `fake` | canned responses for tests |

Any role can have a `fallback:` backend that takes over when the primary
fails (missing key, network error, rate limit); the primary is retried after
`retry_after_s`. Example: fast free answers on Groq that fall back to local
Ollama. Put this in `config.local.yaml` and `GROQ_API_KEY=...` in `.env`, and
get the group's OK first, since the recent transcript goes to Groq:

```yaml
llm:
  answer:
    backend: groq
    model: qwen/qwen3.8-27b
    # hidden thinking (~+1 s); max_tokens must cover the thinking too
    extra: {reasoning_effort: high, reasoning_format: hidden}
    max_tokens: 1500
    timeout_s: 20
    fallback: {backend: ollama, model: qwen2.5:7b, num_ctx: 8192, keep_alive: 30m}
```

Keep `trigger` local: it runs ~4 times a minute and would hit Groq's free
free limits (about 8k tokens/minute and 1,000 requests/day for this model).
Groq's model list changes; `curl -s -H "Authorization: Bearer $GROQ_API_KEY" https://api.groq.com/openai/v1/models` shows what your key can use. Each answer's `served_by` field in
`events.jsonl` records which backend actually answered.

To use a large cloud model for answers, also set `answer.context: full_paper`.
This sends the whole paper instead of retrieved snippets.
