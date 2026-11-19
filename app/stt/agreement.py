"""LocalAgreement-2: commit a word once two consecutive hypotheses agree on it.

This is the trick from "Turning Whisper into Real-Time Transcription System" (Machacek et al., 2023).
Any model that transcribes a whole buffer can be turned into a streaming recogniser: re-decode the
growing buffer, show words that were stable across two passes as committed (they never change again)
and the rest as a tentative tail. That is what stops live captions from flickering and rewriting.
"""
from __future__ import annotations

import re

_STRIP = re.compile(r"[^\w]", re.UNICODE)


def _norm(word: str) -> str:
    return _STRIP.sub("", word.lower())


class LocalAgreement:
    def __init__(self) -> None:
        self.committed: list[str] = []
        self._prev: list[str] = []

    def update(self, text: str) -> tuple[str, str]:
        """Feed the newest hypothesis; returns (committed_text, tentative_text)."""
        cur = text.split()
        k = 0
        limit = min(len(cur), len(self._prev))
        while k < limit and _norm(cur[k]) == _norm(self._prev[k]):
            k += 1
        if k > len(self.committed):
            self.committed = self.committed + cur[len(self.committed):k]
        self._prev = cur
        tentative = cur[len(self.committed):] if len(cur) > len(self.committed) else []
        return " ".join(self.committed), " ".join(tentative)
