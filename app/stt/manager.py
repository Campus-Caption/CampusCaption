"""Caption sessions: settings, recognizer lifecycle, history and fan-out to viewers."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass

import numpy as np

from app.core.config import Settings
from app.stt.audio import pcm16_to_float
from app.stt.models import LOCAL_MODELS, LocalModels
from app.stt.polish import ClaudePolisher
from app.stt.recognizers import (
    ChunkedRecognizer, MistralRealtimeRecognizer, OpenAIRealtimeRecognizer, Recognizer,
)
from app.stt.types import ENGINE_LABELS, SAMPLE_RATE, SessionSettings

log = logging.getLogger("campuscaption.stt")

MAX_PENDING_SECONDS = 10      # audio buffered while the recognizer is still starting
MAX_HISTORY = 3000


class EngineUnavailable(Exception):
    """The chosen engine cannot be used right now (message is shown to the lecturer)."""


class TooManySessions(Exception):
    pass


@dataclass
class Utterance:
    id: int
    start: float
    end: float
    text: str
    raw: str
    polished: bool = False
    engine: str = ""

    def public(self) -> dict:
        return {"utterance": self.id, "text": self.text, "start": round(self.start, 2), "end": round(self.end, 2),
                "polished": self.polished}


class Subscriber:
    """One connected viewer. Partials are coalesced (newest wins); everything else is delivered in order."""

    def __init__(self) -> None:
        self._queue: deque[dict] = deque(maxlen=500)
        self._partial: dict | None = None
        self._event = asyncio.Event()

    def push(self, ev: dict) -> None:
        if ev["type"] == "partial":
            self._partial = ev
        else:
            if ev["type"] in ("clear", "snapshot"):
                self._partial = None  # a stale preview must not reappear after the screen was cleared
            if ev["type"] == "final" and self._partial and self._partial["utterance"] == ev["utterance"]:
                self._partial = None
            self._queue.append(ev)
        self._event.set()

    async def batch(self) -> list[dict]:
        await self._event.wait()
        self._event.clear()
        out = list(self._queue)
        self._queue.clear()
        if self._partial:
            out.append(self._partial)
            self._partial = None
        return out


class CaptionSession:
    def __init__(self, manager: "SessionManager", settings: SessionSettings) -> None:
        self.id = str(uuid.uuid4())
        self.settings = settings
        self.mgr = manager
        self.created = time.time()
        self.touched = time.time()
        self.t0 = time.monotonic()
        self.ingest_active = False
        self.history: deque[Utterance] = deque(maxlen=MAX_HISTORY)
        self.live: dict | None = None
        self.subs: set[Subscriber] = set()
        self.status: dict = {"type": "status", "state": "idle", "message": "", "progress": None}
        self._rec: Recognizer | None = None
        self._pending: deque[np.ndarray] = deque()
        self._pending_n = 0
        self._lock = asyncio.Lock()
        self._max_id = 0
        self._cleared_upto = 0
        self.dropped_samples = 0

    # ------------------------------------------------------------------ fan-out
    def subscribe(self) -> Subscriber:
        sub = Subscriber()
        self.subs.add(sub)
        sub.push(self.snapshot())
        return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        self.subs.discard(sub)

    def broadcast(self, ev: dict) -> None:
        self.touched = time.time()
        for s in list(self.subs):
            s.push(ev)

    def public_settings(self) -> dict:
        s = self.settings
        return {"engine": s.engine, "local_model": s.local_model, "language": s.language, "polish": s.polish,
                "glossary": s.glossary}

    def snapshot(self) -> dict:
        return {"type": "snapshot", "session": self.id, "status": self.status, "settings": self.public_settings(),
                "history": [u.public() for u in list(self.history)[-20:]], "live": self.live}

    def _set_status(self, state: str, message: str = "", progress: float | None = None) -> None:
        self.status = {"type": "status", "state": state, "message": message, "progress": progress,
                       "engine": self.settings.engine}
        self.broadcast(self.status)

    def latest_text(self) -> str:
        parts = [u.text for u in list(self.history)[-3:]]
        if self.live:
            parts.append((self.live["committed"] + " " + self.live["tentative"]).strip())
        return " ".join(parts)

    # ------------------------------------------------------------------ audio in
    async def feed(self, data: bytes) -> None:
        x = pcm16_to_float(data)
        if not x.size:
            return
        rec = self._rec
        if rec is None:
            self._pending.append(x)
            self._pending_n += len(x)
            while self._pending_n > MAX_PENDING_SECONDS * SAMPLE_RATE:
                old = self._pending.popleft()
                self._pending_n -= len(old)
                self.dropped_samples += len(old)
            return
        try:
            await rec.feed(x)
        except Exception:
            log.exception("Recognizer failed while feeding audio")
            self._set_status("error", "Speech recognition failed.")
            self._rec = None

    # ------------------------------------------------------------------ lifecycle
    async def begin(self) -> None:
        async with self._lock:
            if self._rec is not None:
                return
            self._rec = await self._start_new("Starting speech recognition…")
            await self._drain_pending()

    async def end(self, flush: bool = True) -> None:
        async with self._lock:
            rec, self._rec = self._rec, None
            self._pending.clear()
            self._pending_n = 0
            if rec is not None:
                try:
                    await rec.stop(flush)
                except Exception:
                    log.exception("Stopping recognizer failed")
            self.live = None
            self._set_status("idle", "")

    async def apply_settings(self, new: SessionSettings) -> None:
        self.mgr.check_engine(new)
        async with self._lock:
            old = self.settings
            self.settings = new
            self.broadcast({"type": "settings", "settings": self.public_settings()})
            changed = (old.engine, old.local_model, old.language, tuple(old.glossary)) != \
                      (new.engine, new.local_model, new.language, tuple(new.glossary))
            if self._rec is None or not changed:
                return
            try:
                fresh = await self._start_new("Switching speech recognition…")
            except Exception:
                self.settings = old  # keep the working engine running
                self.broadcast({"type": "settings", "settings": self.public_settings()})
                self._set_status("listening", "Switch failed, the previous engine keeps running.")
                raise
            previous, self._rec = self._rec, fresh
            await self._drain_pending()
            try:
                await previous.stop(True)
            except Exception:
                log.exception("Stopping previous recognizer failed")

    async def _drain_pending(self) -> None:
        rec = self._rec
        while rec is not None and self._pending:
            chunk = self._pending.popleft()
            self._pending_n -= len(chunk)
            await rec.feed(chunk)

    async def _start_new(self, message: str) -> Recognizer:
        self._set_status("loading", message, 0.0)
        tracker = asyncio.create_task(self._track_loading())
        try:
            rec = await self._build()
            await rec.start()
        except EngineUnavailable as exc:
            self._set_status("error", str(exc))
            raise
        except Exception as exc:
            log.exception("Could not start recognizer")
            self._set_status("error", f"Start failed: {exc}"[:240])
            raise EngineUnavailable(str(exc)) from exc
        finally:
            tracker.cancel()
        self._set_status("listening", "")
        return rec

    async def _track_loading(self) -> None:
        if self.settings.engine != "local":
            return
        key = self.settings.local_model
        while True:
            st = self.mgr.models.status(key)
            if st["state"] == "loading":
                self._set_status("loading", st["message"] or "Loading model…", st["progress"])
            await asyncio.sleep(0.8)

    async def _build(self) -> Recognizer:
        s, cfg = self.settings, self.mgr.cfg
        base = self._max_id
        emit = lambda ev: self._on_event(ev, base)  # noqa: E731
        lang, gloss = s.language_hint, s.glossary
        self.mgr.check_engine(s)
        if s.engine == "local":
            key = s.local_model
            await self.mgr.models.ensure(key)

            async def transcribe(audio: np.ndarray) -> str:
                return await self.mgr.models.transcribe(key, audio, lang, gloss)

            return ChunkedRecognizer(emit, transcribe)
        if s.engine == "openai":
            return OpenAIRealtimeRecognizer(emit, api_key=cfg.openai_api_key, model=cfg.openai_stt_model,
                                            language=lang, glossary=gloss)
        return MistralRealtimeRecognizer(emit, api_key=cfg.mistral_api_key, model=cfg.mistral_stt_model,
                                         language=lang, glossary=gloss, delay_ms=cfg.mistral_stt_delay_ms)

    # ------------------------------------------------------------------ recogniser events
    def _on_event(self, ev: dict, base: int) -> None:
        t = ev["type"]
        if t == "error":
            soft = ev.get("soft", False)
            if not soft:
                self.status = {**self.status, "message": ev["message"]}
            self.broadcast({"type": "error", "message": ev["message"], "soft": soft})
            return
        gid = base + ev["utterance"]
        self._max_id = max(self._max_id, gid)
        if gid <= self._cleared_upto:
            return  # belongs to a sentence that was cleared from the screen
        if t == "partial":
            self.live = {"type": "partial", "utterance": gid, "committed": ev["committed"], "tentative": ev["tentative"]}
            self.broadcast(self.live)
            return
        if t == "final":
            if self.live and self.live["utterance"] == gid:
                self.live = None
            text = ev["text"]
            if not text:
                self.broadcast({"type": "final", "utterance": gid, "text": ""})
                return
            end = time.monotonic() - self.t0
            u = Utterance(gid, max(0.0, end - max(0.0, ev["end"] - ev["start"])), end, text, text,
                          engine=self.settings.engine)
            self.history.append(u)
            self.broadcast({"type": "final", **u.public(), "latency": ev.get("latency", 0.0)})
            if self.settings.polish and self.mgr.polisher is not None:
                asyncio.ensure_future(self._polish(u))

    async def _polish(self, u: Utterance) -> None:
        polisher = self.mgr.polisher
        if polisher is None:
            return
        context = [x.text for x in list(self.history)[-4:-1]]
        better = await polisher.polish(u.raw, context, self.settings.glossary)
        if better and better != u.text:
            u.text, u.polished = better, True
            self.broadcast({"type": "revise", "utterance": u.id, "text": better})

    # ------------------------------------------------------------------ misc
    def clear(self) -> None:
        self._cleared_upto = self._max_id  # the sentence being spoken right now is dropped too
        self.history.clear()
        self.live = None
        self.broadcast({"type": "clear"})

    def transcript(self) -> list[dict]:
        return [u.public() for u in self.history]

    def stats(self) -> dict:
        return {"dropped_seconds": round(self.dropped_samples / SAMPLE_RATE, 1),
                **(self._rec.stats() if self._rec else {})}


class SessionManager:
    def __init__(self, cfg: Settings) -> None:
        self.cfg = cfg
        self.models = LocalModels(cfg)
        self.sessions: dict[str, CaptionSession] = {}
        self.polisher = ClaudePolisher(cfg.anthropic_api_key, cfg.claude_model) if cfg.anthropic_api_key else None
        self._janitor: asyncio.Task | None = None

    # ------------------------------------------------------------------ setup / teardown
    def start(self) -> None:
        self._janitor = asyncio.create_task(self._sweep())
        if self.cfg.stt_preload:
            self.models.warm(self.cfg.stt_local_model)

    async def close(self) -> None:
        if self._janitor:
            self._janitor.cancel()
        for s in list(self.sessions.values()):
            await s.end(flush=False)
        self.models.shutdown()
        if self.polisher:
            await self.polisher.aclose()

    async def _sweep(self) -> None:
        keep = self.cfg.stt_session_retention_hours * 3600
        while True:
            await asyncio.sleep(600)
            now = time.time()
            for sid, s in list(self.sessions.items()):
                if not s.ingest_active and not s.subs and now - s.touched > keep:
                    self.sessions.pop(sid, None)

    # ------------------------------------------------------------------ engines
    def default_settings(self) -> SessionSettings:
        c = self.cfg
        engine = c.stt_default_engine if c.stt_default_engine in ENGINE_LABELS else "local"
        model = c.stt_local_model if c.stt_local_model in LOCAL_MODELS else "parakeet"
        lang = c.stt_default_language if c.stt_default_language == "auto" or len(c.stt_default_language) == 2 else "auto"
        return SessionSettings(engine=engine, local_model=model, language=lang)

    def check_engine(self, s: SessionSettings) -> None:
        if s.engine == "local":
            ok, reason = self.models.dependency(s.local_model)
            if not ok:
                raise EngineUnavailable(reason)
        elif s.engine == "openai" and not self.cfg.openai_api_key:
            raise EngineUnavailable("OPENAI_API_KEY is not set.")
        elif s.engine == "mistral" and not self.cfg.mistral_api_key:
            raise EngineUnavailable("MISTRAL_API_KEY is not set.")

    def engines(self) -> dict:
        c = self.cfg
        local = [self.models.status(k) for k in LOCAL_MODELS]
        return {
            "engines": [
                {"id": "local", "label": ENGINE_LABELS["local"], "available": any(m["available"] for m in local),
                 "reason": "" if any(m["available"] for m in local) else "pip install -r requirements-local.txt",
                 "models": local, "privacy": "Audio never leaves this computer."},
                {"id": "openai", "label": ENGINE_LABELS["openai"], "available": bool(c.openai_api_key),
                 "reason": "" if c.openai_api_key else "OPENAI_API_KEY is missing", "model": c.openai_stt_model,
                 "privacy": "Audio is sent to OpenAI."},
                {"id": "mistral", "label": ENGINE_LABELS["mistral"], "available": bool(c.mistral_api_key),
                 "reason": "" if c.mistral_api_key else "MISTRAL_API_KEY is missing", "model": c.mistral_stt_model,
                 "privacy": "Audio is sent to Mistral (EU)."},
            ],
            "polish": {"available": self.polisher is not None, "model": c.claude_model,
                       "reason": "" if self.polisher else "ANTHROPIC_API_KEY is missing",
                       "privacy": "Finished sentences (text only) are sent to Anthropic."},
            "defaults": self.default_settings().model_dump(),
        }

    # ------------------------------------------------------------------ sessions
    def create(self, settings: SessionSettings | None) -> CaptionSession:
        for sid, s in list(self.sessions.items()):  # reloading the page must not leak idle sessions
            if not s.ingest_active and not s.subs:
                self.sessions.pop(sid, None)
        live = [s for s in self.sessions.values() if s.ingest_active]
        if len(live) >= self.cfg.stt_max_sessions:
            raise TooManySessions()
        s = CaptionSession(self, settings or self.default_settings())
        self.sessions[s.id] = s
        return s

    def get(self, sid: str) -> CaptionSession | None:
        return self.sessions.get(sid)
