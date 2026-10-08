"""Engaged mode: periodic trigger checks and the gate that turns them into raised hands."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from core.llm import LLM
from core.prompts import load_prompt, system_prompt

TRIGGER_TYPES = ("contradiction", "gap")


@dataclass
class TriggerResult:
    trigger: str               # contradiction | gap | none
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


@dataclass
class Gate:
    """Threshold per type, a global cooldown (meeting seconds), and de-duplication."""

    thresholds: dict
    cooldown_s: float = 180.0
    dedup_ratio: float = 70.0
    last_raised_t: float | None = None
    raised_reasons: list[str] = field(default_factory=list)

    def check(self, r: TriggerResult, now: float, human_lines: list[str] | None = None) -> str:
        """Return the outcome: raised | below_threshold | cooldown | duplicate | ungrounded | none | invalid."""
        if not r.valid:
            return "invalid"
        if r.trigger == "none":
            return "none"
        if human_lines is not None and not quote_is_grounded(r.quote, human_lines):
            return "ungrounded"
        if r.confidence < float(self.thresholds.get(r.trigger, 1.01)):
            return "below_threshold"
        key = f"{r.quote} {r.reason}"
        if self.is_duplicate(key):
            return "duplicate"
        if self.last_raised_t is not None and now - self.last_raised_t < self.cooldown_s:
            return "cooldown"
        self.last_raised_t = now
        self.raised_reasons.append(key)
        return "raised"

    def is_duplicate(self, reason: str) -> bool:
        return any(fuzz.token_set_ratio(reason.lower(), prev.lower()) >= self.dedup_ratio
                   for prev in self.raised_reasons)


class TriggerChecker:
    def __init__(self, cfg: dict, llm: LLM, brief: str, paper=None):
        self.cfg = cfg
        self.llm = llm
        self.brief = brief or "(no brief available)"
        self.paper = paper

    def build(self, transcript: str, already_raised: list[str]) -> tuple[str, str]:
        from core.llm import active

        full = self.paper is not None and active(self.llm).cfg.get("context") == "full_paper"
        system = system_prompt(self.cfg, self.brief, "trigger", paper_text=self.paper.full_text if full else None)
        raised = "\n".join(f"- {r}" for r in already_raised) or "(none)"
        user = load_prompt(self.cfg, "trigger_user", transcript=transcript or "(silence)", raised=raised)
        return system, user

    def check(self, transcript: str, already_raised: list[str]) -> TriggerResult:
        system, user = self.build(transcript, already_raised)
        try:
            raw = self.llm.complete(system, user, json_mode=True)
        except Exception as e:
            return TriggerResult("none", 0.0, f"backend error: {e}", "", valid=False)
        return parse_trigger(raw)
