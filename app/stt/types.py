"""Shared constants and the per-session settings model."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SAMPLE_RATE = 16_000   # every engine receives 16 kHz mono float32
FRAME = 512            # Silero VAD frame: 32 ms at 16 kHz
FRAME_SECONDS = FRAME / SAMPLE_RATE

LocalModel = Literal["parakeet", "whisper-turbo", "whisper-small"]


class SessionSettings(BaseModel):
    """What the lecturer can switch while a session runs."""

    local_model: LocalModel = "parakeet"
    language: str = Field("auto", pattern=r"^(auto|[a-z]{2})$")
    polish: bool = False  # Claude cleans up punctuation on finished sentences

    @property
    def language_hint(self) -> str | None:
        return None if self.language == "auto" else self.language
