"""NVIDIA Nemotron 3.5 ASR Streaming 0.6B (multilingual, incl. German + English) through sherpa-onnx.

The fastest and lightest local model: a cache-aware streaming FastConformer transducer, int8 ONNX, 560 ms
chunks, runs faster than real time on CPU. Punctuation and casing come out of the model. The language is a
per-stream option ("de", "en", ... or "auto"); a language the model does not know falls back to auto.
Files come from the sherpa-onnx GitHub releases (no Hugging Face access needed), ~475 MB, downloaded once.
License of the weights: NVIDIA Open Model License.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from app.stt.engines.parakeet import Progress, download_archive
from app.stt.types import SAMPLE_RATE

FOLDER = "sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-560ms-int8-2026-06-11"
ARCHIVE_URL = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/{FOLDER}.tar.bz2"
FILES = ("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt")
# A streaming model needs a little context before the first word, or it drops it
# ("Alles hat ein Ende" came out as "hat ein Ende" without lead-in). Tail padding flushes the last chunk.
LEAD_IN = np.zeros(int(0.64 * SAMPLE_RATE), dtype=np.float32)
TAIL = np.zeros(int(1.2 * SAMPLE_RATE), dtype=np.float32)


def ensure_model(model_dir: Path, progress: Progress | None = None, url: str = ARCHIVE_URL) -> Path:
    return download_archive(model_dir, FOLDER, url, FILES, "Nemotron 3.5 ASR Streaming model (~475 MB)", progress)


class NemotronEngine:
    label = "Nemotron 3.5 ASR Streaming 0.6B"

    def __init__(self, folder: Path, threads: int) -> None:
        import sherpa_onnx

        self._rec = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=str(folder / "tokens.txt"),
            encoder=str(folder / "encoder.int8.onnx"),
            decoder=str(folder / "decoder.int8.onnx"),
            joiner=str(folder / "joiner.int8.onnx"),
            num_threads=threads,
            sample_rate=SAMPLE_RATE,
            provider="cpu",
        )

    def transcribe(self, audio: np.ndarray, language: str | None) -> str:
        stream = self._rec.create_stream()
        stream.set_option("language", language or "auto")
        stream.accept_waveform(SAMPLE_RATE, np.concatenate([LEAD_IN, audio.astype(np.float32, copy=False), TAIL]))
        stream.input_finished()
        while self._rec.is_ready(stream):
            self._rec.decode_stream(stream)
        return " ".join(self._rec.get_result(stream).split())
