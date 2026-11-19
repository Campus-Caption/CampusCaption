"""Streaming voice activity detection (Silero VAD v6 via onnxruntime, energy fallback)."""
from __future__ import annotations

import threading
from pathlib import Path

import numpy as np

from app.stt.types import FRAME

MODEL_PATH = Path(__file__).parent / "assets" / "silero_vad_v6.onnx"
CONTEXT = 64  # samples of left context the model expects in front of every frame

_session = None
_session_lock = threading.Lock()


def _get_session():
    global _session
    with _session_lock:
        if _session is None:
            import onnxruntime as ort

            opts = ort.SessionOptions()
            opts.inter_op_num_threads = 1
            opts.intra_op_num_threads = 1
            opts.log_severity_level = 4
            _session = ort.InferenceSession(str(MODEL_PATH), sess_options=opts, providers=["CPUExecutionProvider"])
    return _session


class SileroVad:
    """One instance per audio stream (it carries the recurrent state). Call prob() with 512-sample frames."""

    def __init__(self) -> None:
        self._session = _get_session()
        self.reset()

    def reset(self) -> None:
        self._h = np.zeros((1, 1, 128), dtype=np.float32)
        self._c = np.zeros((1, 1, 128), dtype=np.float32)
        self._ctx = np.zeros(CONTEXT, dtype=np.float32)

    def prob(self, frame: np.ndarray) -> float:
        assert frame.shape[0] == FRAME
        x = np.concatenate([self._ctx, frame]).astype(np.float32)[None, :]
        out, self._h, self._c = self._session.run(None, {"input": x, "h": self._h, "c": self._c})
        self._ctx = frame[-CONTEXT:].copy()
        return float(out.reshape(-1)[0])


class EnergyVad:
    """Fallback when onnxruntime is missing: adaptive loudness gate. Much worse in noise, but never crashes."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._noise = 0.003

    def prob(self, frame: np.ndarray) -> float:
        rms = float(np.sqrt(np.mean(frame * frame)))
        loud = rms > max(0.008, self._noise * 3)
        if not loud:
            self._noise = 0.95 * self._noise + 0.05 * rms
        return 0.9 if loud else 0.05


def make_vad():
    try:
        return SileroVad()
    except Exception:  # onnxruntime not installed or model unreadable
        return EnergyVad()
