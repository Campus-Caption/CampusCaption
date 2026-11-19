"""Cleanup for raw recogniser output: hallucinations on near-silence and repetition loops."""
from __future__ import annotations

import re

# Phrases Whisper-style models invent for silence or music. Dropped only when they ARE the whole utterance.
_HALLUCINATIONS = {
    "untertitel der amara.org-community",
    "untertitel im auftrag des zdf für funk, 2017",
    "untertitelung des zdf, 2020",
    "vielen dank fürs zuschauen",
    "thanks for watching",
    "thank you for watching",
    "subtitles by the amara.org community",
}

_LOOP = re.compile(r"(\b[\w'’-]+(?:\s+[\w'’-]+){0,5}?)(?:\s+\1\b){3,}", re.IGNORECASE | re.UNICODE)


def clean(text: str) -> str:
    text = " ".join((text or "").split())
    if not text:
        return ""
    text = _LOOP.sub(r"\1", text)
    if text.lower().strip(" .!") in _HALLUCINATIONS:
        return ""
    return text
