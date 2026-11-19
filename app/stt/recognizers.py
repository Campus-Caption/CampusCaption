"""Turns a 16 kHz audio stream into caption events.

Three implementations share one interface:

* ChunkedRecognizer      - local models. VAD cuts utterances; while someone speaks the growing buffer is
                           re-decoded (at most one decode in flight, so the work can never pile up) and
                           LocalAgreement keeps the shown words stable.
* OpenAIRealtimeRecognizer - OpenAI Realtime transcription over a WebSocket. We detect speech ourselves
                           (gpt-live-transcribe has no server VAD), stream only speech and commit at pauses.
* MistralRealtimeRecognizer - Mistral Voxtral Realtime over a WebSocket, continuous stream.

Events handed to `emit` (plain dicts):
  {"type": "partial", "utterance": n, "committed": str, "tentative": str}
  {"type": "final",   "utterance": n, "text": str, "start": s, "end": s, "latency": s}   (text "" = discard)
  {"type": "error",   "message": str}
"""
from __future__ import annotations

import abc
import asyncio
import json
import logging
import time
from typing import Awaitable, Callable

import numpy as np

from app.stt.agreement import LocalAgreement
from app.stt.audio import float_to_b64, resample_16k_to_24k
from app.stt.segmenter import Cut, Dropped, Segmenter, Started
from app.stt.textfilter import clean
from app.stt.types import FRAME, SAMPLE_RATE
from app.stt.vad import make_vad

log = logging.getLogger("campuscaption.stt")

EmitFn = Callable[[dict], None]
Transcribe = Callable[[np.ndarray], Awaitable[str]]


class Recognizer(abc.ABC):
    def __init__(self, emit: EmitFn) -> None:
        self._emit = emit
        self.samples = 0  # total samples received, doubles as the stream clock

    @property
    def clock(self) -> float:
        return self.samples / SAMPLE_RATE

    @abc.abstractmethod
    async def start(self) -> None: ...

    @abc.abstractmethod
    async def feed(self, pcm: np.ndarray) -> None: ...

    @abc.abstractmethod
    async def stop(self, flush: bool = True) -> None: ...

    def stats(self) -> dict:
        return {}


