# Live test script (papers/GLD.pdf)

Say these out loud to exercise every ability. Expected answers were checked against the paper.

Start with short cooldowns so a 15-minute test isn't blocked by the meeting defaults (3 min between
hands, 5 min between points):

```bash
python -m assistant --paper papers/GLD.pdf --profile nexus \
  --set 'audio_device="MacBook Air Microphone"' --set voice.output_device=default \
  --set trigger.cooldown_s=20 --set trigger.type_cooldown_s.point=20
```

(In Zoom, drop the two `audio_device`/`output_device` settings and speak through a phone that joined
the meeting.) Keep `/control` open: it shows the models, and the terminal prints every check as
`.. trigger ... -> raised | cooldown | stale | already_said | below_threshold | none`.
Pause about 2 s after each question, and 10–15 s after each hand scenario (checks run every 10 s).

## 1. Ask mode: direct questions

| Say | Expect |
|---|---|
| "Sherlock, what PSNR does GLD get on RealEstate10K compared to the VAE?" | 16.362 vs 15.656 (Table 3). In the brief. |
| "Sherlock, and how does that compare to DINO?" | 15.638: uses the previous answer as context. |
| "Sherlock, which datasets is it trained on?" | Re10K, DL3DV, HyperSim, TartanAir (§5.1). Not in the brief. |
| "Sherlock, what guidance scale do they use at inference?" | CFG 1.5, with 10% camera-embedding dropout (§5.1). Not in the brief. |
| "Sherlock, how long does it take to generate one scene?" | 66.1 s sampling vs 28.0 s for the VAE (Table 15). |
| "Sherlock, how much did training cost in dollars?" | "The paper doesn't say." (No guessing.) |
| "Sherlock, what are the main weaknesses of the paper?" | Up to 3 sentences from the brief's weak points, with locations. |
| "Hey Sherlock ... um ... how many GPUs did they train on?" (with a hesitation) | 8 B200 GPUs, 175k iterations (§5.1): the question is collected until the pause. |
| "What's the batch size?" (no name) | No answer: only questions addressed to Sherlock get one. |

Each answer appears on the display and is spoken; the terminal shows the latency.

## 2. Engaged mode: corrections (contradiction)

| Say | Expect |
|---|---|
| "So they found the deepest level, level 3, works best as the synthesis boundary." | ✋ contradiction: level 1 is best (Table 2, §4.3). |
| "They train for something like 500 thousand iterations." | ✋ contradiction: 175k iterations (§5.1). |
| "The main backbone is VGGT, right? That's what they use throughout." | ✋ contradiction: DA3 is the main backbone; VGGT only in the appendix (App. C.1). |
| "They pick level 1 as the boundary." (correct) | No hand. |
| "I think the figures are really nice." (opinion) | No hand. |

Press **R** (display) or **Reveal** (/control): the screen shows *Re: "..." (time)* and the voice starts
"Earlier, someone said: ...". Press **D** on another to dismiss it.

## 3. Engaged mode: unanswered questions (gap)

| Say | Expect |
|---|---|
| "How many source views do they use in the main evaluation? ... I honestly don't remember. ... Let's move on." | ✋ gap: N = 2, first and last frames (§5.1, App. A.3). |
| "What guidance scale do they use? ... No idea. Anyway." | ✋ gap: CFG 1.5 (§5.1). |
| "How many source views do they use? ... Oh, it's two, first and last frame." (answered) | No hand. |

## 4. Discuss mode: points that add to the topic

| Say | Expect |
|---|---|
| "I wonder if this is slower than a VAE at inference. I think there's a cost section in the appendix but I haven't read it." | ✋ point: 66.1 s vs 28.0 s; the level-0 cascade adds 28.4 s (Table 15). |
| "I'm curious how well the decoder can reconstruct images from those features." | ✋ point: 35.41 PSNR vs 34.53 for SD-VAE (Table 7). |
| "The depth maps look great, but I wonder how they evaluate them." | ✋ point: ETH3D AbsRel 0.160 vs Matrix3D 0.197, on only 50 samples (Table 9). |
| "Table 15 says 66 seconds versus 28 for the VAE, so it's slower." | No hand: you already said it (`already_said`). |
| Talk about lunch or next week's schedule for ~30 s | No hand. |
| Bring up inference cost, then talk about something else for 2 minutes without revealing | The point hand expires (`✋ H… expired`). |
| Make a point, then immediately make a wrong claim (section 2) | The contradiction is raised anyway and is revealed first (corrections outrank points). |

## 5. Controls and voice

| Do | Expect |
|---|---|
| Press **S** while Sherlock is speaking | Speech stops within ~0.1 s. |
| Talk while Sherlock is speaking | Not transcribed (listening pauses while it speaks). |
| On /control, switch Mode to `ask` | No more hands; answers still work. |
| On /control, switch the answer model to a local one | The next answer comes from it (slower); the status line shows it. |
| Mute the source audio for 20 s (live mode) | Status turns red: "no audio from ...". |
| Ctrl-C | ~50 s later `meetings/<date>/log.md` has a summary, every answer and hand with context, and `helpful:` lines. |

## 6. Fallback (optional)

Run `scripts/nexus_down.sh` mid-session (or disconnect the VPN), then ask a question: the terminal
shows `[llm] ... failed ... using <groq ...>` and the answer still arrives, from excerpts instead of
the whole paper.
