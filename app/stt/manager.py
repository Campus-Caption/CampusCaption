"""Caption sessions: settings, recognizer lifecycle, history and fan-out to viewers."""
from __future__ import annotations

import asyncio
import difflib
import logging
import re
import time
import uuid
from collections import deque
from dataclasses import dataclass

import numpy as np

from app.core.config import Settings
from app.stt.audio import pcm16_to_float
from app.stt.models import LOCAL_MODELS, LocalModels
from app.stt.analysis import LectureAnalyzer
from app.stt.polish import ClaudePolisher
from app.stt.recognizers import ChunkedRecognizer, Recognizer
from app.stt.types import SAMPLE_RATE, SessionSettings

log = logging.getLogger("campuscaption.stt")

MAX_PENDING_SECONDS = 10      # audio buffered while the recognizer is still starting
MAX_HISTORY = 3000
MAX_MICS = 2                  # e.g. lecturer lav mic + audience mic
CROSSTALK_WINDOW_S = 6.0      # a sentence repeated by the other mic within this window is a duplicate
CROSSTALK_SIMILARITY = 0.8
_WORD = re.compile(r"\w+", re.UNICODE)


def _letters(text: str) -> str:
    """Lower-case letters and digits only, so "back propagation" and "Backpropagation." compare equal."""
    return "".join(_WORD.findall(text)).lower()


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
    model: str = ""
    speaker: int = 1  # microphone number

    def public(self) -> dict:
        return {"utterance": self.id, "text": self.text, "start": round(self.start, 2), "end": round(self.end, 2),
                "polished": self.polished, "speaker": self.speaker}


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


class Channel:
    """One microphone feeding a session. Each has its own recognizer, so two mics never mix their audio."""

    def __init__(self, number: int) -> None:
        self.number = number
        self.ingest_active = False
        self.rec: Recognizer | None = None
        self.pending: deque[np.ndarray] = deque()
        self.pending_n = 0
        self.dropped_samples = 0
        self.ids: dict[tuple[int, int], int] = {}  # (recognizer generation, local utterance) -> caption id
        self.generation = 0

    def drop_pending(self) -> None:
        self.pending.clear()
        self.pending_n = 0


