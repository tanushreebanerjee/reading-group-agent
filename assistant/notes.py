"""Running notes of the meeting: what was discussed, claims, questions, disagreements, and what
Sherlock said. Updated about once a minute from the new transcript, and given to every hand check
and answer, so Sherlock follows the whole meeting rather than only the last two minutes."""
from __future__ import annotations

from core.llm import LLM
from core.prompts import load_prompt, system_prompt
from core.transcript import Segment, format_segments

EMPTY = "(nothing yet)"


class MeetingNotes:
    def __init__(self, cfg: dict, llm: LLM, brief: str):
        self.cfg = cfg
        self.llm = llm
        self.brief = brief or "(no brief available)"
        self.text = ""
        self.upto = 0.0          # meeting time the notes cover up to

    @property
    def for_prompt(self) -> str:
        return self.text or EMPTY

    def build(self, new: list[Segment]) -> tuple[str, str]:
        t = self.cfg.get("trigger", {})
        system = system_prompt(self.cfg, self.brief, "notes", max_words=int(t.get("notes_max_words", 250)))
        user = load_prompt(self.cfg, "notes_user", notes=self.for_prompt, transcript=format_segments(new))
        return system, user

    def update(self, new: list[Segment]) -> str:
        """Fold new transcript lines into the notes (blocking: call from a worker thread)."""
        if not new:
            return self.text
        system, user = self.build(new)
        out = self.llm.complete(system, user, max_tokens=int(self.cfg.get("trigger", {}).get("notes_max_tokens", 600)))
        lines = [ln.rstrip() for ln in out.strip().splitlines() if ln.strip()]
        if lines and len(" ".join(lines).split()) > 40 and not lines[-1].endswith((".", ")", "?", ":")):
            lines = lines[:-1]   # hit max_tokens mid-line: drop the cut-off bullet
        if lines:   # keep the old notes if the model returned nothing usable
            self.text = "\n".join(lines)
        self.upto = max(s.end for s in new)
        return self.text
