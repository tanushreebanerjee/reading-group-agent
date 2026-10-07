"""Fuzzy detection of the assistant's name in transcript text.

STT mangles names ("Sherlock" -> "Sherlok", "sure lock", "Shirlock"), so we compare the
first few words (and any word right after hey/ok/so) against the name and
configured aliases with rapidfuzz. The bare phrase "at last" is never a hit.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz

WAKE_PREFIXES = {"hey", "ok", "okay", "so", "um", "uh", "and", "right", "well", "oh"}
NEVER = {"at last"}
# "a sure lock", "the share lock": a phrase in the sentence, not someone addressing the assistant
DETERMINERS = {"a", "an", "the", "this", "that", "these", "those", "of", "my", "our", "their", "its", "his", "her"}


@dataclass
class NameHit:
    matched: str
    score: float
    rest: str          # text after the name: the start of the question


def _words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9']+|[,.?!]", text)


class NameDetector:
    def __init__(self, name: str, aliases: list[str] | None = None, search_words: int = 8,
                 min_ratio: float = 80):
        targets = {name.lower(), *[a.lower() for a in (aliases or [])]}
        self.single = sorted(t for t in targets if " " not in t)
        self.multi = sorted(t for t in targets if " " in t)
        self.search_words = search_words
        self.min_ratio = min_ratio

    def _score(self, cand: str) -> float:
        if cand in NEVER:
            return 0.0
        if " " in cand:
            # two-word split names ("sure lock") are easy to hit by accident ("the lock"),
            # so they must nearly match a configured multi-word alias
            if not self.multi or cand.split()[0] in DETERMINERS | WAKE_PREFIXES:
                return 0.0
            best = max(fuzz.ratio(cand, t) for t in self.multi)
            return best if best >= 90 else 0.0
        return max(fuzz.ratio(cand, t) for t in self.single)

    def detect(self, text: str) -> NameHit | None:
        toks = _words(text)
        word_idx = [i for i, t in enumerate(toks) if t[0].isalnum()]
        best = None
        for n, i in enumerate(word_idx):
            prev = toks[word_idx[n - 1]].lower() if n > 0 else ""
            near_start = n < self.search_words
            after_wake = prev in WAKE_PREFIXES
            if not (near_start or after_wake) or prev in DETERMINERS:
                continue
            # try 1-word and 2-word candidates ("at las")
            for span in (1, 2):
                if n + span > len(word_idx):
                    continue
                j = word_idx[n + span - 1]
                cand = " ".join(toks[k].lower() for k in word_idx[n:n + span])
                if len(cand) < 4:
                    continue
                score = self._score(cand)
                if score >= self.min_ratio and (best is None or score > best[0]):
                    best = (score, cand, j)
        if not best:
            return None
        score, cand, j = best
        rest = " ".join(toks[j + 1:])
        rest = re.sub(r"\s+([,.?!])", r"\1", rest).lstrip(",. ")
        return NameHit(cand, score, rest)
