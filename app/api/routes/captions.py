"""Live captions: session control (REST) and audio in / captions out (WebSocket)."""
import asyncio
import hmac
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, status
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import ValidationError

from app.core.config import get_settings
from app.models.schemas import CaptionChunk, CaptionSession as CaptionSessionOut, SessionCreate, SettingsPatch
from app.stt import export
from app.stt.manager import CaptionSession, EngineUnavailable, SessionManager, TooManySessions
from app.stt.models import LOCAL_MODELS
from app.stt.types import SessionSettings

log = logging.getLogger("campuscaption.captions")
router = APIRouter(prefix="/captions", tags=["captions"])

MAX_FRAME_BYTES = 64 * 1024
MAX_LISTENERS = 500
SECRET_HEADER = "X-Lecturer-Secret"
AUTH_TIMEOUT_S = 5.0


def get_manager(request: Request) -> SessionManager:
    return request.app.state.captions


def _http(code: int, key: str, message: str) -> HTTPException:
    return HTTPException(status_code=code, detail={"code": key, "message": message})


def secret_ok(given: str | None) -> bool:
    """True when no lecturer secret is configured, or `given` matches it (constant-time compare)."""
    expected = get_settings().lecturer_secret
    if not expected:
        return True
    return given is not None and hmac.compare_digest(given.encode(), expected.encode())


def require_lecturer(request: Request) -> None:
    """Guards every control endpoint. Viewers (overlay, live feed, public status) never need the secret."""
    if not secret_ok(request.headers.get(SECRET_HEADER)):
        raise HTTPException(status_code=401, detail={"code": "unauthorized", "message": "Lecturer password required."},
                            headers={"WWW-Authenticate": "LecturerSecret"})


def owned_session(session_id: str, _: None = Depends(require_lecturer),
                  manager: SessionManager = Depends(get_manager)) -> CaptionSession:
    s = manager.get(session_id)
    if s is None:
        raise _http(404, "no_session", "Session not found.")
    return s


def _out(s: CaptionSession) -> CaptionSessionOut:
    return CaptionSessionOut(session_id=s.id, language=s.settings.language, settings=s.settings)


# --------------------------------------------------------------------------- engines
@router.get("/engines")
def engines(manager: SessionManager = Depends(get_manager)):
    return {**manager.engines(), "auth_required": bool(get_settings().lecturer_secret)}


@router.post("/engines/local/{model}/warm", dependencies=[Depends(require_lecturer)])
async def warm_model(model: str, manager: SessionManager = Depends(get_manager)):
    """Start loading (and, the first time, downloading) a local model so the first caption is instant."""
    if model not in LOCAL_MODELS:
        raise _http(404, "unknown_model", "Unknown model.")
    ok, reason = manager.models.dependency(model)
    if not ok:
        raise _http(409, "unavailable", reason)
    manager.models.warm(model)
    return manager.models.status(model)


# --------------------------------------------------------------------------- sessions
# Only lecturers/admins may start a captioning session. The body is optional (defaults come from .env).
@router.post("/session", response_model=CaptionSessionOut, dependencies=[Depends(require_lecturer)])
async def start_session(body: SessionCreate | None = None, manager: SessionManager = Depends(get_manager)):
    settings = (body.settings if body else None) or manager.default_settings()
    try:
        manager.check_engine(settings)
    except EngineUnavailable as exc:
        raise _http(409, "engine_unavailable", str(exc))
    try:
        session = manager.create(settings)
    except TooManySessions:
        raise _http(429, "too_many_sessions", "Too many caption sessions are already running.")
    if settings.engine == "local":
        manager.models.warm(settings.local_model)
    return _out(session)


@router.get("/session/{session_id}")
def session_info(session_id: str, manager: SessionManager = Depends(get_manager)):
    """Public (the overlay needs it): current status and the last few sentences."""
    s = manager.get(session_id)
    if s is None:
        raise _http(404, "no_session", "Session not found.")
    return s.snapshot()


@router.patch("/session/{session_id}/settings", response_model=CaptionSessionOut)
async def patch_settings(patch: SettingsPatch, s: CaptionSession = Depends(owned_session)):
    data = s.settings.model_dump()
    data.update(patch.model_dump(exclude_none=True))
    try:
        new = SessionSettings(**data)
    except ValidationError as exc:
        raise _http(422, "invalid_settings", "; ".join(e["msg"] for e in exc.errors()))
    try:
        await s.apply_settings(new)
    except EngineUnavailable as exc:
        raise _http(409, "engine_unavailable", str(exc))
    return _out(s)


@router.post("/session/{session_id}/clear")
def clear(s: CaptionSession = Depends(owned_session)):
    s.clear()
    return {"ok": True}


