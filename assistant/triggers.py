"""Engaged mode: periodic trigger checks and the gate that turns them into raised hands."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from core.llm import LLM
from core.prompts import load_prompt, system_prompt

TRIGGER_TYPES = ("contradiction", "gap", "point")
# which hand types each mode may raise
MODE_TYPES = {"engaged": ("contradiction", "gap"), "discuss": ("contradiction", "gap", "point")}
HAND_MODES = tuple(MODE_TYPES)


@dataclass
class TriggerResult:
    trigger: str               # contradiction | gap | point | none
    confidence: float
    reason: str
    raw: str = ""
    valid: bool = True
    quote: str = ""


def parse_trigger(raw: str) -> TriggerResult:
    """Parse the checker's JSON robustly: code fences, extra prose, bad types, unknown labels."""
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    m = re.search(r"\{.*\}", text, re.S)
    try:
        data = json.loads(m.group(0) if m else text)
    except (json.JSONDecodeError, AttributeError):
        return TriggerResult("none", 0.0, "unparseable", raw, valid=False)
    if not isinstance(data, dict):
        return TriggerResult("none", 0.0, "not an object", raw, valid=False)
    trig = str(data.get("trigger", "none")).strip().lower()
    if trig not in TRIGGER_TYPES:
        trig = "none"
    try:
        conf = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    if conf > 1.0 and conf <= 100.0:  # some models answer in percent
        conf /= 100.0
    conf = min(1.0, max(0.0, conf))
    reason = str(data.get("reason", "")).strip()
    quote = str(data.get("quote", "")).strip().strip('"')
    return TriggerResult(trig, conf, reason, raw, quote=quote)


def quote_is_grounded(quote: str, human_lines: list[str], min_ratio: float = 80) -> bool:
    """True if the quote really appears (fuzzily) in something a person said.

    Stops the checker from "raising" points it took from the brief instead of the meeting.
    """
    q = quote.lower().strip()
    if len(q) < 8:
        return False
    return any(fuzz.partial_ratio(q, line.lower()) >= min_ratio for line in human_lines if line)


def already_said(point: str, human_lines: list[str], min_ratio: float = 80) -> bool:
    """True if someone already made this point (so raising it would add nothing)."""
    p = point.lower().strip()
    return bool(p) and any(fuzz.token_set_ratio(p, line.lower()) >= min_ratio for line in human_lines if line)


@dataclass
class Gate:
    """Threshold per type, a global cooldown (meeting seconds), and de-duplication.

    "point" hands (discuss mode) must also quote something said in the last few seconds
    (recent_lines: they add to the live thread, not an old one), must not repeat what someone
    already said, and have their own, longer cooldown (type_cooldown_s)."""

    thresholds: dict
    cooldown_s: float = 180.0
    dedup_ratio: float = 70.0
    type_cooldown_s: dict = field(default_factory=dict)
    allowed: tuple = ("contradiction", "gap")
    last_raised_t: float | None = None
    last_by_type: dict = field(default_factory=dict)
    raised_reasons: list[str] = field(default_factory=list)

    def check(self, r: TriggerResult, now: float, human_lines: list[str] | None = None,
              recent_lines: list[str] | None = None) -> str:
        """Return the outcome: raised | below_threshold | cooldown | duplicate | ungrounded |
        stale | already_said | none | invalid."""
        if not r.valid:
            return "invalid"
        if r.trigger == "none" or r.trigger not in self.allowed:
            return "none"
        if human_lines is not None and not quote_is_grounded(r.quote, human_lines):
            return "ungrounded"
        if r.trigger == "point":
            if recent_lines is not None and not quote_is_grounded(r.quote, recent_lines):
                return "stale"
            if human_lines is not None and already_said(r.reason, human_lines):
                return "already_said"
        if r.confidence < float(self.thresholds.get(r.trigger, 1.01)):
            return "below_threshold"
        key = f"{r.quote} {r.reason}"
        if self.is_duplicate(key):
            return "duplicate"
        # Corrections and unanswered questions outrank points: a point never delays them, so
        # their cooldown counts only their own hands; a point waits for every kind of hand.
        major = [t for k, t in self.last_by_type.items() if k != "point"]
        since = max(major, default=None) if r.trigger != "point" else self.last_raised_t
        if since is not None and now - since < self.cooldown_s:
            return "cooldown"
        own = self.type_cooldown_s.get(r.trigger)
        last = self.last_by_type.get(r.trigger)
        if own is not None and last is not None and now - last < float(own):
            return "cooldown"
        self.last_raised_t = now
        self.last_by_type[r.trigger] = now
        self.raised_reasons.append(key)
        return "raised"

    def is_duplicate(self, reason: str) -> bool:
        return any(fuzz.token_set_ratio(reason.lower(), prev.lower()) >= self.dedup_ratio
                   for prev in self.raised_reasons)


class TriggerChecker:
    def __init__(self, cfg: dict, llm: LLM, brief: str, paper=None, mode: str = "engaged"):
        self.cfg = cfg
        self.llm = llm
        self.brief = brief or "(no brief available)"
        self.paper = paper
        self.mode = mode
        self.retriever = None
        if paper is not None:
            from core.paper import Retriever

            self.retriever = Retriever(paper)

    def build(self, transcript: str, already_raised: list[str]) -> tuple[str, str]:
        from core.llm import active

        full = self.paper is not None and active(self.llm).cfg.get("context") == "full_paper"
        types = MODE_TYPES.get(self.mode, MODE_TYPES["engaged"])
        point_rule = load_prompt(self.cfg, "trigger_point") if "point" in types else ""
        system = system_prompt(self.cfg, self.brief, "trigger", paper_text=self.paper.full_text if full else None,
                               point_rule=point_rule.strip(),
                               types=" | ".join(f'"{t}"' for t in types + ("none",)))
        raised = "\n".join(f"- {r}" for r in already_raised) or "(none)"
        # Without the whole paper, give the checker the passages that match what was just said
        # (a short prompt keeps frequent checks fast on any model; the hand text that follows
        # may still see the whole paper).
        excerpts = "(the full paper text is in the context above)" if full else "(none)"
        if not full and self.retriever is not None and transcript:
            k = int(self.cfg.get("trigger", {}).get("retrieval_k", 3))
            recent = " ".join(transcript.splitlines()[-8:])
            from core.paper import Retriever

            excerpts = Retriever.format(self.retriever.search(recent, k)) or "(none)"
        user = load_prompt(self.cfg, "trigger_user", transcript=transcript or "(silence)", raised=raised,
                           excerpts=excerpts)
        return system, user

    def warmup(self) -> None:
        """Prefill the shared prefix (identity + brief [+ paper]) so the first check is fast."""
        system, _ = self.build("", [])
        self.llm.complete(system, "Reply with: ready", max_tokens=1)

    def check(self, transcript: str, already_raised: list[str]) -> TriggerResult:
        system, user = self.build(transcript, already_raised)
        try:
            raw = self.llm.complete(system, user, json_mode=True)
        except Exception as e:
            return TriggerResult("none", 0.0, f"backend error: {e}", "", valid=False)
        return parse_trigger(raw)
