"""Test doubles for the speech models."""
import numpy as np

SENTENCE = "das ist ein kurzer test der live untertitel für die vorlesung".split()


class FakeEngine:
    """Pretends to recognise speech: reveals ~3 words per second of audio, like a real growing hypothesis."""

    label = "fake"

    def __init__(self, delay: float = 0.0):
        self.delay = delay
        self.calls = 0
        self.language = None

    def transcribe(self, audio: np.ndarray, language) -> str:
        import time

        self.calls += 1
        self.language = language
        if self.delay:
            time.sleep(self.delay)
        n = max(1, int(len(audio) / 16000 * 3))
        words = (SENTENCE * 5)[:n]
        return " ".join(words).capitalize() + ("." if n >= len(SENTENCE) else "")


def speech(seconds: float, amp: float = 0.3, sr: int = 16000) -> np.ndarray:
    t = np.arange(int(seconds * sr)) / sr
    return (amp * np.sin(2 * np.pi * 220 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t))).astype(np.float32)


def silence(seconds: float, sr: int = 16000) -> np.ndarray:
    return np.zeros(int(seconds * sr), dtype=np.float32)


def pcm16(x: np.ndarray) -> bytes:
    return (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()
