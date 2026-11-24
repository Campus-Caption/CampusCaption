"""Nemotron 3.5 ASR Streaming and Voxtral Mini 4B Realtime as local models (no network, no real weights)."""
import io
import os
import tarfile
from pathlib import Path

import numpy as np
import pytest

from app.stt import models
from app.stt.engines import nemotron, parakeet
from app.stt.engines.voxtral import pick_device


def test_both_models_are_offered_with_their_labels(client):
    data = {m["id"]: m for m in client.get("/api/captions/engines").json()["models"]}
    assert "fastest and lightest" in data["nemotron"]["label"] and "Nemotron 3.5" in data["nemotron"]["label"]
    assert "best accuracy" in data["voxtral"]["label"] and "Voxtral Mini 4B Realtime" in data["voxtral"]["label"]


@pytest.mark.parametrize("key", ["nemotron", "voxtral"])
def test_session_can_switch_to_the_new_models(client, key):
    sid = client.post("/api/captions/session").json()["session_id"]
    r = client.patch(f"/api/captions/session/{sid}/settings", json={"local_model": key})
    assert r.status_code == 200 and r.json()["settings"]["local_model"] == key


def test_missing_packages_are_reported_with_the_install_command(monkeypatch):
    lm = models.LocalModels.__new__(models.LocalModels)
    monkeypatch.setitem(models.FACTORIES, "voxtral", models._voxtral_factory)
    real = models.importlib.util.find_spec
    monkeypatch.setattr(models.importlib.util, "find_spec", lambda m: None if m == "mistral_common" else real(m))
    ok, reason = lm.dependency("voxtral")
    assert not ok and "requirements-voxtral.txt" in reason


def test_voxtral_uses_the_gpu_and_refuses_to_crawl_on_cpu_unless_forced():
    assert pick_device("auto", True) == "cuda"
    assert pick_device("cpu", True) == "cpu"
    assert pick_device("cpu", False) == "cpu"
    with pytest.raises(RuntimeError, match="NVIDIA GPU"):
        pick_device("auto", False)
    with pytest.raises(RuntimeError, match="no NVIDIA GPU"):
        pick_device("cuda", False)


def _fake_archive(folder: str, files) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:bz2") as tar:
        for name in [*files, "README.md", "test_wavs/de.wav"]:
            data = b"x" * 10
            info = tarfile.TarInfo(f"{folder}/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_nemotron_download_unpacks_only_model_files(tmp_path, monkeypatch):
    blob = _fake_archive(nemotron.FOLDER, nemotron.FILES)

    class Resp:
        headers = {"content-length": str(len(blob))}
        def raise_for_status(self): pass
        def iter_bytes(self, n): yield blob
        def __enter__(self): return self
        def __exit__(self, *a): pass

    seen = {}
    monkeypatch.setattr(parakeet.httpx, "stream", lambda m, url, **kw: seen.setdefault("url", url) and Resp())
    folder = nemotron.ensure_model(tmp_path)
    assert seen["url"] == nemotron.ARCHIVE_URL and "github.com/k2-fsa/sherpa-onnx" in seen["url"]
    assert sorted(p.name for p in folder.iterdir()) == sorted(nemotron.FILES)
    assert not list(tmp_path.rglob("*.wav")), "sample recordings must never be unpacked"
    assert not list(tmp_path.glob("*.part")) and not list(tmp_path.glob("*.tmp"))
    # second call: already there, no download
    monkeypatch.setattr(parakeet.httpx, "stream", lambda *a, **k: pytest.fail("downloaded twice"))
    assert nemotron.ensure_model(tmp_path) == folder


class _FakeOnline:
    """Stands in for sherpa_onnx.OnlineRecognizer to check what the engine sends."""
    def __init__(self):
        self.options, self.audio, self.finished = {}, None, False

    def create_stream(self):
        rec = self

        class Stream:
            def set_option(self, k, v): rec.options[k] = v
            def accept_waveform(self, sr, x): rec.sr, rec.audio = sr, x
            def input_finished(self): rec.finished = True
        return Stream()

    def is_ready(self, s):
        self._n = getattr(self, "_n", 0) + 1
        return self._n < 3

    def decode_stream(self, s): pass
    def get_result(self, s): return "  Alles hat ein Ende.  Nur die Wurst hat zwei "


@pytest.mark.parametrize("hint,expected", [("de", "de"), (None, "auto")])
def test_nemotron_engine_pads_and_passes_the_language(hint, expected):
    eng = nemotron.NemotronEngine.__new__(nemotron.NemotronEngine)
    eng._rec = fake = _FakeOnline()
    audio = np.ones(16000, dtype=np.float32)
    text = eng.transcribe(audio, hint)
    assert text == "Alles hat ein Ende. Nur die Wurst hat zwei"
    assert fake.options["language"] == expected and fake.finished and fake.sr == 16000
    lead = len(nemotron.LEAD_IN)
    assert lead >= 0.6 * 16000 and not fake.audio[:lead].any() and fake.audio[lead:lead + 16000].all()


NEMO_DIR = Path(os.environ.get("CC_TEST_MODEL_DIR", "./data/models")) / nemotron.FOLDER


@pytest.mark.skipif(not (NEMO_DIR / "encoder.int8.onnx").exists(), reason="Nemotron model not on disk")
def test_real_nemotron_transcribes_german():
    """Run with CC_TEST_MODEL_DIR pointing at a folder holding the unpacked model and a de.wav sample."""
    import wave
    wav = Path(os.environ.get("CC_TEST_NEMOTRON_WAV", NEMO_DIR / "test_wavs" / "de.wav"))
    if not wav.exists():
        pytest.skip("no German sample")
    with wave.open(str(wav)) as w:
        sr = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768
    x = np.interp(np.arange(int(len(x) * 16000 / sr)) * sr / 16000, np.arange(len(x)), x).astype(np.float32)
    text = nemotron.NemotronEngine(NEMO_DIR, 4).transcribe(x, "de")
    assert "Alles hat ein Ende" in text and "Wurst" in text