# ====================================================================== local
class ChunkedRecognizer(Recognizer):
    def __init__(
        self,
        emit: EmitFn,
        transcribe: Transcribe,
        *,
        vad=None,
        partial_interval: float = 0.6,
        partial_min_s: float = 0.7,
        max_utterance_s: float = 12.0,
    ) -> None:
        super().__init__(emit)
        self._transcribe = transcribe
        self._vad = vad or make_vad()
        self._seg = Segmenter(max_utterance_s=max_utterance_s)
        self._interval, self._partial_min = partial_interval, partial_min_s
        self._tail = np.zeros(0, dtype=np.float32)
        self._utt = 0
        self._agree = LocalAgreement()
        self._last_emitted: tuple[str, str] | None = None
        self._partial_task: asyncio.Task | None = None
        self._partial_done_at = 0.0
        self._partial_cost = 0.0
        self._last_partial_dur = 0.0
        self._finals: asyncio.Queue = asyncio.Queue()
        self._worker: asyncio.Task | None = None
        self._n_final = 0
        self._lat_sum = 0.0

    async def start(self) -> None:
        self._worker = asyncio.create_task(self._final_loop())

    async def feed(self, pcm: np.ndarray) -> None:
        self.samples += len(pcm)
        buf = np.concatenate([self._tail, pcm]) if self._tail.size else pcm
        n = len(buf) // FRAME * FRAME
        for i in range(0, n, FRAME):
            frame = buf[i:i + FRAME].copy()
            for ev in self._seg.push(frame, self._vad.prob(frame)):
                self._on_segment(ev)
        self._tail = buf[n:].copy()
        self._maybe_partial()

    async def stop(self, flush: bool = True) -> None:
        if flush:
            for ev in self._seg.flush():
                self._on_segment(ev)
            try:
                await asyncio.wait_for(self._finals.join(), 30)
            except asyncio.TimeoutError:
                log.warning("Timed out flushing the last utterance")
        for task in (self._partial_task, self._worker):
            if task and not task.done():
                task.cancel()

    def stats(self) -> dict:
        return {
            "utterances": self._n_final,
            "avg_final_latency": round(self._lat_sum / self._n_final, 2) if self._n_final else None,
            "partial_decode_s": round(self._partial_cost, 2),
            "backlog": self._finals.qsize(),
        }

    # ------------------------------------------------------------------ segments
    def _on_segment(self, ev) -> None:
        if isinstance(ev, Started):
            self._utt += 1
            self._agree = LocalAgreement()
            self._last_emitted = None
            self._last_partial_dur = 0.0
        elif isinstance(ev, Cut):
            self._finals.put_nowait((self._utt, ev, time.monotonic()))
        elif isinstance(ev, Dropped):
            self._emit({"type": "final", "utterance": self._utt, "text": "", "start": 0.0, "end": 0.0, "latency": 0.0})

    def _maybe_partial(self) -> None:
        if self._partial_task and not self._partial_task.done():
            return  # never more than one partial decode in flight
        if not self._seg.in_speech or self._finals.qsize() >= 2:
            return  # a finished sentence is waiting: it has priority over previews
        dur = self._seg.duration
        if dur < self._partial_min or dur - self._last_partial_dur < 0.4:
            return
        # adaptive cadence: decoding may use at most ~2/3 of the time, so slow machines degrade gracefully
        if time.monotonic() - self._partial_done_at < max(self._interval, 1.5 * self._partial_cost):
            return
        audio = self._seg.snapshot()
        if audio is None:
            return
        self._last_partial_dur = dur
        self._partial_task = asyncio.create_task(self._run_partial(self._utt, audio))

    async def _run_partial(self, utt: int, audio: np.ndarray) -> None:
        t0 = time.monotonic()
        try:
            text = clean(await self._transcribe(audio))
        except Exception:
            log.exception("Partial decode failed")
            text = ""
        finally:
            self._partial_cost = time.monotonic() - t0
            self._partial_done_at = time.monotonic()
        if utt != self._utt or not self._seg.in_speech or not text:
            return  # stale: the sentence already ended, the final result will replace it
        pair = self._agree.update(text)
        if pair != self._last_emitted:
            self._last_emitted = pair
            self._emit({"type": "partial", "utterance": utt, "committed": pair[0], "tentative": pair[1]})

    async def _final_loop(self) -> None:
        while True:
            utt, cut, t_cut = await self._finals.get()
            batch = [(utt, cut, t_cut)]
            # Catching up: Whisper-style models cost about the same for 3 s as for 25 s of audio, so when
            # sentences queue up (slow CPU), decode the waiting ones together instead of one by one.
            total = len(cut.audio)
            while not self._finals.empty() and total < 22 * SAMPLE_RATE:
                nxt = self._finals.get_nowait()
                batch.append(nxt)
                total += len(nxt[1].audio)
            audio = cut.audio if len(batch) == 1 else np.concatenate(
                [np.concatenate([c.audio, np.zeros(int(0.3 * SAMPLE_RATE), np.float32)]) for _, c, _ in batch]
            )
            try:
                text = clean(await self._transcribe(audio))
            except Exception as exc:
                log.exception("Final decode failed")
                self._emit({"type": "error", "message": f"Recognition failed: {exc}"[:200]})
                text = ""
            latency = time.monotonic() - t_cut
            if text:
                self._n_final += 1
                self._lat_sum += latency
            first, last = batch[0][1], batch[-1][1]
            self._emit({
                "type": "final", "utterance": utt, "text": text,
                "start": first.start / SAMPLE_RATE, "end": last.end / SAMPLE_RATE, "latency": round(latency, 2),
            })
            for u2, _, _ in batch[1:]:  # their preview lines are replaced by the merged sentence
                self._emit({"type": "final", "utterance": u2, "text": "", "start": 0.0, "end": 0.0, "latency": 0.0})
            for _ in batch:
                self._finals.task_done()


