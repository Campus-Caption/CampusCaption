"""Mistral Voxtral Mini 4B Realtime (2602) running locally through Hugging Face Transformers.

The most accurate local model, and the heaviest: 4B parameters, meant for an NVIDIA GPU (about 16 GB of video
memory, bfloat16). It transcribes many languages and detects the language itself, so the language hint is not
used. Weights (~9 GB) are downloaded once from Hugging Face into STT_MODEL_DIR/hf; after that it runs offline.
Needs: pip install -r requirements-voxtral.txt (PyTorch, transformers >= 5.2, mistral-common).
Check the licence on the model card before using it.
"""
from __future__ import annotations

import logging

import numpy as np

from app.stt.types import SAMPLE_RATE

log = logging.getLogger("campuscaption.stt")

REPO_ID = "mistralai/Voxtral-Mini-4B-Realtime-2602"
MIN_SAMPLES = int(0.4 * SAMPLE_RATE)


def pick_device(requested: str, cuda_available: bool) -> str:
    """auto -> GPU when there is one. Without a GPU the model is far too slow for live captions, so auto refuses
    instead of silently crawling; STT_DEVICE=cpu forces it anyway (fine for tests, not for a lecture)."""
    if requested == "cpu":
        return "cpu"
    if cuda_available:
        return "cuda"
    if requested == "cuda":
        raise RuntimeError("STT_DEVICE=cuda but PyTorch sees no NVIDIA GPU. Install the CUDA build of PyTorch.")
    raise RuntimeError("Voxtral needs an NVIDIA GPU (about 16 GB). Choose Nemotron or Parakeet on this computer, "
                       "or set STT_DEVICE=cpu to force it (much slower than real time).")


class VoxtralEngine:
    label = "Voxtral Mini 4B Realtime"

    def __init__(self, device: str, progress=None) -> None:
        import torch
        from transformers import AutoProcessor, VoxtralRealtimeForConditionalGeneration

        self.device = pick_device(device, torch.cuda.is_available())
        self._dtype = torch.bfloat16 if self.device == "cuda" else torch.float32
        if progress:
            progress(0.1, "Loading model (first time downloads ~9 GB)")
        self._processor = AutoProcessor.from_pretrained(REPO_ID)
        self._model = VoxtralRealtimeForConditionalGeneration.from_pretrained(REPO_ID, dtype=self._dtype)
        self._model.to(self.device).eval()
        self._torch = torch
        self.label = f"Voxtral Mini 4B Realtime ({'GPU' if self.device == 'cuda' else 'CPU'})"
        log.info("Loaded %s", self.label)

    def transcribe(self, audio: np.ndarray, language: str | None) -> str:
        # Voxtral detects the language itself; the hint is not used.
        if len(audio) < MIN_SAMPLES:
            audio = np.pad(audio, (0, MIN_SAMPLES - len(audio)))
        inputs = self._processor(audio.astype(np.float32, copy=False), return_tensors="pt", sampling_rate=SAMPLE_RATE)
        inputs = inputs.to(self._model.device, dtype=self._model.dtype)
        with self._torch.inference_mode():
            out = self._model.generate(**inputs)
        return " ".join(self._processor.batch_decode(out, skip_special_tokens=True)[0].split())
