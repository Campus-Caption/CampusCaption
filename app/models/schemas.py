from pydantic import BaseModel


class CaptionSession(BaseModel):
    session_id: str
    language: str = "de"


class CaptionChunk(BaseModel):
    session_id: str
    text: str
    is_final: bool = False