@router.delete("/session/{session_id}")
async def end_session(s: CaptionSession = Depends(owned_session), manager: SessionManager = Depends(get_manager)):
    await s.end(flush=False)
    s.broadcast({"type": "ended"})
    manager.sessions.pop(s.id, None)
    return {"ok": True}


# Compatibility / OBS fallback: plain polling still works.
@router.get("/session/{session_id}/latest", response_model=CaptionChunk)
def latest_caption(session_id: str, manager: SessionManager = Depends(get_manager)):
    s = manager.get(session_id)
    return CaptionChunk(session_id=session_id, text=s.latest_text() if s else "No session.", is_final=False)


@router.get("/session/{session_id}/transcript")
def transcript(session_id: str, format: str = "txt", s: CaptionSession = Depends(owned_session)):
    items = s.transcript()
    if format == "json":
        return JSONResponse(items)
    if format not in ("txt", "vtt", "srt"):
        raise _http(422, "bad_format", "Format: txt, vtt, srt oder json.")
    body = {"txt": export.to_txt, "vtt": export.to_vtt, "srt": export.to_srt}[format](items)
    media = "text/vtt" if format == "vtt" else "text/plain"
    return PlainTextResponse(body, media_type=media + "; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="untertitel.{format}"'})


# --------------------------------------------------------------------------- websockets
@router.websocket("/ws/ingest/{session_id}")
async def ws_ingest(ws: WebSocket, session_id: str):
    """Browser microphone -> server: binary frames of 16 kHz mono PCM16 little-endian, plus JSON control
    messages ({"type":"start"} / {"type":"stop"} / {"type":"ping"}).

    If a lecturer secret is configured, the first message must be {"type":"auth","secret":"..."} (sent in the
    body rather than the URL so it never shows up in access logs); otherwise the socket is closed with 4401."""
    manager: SessionManager = ws.app.state.captions
    await ws.accept()
    if get_settings().lecturer_secret:
        try:
            first = await asyncio.wait_for(ws.receive_text(), AUTH_TIMEOUT_S)
            msg = json.loads(first)
            given = msg.get("secret") if isinstance(msg, dict) and msg.get("type") == "auth" else None
        except Exception:
            given = None
        if not isinstance(given, str) or not secret_ok(given):
            await ws.close(4401)
            return
    session = manager.get(session_id)
    if session is None:
        await ws.close(4404)
        return
    if session.ingest_active:
        await ws.close(4409)
        return

    session.ingest_active = True
    starter: asyncio.Task | None = None

    async def start() -> None:
        try:
            await session.begin()
            await ws.send_json({"type": "started"})
        except EngineUnavailable as exc:
            await ws.send_json({"type": "start_failed", "message": str(exc)})
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("start failed")
            await ws.send_json({"type": "start_failed", "message": str(exc)[:200]})

    try:
        await ws.send_json({"type": "ready"})
        while True:
            m = await ws.receive()
            if m["type"] == "websocket.disconnect":
                break
            if m.get("bytes") is not None:
                if len(m["bytes"]) > MAX_FRAME_BYTES:
                    await ws.close(1009)
                    break
                await session.feed(m["bytes"])
            elif m.get("text"):
                try:
                    cmd = json.loads(m["text"]).get("type")
                except json.JSONDecodeError:
                    continue
                if cmd == "start" and (starter is None or starter.done()):
                    starter = asyncio.create_task(start())
                elif cmd == "stop":
                    if starter and not starter.done():
                        starter.cancel()
                    await session.end(flush=True)
                    await ws.send_json({"type": "stopped"})
                elif cmd == "ping":
                    await ws.send_json({"type": "pong", "stats": session.stats()})
    except Exception:
        log.debug("ingest connection closed", exc_info=True)
    finally:
        session.ingest_active = False
        if starter and not starter.done():
            starter.cancel()
        await session.end(flush=True)


@router.websocket("/ws/listen/{session_id}")
async def ws_listen(ws: WebSocket, session_id: str):
    """Public caption feed for the overlay and the lecturer's console."""
    manager: SessionManager = ws.app.state.captions
    await ws.accept()
    session = manager.get(session_id)
    if session is None:
        await ws.send_json({"type": "error", "message": "Session not found.", "fatal": True})
        await ws.close(4404)
        return
    if len(session.subs) >= MAX_LISTENERS:
        await ws.close(4429)
        return
    sub = session.subscribe()

    async def sender() -> None:
        while True:
            for ev in await sub.batch():
                await ws.send_json(ev)

    async def receiver() -> None:
        while True:
            await ws.receive_text()

    tasks = [asyncio.create_task(sender()), asyncio.create_task(receiver())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        session.unsubscribe(sub)
