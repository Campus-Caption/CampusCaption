"""Cuts a continuous 16 kHz stream into utterances from per-frame speech probabilities.

Pure state machine: no model, no I/O, so it is easy to test. Feed it 512-sample frames plus the VAD
probability of each frame; it reports when an utterance starts, ends or is dropped.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

import numpy as np

from app.stt.types import FRAME, FRAME_SECONDS, SAMPLE_RATE


@dataclass
class Started:
    sample: int  # stream position of the first sample (including pre-roll)


@dataclass
class Cut:
    audio: np.ndarray  # float32 mono, trailing silence trimmed
    start: int
    end: int
    reason: str  # "silence" | "max_length" | "flush"


@dataclass
class Dropped:
    """Too little real speech (a click, a cough): the utterance is discarded."""


class Segmenter:
    def __init__(
        self,
        *,
        threshold: float = 0.5,
        neg_threshold: float = 0.35,
        start_ms: int = 96,
        end_ms: int = 640,
        preroll_ms: int = 320,
        tail_ms: int = 192,
        min_speech_ms: int = 200,
        max_utterance_s: float = 14.0,
    ) -> None:
        f = FRAME_SECONDS * 1000
        self.threshold, self.neg_threshold = threshold, neg_threshold
        self._start_frames = max(1, math.ceil(start_ms / f))
        self._end_frames = max(1, math.ceil(end_ms / f))
        self._tail_frames = math.ceil(tail_ms / f)
        self._min_voiced = max(1, math.ceil(min_speech_ms / f))
        self._max_frames = int(max_utterance_s / FRAME_SECONDS)
        self._preroll: deque[tuple[np.ndarray, float]] = deque(maxlen=math.ceil(preroll_ms / f))
        self.in_speech = False
        self._frames: list[np.ndarray] = []
        self._probs: list[float] = []
        self._pos = 0            # stream sample index of the NEXT frame
        self._utt_start = 0
        self._speech_run = 0
        self._silence_run = 0
        self._voiced = 0

    # ------------------------------------------------------------------ queries
    @property
    def duration(self) -> float:
        return len(self._frames) * FRAME_SECONDS

    @property
    def position(self) -> float:
        return self._pos / SAMPLE_RATE

    def snapshot(self) -> np.ndarray | None:
        """Audio of the utterance so far (copy), or None when nobody is speaking."""
        if not self.in_speech or not self._frames:
            return None
        return np.concatenate(self._frames)

    # ------------------------------------------------------------------ feeding
    def push(self, frame: np.ndarray, prob: float) -> list[Started | Cut | Dropped]:
        events: list[Started | Cut | Dropped] = []
        frame_start = self._pos
        self._pos += FRAME

        if not self.in_speech:
            self._preroll.append((frame, prob))
            self._speech_run = self._speech_run + 1 if prob >= self.threshold else 0
            if self._speech_run >= self._start_frames:
                self.in_speech = True
                self._frames = [f for f, _ in self._preroll]
                self._probs = [p for _, p in self._preroll]
                self._utt_start = frame_start - (len(self._frames) - 1) * FRAME
                self._preroll.clear()
                self._silence_run = 0
                self._voiced = sum(p >= self.threshold for p in self._probs)
                events.append(Started(self._utt_start))
            return events

        self._frames.append(frame)
        self._probs.append(prob)
        if prob >= self.threshold:
            self._voiced += 1
            self._silence_run = 0
        elif prob < self.neg_threshold:
            self._silence_run += 1

        if self._silence_run >= self._end_frames:
            keep = len(self._frames) - self._silence_run + self._tail_frames
            events.append(self._finish(keep, "silence"))
        elif len(self._frames) >= self._max_frames:
            split = self._best_split()
            cut = self._finish(split, "max_length", keep_going=True)
            events.append(cut)
            events.append(Started(self._utt_start))
        return events

    def flush(self) -> list[Cut | Dropped]:
        """Stream ended: close an utterance that is still open."""
        if not self.in_speech:
            return []
        return [self._finish(len(self._frames), "flush")]

    # ------------------------------------------------------------------ internals
    def _best_split(self) -> int:
        """Where to cut an over-long utterance: the middle of the latest pause in the last ~5 s."""
        lo = max(0, len(self._probs) - int(5.0 / FRAME_SECONDS))
        best_end, run = -1, 0
        for i in range(lo, len(self._probs)):
            if self._probs[i] < self.neg_threshold:
                run += 1
            else:
                if run >= 3:
                    best_end = i
                run = 0
        if run >= 3:
            best_end = len(self._probs)
        if best_end < 0:
            return len(self._frames)  # no pause at all: hard cut
        # find the start of that pause and cut in its middle
        i = best_end
        while i > lo and self._probs[i - 1] < self.neg_threshold:
            i -= 1
        return (i + best_end) // 2

    def _finish(self, keep: int, reason: str, keep_going: bool = False) -> Cut | Dropped:
        keep = max(1, min(keep, len(self._frames)))
        head, rest = self._frames[:keep], self._frames[keep:]
        head_probs, rest_probs = self._probs[:keep], self._probs[keep:]
        voiced = sum(p >= self.threshold for p in head_probs)
        start = self._utt_start
        end = start + keep * FRAME
        if keep_going:
            self._frames, self._probs = rest, rest_probs
            self._utt_start = end
            self._silence_run = 0
            self._voiced = sum(p >= self.threshold for p in rest_probs)
        else:
            self.in_speech = False
            self._frames, self._probs = [], []
            self._speech_run = self._silence_run = self._voiced = 0
        if voiced < self._min_voiced:
            return Dropped()
        return Cut(np.concatenate(head), start, end, reason)
