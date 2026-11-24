"""Stream a WAV file through the real caption pipeline and report text, latency and speed.

    python scripts/stt_smoke.py path/to/audio.wav [--model parakeet] [--language de] [--polish] [--realtime]

Use this on your own machine to check that a local model works end to end (download, load, speed)
before a lecture. --realtime paces the audio like a live microphone; without it the file is fed as fast as
the engine can take it, which measures raw speed.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.stt.manager import SessionManager  # noqa: E402
from app.stt.types import SessionSettings  # noqa: E402


def load_wav(path: str) -> np.ndarray:
    with wave.open(path) as w:
        if w.getnchannels() != 1 or w.getframerate() != 16000 or w.getsampwidth() != 2:
            sys.exit("Please use a 16 kHz mono 16-bit WAV (ffmpeg -i in.mp3 -ar 16000 -ac 1 out.wav)")
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").copy()


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("wav")
    ap.add_argument("--model", default=None, help="local model: parakeet | whisper-turbo | whisper-small")
    ap.add_argument("--language", default="auto")
    ap.add_argument("--polish", action="store_true")
    ap.add_argument("--realtime", action="store_true")
    a = ap.parse_args()

    cfg = get_settings()
    mgr = SessionManager(cfg)
    mgr.start()
    s = SessionSettings(local_model=a.model or cfg.stt_local_model, language=a.language, polish=a.polish)
    try:
        mgr.check_engine(s)
    except Exception as exc:
        print("Model not available:", exc)
        return 2
    session = mgr.create(s)
    sub = session.subscribe()
    audio = load_wav(a.wav)
    seconds = len(audio) / 16000

    t0 = time.monotonic()
    print(f"Starting {s.local_model} – first run downloads the model…")
    await session.begin()
    print(f"ready after {time.monotonic() - t0:.1f}s")
    t1 = time.monotonic()
    chunk = 1600
    for i in range(0, len(audio), chunk):
        await session.feed(audio[i:i + chunk].tobytes())
        if a.realtime:
            await asyncio.sleep(chunk / 16000)
    await session.end(flush=True)
    await asyncio.sleep(1.5 if a.polish else 0.2)
    wall = time.monotonic() - t1

    finals = [e for e in session.history]
    for u in finals:
        print(f"[{u.start:6.1f}-{u.end:6.1f}] {u.text}{'  (Claude)' if u.polished else ''}")
    print(f"\n{len(finals)} sentences, audio {seconds:.1f}s, processing {wall:.1f}s (RTF {wall / seconds:.2f})")
    print("stats:", session.stats())
    await mgr.close()
    return 0 if finals else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
