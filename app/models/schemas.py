from pydantic import BaseModel, Field

from app.stt.types import SessionSettings


class CaptionSession(BaseModel):
    session_id: str
    language: str = "de"
    settings: SessionSettings | None = None
    code: str = ""       # short room code students type or scan, e.g. K7P42Q (shown as K7P-42Q)
    join_url: str = ""   # what the QR code opens


class SessionCreate(BaseModel):
    settings: SessionSettings | None = None


class SettingsPatch(BaseModel):
    """Partial update: only the fields that are sent change."""

    local_model: str | None = None
    language: str | None = Field(None, pattern=r"^(auto|[a-z]{2})$")
    polish: bool | None = None
    speakers: list[str] | None = None

