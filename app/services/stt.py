import uuid

from app.models.schemas import CaptionChunk, CaptionSession

_sessions: dict[str, str] = {}


def create_session(language: str = "de") -> CaptionSession:
    sid = str(uuid.uuid4())
    _sessions[sid] = "Bereit für Live-Untertitel."
    return CaptionSession(session_id=sid, language=language)


def get_latest(session_id: str) -> CaptionChunk:
    text = _sessions.get(session_id, "Keine Session.")
    return CaptionChunk(session_id=session_id, text=text, is_final=False)