# ====================================================================== cloud (WebSocket)
class _WsRecognizer(Recognizer):
    """Shared connection handling: connect, run receive loop, reconnect with backoff."""

    def __init__(self, emit: EmitFn, *, connect=None) -> None:
        super().__init__(emit)
        self._connect_fn = connect
        self._ws = None
        self._closing = False
        self._ready = asyncio.Event()
        self._first_error: Exception | None = None
        self._task: asyncio.Task | None = None

    # subclasses
    def _url(self) -> str: raise NotImplementedError
    def _headers(self) -> dict: raise NotImplementedError
    async def _on_open(self, ws) -> None: raise NotImplementedError
    def _on_message(self, msg: dict) -> None: raise NotImplementedError
    def _on_lost(self) -> None: ...

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run())
        await asyncio.wait_for(self._ready.wait(), 20)
        if self._first_error:
            raise self._first_error

    async def _open(self):
        if self._connect_fn:
            return await self._connect_fn(self._url(), self._headers())
        from websockets.asyncio.client import connect

        return await connect(self._url(), additional_headers=self._headers(), max_size=None, open_timeout=10)

    async def _run(self) -> None:
        delay, attempts, first = 1.0, 0, True
        while not self._closing:
            try:
                ws = await self._open()
                await self._on_open(ws)
                self._ws, attempts, delay = ws, 0, 1.0
                if first:
                    first = False
                    self._ready.set()
                async for raw in ws:
                    try:
                        self._on_message(json.loads(raw))
                    except json.JSONDecodeError:
                        continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if first:
                    self._first_error, first = exc, False
                    self._ready.set()
                    return
                log.warning("Realtime connection error: %s", exc)
            self._ws = None
            self._on_lost()
            if self._closing:
                return
            attempts += 1
            if attempts > 5:
                self._emit({"type": "error", "message": "Lost connection to the cloud service."})
                return
            self._emit({"type": "error", "message": f"Connection interrupted, retrying ({attempts}/5)…", "soft": True})
            await asyncio.sleep(delay)
            delay = min(delay * 2, 10.0)

    async def _send(self, obj: dict) -> None:
        ws = self._ws
        if ws is None:
            return
        try:
            await ws.send(json.dumps(obj))
        except Exception:
            log.debug("send failed (connection closing)")

    async def _close_ws(self) -> None:
        self._closing = True
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass
        if self._task and not self._task.done():
            self._task.cancel()


