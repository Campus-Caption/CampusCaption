"""A half-working CUDA install can make the GPU test inference hang instead of raising.
The engine must then fall back to CPU instead of staying in "loading" forever."""
import sys
import threading
import time
import types

import pytest

from app.stt.engines import whisper


class FakeModel:
    def __init__(self, name, device, **kw):
        self.device = device

    def transcribe(self, audio, **kw):
        if self.device == "cuda":
            threading.Event().wait()  # hangs like a broken cuDNN does
        return iter(()), None


@pytest.fixture
def fake_gpu(monkeypatch):
    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeModel))
    monkeypatch.setattr(whisper, "_cuda_available", lambda: True)
    monkeypatch.setattr(whisper, "_GPU_BROKEN", None)
    monkeypatch.setenv("STT_GPU_PROBE_TIMEOUT", "0.3")


def test_hanging_gpu_probe_falls_back_to_cpu(fake_gpu):
    t0 = time.monotonic()
    eng = whisper.WhisperEngine("whisper-small", "unused", "auto", 1)
    assert eng.device == "cpu"
    assert time.monotonic() - t0 < 5


def test_later_loads_skip_the_broken_gpu(fake_gpu):
    whisper.WhisperEngine("whisper-small", "unused", "auto", 1)
    t0 = time.monotonic()
    eng = whisper.WhisperEngine("whisper-turbo", "unused", "auto", 1)
    assert eng.device == "cpu"
    assert time.monotonic() - t0 < 0.2  # no second probe wait


def test_forced_cuda_reports_the_hang(fake_gpu):
    with pytest.raises(RuntimeError, match="GPU failed"):
        whisper.WhisperEngine("whisper-small", "unused", "cuda", 1)
