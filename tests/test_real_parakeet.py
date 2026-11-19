"""Runs the real Parakeet model through the pipeline. Skipped unless the model is already on disk.

Point CC_TEST_MODEL_DIR at a folder that contains sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8
(or let the app download it once into ./data/models and run with CC_TEST_MODEL_DIR=./data/models).
"""
import os
import wave
from pathlib import Path

import pytest

MODEL_DIR = Path(os.environ.get("CC_TEST_MODEL_DIR", "./data/models"))
SPEECH = Path(__file__).parent / "data" / "speech_en.wav"
MODEL = MODEL_DIR / "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8"

pytestmark = pytest.mark.skipif(not MODEL.exists() or not pytest.importorskip("sherpa_onnx"), reason="Parakeet model not on disk")


def test_parakeet_transcribes_english_sample():
    import numpy as np
    from app.stt.engines.parakeet import ParakeetEngine

    eng = ParakeetEngine(MODEL, 2)
    with wave.open(str(SPEECH)) as w:
        audio = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768
    text = eng.transcribe(audio, None, []).lower()
    assert "quick brown fox" in text and "caption" in text
