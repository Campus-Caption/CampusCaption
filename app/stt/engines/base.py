"""Interface for engines that transcribe one finished audio buffer (the local models)."""
from __future__ import annotations

from typing import Protocol

import numpy as np


class BatchEngine(Protocol):
    label: str

    def transcribe(self, audio: np.ndarray, language: str | None) -> str:
        """Blocking. audio: float32 mono 16 kHz. Returns plain text (empty string for no speech)."""
        ...
