"""Orchestrator: audio source -> transcript -> name detection / triggers -> display + event log."""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

from assistant.answer import Answerer
from assistant.hands import Hand, HandQueue
from assistant.names import NameDetector
from assistant.questions import Question, QuestionCollector
from assistant.triggers import Gate, TriggerChecker
from core.llm import make_llm, same_resource
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

        t = cfg.get("trigger", {})
        self.trigger_llm = make_llm(cfg["llm"]["trigger"])
        self.checker = TriggerChecker(cfg, self.trigger_llm, brief)
        self.gate = Gate(t.get("thresholds", {}), float(t.get("cooldown_s", 180)), float(t.get("dedup_ratio", 70)))
        self.hands = HandQueue()

        # One call at a time per backend: roles on the same local Ollama share a lock (it
        # serializes anyway, and answers must not queue behind a trigger check); a role on a
        # cloud API gets its own, so a local trigger check never delays a cloud answer.
        self.shared_llm = same_resource(cfg["llm"]["answer"], cfg["llm"]["trigger"])
        self.answer_lock = asyncio.Lock()
        self.trigger_lock = self.answer_lock if self.shared_llm else asyncio.Lock()
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
                             "device": self.cfg.get("audio_device")})

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
        await self.send_status()
        # record the answer in the transcript so later prompts know the question was answered
        self.store.add(Segment(round(q.end + 0.01, 2), round(self.now, 2), res.text,
                               speaker=self.cfg.get("assistant_name", "Sherlock")))
        self.events.write("answer", id=aid, t=round(self.now, 2), question=question, q_start=q.start,
                          q_end=q.end, detected_t=round(detected_t, 2), text=res.text, latency_s=round(e2e, 2),
                          first_words_s=first_words and round(first_words, 2),
                          llm_s=round(res.latency_s, 2), first_token_s=res.first_token_s and round(res.first_token_s, 2),
                          cited=res.cited, sources=res.sources,
                          served_by=repr(getattr(self.answer_llm, "last_used", self.answer_llm)))
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
        interval, window = float(t.get("interval_s", 15)), float(t.get("window_s", 120))
        last_seen = 0
        next_t = interval
        while True:
            await self.source.clock.sleep_until(next_t)
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
            async with self.trigger_lock:
                t0 = time.monotonic()
                r = await asyncio.to_thread(self.checker.check, transcript, self.gate.raised_reasons)
                took = time.monotonic() - t0
            now = self.now
            # don't let slow checks crowd out answers: wait at least 1.5x the last check's duration
            next_t = max(next_t, now + 1.5 * took * self.source.clock.speed)
            outcome = self.gate.check(r, now, human)
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

    async def raise_hand(self, tid: str, r, now: float, transcript: str) -> None:
        hid = f"H{len(self.hands.hands) + 1}"
        # prepare the interjection now so Reveal is instant; it is not shown until revealed
        async with self.answer_lock:
            # retrieve with the person's words plus the checker's short reason (which names the topic)
            res = await self.answerer.run("interjection", f"{r.quote} {r.reason}", transcript, trigger=r.trigger,
                                          reason=f'"{r.quote}" ({r.reason})', max_sentences=2)
        hand = Hand(hid, round(now, 2), tid, r.trigger, r.confidence, r.reason, res.text)
        self.hands.add(hand)
        self.events.write("hand", id=hid, t=hand.t, trigger_id=tid, trigger=r.trigger, confidence=r.confidence,
                          reason=r.reason, quote=r.quote, text=res.text, cited=res.cited, status="pending")
        log(f"    ✋ {hid} raised ({r.trigger}); prepared: {res.text}")
        await self.send_hand_state()

    async def on_action(self, action: str) -> None:
        status = {"reveal": "revealed", "dismiss": "dismissed"}[action]
        h = self.hands.resolve(status, self.now)
        if not h:
            return
        self.events.write("hand_status", id=h.id, status=status, t=round(self.now, 2))
        log(f"    ✋ {h.id} {status}")
        if status == "revealed":
            await self.hub.send({"type": "hand_revealed", "id": h.id, "trigger": h.trigger, "text": h.text})
        await self.send_hand_state()

    # ---------- lifecycle ----------

    async def run(self) -> None:
        d = self.cfg["display"]
        server = await serve(self.hub, d["host"], int(d["port"]))
        log(f"[assistant] display at http://{d['host']}:{d['port']}  mode={self.mode}  meeting={self.meeting_dir}")
        self.events.write("meeting_start", mode=self.mode, source=str(getattr(self.source, "path", "live")),
                          paper=self.paper.title if self.paper else None, brief=self.brief_path,
                          answer_llm=repr(self.answer_llm), trigger_llm=repr(self.trigger_llm),
                          stt_model=self.cfg["stt"].get("model"), speed=self.source.clock.speed)
        await self.send_status()
        t0 = time.monotonic()
        try:
            await asyncio.to_thread(self.answerer.warmup)
            log(f"[assistant] answer model warm ({time.monotonic() - t0:.1f}s)")
        except Exception as e:
            log(f"[assistant] WARNING: answer model warmup failed: {e}")
        loops = [asyncio.create_task(self.question_loop())]
        if hasattr(self.source, "last_sound"):
            loops.append(asyncio.create_task(self.audio_watch()))
        if self.mode == "engaged":
            loops.append(asyncio.create_task(self.trigger_loop()))
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
            for t in loops:
                t.cancel()
            await self.finish()
            server.server.should_exit = True
            await asyncio.gather(server, return_exceptions=True)

    async def finish(self) -> None:
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
