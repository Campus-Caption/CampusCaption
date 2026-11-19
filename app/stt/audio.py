"""Small PCM helpers."""
from __future__ import annotations

import base64

import numpy as np


def pcm16_to_float(data: bytes) -> np.ndarray:
    if len(data) % 2:
        data = data[:-1]
    return np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0


def float_to_pcm16(x: np.ndarray) -> bytes:
    return (np.clip(x, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


def float_to_b64(x: np.ndarray) -> str:
    return base64.b64encode(float_to_pcm16(x)).decode("ascii")


def resample_16k_to_24k(x: np.ndarray) -> np.ndarray:
    """Linear-interpolation upsampling (3/2). Good enough for speech recognition input."""
    n = int(len(x) * 3 // 2)
    if n == 0:
        return x[:0]
    pos = np.arange(n, dtype=np.float64) * (2.0 / 3.0)
    return np.interp(pos, np.arange(len(x)), x).astype(np.float32)
