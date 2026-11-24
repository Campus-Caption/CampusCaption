from pydantic import BaseModel, Field

from app.stt.types import SessionSettings


class CaptionSession(BaseModel):
    session_id: str
    language: str = "de"
    settings: SessionSettings | None = None


class SessionCreate(BaseModel):
    settings: SessionSettings | None = None


class SettingsPatch(BaseModel):
    """Partial update: only the fields that are sent change."""

    local_model: str | None = None
    language: str | None = Field(None, pattern=r"^(auto|[a-z]{2})$")
    polish: bool | None = None
    speakers: list[str] | None = None


class CaptionChunk(BaseModel):
    session_id: str
    text: str
    is_final: bool = False
