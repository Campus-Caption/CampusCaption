"""Turns a 16 kHz audio stream into caption events.

ChunkedRecognizer runs the local models: VAD cuts utterances; while someone speaks the growing buffer is
re-decoded (at most one decode in flight, so the work can never pile up) and LocalAgreement keeps the shown
words stable. Audio never leaves this computer.

Events handed to `emit` (plain dicts):
  {"type": "partial", "utterance": n, "committed": str, "tentative": str}
  {"type": "final",   "utterance": n, "text": str, "start": s, "end": s, "latency": s}   (text "" = discard)
  {"type": "error",   "message": str}
"""
from __future__ import annotations

import abc
import asyncio
import logging
import time
from typing import Awaitable, Callable

import numpy as np

from app.stt.agreement import LocalAgreement
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