class OpenAIRealtimeRecognizer(_WsRecognizer):
    URL = "wss://api.openai.com/v1/realtime?intent=transcription"

    def __init__(self, emit: EmitFn, *, api_key: str, model: str, language: str | None,
                 glossary: list[str], url: str | None = None, connect=None, vad=None) -> None:
        super().__init__(emit, connect=connect)
        self._key, self._model, self._lang, self._glossary = api_key, model, language, glossary
        self._url_override = url
        self._vad = vad or make_vad()
        self._seg = Segmenter(max_utterance_s=30.0)
        self._tail = np.zeros(0, dtype=np.float32)
        self._pending: list[np.ndarray] = []
        self._pending_n = 0
        self._skip_snapshot = False
        self._ids: dict[str, int] = {}
        self._text: dict[str, str] = {}
        self._started_at: dict[str, float] = {}
        self._done: set[str] = set()

    def _url(self) -> str:
        return self._url_override or self.URL

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._key}"}

    def _session_update(self) -> dict:
        tr: dict = {"model": self._model}
        new_style = self._model.startswith(("gpt-live", "gpt-transcribe"))
        if self._lang:
            tr["languages" if new_style else "language"] = [self._lang] if new_style else self._lang
        if self._glossary:
            if new_style:
                tr["keywords"] = self._glossary
            else:
                tr["prompt"] = "Glossary: " + ", ".join(self._glossary)
        return {"type": "session.update", "session": {"type": "transcription", "audio": {"input": {
            "format": {"type": "audio/pcm", "rate": 24000}, "transcription": tr, "turn_detection": None}}}}

    async def _on_open(self, ws) -> None:
        await ws.send(json.dumps(self._session_update()))

    async def feed(self, pcm: np.ndarray) -> None:
        self.samples += len(pcm)
        buf = np.concatenate([self._tail, pcm]) if self._tail.size else pcm
        n = len(buf) // FRAME * FRAME
        for i in range(0, n, FRAME):
            frame = buf[i:i + FRAME].copy()
            started = False
            for ev in self._seg.push(frame, self._vad.prob(frame)):
                if isinstance(ev, Started):
                    started = True
                    if not self._skip_snapshot:
                        snap = self._seg.snapshot()
                        if snap is not None:
                            self._queue(snap)
                    self._skip_snapshot = False
                elif isinstance(ev, (Cut, Dropped)):
                    await self._commit(isinstance(ev, Cut))
                    self._skip_snapshot = isinstance(ev, Cut) and ev.reason == "max_length"
            if self._seg.in_speech and not started:
                self._queue(frame)
            await self._drain(force=False)
        self._tail = buf[n:].copy()

    def _queue(self, x: np.ndarray) -> None:
        self._pending.append(x)
        self._pending_n += len(x)

    async def _drain(self, force: bool) -> None:
        if self._pending_n >= 3 * FRAME or (force and self._pending_n):
            audio = resample_16k_to_24k(np.concatenate(self._pending))
            self._pending, self._pending_n = [], 0
            await self._send({"type": "input_audio_buffer.append", "audio": float_to_b64(audio)})

    async def _commit(self, real: bool) -> None:
        await self._drain(force=True)
        if real:
            await self._send({"type": "input_audio_buffer.commit"})

    async def stop(self, flush: bool = True) -> None:
        if flush:
            for ev in self._seg.flush():
                await self._commit(isinstance(ev, Cut))
            await asyncio.sleep(0)
            await self._wait_idle(8.0)
        await self._close_ws()

    async def _wait_idle(self, timeout: float) -> None:
        end = time.monotonic() + timeout
        while time.monotonic() < end and any(i not in self._done for i in self._ids):
            await asyncio.sleep(0.1)

    def _utt(self, item: str) -> int:
        if item not in self._ids:
            self._ids[item] = len(self._ids) + 1
            self._started_at[item] = self.clock
        return self._ids[item]

    def _on_message(self, msg: dict) -> None:
        t = msg.get("type", "")
        item = msg.get("item_id", "")
        if t == "conversation.item.input_audio_transcription.delta":
            self._text[item] = self._text.get(item, "") + msg.get("delta", "")
            self._emit({"type": "partial", "utterance": self._utt(item),
                        "committed": self._text[item].strip(), "tentative": ""})
        elif t == "conversation.item.input_audio_transcription.completed":
            utt = self._utt(item)
            self._done.add(item)
            self._emit({"type": "final", "utterance": utt, "text": clean(msg.get("transcript", "")),
                        "start": self._started_at.get(item, 0.0), "end": self.clock, "latency": 0.0})
        elif t == "conversation.item.input_audio_transcription.failed":
            utt = self._utt(item)
            self._done.add(item)
            self._emit({"type": "final", "utterance": utt, "text": "", "start": 0.0, "end": 0.0, "latency": 0.0})
        elif t == "error":
            err = msg.get("error") or {}
            self._emit({"type": "error", "message": f"OpenAI: {err.get('message', 'error')}"[:200]})



