"""Loads and caches the local speech models; runs inference on a dedicated thread.

One inference thread is shared by all sessions on purpose: CPU inference already uses every core, so
running two decodes at once only makes both slower. Loading runs on a second thread so a model that is
still downloading never blocks captions that are already running.
"""
from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

import numpy as np

from app.core.config import Settings
from app.stt.engines.base import BatchEngine

log = logging.getLogger("campuscaption.stt")

LOCAL_MODELS: dict[str, dict] = {
    "parakeet": {
        "label": "Parakeet v3 (fast, 25 languages)",
        "needs": "sherpa_onnx",
        "pip": "sherpa-onnx",
        "size": "465 MB",
    },
    "whisper-turbo": {
        "label": "Whisper large-v3-turbo (99 languages, GPU recommended)",
        "needs": "faster_whisper",
        "pip": "faster-whisper",
        "size": "1,6 GB",
    },
    "whisper-small": {
        "label": "Whisper small (leicht, geringere Genauigkeit)",
        "needs": "faster_whisper",
        "pip": "faster-whisper",
        "size": "470 MB",
    },
}

Factory = Callable[[Settings, str, Callable[[float, str], None]], BatchEngine]


def _parakeet_factory(cfg: Settings, key: str, progress) -> BatchEngine:
    from app.stt.engines import parakeet

    override = os.environ.get("STT_PARAKEET_DIR")
    folder = Path(override) if override else parakeet.ensure_model(Path(cfg.stt_model_dir), progress)
    progress(0.98, "Starting model")
    return parakeet.ParakeetEngine(folder, cfg.resolved_threads)


def _whisper_factory(cfg: Settings, key: str, progress) -> BatchEngine:
    from app.stt.engines.whisper import WhisperEngine

    progress(0.1, "Loading model")
    return WhisperEngine(key, str(Path(cfg.stt_model_dir) / "whisper"), cfg.stt_device, cfg.resolved_threads)


# Tests replace entries here with fakes.
FACTORIES: dict[str, Factory] = {
    "parakeet": _parakeet_factory,
    "whisper-turbo": _whisper_factory,
    "whisper-small": _whisper_factory,
}


class _Entry:
    def __init__(self) -> None:
        self.state = "unloaded"  # unloaded | loading | ready | error
        self.progress = 0.0
        self.message = ""
        self.error = ""
        self.engine: BatchEngine | None = None
        self.task: asyncio.Task | None = None
        self.loaded_at = 0.0


class LocalModels:
    def __init__(self, cfg: Settings) -> None:
        self._cfg = cfg
        self._entries = {k: _Entry() for k in LOCAL_MODELS}
        self._infer = ThreadPoolExecutor(1, thread_name_prefix="stt-infer")
        self._load = ThreadPoolExecutor(1, thread_name_prefix="stt-load")
        self._lock = threading.Lock()
        os.environ.setdefault("HF_HOME", str(Path(cfg.stt_model_dir) / "hf"))

    # ------------------------------------------------------------------ status
    def dependency(self, key: str) -> tuple[bool, str]:
        spec = LOCAL_MODELS[key]
        if key in FACTORIES and FACTORIES[key] not in (_parakeet_factory, _whisper_factory):
            return True, ""  # a test double is installed
        if importlib.util.find_spec(spec["needs"]) is None:
            return False, f"Paket fehlt: pip install {spec['pip']}"
        return True, ""

    def status(self, key: str) -> dict:
        e = self._entries[key]
        ok, reason = self.dependency(key)
        return {
            "id": key,
            "label": LOCAL_MODELS[key]["label"],
            "size": LOCAL_MODELS[key]["size"],
            "available": ok,
            "reason": reason,
            "state": e.state,
            "progress": round(e.progress, 3),
            "message": e.message,
            "error": e.error,
        }

    # ------------------------------------------------------------------ loading
    def _set(self, key: str, **kw) -> None:
        e = self._entries[key]
        with self._lock:
            for k, v in kw.items():
                setattr(e, k, v)

    def _load_blocking(self, key: str) -> BatchEngine:
        def progress(p: float, msg: str) -> None:
            self._set(key, progress=p, message=msg)

        t0 = time.monotonic()
        engine = FACTORIES[key](self._cfg, key, progress)
        log.info("Loaded local model %s in %.1fs", key, time.monotonic() - t0)
        return engine

    async def ensure(self, key: str) -> BatchEngine:
        e = self._entries[key]
        if e.engine is not None:
            return e.engine
        ok, reason = self.dependency(key)
        if not ok:
            raise RuntimeError(reason)
        if e.task is None or (e.task.done() and e.state == "error"):
            self._set(key, state="loading", progress=0.0, message="Preparing", error="")
            e.task = asyncio.create_task(self._run_load(key))
        return await asyncio.shield(e.task)

    async def _run_load(self, key: str) -> BatchEngine:
        e = self._entries[key]
        try:
            engine = await asyncio.get_running_loop().run_in_executor(self._load, self._load_blocking, key)
        except Exception as exc:  # surface to the UI instead of dying silently
            log.exception("Loading local model %s failed", key)
            self._set(key, state="error", error=str(exc)[:300], message="")
            raise
        self._set(key, state="ready", engine=engine, progress=1.0, message="", loaded_at=time.time())
        return engine

    def warm(self, key: str) -> None:
        """Start loading in the background (no-op if loaded/loading or deps missing)."""
        e = self._entries[key]
        if e.engine is not None or (e.task is not None and not e.task.done()):
            return
        if not self.dependency(key)[0]:
            return
        task = asyncio.ensure_future(self.ensure(key))
        task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)

    # ------------------------------------------------------------------ inference
    async def transcribe(self, key: str, audio: np.ndarray, language: str | None) -> str:
        engine = await self.ensure(key)
        return await asyncio.get_running_loop().run_in_executor(self._infer, engine.transcribe, audio, language)

    def shutdown(self) -> None:
        self._infer.shutdown(wait=False, cancel_futures=True)
        self._load.shutdown(wait=False, cancel_futures=True)
