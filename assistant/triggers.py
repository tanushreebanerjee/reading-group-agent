"""Engaged mode: periodic trigger checks and the gate that turns them into raised hands."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from core.llm import LLM
from core.prompts import load_prompt

TRIGGER_TYPES = ("contradiction", "gap")


@dataclass
class TriggerResult:
    trigger: str               # contradiction | gap | none
    confidence: float
    reason: str
    raw: str = ""
    valid: bool = True


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
    return TriggerResult(trig, conf, reason, raw)


@dataclass
class Gate:
    """Threshold per type, a global cooldown (meeting seconds), and de-duplication."""

    thresholds: dict
    cooldown_s: float = 180.0
    dedup_ratio: float = 70.0
    last_raised_t: float | None = None
    raised_reasons: list[str] = field(default_factory=list)

    def check(self, r: TriggerResult, now: float) -> str:
        """Return the outcome: raised | below_threshold | cooldown | duplicate | none | invalid."""
        if not r.valid:
            return "invalid"
        if r.trigger == "none":
            return "none"
        if r.confidence < float(self.thresholds.get(r.trigger, 1.01)):
            return "below_threshold"
        if self.is_duplicate(r.reason):
            return "duplicate"
        if self.last_raised_t is not None and now - self.last_raised_t < self.cooldown_s:
            return "cooldown"
        self.last_raised_t = now
        self.raised_reasons.append(r.reason)
        return "raised"

    def is_duplicate(self, reason: str) -> bool:
        return any(fuzz.token_set_ratio(reason.lower(), prev.lower()) >= self.dedup_ratio
                   for prev in self.raised_reasons)


class TriggerChecker:
    def __init__(self, cfg: dict, llm: LLM, brief: str):
        self.cfg = cfg
        self.llm = llm
        self.brief = brief or "(no brief available)"

    def build(self, transcript: str, already_raised: list[str]) -> tuple[str, str]:
        system = load_prompt(self.cfg, "trigger", brief=self.brief)
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
