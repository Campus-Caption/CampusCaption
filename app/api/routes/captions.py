from fastapi import APIRouter

from app.models.schemas import CaptionChunk, CaptionSession
from app.services import stt

router = APIRouter(prefix="/captions", tags=["captions"])


@router.post("/session", response_model=CaptionSession)
def start_session():
    return stt.create_session()


@router.get("/session/{session_id}/latest", response_model=CaptionChunk)
def latest_caption(session_id: str):
    return stt.get_latest(session_id)