class MistralRealtimeRecognizer(_WsRecognizer):
    URL = "wss://api.mistral.ai/v1/audio/transcriptions/realtime"

    def __init__(self, emit: EmitFn, *, api_key: str, model: str, language: str | None,
                 glossary: list[str], delay_ms: int = 480, url: str | None = None, connect=None) -> None:
        super().__init__(emit, connect=connect)
        self._key, self._model, self._lang, self._glossary = api_key, model, language, glossary
        self._delay = delay_ms
        self._url_override = url
        self._buf = np.zeros(0, dtype=np.float32)
        self._utt_no = 0
        self._text = ""
        self._last_delta = 0.0
        self._utt_start = 0.0
        self._monitor: asyncio.Task | None = None
        self._done = asyncio.Event()

    def _url(self) -> str:
        return self._url_override or f"{self.URL}?model={self._model}"

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._key}"}

    async def _on_open(self, ws) -> None:
        # The server speaks first (session.created); wait for it, then configure before any audio.
        raw = await asyncio.wait_for(ws.recv(), 10)
        first = json.loads(raw)
        if first.get("type") == "error":
            raise RuntimeError(first.get("error", {}).get("message", "Mistral error"))
        session: dict = {"audio_format": {"encoding": "pcm_s16le", "sample_rate": SAMPLE_RATE},
                         "target_streaming_delay_ms": self._delay}
        if self._lang:
            session["language"] = self._lang
        if self._glossary:
            session["context_bias"] = self._glossary
        await ws.send(json.dumps({"type": "session.update", "session": session}))

    async def start(self) -> None:
        await super().start()
        self._monitor = asyncio.create_task(self._watch())

    async def feed(self, pcm: np.ndarray) -> None:
        self.samples += len(pcm)
        buf = np.concatenate([self._buf, pcm]) if self._buf.size else pcm
        step = SAMPLE_RATE // 10  # 100 ms chunks
        n = len(buf) // step * step
        for i in range(0, n, step):
            await self._send({"type": "input_audio.append", "audio": float_to_b64(buf[i:i + step])})
        self._buf = buf[n:].copy()

    def _on_message(self, msg: dict) -> None:
        t = msg.get("type", "")
        if t == "transcription.text.delta":
            if not self._text:
                self._utt_no += 1
                self._utt_start = self.clock
            self._text += msg.get("text", "")
            self._last_delta = time.monotonic()
            self._emit({"type": "partial", "utterance": self._utt_no, "committed": self._text.strip(), "tentative": ""})
        elif t == "transcription.done":
            self._finish()
            self._done.set()
        elif t == "error":
            err = msg.get("error") or {}
            self._emit({"type": "error", "message": f"Mistral: {err.get('message', 'error')}"[:200]})

    def _on_lost(self) -> None:
        self._finish()

    def _finish(self) -> None:
        text = clean(self._text)
        if self._text:
            self._emit({"type": "final", "utterance": self._utt_no, "text": text,
                        "start": self._utt_start, "end": self.clock, "latency": 0.0})
        self._text = ""

    async def _watch(self) -> None:
        """The API has no sentence boundaries, so close a caption line on a pause or at a sentence end."""
        while True:
            await asyncio.sleep(0.1)
            if not self._text:
                continue
            idle = time.monotonic() - self._last_delta
            t = self._text.rstrip()
            ends_sentence = t.endswith((".", "!", "?", "…"))
            ends_clause = t.endswith((",", ";", ":")) or ends_sentence
            if (idle >= 1.2 or (ends_sentence and idle >= 0.6)
                    or (len(t) >= 220 and ends_clause and idle >= 0.25) or len(t) >= 400):
                self._finish()

    async def stop(self, flush: bool = True) -> None:
        if flush and self._ws is not None:
            await self._send({"type": "input_audio.end"})
            try:
                await asyncio.wait_for(self._done.wait(), 3.0)
            except asyncio.TimeoutError:
                self._finish()
        else:
            self._finish()
        if self._monitor:
            self._monitor.cancel()
        await self._close_ws()
