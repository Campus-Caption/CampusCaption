"""Live captions: session control (REST) and audio in / captions out (WebSocket)."""
import asyncio
import hmac
import io
import json
import logging

import segno
from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, status
from fastapi.responses import PlainTextResponse, Response
from pydantic import ValidationError

from app.core.config import get_settings, url_is_loopback
from app.models.schemas import CaptionSession as CaptionSessionOut, SessionCreate, SettingsPatch
from app.stt import export
from app.stt.analysis import AnalysisUnavailable, lecture_stats
from app.stt.manager import CaptionSession, EngineUnavailable, SessionManager, TooManySessions, format_code
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


def public_base(request: Request) -> str:
    """Where students reach this server: PUBLIC_URL / the LAN address, else the address the lecturer used."""
    return get_settings().public_base_url() or str(request.base_url).rstrip("/")


def join_url(request: Request, s: CaptionSession) -> str:
    return f"{public_base(request)}/j/{s.code}"


def _out(s: CaptionSession, request: Request) -> CaptionSessionOut:
    return CaptionSessionOut(session_id=s.id, language=s.settings.language, settings=s.settings,
                             code=format_code(s.code), join_url=join_url(request, s))


# --------------------------------------------------------------------------- engines
@router.get("/engines")
def engines(request: Request, manager: SessionManager = Depends(get_manager)):
    base = public_base(request)
    # lan_ready is False when the join link would point at 127.0.0.1, which a phone cannot open
    return {**manager.engines(), "auth_required": bool(get_settings().lecturer_secret),
            "public_url": base, "lan_ready": not url_is_loopback(base)}


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
async def start_session(request: Request, body: SessionCreate | None = None,
                        manager: SessionManager = Depends(get_manager)):
    settings = (body.settings if body else None) or manager.default_settings()
    try:
        manager.check_engine(settings)
    except EngineUnavailable as exc:
        raise _http(409, "engine_unavailable", str(exc))
    try:
        session = manager.create(settings)
    except TooManySessions:
        raise _http(429, "too_many_sessions", "Too many caption sessions are already running.")
    manager.models.warm(settings.local_model)
    return _out(session, request)


@router.get("/join/{code}")
def join(code: str, manager: SessionManager = Depends(get_manager)):
    """Public: the room code a student typed -> the session to watch."""
    s = manager.by_code(code)
    if s is None:
        raise _http(404, "no_session", "No lecture with this code is running.")
    return {"session_id": s.id, "code": format_code(s.code)}


@router.get("/session/{session_id}")
def session_info(session_id: str, manager: SessionManager = Depends(get_manager)):
    """Public (the overlay needs it): current status and the last few sentences."""
    s = manager.get(session_id)
    if s is None:
        raise _http(404, "no_session", "Session not found.")
    return s.snapshot()


@router.get("/session/{session_id}/qr.svg")
def join_qr(session_id: str, request: Request, manager: SessionManager = Depends(get_manager)):
    """Public (the projector shows it): QR code of the students' join link."""
    s = manager.get(session_id)
    if s is None:
        raise _http(404, "no_session", "Session not found.")
    buf = io.BytesIO()
    # a regular QR code, never a Micro QR: many phone camera apps cannot read those
    segno.make_qr(join_url(request, s), error="m").save(buf, kind="svg", xmldecl=False, scale=8, border=2,
                                                        dark="#000000", light="#ffffff")
    return Response(buf.getvalue(), media_type="image/svg+xml", headers={"Cache-Control": "no-store"})


@router.patch("/session/{session_id}/settings", response_model=CaptionSessionOut)
async def patch_settings(patch: SettingsPatch, request: Request, s: CaptionSession = Depends(owned_session)):
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
    return _out(s, request)


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


@router.post("/session/{session_id}/analysis")
async def analyse_session(ai: bool = True, s: CaptionSession = Depends(owned_session),
                          manager: SessionManager = Depends(get_manager)):
    """End-of-lecture analysis for the professor: exact numbers always, Claude's analysis when `ai` and a key
    is configured. Call it before ending the session; nothing is stored on the server."""
    items = s.transcript()
    if not items:
        raise _http(422, "empty", "Nothing was captioned in this session.")
    labels = list(s.settings.speakers) if s.multi_mic else None
    stats = lecture_stats(items, labels)
    result = {"stats": stats, "analysis": None, "reason": "", "model": ""}
    if not ai:
        result["reason"] = "AI analysis was switched off."
    elif manager.analyzer is None:
        result["reason"] = "AI analysis needs ANTHROPIC_API_KEY in .env."
    else:
        try:
            analysis = await manager.analyzer.analyse(items, labels, stats)
            result.update(analysis=analysis.model_dump(), model=manager.analyzer.model)
        except AnalysisUnavailable as exc:
            result["reason"] = str(exc)
    return result


@router.get("/session/{session_id}/transcript")
def transcript(s: CaptionSession = Depends(owned_session)):
    """The transcript as plain text, one sentence per line."""
    return PlainTextResponse(export.to_txt(s.transcript()), media_type="text/plain; charset=utf-8",
                             headers={"Content-Disposition": 'attachment; filename="untertitel.txt"'})


# --------------------------------------------------------------------------- websockets
@router.websocket("/ws/ingest/{session_id}")
async def ws_ingest(ws: WebSocket, session_id: str, mic: int = 1):
    """Browser microphone -> server: binary frames of 16 kHz mono PCM16 little-endian, plus JSON control
    messages ({"type":"start"} / {"type":"stop"} / {"type":"ping"}).

    `?mic=1` (default) or `?mic=2`: each microphone connects on its own socket and gets its own recognizer,
    so a lecturer mic and an audience mic are captioned separately and tagged with their speaker label.

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
    if mic not in session.channels:
        await ws.close(4422)
        return
    channel = session.channel(mic)
    if channel.ingest_active:
        await ws.close(4409)
        return

    channel.ingest_active = True
    starter: asyncio.Task | None = None

    async def start() -> None:
        try:
            await session.begin(mic)
            await ws.send_json({"type": "started", "mic": mic})
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
                await session.feed(m["bytes"], mic)
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
                    await session.end(flush=True, mic=mic)
                    await ws.send_json({"type": "stopped", "mic": mic})
                elif cmd == "ping":
                    await ws.send_json({"type": "pong", "stats": session.stats(mic)})
    except Exception:
        log.debug("ingest connection closed", exc_info=True)
    finally:
        channel.ingest_active = False
        if starter and not starter.done():
            starter.cancel()
        await session.end(flush=True, mic=mic)


@router.websocket("/ws/listen/{session_id}")
async def ws_listen(ws: WebSocket, session_id: str, history: str = ""):
    """Public caption feed for the overlay, the lecturer's console and students' phones.

    `?history=all` (the phone view): the first snapshot holds every sentence of the session so far instead of
    the last 20, so a student who arrives late can scroll back to the start. Clearing the screen or ending the
    session removes that text for everyone."""
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
    sub = session.subscribe(full=history == "all")

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
