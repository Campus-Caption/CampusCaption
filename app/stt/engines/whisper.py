"""Whisper via faster-whisper (CTranslate2): 99 languages, optional NVIDIA GPU."""
from __future__ import annotations

import ctypes
import glob
import importlib.util
import logging
import os
import sys
import threading

import numpy as np

from app.stt.types import SAMPLE_RATE

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")  # Windows: harmless, just noisy

log = logging.getLogger("campuscaption.stt")

MODEL_NAMES = {"whisper-turbo": "large-v3-turbo", "whisper-small": "small"}
MIN_SAMPLES = int(0.4 * SAMPLE_RATE)
_GPU_BROKEN: str | None = None  # set once the GPU probe failed or hung in this process


def _prepare_cuda_libs() -> None:
    """Make the CUDA libraries installed by `pip install nvidia-cublas-cu12 nvidia-cudnn-cu12` findable.
    CTranslate2 needs cuBLAS and cuDNN; the pip wheels put them in site-packages/nvidia/*/bin (Windows)
    or lib (Linux), which are not on the library search path by default."""
    spec = importlib.util.find_spec("nvidia")
    if spec is None or not spec.submodule_search_locations:
        return
    for root in spec.submodule_search_locations:
        if sys.platform == "win32":
            for d in glob.glob(os.path.join(root, "*", "bin")):
                try:
                    os.add_dll_directory(d)
                except OSError:
                    pass
                os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
        else:
            for lib in glob.glob(os.path.join(root, "*", "lib", "lib*.so*")):
                try:
                    ctypes.CDLL(lib, mode=ctypes.RTLD_GLOBAL)
                except OSError:
                    pass


def _cuda_available() -> bool:
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


class WhisperEngine:
    def __init__(self, key: str, download_root: str, device: str, threads: int) -> None:
        from faster_whisper import WhisperModel

        _prepare_cuda_libs()
        name = MODEL_NAMES[key]
        want = device
        global _GPU_BROKEN
        if device == "auto":
            device = "cuda" if (_GPU_BROKEN is None and _cuda_available()) else "cpu"
        elif device == "cuda" and _GPU_BROKEN:
            raise RuntimeError(f"GPU failed earlier in this process ({_GPU_BROKEN}). Restart after fixing the CUDA install.")
        self.device = device
        self._model = self._load(WhisperModel, name, device, threads, download_root)
        if device == "cuda":
            try:  # cuBLAS / cuDNN problems only show up on the first real inference, so test it now
                self._probe()
            except Exception as exc:
                _GPU_BROKEN = str(exc)  # a broken CUDA setup stays broken; later loads go straight to CPU
                if want == "cuda":
                    raise RuntimeError(f"GPU failed ({exc}). Install: pip install nvidia-cublas-cu12 nvidia-cudnn-cu12") from exc
                log.warning("GPU unavailable for Whisper (%s) - falling back to CPU. "
                            "Fix: pip install nvidia-cublas-cu12 nvidia-cudnn-cu12", exc)
                self.device = "cpu"
                self._model = None
                self._model = self._load(WhisperModel, name, "cpu", threads, download_root)
        self.label = f"Whisper {name} ({'GPU' if self.device == 'cuda' else 'CPU'})"
        log.info("Loaded %s", self.label)

    @staticmethod
    def _load(WhisperModel, name, device, threads, download_root):
        return WhisperModel(
            name,
            device=device,
            compute_type="float16" if device == "cuda" else "int8",
            cpu_threads=threads,
            download_root=download_root,
        )

    def _probe(self, timeout: float | None = None) -> None:
        """One tiny GPU inference. A half-working CUDA install (e.g. mismatched cuDNN) can hang here forever
        instead of raising, which left the model stuck in "loading" - so the probe runs in a daemon thread
        and counts as failed if it does not answer in time."""
        if timeout is None:
            timeout = float(os.environ.get("STT_GPU_PROBE_TIMEOUT", "30"))
        model, result = self._model, {}

        def run() -> None:
            try:
                segments, _ = model.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32), beam_size=1, language="en",
                                               without_timestamps=True)
                list(segments)
                result["ok"] = True
            except Exception as exc:  # noqa: BLE001 - reported to the caller below
                result["error"] = exc

        t = threading.Thread(target=run, name="whisper-gpu-probe", daemon=True)
        t.start()
        t.join(timeout)
        if t.is_alive():
            raise TimeoutError(f"GPU test inference did not finish within {timeout:.0f} s")
        if "error" in result:
            raise result["error"]

    def transcribe(self, audio: np.ndarray, language: str | None, glossary: list[str]) -> str:
        if len(audio) < MIN_SAMPLES:
            audio = np.pad(audio, (0, MIN_SAMPLES - len(audio)))
        prompt = ("Glossary: " + ", ".join(glossary) + ".") if glossary else None
        segments, _ = self._model.transcribe(
            audio.astype(np.float32, copy=False),
            language=language,
            beam_size=1,
            temperature=0.0,
            condition_on_previous_text=False,
            without_timestamps=True,
            vad_filter=False,  # we already cut on speech
            initial_prompt=prompt,
        )
        return " ".join(s.text.strip() for s in segments if s.no_speech_prob < 0.6).strip()
