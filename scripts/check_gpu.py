"""Quick check: can Whisper use your NVIDIA GPU?   .venv\\Scripts\\python scripts\\check_gpu.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.stt.engines.whisper import _cuda_available, _prepare_cuda_libs  # noqa: E402

_prepare_cuda_libs()
print("NVIDIA GPU seen by CTranslate2:", _cuda_available())
try:
    import numpy as np
    from faster_whisper import WhisperModel

    m = WhisperModel("tiny", device="cuda", compute_type="float16")
    list(m.transcribe(np.zeros(16000, dtype=np.float32))[0])
    print("GPU inference works. Whisper will use the GPU.")
except Exception as exc:
    print("GPU inference FAILED:", exc)
    print("Fix: .venv\\Scripts\\pip install nvidia-cublas-cu12 nvidia-cudnn-cu12 and update your NVIDIA driver.")