class CaptionSession:
    def __init__(self, manager: "SessionManager", settings: SessionSettings) -> None:
        self.id = str(uuid.uuid4())
        self.settings = settings
        self.mgr = manager
        self.created = time.time()
        self.touched = time.time()
        self.t0 = time.monotonic()
        self.channels: dict[int, Channel] = {n: Channel(n) for n in range(1, MAX_MICS + 1)}
        self.mics_used: set[int] = set()  # once a second mic was used, captions show who is speaking
        self.history: deque[Utterance] = deque(maxlen=MAX_HISTORY)
        self.live: dict[int, dict] = {}  # channel -> current partial
        self.subs: set[Subscriber] = set()
        self.status: dict = {"type": "status", "state": "idle", "message": "", "progress": None}
        self._lock = asyncio.Lock()
        self._max_id = 0
        self._cleared_upto = 0

    @property
    def ingest_active(self) -> bool:
        return any(c.ingest_active for c in self.channels.values())

    @property
    def multi_mic(self) -> bool:
        return len(self.mics_used) > 1

    def channel(self, n: int) -> Channel:
        return self.channels[n]

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
        return {"local_model": s.local_model, "language": s.language, "polish": s.polish,
                "speakers": list(s.speakers), "multi_mic": self.multi_mic}

    def snapshot(self) -> dict:
        return {"type": "snapshot", "session": self.id, "status": self.status, "settings": self.public_settings(),
                "history": [u.public() for u in list(self.history)[-20:]], "live": list(self.live.values())}

    def _set_status(self, state: str, message: str = "", progress: float | None = None) -> None:
        self.status = {"type": "status", "state": state, "message": message, "progress": progress,
                       "model": self.settings.local_model}
        self.broadcast(self.status)

    def _refresh_status(self) -> None:
        listening = any(c.rec is not None for c in self.channels.values())
        self._set_status("listening" if listening else "idle", "")

    def latest_text(self) -> str:
        parts = [u.text for u in list(self.history)[-3:]]
        for lv in self.live.values():
            parts.append((lv["committed"] + " " + lv["tentative"]).strip())
        return " ".join(parts)

    # ------------------------------------------------------------------ audio in
    async def feed(self, data: bytes, mic: int = 1) -> None:
        x = pcm16_to_float(data)
        if not x.size:
            return
        ch = self.channels[mic]
        rec = ch.rec
        if rec is None:
            ch.pending.append(x)
            ch.pending_n += len(x)
            while ch.pending_n > MAX_PENDING_SECONDS * SAMPLE_RATE:
                old = ch.pending.popleft()
                ch.pending_n -= len(old)
                ch.dropped_samples += len(old)
            return
        try:
            await rec.feed(x)
        except Exception:
            log.exception("Recognizer failed while feeding audio")
            self._set_status("error", "Speech recognition failed.")
            ch.rec = None

    # ------------------------------------------------------------------ lifecycle
    async def begin(self, mic: int = 1) -> None:
        async with self._lock:
            ch = self.channels[mic]
            if ch.rec is not None:
                return
            ch.rec = await self._start_new(ch, "Starting speech recognition…")
            if mic not in self.mics_used:
                self.mics_used.add(mic)
                if self.multi_mic:
                    self.broadcast({"type": "settings", "settings": self.public_settings()})
            await self._drain_pending(ch)

    async def end(self, flush: bool = True, mic: int | None = None) -> None:
        """Stop one microphone, or all of them when `mic` is None."""
        async with self._lock:
            for ch in self.channels.values():
                if mic is not None and ch.number != mic:
                    continue
                rec, ch.rec = ch.rec, None
                ch.drop_pending()
                if rec is not None:
                    try:
                        await rec.stop(flush)
                    except Exception:
                        log.exception("Stopping recognizer failed")
                self.live.pop(ch.number, None)
            self._refresh_status()

    async def apply_settings(self, new: SessionSettings) -> None:
        self.mgr.check_engine(new)
        async with self._lock:
            old = self.settings
            self.settings = new
            self.broadcast({"type": "settings", "settings": self.public_settings()})
            changed = (old.local_model, old.language) != (new.local_model, new.language)
            running = [c for c in self.channels.values() if c.rec is not None]
            if not running or not changed:
                return
            fresh: dict[int, Recognizer] = {}
            try:
                for ch in running:
                    fresh[ch.number] = await self._start_new(ch, "Switching speech recognition…")
            except Exception:
                for rec in fresh.values():
                    await rec.stop(False)
                self.settings = old  # keep the working model running
                self.broadcast({"type": "settings", "settings": self.public_settings()})
                self._set_status("listening", "Switch failed, the previous model keeps running.")
                raise
            for ch in running:
                previous, ch.rec = ch.rec, fresh[ch.number]
                await self._drain_pending(ch)
                try:
                    await previous.stop(True)
                except Exception:
                    log.exception("Stopping previous recognizer failed")

    async def _drain_pending(self, ch: Channel) -> None:
        while ch.rec is not None and ch.pending:
            chunk = ch.pending.popleft()
            ch.pending_n -= len(chunk)
            await ch.rec.feed(chunk)

    async def _start_new(self, ch: Channel, message: str) -> Recognizer:
        self._set_status("loading", message, 0.0)
        tracker = asyncio.create_task(self._track_loading())
        try:
            rec = await self._build(ch)
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
        key = self.settings.local_model
        while True:
            st = self.mgr.models.status(key)
            if st["state"] == "loading":
                self._set_status("loading", st["message"] or "Loading model…", st["progress"])
            await asyncio.sleep(0.8)

    async def _build(self, ch: Channel) -> Recognizer:
        s = self.settings
        ch.generation += 1
        gen = ch.generation
        emit = lambda ev: self._on_event(ev, ch, gen)  # noqa: E731
        key, lang = s.local_model, s.language_hint
        self.mgr.check_engine(s)
        await self.mgr.models.ensure(key)

        async def transcribe(audio: np.ndarray) -> str:
            return await self.mgr.models.transcribe(key, audio, lang)

        return ChunkedRecognizer(emit, transcribe)

    # ------------------------------------------------------------------ recogniser events
    def _caption_id(self, ch: Channel, gen: int, local: int) -> int:
        """Both mics count their sentences from 1; give every sentence one session-wide id."""
        key = (gen, local)
        gid = ch.ids.get(key)
        if gid is None:
            self._max_id += 1
            gid = ch.ids[key] = self._max_id
            if len(ch.ids) > 4 * MAX_HISTORY:
                for k in list(ch.ids)[: len(ch.ids) // 2]:
                    del ch.ids[k]
        return gid

    def _is_crosstalk(self, ch: Channel, text: str, end: float) -> bool:
        """True if another mic already captioned the same words just now (one mic picked up the other)."""
        mine = _letters(text)
        if not mine:
            return False
        for u in reversed(self.history):
            if end - u.end > CROSSTALK_WINDOW_S:
                break
            if u.speaker == ch.number:
                continue
            other = _letters(u.raw)
            if other and difflib.SequenceMatcher(None, mine, other, autojunk=False).ratio() >= CROSSTALK_SIMILARITY:
                return True
        return False

    def _on_event(self, ev: dict, ch: Channel, gen: int) -> None:
        t = ev["type"]
        if t == "error":
            soft = ev.get("soft", False)
            if not soft:
                self.status = {**self.status, "message": ev["message"]}
            self.broadcast({"type": "error", "message": ev["message"], "soft": soft})
            return
        gid = self._caption_id(ch, gen, ev["utterance"])
        if gid <= self._cleared_upto:
            return  # belongs to a sentence that was cleared from the screen
        speaker = ch.number
        if t == "partial":
            lv = {"type": "partial", "utterance": gid, "speaker": speaker,
                  "committed": ev["committed"], "tentative": ev["tentative"]}
            self.live[speaker] = lv
            self.broadcast(lv)
            return
        if t == "final":
            if self.live.get(speaker, {}).get("utterance") == gid:
                self.live.pop(speaker, None)
            text = ev["text"]
            end = time.monotonic() - self.t0
            if not text or (self.multi_mic and self._is_crosstalk(ch, text, end)):
                self.broadcast({"type": "final", "utterance": gid, "text": ""})
                return
            u = Utterance(gid, max(0.0, end - max(0.0, ev["end"] - ev["start"])), end, text, text,
                          model=self.settings.local_model, speaker=speaker)
            self.history.append(u)
            self.broadcast({"type": "final", **u.public(), "latency": ev.get("latency", 0.0)})
            if self.settings.polish and self.mgr.polisher is not None:
                asyncio.ensure_future(self._polish(u))

    async def _polish(self, u: Utterance) -> None:
        polisher = self.mgr.polisher
        if polisher is None:
            return
        context = [x.text for x in list(self.history)[-4:-1]]
        better = await polisher.polish(u.raw, context)
        if better and better != u.text:
            u.text, u.polished = better, True
            self.broadcast({"type": "revise", "utterance": u.id, "text": better})

    # ------------------------------------------------------------------ misc
    def clear(self) -> None:
        self._cleared_upto = self._max_id  # the sentences being spoken right now are dropped too
        self.history.clear()
        self.live.clear()
        self.broadcast({"type": "clear"})

    def transcript(self) -> list[dict]:
        labels = self.settings.speakers if self.multi_mic else None
        return [{**u.public(), "label": labels[u.speaker - 1] if labels else ""} for u in self.history]

    def stats(self, mic: int = 1) -> dict:
        ch = self.channels[mic]
        return {"mic": mic, "dropped_seconds": round(ch.dropped_samples / SAMPLE_RATE, 1),
                **(ch.rec.stats() if ch.rec else {})}


class SessionManager:
    def __init__(self, cfg: Settings) -> None:
        self.cfg = cfg
        self.models = LocalModels(cfg)
        self.sessions: dict[str, CaptionSession] = {}
        self.polisher = ClaudePolisher(cfg.anthropic_api_key, cfg.claude_model) if cfg.anthropic_api_key else None
        self.analyzer = (LectureAnalyzer(cfg.anthropic_api_key, cfg.claude_analysis_model)
                         if cfg.anthropic_api_key else None)
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
        if self.analyzer:
            await self.analyzer.aclose()

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
        model = c.stt_local_model if c.stt_local_model in LOCAL_MODELS else "parakeet"
        lang = c.stt_default_language if c.stt_default_language == "auto" or len(c.stt_default_language) == 2 else "auto"
        return SessionSettings(local_model=model, language=lang)

    def check_engine(self, s: SessionSettings) -> None:
        ok, reason = self.models.dependency(s.local_model)
        if not ok:
            raise EngineUnavailable(reason)

    def engines(self) -> dict:
        c = self.cfg
        return {
            "models": [self.models.status(k) for k in LOCAL_MODELS],
            "privacy": "Audio never leaves this computer.",
            "polish": {"available": self.polisher is not None, "model": c.claude_model,
                       "reason": "" if self.polisher else "ANTHROPIC_API_KEY is missing",
                       "privacy": "Finished sentences (text only) are sent to Anthropic."},
            "analysis": {"available": self.analyzer is not None,
                         "model": self.analyzer.model if self.analyzer else "",
                         "reason": "" if self.analyzer else "ANTHROPIC_API_KEY is missing",
                         "privacy": "When the session ends, the transcript (text only) is sent to Anthropic once."},
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
