"""Shared constants and the per-session settings model."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

SAMPLE_RATE = 16_000   # every engine receives 16 kHz mono float32
FRAME = 512            # Silero VAD frame: 32 ms at 16 kHz
FRAME_SECONDS = FRAME / SAMPLE_RATE

EngineId = Literal["local", "openai", "mistral"]
LocalModel = Literal["parakeet", "whisper-turbo", "whisper-small"]

ENGINE_LABELS = {
    "local": "On this computer",
    "openai": "Cloud Whisper (OpenAI)",
    "mistral": "Cloud Voxtral (Mistral)",
}


class SessionSettings(BaseModel):
    """What the lecturer can switch while a session runs."""

    engine: EngineId = "local"
    local_model: LocalModel = "parakeet"
    language: str = Field("auto", pattern=r"^(auto|[a-z]{2})$")
    polish: bool = False  # Claude cleans up punctuation / terms on finished sentences
    glossary: list[str] = Field(default_factory=list, max_length=60)

    @field_validator("glossary")
    @classmethod
    def _clean_glossary(cls, terms: list[str]) -> list[str]:
        out: list[str] = []
        for t in terms:
            t = " ".join(t.split())
            # Terms end up inside prompts; keep them plain (no markup, no line breaks).
            if t and len(t) <= 60 and not any(c in t for c in "<>"):
                out.append(t)
        return out[:60]

    @property
    def language_hint(self) -> str | None:
        return None if self.language == "auto" else self.language
