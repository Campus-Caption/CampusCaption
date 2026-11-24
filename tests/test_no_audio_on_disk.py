"""#14: audio lives in RAM only. Stream audio through the whole pipeline (WebSocket -> VAD -> segmenter ->
engine -> captions) and check that nothing was written to disk and no audio ended up in the logs.

Covers: every file opened for writing (Python `open`, `os.open`, `io.open`), new or changed files in the temp
folder, the project folder and the model folder (the Docker volume), and the captured log output."""
import builtins
import io
import logging
import os
import tempfile
from pathlib import Path

import numpy as np
import pytest

from app.core.config import get_settings
from app.stt import recognizers
from tests.fakes import pcm16, silence, speech

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "node_modules"}
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".ogg", ".opus", ".flac", ".webm", ".pcm", ".raw", ".aac"}


class EnergyVad:
    """Deterministic stand-in for Silero so the test does not depend on what the model calls speech."""

    def prob(self, frame: np.ndarray) -> float:
        return 0.95 if float(np.sqrt(np.mean(frame ** 2))) > 0.02 else 0.02


def snapshot(*roots: Path) -> dict[str, tuple[int, int]]:
    out = {}
    for root in roots:
        if not root.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for name in filenames:
                p = os.path.join(dirpath, name)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                out[p] = (st.st_size, st.st_mtime_ns)
    return out


@pytest.fixture()
def write_spy(monkeypatch):
    """Records every file the process opens for writing while the test runs."""
    writes: list[str] = []
    real_open, real_os_open = builtins.open, os.open

    def spy_open(file, mode="r", *a, **kw):
        if isinstance(file, (str, bytes, os.PathLike)) and any(c in str(mode) for c in "wax+"):
            writes.append(os.fsdecode(file))
        return real_open(file, mode, *a, **kw)

    def spy_os_open(path, flags, *a, **kw):
        if flags & WRITE_FLAGS:
            writes.append(os.fsdecode(path))
        return real_os_open(path, flags, *a, **kw)

    monkeypatch.setattr(builtins, "open", spy_open)
    monkeypatch.setattr(io, "open", spy_open)
    monkeypatch.setattr(os, "open", spy_os_open)
    return writes


def test_streaming_audio_writes_nothing_to_disk_or_logs(client, monkeypatch, caplog, write_spy):
    monkeypatch.setattr(recognizers, "make_vad", EnergyVad)
    caplog.set_level(logging.DEBUG)

    sid = client.post("/api/captions/session").json()["session_id"]
    watched = (Path(tempfile.gettempdir()), ROOT, Path(get_settings().stt_model_dir))
    before = snapshot(*watched)
    write_spy.clear()  # only count what happens while audio flows

    audio = np.concatenate([silence(0.5), speech(3.0), silence(1.5), speech(2.0), silence(1.5)])
    pcm = pcm16(audio)
    finals = []
    with client.websocket_connect(f"/api/captions/ws/listen/{sid}") as lis:
        assert lis.receive_json()["type"] == "snapshot"
        with client.websocket_connect(f"/api/captions/ws/ingest/{sid}") as ws:
            assert ws.receive_json()["type"] == "ready"
            ws.send_json({"type": "start"})
            assert ws.receive_json()["type"] == "started"
            for i in range(0, len(pcm), 3200):
                ws.send_bytes(pcm[i:i + 3200])
            ws.send_json({"type": "stop"})
            while ws.receive_json()["type"] != "stopped":
                pass
        for _ in range(200):
            ev = lis.receive_json()
            if ev.get("type") == "final":
                finals.append(ev)
                if len(finals) == 2:
                    break
    assert finals, "the pipeline produced no captions, so the test proved nothing"

    # 1. Nothing opened a file for writing.
    assert write_spy == [], f"files opened for writing while audio was processed: {write_spy}"

    # 2. No file appeared or changed in temp, the project or the model volume.
    after = snapshot(*watched)
    changed = sorted(p for p, meta in after.items() if before.get(p) != meta)
    assert changed == [], f"files created or modified while audio was processed: {changed}"
    assert not [p for p in after if Path(p).suffix.lower() in AUDIO_EXT and p not in before]

    # 3. No audio in the logs: no raw PCM bytes, no byte/array dumps, no huge log lines.
    text = caplog.text
    sample = pcm[16000:16064]  # 32 samples from the middle of the first speech burst
    assert sample.hex() not in text and repr(sample)[2:-1] not in text
    assert "\\x" not in text, "a bytes object was written to the logs"
    assert max((len(r.getMessage()) for r in caplog.records), default=0) < 2000


def test_session_end_drops_the_audio_buffers(client, monkeypatch):
    """After the lecturer ends a session the server keeps no reference to its audio."""
    monkeypatch.setattr(recognizers, "make_vad", EnergyVad)
    sid = client.post("/api/captions/session").json()["session_id"]
    with client.websocket_connect(f"/api/captions/ws/ingest/{sid}") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "start"})
        assert ws.receive_json()["type"] == "started"
        ws.send_bytes(pcm16(speech(1.0)))
    assert client.delete(f"/api/captions/session/{sid}").status_code == 200
    assert client.app.state.captions.get(sid) is None
