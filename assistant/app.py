"""Orchestrator: audio source -> transcript -> name detection / triggers -> display + event log."""
from __future__ import annotations

import asyncio
import copy
import sys
import threading
import time
from pathlib import Path

from assistant.answer import Answerer
from assistant.hands import Hand, HandQueue, spoken_intro
from assistant.names import NameDetector
from assistant.questions import Question, QuestionCollector
from assistant import settings as S
from assistant.triggers import HAND_MODES, MODE_TYPES, Gate, TriggerChecker
from core.llm import make_llm, same_resource, served_by
from core.paper import Paper
from core.transcript import Segment, TranscriptStore, format_segments
from display.server import Hub, serve
from log.events import EventLog


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


class App:
    def __init__(self, cfg: dict, source, paper: Paper | None, brief: str, meeting_dir: Path,
                 hold_s: float = 0.0, brief_path: str | None = None):
        self.brief_path = brief_path
        self.cfg = cfg
        self.mode = cfg.get("mode", "ask")
        self.source = source
        self.paper = paper
        self.brief = brief
        self.meeting_dir = meeting_dir
        self.hold_s = hold_s

        self.store = TranscriptStore(meeting_dir / "transcript.jsonl")
        self.events = EventLog(meeting_dir / "events.jsonl")
        self.hub = Hub()
        self.hub.on_action = self.on_action

        self.names = NameDetector(cfg.get("assistant_name", "Sherlock"), cfg.get("name_aliases"),
                                  int(cfg.get("name_search_words", 8)), float(cfg.get("name_match_ratio", 80)))
        self.collector = QuestionCollector(float(cfg.get("question_pause_s", 1.5)),
                                           float(cfg.get("question_max_s", 30)))
        self.answer_llm = make_llm(cfg["llm"]["answer"])
        self.answerer = Answerer(cfg, self.answer_llm, brief, paper)
        # raised-hand text is prepared in the background when a hand goes up, long before
        # Reveal, so it may use a slower, more careful model than live answers (llm.interjection)
        cfg["llm"].setdefault("interjection", copy.deepcopy(cfg["llm"]["answer"]))
        self.interject_llm = make_llm(cfg["llm"]["interjection"])
        self.interjector = Answerer(cfg, self.interject_llm, brief, paper)

        t = cfg.get("trigger", {})
        self.trigger_llm = make_llm(cfg["llm"]["trigger"])
        self.checker = TriggerChecker(cfg, self.trigger_llm, brief, paper, mode=self.mode)
        self.gate = Gate(t.get("thresholds", {}), float(t.get("cooldown_s", 180)), float(t.get("dedup_ratio", 70)),
                         type_cooldown_s=dict(t.get("type_cooldown_s") or {}),
                         allowed=MODE_TYPES.get(self.mode, MODE_TYPES["engaged"]))
        self.hands = HandQueue()

        # mid-meeting settings (control page): what each role's model came from
        self.role_base = {r: copy.deepcopy(cfg["llm"][r]) for r in ("answer", "trigger", "interjection")}
        self.model_choice = {"answer": S.FROM_CONFIG, "trigger": S.FROM_CONFIG, "interjection": S.FROM_CONFIG}
        self.model_opts: dict[str, dict] = {}
        self.trigger_task: asyncio.Task | None = None

        # voice output (off unless voice.mode is answers or answers+reveal)
        self.speaker = None
        self.speaking = False
        self.loop: asyncio.AbstractEventLoop | None = None

        # One call at a time per backend: roles on the same local Ollama share a lock (it
        # serializes anyway, and answers must not queue behind a trigger check); a role on a
        # cloud API gets its own, so a local trigger check never delays a cloud answer.
        self.shared_llm = same_resource(cfg["llm"]["answer"], cfg["llm"]["trigger"])
        self.answer_lock = asyncio.Lock()
        self.trigger_lock = self.answer_lock if self.shared_llm else asyncio.Lock()
        self.interject_lock = self.lock_for("interjection")
        self.answering = 0
        self.n_answers = 0
        self.n_triggers = 0
        self.last_latency: float | None = None
        self.listening = False
        self.audio_ok = True               # False while a live input has been silent too long
        self.tasks: set[asyncio.Task] = set()

    # ---------- helpers ----------

    @property
    def now(self) -> float:
        return self.source.clock.now()

    def spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self._task_done)
        return task

    def _task_done(self, task: asyncio.Task) -> None:
        self.tasks.discard(task)
        if not task.cancelled() and task.exception():
            import traceback

            log("[assistant] ERROR in background task:\n" + "".join(
                traceback.format_exception(task.exception())))

    async def send_status(self) -> None:
        await self.hub.send({"type": "status", "mode": self.mode, "listening": self.listening,
                             "latency_s": self.last_latency, "name": self.cfg.get("assistant_name"),
                             "source": "replay" if getattr(self.source, "path", None) else "live",
                             "heard": len(self.store.segments), "audio_ok": self.audio_ok,
                             "device": self.cfg.get("audio_device"), "models": self.models_in_use(),
                             "voice": self.voice_mode(), "speaking": self.speaking})

    def models_in_use(self) -> dict:
        def show(role, llm):
            # walk the fallback chain to the level currently serving requests
            fell_back = False
            while getattr(llm, "fallback", None) is not None and not llm._use_primary():
                llm, fell_back = llm.fallback, True
            cfg = llm.primary.cfg if hasattr(llm, "primary") else llm.cfg
            return S.describe(cfg) + (" (fallback)" if fell_back else "")
        return {"answer": show("answer", self.answer_llm), "trigger": show("trigger", self.trigger_llm),
                "interjection": show("interjection", self.interject_llm), "stt": self.cfg["stt"].get("model")}

    async def send_hand_state(self) -> None:
        head = self.hands.head()
        n = len(self.hands.pending)
        msg = {"type": "hand_raised" if n else "hand_cleared", "count": n,
               "id": head.id if head else None, "trigger": head.trigger if head else None}
        await self.hub.send(msg)

    def recent_transcript(self, seconds: float) -> str:
        return format_segments(self.store.window(self.now, seconds))

    # ---------- transcript ----------

    async def consume(self) -> None:
        self.listening = True
        await self.send_status()
        async for seg in self.source.segments():
            self.on_segment(seg)
        self.listening = False
        await self.send_status()

    def on_segment(self, seg: Segment) -> None:
        self.store.add(seg)
        log(f"[{seg.start:7.1f}] {seg.text}")
        if len(self.store.segments) % 5 == 1:   # keep the display's "heard" count roughly current
            self.spawn(self.send_status())
        if self.collector.collecting:
            self.collector.add(seg)
            return
        hit = self.names.detect(seg.text)
        if hit:
            log(f"    -> name heard ({hit.matched!r}, {hit.score:.0f}); collecting question")
            self.collector.start(seg, hit.rest)

    async def question_loop(self) -> None:
        while True:
            q = self.collector.poll(self.now, self.source.pending_start())
            if q:
                self.spawn(self.answer(q))
            await asyncio.sleep(0.05)

    # ---------- ask mode ----------

    async def answer(self, q: Question) -> None:
        self.n_answers += 1
        aid = f"A{self.n_answers}"
        question = q.text or "(no question heard)"
        detected_t = self.now
        log(f"    -> {aid} question: {question!r}")
        self.answering += 1
        try:
            await self.hub.send({"type": "answer_start", "id": aid, "question": question})

            async def on_delta(piece: str):
                await self.hub.send({"type": "answer_delta", "id": aid, "text": piece})

            async with self.answer_lock:
                res = await self.answerer.answer(question, self.recent_transcript(
                    float(self.cfg["answer"].get("transcript_window_s", 90))), on_delta)
        finally:
            self.answering -= 1
        # end-to-end latency in wall seconds, measured from the end of the question audio:
        # first words on screen (answers stream) and complete answer
        e2e = (self.now - q.end) / self.source.clock.speed
        first_words = e2e - res.latency_s + res.first_token_s if res.first_token_s is not None else None
        self.last_latency = first_words if first_words is not None else e2e
        await self.hub.send({"type": "answer_done", "id": aid, "question": question, "text": res.text,
                             "latency_s": self.last_latency, "complete_s": e2e, "cited": res.cited})
        if self.voice_mode() in ("answers", "answers+reveal") and not res.text.startswith("(answer failed"):
            self.speak(res.text)
        await self.send_status()
        # record the answer in the transcript so later prompts know the question was answered
        self.store.add(Segment(round(q.end + 0.01, 2), round(self.now, 2), res.text,
                               speaker=self.cfg.get("assistant_name", "Sherlock")))
        self.events.write("answer", id=aid, t=round(self.now, 2), question=question, q_start=q.start,
                          q_end=q.end, detected_t=round(detected_t, 2), text=res.text, latency_s=round(e2e, 2),
                          first_words_s=first_words and round(first_words, 2),
                          llm_s=round(res.latency_s, 2), first_token_s=res.first_token_s and round(res.first_token_s, 2),
                          cited=res.cited, sources=res.sources,
                          served_by=repr(served_by(self.answer_llm)))
        log(f"    -> {aid} (first words {first_words or -1:.1f}s / complete {e2e:.1f}s after question end, llm {res.latency_s:.1f}s"
            f"{'' if res.cited else ', NO CITATION'}): {res.text}")

    async def audio_watch(self) -> None:
        """Live input only: warn (terminal + display) when the device has been silent for a while,
        which almost always means Zoom's speaker is not routed into it."""
        after = float(self.cfg["stt"].get("silence_warn_s", 20))
        while True:
            await asyncio.sleep(2)
            src = self.source
            if src.started_at is None:
                continue
            quiet = time.monotonic() - (src.last_sound or src.started_at)
            ok = quiet < after
            if ok != self.audio_ok:
                self.audio_ok = ok
                if ok:
                    log(f"[audio] sound is coming in from {self.cfg.get('audio_device')} again")
                else:
                    log(f"[audio] WARNING: no sound from {self.cfg.get('audio_device')} for {quiet:.0f}s. "
                        f"Is Zoom's speaker (Settings > Audio > Speaker) set to it, and is the call live? "
                        f"Quick test: say -a \"{self.cfg.get('audio_device')}\" \"{self.cfg.get('assistant_name')}, "
                        f"what is this paper about?\"")
                await self.send_status()

    # ---------- engaged mode ----------

    async def trigger_loop(self) -> None:
        t = self.cfg.get("trigger", {})
        last_seen = 0
        next_t = self.now + float(t.get("interval_s", 15))
        while True:
            await self.source.clock.sleep_until(next_t)
            interval, window = float(t.get("interval_s", 15)), float(t.get("window_s", 120))
            next_t = self.now + interval
            if len(self.store.segments) == last_seen:
                continue  # nothing new was said
            # While a question to the assistant is open or being answered, skip: it is not a
            # stalled gap, and on a shared local model the check would also delay the answer.
            if self.answering or self.collector.collecting or (self.shared_llm and self.answer_lock.locked()):
                self.events.write("trigger_skip", t=round(self.now, 2), why="busy")
                next_t = self.now + 2.0  # retry shortly instead of losing a whole interval
                continue
            last_seen = len(self.store.segments)
            transcript = self.recent_transcript(window)
            name = self.cfg.get("assistant_name", "Sherlock")
            human = [s.text for s in self.store.window(self.now, window) if s.speaker != name]
            # a "point" must add to what was said in the last few seconds, not an old thread
            recent = [s.text for s in self.store.window(self.now, float(t.get("point_recent_s", 60)))
                      if s.speaker != name]
            async with self.trigger_lock:
                t0 = time.monotonic()
                r = await asyncio.to_thread(self.checker.check, transcript, self.gate.raised_reasons)
                took = time.monotonic() - t0
            now = self.now
            # don't let slow checks crowd out answers: wait at least 1.5x the last check's duration
            next_t = max(next_t, now + 1.5 * took * self.source.clock.speed)
            outcome = self.gate.check(r, now, human, recent)
            await self.expire_hands(now)
            if outcome == "none":
                log(f"    .. trigger check at {now:.0f}s: none ({took:.1f}s)")
                continue
            self.n_triggers += 1
            tid = f"T{self.n_triggers}"
            self.events.write("trigger", id=tid, t=round(now, 2), trigger=r.trigger, confidence=r.confidence,
                              reason=r.reason, quote=r.quote, outcome=outcome, llm_s=round(took, 2),
                              raw=r.raw if outcome == "invalid" else None,
                              window_start=round(max(0.0, now - window), 2))
            log(f"    .. trigger {tid} at {now:.0f}s: {r.trigger} {r.confidence:.2f} -> {outcome} ({took:.1f}s): "
                f"{r.quote!r} | {r.reason}")
            if outcome == "raised":
                await self.raise_hand(tid, r, now, transcript)

    def quote_time(self, quote: str) -> float | None:
        """Meeting time of the transcript line that best matches a quote (for 'Re: ... (2:05)')."""
        from rapidfuzz import fuzz

        q = quote.lower().strip()
        best = max(((fuzz.partial_ratio(q, s.text.lower()), s.start) for s in self.store.segments
                    if s.speaker != self.cfg.get("assistant_name", "Sherlock")), default=(0, None))
        return round(best[1], 2) if q and best[0] >= 80 else None

    async def expire_hands(self, now: float) -> None:
        """Lower hands nobody revealed in time (trigger.expire_s per type): the topic has moved on."""
        gone = self.hands.expire(now, self.cfg.get("trigger", {}).get("expire_s") or {})
        for h in gone:
            self.events.write("hand_status", id=h.id, status="expired", t=round(now, 2))
            log(f"    ✋ {h.id} expired ({h.trigger}, not revealed within its time limit)")
        if gone:
            await self.send_hand_state()

    async def raise_hand(self, tid: str, r, now: float, transcript: str) -> None:
        hid = f"H{len(self.hands.hands) + 1}"
        # prepare the interjection now so Reveal is instant; it is not shown until revealed
        async with self.interject_lock:
            # retrieve with the person's words plus the checker's short reason (which names the topic)
            res = await self.interjector.run("interjection", f"{r.quote} {r.reason}", transcript, trigger=r.trigger,
                                          reason=f'"{r.quote}" ({r.reason})', max_sentences=2)
        hand = Hand(hid, round(now, 2), tid, r.trigger, r.confidence, r.reason, res.text,
                    quote=r.quote, quote_t=self.quote_time(r.quote))
        self.hands.add(hand)
        self.events.write("hand", id=hid, t=hand.t, trigger_id=tid, trigger=r.trigger, confidence=r.confidence,
                          reason=r.reason, quote=r.quote, text=res.text, cited=res.cited, status="pending")
        log(f"    ✋ {hid} raised ({r.trigger}); prepared: {res.text}")
        await self.send_hand_state()

    async def on_action(self, msg: dict) -> None:
        action = msg.get("action")
        if action == "set":
            await self.apply_setting(str(msg.get("key")), msg.get("value"))
            return
        if action == "stop_speaking":
            if self.speaker:
                await asyncio.to_thread(self.speaker.stop)
                log("[voice] stopped")
            return
        if action not in ("reveal", "dismiss"):
            return
        status = {"reveal": "revealed", "dismiss": "dismissed"}[action]
        h = self.hands.resolve(status, self.now)
        if not h:
            return
        self.events.write("hand_status", id=h.id, status=status, t=round(self.now, 2))
        log(f"    ✋ {h.id} {status}")
        if status == "revealed":
            await self.hub.send({"type": "hand_revealed", "id": h.id, "trigger": h.trigger, "text": h.text,
                                 "quote": h.quote, "quote_t": h.quote_t})
            if self.voice_mode() == "answers+reveal":
                self.speak(" ".join(x for x in (spoken_intro(h), h.text) if x))
        await self.send_hand_state()

    # ---------- voice output ----------

    def voice_mode(self) -> str:
        mode = (self.cfg.get("voice") or {}).get("mode", "off")
        return "off" if mode in (False, None, "") else str(mode)   # YAML reads a bare off as False

    def make_speaker(self):
        from audio.tts import Speaker, make_tts

        v = self.cfg.get("voice") or {}
        sp = Speaker(make_tts(v), v.get("output_device"), on_start=self._spoke_start, on_end=self._spoke_end)
        sp.gain = float(v.get("gain", 1.0))
        log(f"[voice] {v.get('backend', 'kokoro')} voice, speaking into {sp.device_name}")
        return sp

    def speak(self, text: str) -> None:
        if self.speaker is None:
            try:
                self.speaker = self.make_speaker()
            except Exception as e:
                log(f"[voice] unavailable: {e}")
                return
        self.speaker.say(text)

    def _spoke_start(self) -> None:          # called on the speaker thread
        log(f"[voice] speaking (t={self.now:.1f})")
        if hasattr(self.source, "muted"):
            self.source.muted = True
        self._voice_state(True)

    def _spoke_end(self) -> None:            # called on the speaker thread
        tail = float((self.cfg.get("voice") or {}).get("echo_tail_s", 0.5))
        def unmute():
            if hasattr(self.source, "muted") and not (self.speaker and self.speaker.speaking):
                self.source.muted = False
        threading.Timer(tail, unmute).start()
        log(f"[voice] done (t={self.now:.1f})")
        self._voice_state(False)

    def _voice_state(self, speaking: bool) -> None:
        self.speaking = speaking
        if self.loop:
            self.loop.call_soon_threadsafe(lambda: self.spawn(self.send_status()))

    # ---------- mid-meeting settings ----------

    def settings_state(self) -> dict:
        fields = []
        for f in S.FIELDS:
            d = f.as_dict()
            if f.kind == "model":
                role = f.key.split(".")[1]
                d["options"] = [S.FROM_CONFIG + " · " + S.describe(self.role_base[role])] + list(self.model_opts)
                d["value"] = (d["options"][0] if self.model_choice[role] == S.FROM_CONFIG
                              else self.model_choice[role])
            else:
                d["value"] = self.mode if f.key == "mode" else S.get_path(self.cfg, f.key)
            fields.append(d)
        return {"type": "settings", "fields": fields}

    async def send_settings(self, error: str | None = None) -> None:
        msg = self.settings_state()
        if error:
            msg["error"] = error
        await self.hub.send(msg)

    async def apply_setting(self, key: str, value) -> None:
        f = S.BY_KEY.get(key)
        try:
            if not f:
                raise ValueError(f"unknown setting {key!r}")
            value = S.coerce(f, value)
            if f.kind == "model":
                await self.set_model(key.split(".")[1], value)
            elif key == "mode":
                self.set_mode(value)
            elif key.startswith("voice."):
                S.set_path(self.cfg, key, value)
                if self.speaker:
                    await asyncio.to_thread(self.speaker.stop)
                self.speaker = None   # rebuilt (new backend/voice) on next use
                if self.voice_mode() != "off":
                    self.spawn(asyncio.to_thread(self.warm_voice))
            else:
                S.set_path(self.cfg, key, value)
                for a in (self.answerer, self.interjector):
                    a.k = int(self.cfg["answer"].get("retrieval_k", 3))
                    a.max_sentences = int(self.cfg["answer"].get("max_sentences", 3))
                    a.mode = self.cfg["answer"].get("context", "retrieval")
                self.collector.pause_s = float(self.cfg.get("question_pause_s", 1.5))
                th = self.cfg["trigger"]
                self.gate.thresholds = dict(th.get("thresholds", {}))
                self.gate.cooldown_s = float(th.get("cooldown_s", 180))
        except Exception as e:
            log(f"[settings] {key} = {value!r} rejected: {e}")
            await self.send_settings(error=str(e))
            return
        log(f"[settings] {key} = {value!r}")
        self.events.write("setting", t=round(self.now, 2), key=key, value=value)
        await self.send_settings()
        await self.send_status()

    async def set_model(self, role: str, label: str) -> None:
        if label.startswith(S.FROM_CONFIG):
            role_cfg, label = copy.deepcopy(self.role_base[role]), S.FROM_CONFIG
        elif label in self.model_opts:
            role_cfg = S.role_config(self.model_opts[label], self.role_base[role])
        else:
            raise ValueError(f"unknown model {label!r}")
        llm = make_llm(role_cfg)
        self.cfg["llm"][role] = role_cfg
        self.model_choice[role] = label
        if role == "answer":
            self.answer_llm = self.answerer.llm = llm
            self.spawn(asyncio.to_thread(self.answerer.warmup))   # load/prefill the new model now
        elif role == "interjection":
            self.interject_llm = self.interjector.llm = llm
        else:
            self.trigger_llm = self.checker.llm = llm
        self.shared_llm = same_resource(self.cfg["llm"]["answer"], self.cfg["llm"]["trigger"])
        self.trigger_lock = self.answer_lock if self.shared_llm else asyncio.Lock()
        self.interject_lock = self.lock_for("interjection")

    def lock_for(self, role: str) -> asyncio.Lock:
        """Share the answer lock with a role only if both run on one single-request server."""
        return self.answer_lock if same_resource(self.cfg["llm"]["answer"], self.cfg["llm"][role]) else asyncio.Lock()

    def set_mode(self, mode: str) -> None:
        self.mode = self.cfg["mode"] = mode
        self.checker.mode = mode
        self.gate.allowed = MODE_TYPES.get(mode, ())
        if mode in HAND_MODES and not (self.trigger_task and not self.trigger_task.done()):
            self.trigger_task = asyncio.create_task(self.trigger_loop())
        elif mode == "ask" and self.trigger_task:
            self.trigger_task.cancel()
            self.trigger_task = None

    async def warm(self, name: str, fn) -> None:
        t0 = time.monotonic()
        try:
            await asyncio.to_thread(fn)
            log(f"[assistant] {name} model warm ({time.monotonic() - t0:.1f}s)")
        except Exception as e:
            log(f"[assistant] WARNING: {name} warmup failed: {e}")

    def warm_voice(self) -> None:
        """Load the voice model now so the first spoken answer isn't delayed by it."""
        try:
            if self.speaker is None:
                self.speaker = self.make_speaker()
            self.speaker.tts.warmup()
            log("[voice] ready")
        except Exception as e:
            log(f"[voice] unavailable: {e}")

    def list_ollama_models(self) -> list[str]:
        try:
            import ollama

            r = ollama.Client(host=self.cfg["llm"]["answer"].get("host")).list()
            models = r.get("models", []) if isinstance(r, dict) else getattr(r, "models", [])
            return sorted((m.get("model") or m.get("name")) if isinstance(m, dict) else m.model for m in models)
        except Exception:
            return []

    # ---------- lifecycle ----------

    async def run(self) -> None:
        d = self.cfg["display"]
        server = await serve(self.hub, d["host"], int(d["port"]))
        log(f"[assistant] display at http://{d['host']}:{d['port']}  mode={self.mode}  meeting={self.meeting_dir}")
        self.events.write("meeting_start", mode=self.mode, source=str(getattr(self.source, "path", "live")),
                          paper=self.paper.title if self.paper else None, brief=self.brief_path,
                          answer_llm=repr(self.answer_llm), trigger_llm=repr(self.trigger_llm),
                          interjection_llm=repr(self.interject_llm),
                          stt_model=self.cfg["stt"].get("model"), speed=self.source.clock.speed)
        await self.send_status()
        t0 = time.monotonic()
        try:
            await asyncio.to_thread(self.answerer.warmup)
            log(f"[assistant] answer model warm ({time.monotonic() - t0:.1f}s)")
        except Exception as e:
            log(f"[assistant] WARNING: answer model warmup failed: {e}")
        # the hand checker and hand-text models may be different (e.g. a GPU model with the whole
        # paper in its prompt): warm them in the background so the first hand isn't slow
        if self.mode in HAND_MODES:
            for name, fn in (("hand checker", self.checker.warmup), ("hand text", self.interjector.warmup)):
                self.spawn(self.warm(name, fn))
        loops = [asyncio.create_task(self.question_loop())]
        if hasattr(self.source, "last_sound"):
            loops.append(asyncio.create_task(self.audio_watch()))
        if self.mode in HAND_MODES:
            self.trigger_task = asyncio.create_task(self.trigger_loop())
        self.loop = asyncio.get_running_loop()
        if self.voice_mode() != "off":
            self.spawn(asyncio.to_thread(self.warm_voice))
        self.model_opts = S.model_options(self.cfg, await asyncio.to_thread(self.list_ollama_models))
        await self.send_settings()
        try:
            await self.consume()
            # replay finished: let a trailing question close, then wait for in-flight work
            await asyncio.sleep(float(self.cfg.get("question_pause_s", 1.5)) / self.source.clock.speed + 0.2)
            while self.tasks or self.collector.collecting:
                await asyncio.sleep(0.1)
            if self.hold_s:
                log(f"[assistant] replay done; holding {self.hold_s:.0f}s for reveal/dismiss")
                await asyncio.sleep(self.hold_s)
        finally:
            for t in loops + ([self.trigger_task] if self.trigger_task else []):
                t.cancel()
            await self.finish()
            server.server.should_exit = True
            await asyncio.gather(server, return_exceptions=True)

    async def finish(self) -> None:
        if self.speaker:
            await asyncio.to_thread(self.speaker.stop)
        for h in self.hands.close(self.now):
            self.events.write("hand_status", id=h.id, status="ignored", t=round(self.now, 2))
        self.events.write("meeting_end", t=round(self.now, 2))
        self.events.close()
        self.store.close()
        try:
            from log.build import build_log

            path = await asyncio.to_thread(build_log, self.cfg, self.meeting_dir)
            log(f"[assistant] meeting log: {path}")
        except Exception as e:
            log(f"[assistant] could not build log.md: {e}")
