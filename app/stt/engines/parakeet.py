"""NVIDIA Parakeet TDT 0.6B v3 (25 European languages incl. German + English) through sherpa-onnx.

int8 ONNX, runs on CPU faster than real time, punctuation and casing come out of the model. The model
files are hosted on GitHub releases (no Hugging Face account/network rule needed) and downloaded once.
License of the weights: CC-BY-4.0 (NVIDIA).
"""
from __future__ import annotations

import logging
import shutil
import tarfile
from pathlib import Path
from typing import Callable

import httpx
import numpy as np

from app.stt.types import SAMPLE_RATE

log = logging.getLogger("campuscaption.stt")

ARCHIVE_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
    "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8.tar.bz2"
)
FOLDER = "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8"
FILES = ("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt")
MIN_SAMPLES = int(0.4 * SAMPLE_RATE)

Progress = Callable[[float, str], None]


def model_ready(folder: Path, files: tuple[str, ...] = FILES) -> bool:
    return all((folder / f).is_file() for f in files)


def download_archive(model_dir: Path, folder_name: str, url: str, files: tuple[str, ...], label: str,
                     progress: Progress | None = None) -> Path:
    """Download a sherpa-onnx model archive (.tar.bz2) once and unpack only the model files. Blocking.
    Shared by Parakeet and Nemotron; sample WAVs shipped in the archives are never written to disk."""
    folder = model_dir / folder_name
    if model_ready(folder, files):
        return folder
    model_dir.mkdir(parents=True, exist_ok=True)
    archive = model_dir / (folder_name + ".tar.bz2.part")
    log.info("Downloading %s to %s", label, model_dir)
    with httpx.stream("GET", url, follow_redirects=True, timeout=httpx.Timeout(30.0, read=120.0)) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        done = 0
        with open(archive, "wb") as fh:
            for chunk in r.iter_bytes(1 << 20):
                fh.write(chunk)
                done += len(chunk)
                if progress and total:
                    progress(min(0.95, done / total * 0.95), "Loading model")
    if progress:
        progress(0.96, "Unpacking model")
    tmp = model_dir / (folder_name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    wanted = {f"{folder_name}/{f}" for f in files}
    try:
        with tarfile.open(archive, "r:bz2") as tar:
            for member in tar:
                target = (tmp / member.name).resolve()
                if tmp.resolve() not in target.parents and target != tmp.resolve():
                    raise RuntimeError(f"Unsafe path in model archive: {member.name}")
                if member.isreg() and member.name in wanted:
                    tar.extract(member, tmp)
        src = tmp / folder_name
        if not model_ready(src, files):
            raise RuntimeError("Model archive is missing expected files")
        shutil.rmtree(folder, ignore_errors=True)
        src.rename(folder)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        archive.unlink(missing_ok=True)
    return folder


def ensure_model(model_dir: Path, progress: Progress | None = None, url: str = ARCHIVE_URL) -> Path:
    """Return the Parakeet model folder, downloading and unpacking it first if needed. Blocking."""
    return download_archive(model_dir, FOLDER, url, FILES, "Parakeet v3 model (~465 MB)", progress)


class ParakeetEngine:
    label = "Parakeet TDT 0.6B v3"

    def __init__(self, folder: Path, threads: int) -> None:
        import sherpa_onnx

        self._rec = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(folder / "encoder.int8.onnx"),
            decoder=str(folder / "decoder.int8.onnx"),
            joiner=str(folder / "joiner.int8.onnx"),
            tokens=str(folder / "tokens.txt"),
            num_threads=threads,
            model_type="nemo_transducer",
            provider="cpu",
        )

    def transcribe(self, audio: np.ndarray, language: str | None) -> str:
        # Parakeet detects the language itself; the hint is not used.
        if len(audio) < MIN_SAMPLES:
            audio = np.pad(audio, (0, MIN_SAMPLES - len(audio)))
        stream = self._rec.create_stream()
        stream.accept_waveform(SAMPLE_RATE, audio.astype(np.float32, copy=False))
        self._rec.decode_stream(stream)
        return stream.result.text.strip()
