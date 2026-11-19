"""Download the default local speech model now, so the first start is instant (run.bat does this once)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings  # noqa: E402
from app.stt.engines.parakeet import ensure_model  # noqa: E402

cfg = get_settings()
last = [-1]


def progress(p: float, msg: str) -> None:
    pct = int(p * 100)
    if pct != last[0] and pct % 5 == 0:
        last[0] = pct
        print(f"{msg}: {pct}%", flush=True)


print("Checking the Parakeet model (about 465 MB, downloaded only once)…")
print("Ready:", ensure_model(Path(cfg.stt_model_dir), progress))
