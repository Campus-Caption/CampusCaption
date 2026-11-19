import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

# Settings must exist before the app is imported.
os.environ.update({
    "STT_MODEL_DIR": os.path.join(tempfile.gettempdir(), "campuscaption-test-models"),
    "OPENAI_API_KEY": "test-openai-key",
    "MISTRAL_API_KEY": "test-mistral-key",
    "ANTHROPIC_API_KEY": "test-anthropic-key",
})

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.stt import models as stt_models  # noqa: E402
from tests.fakes import FakeEngine  # noqa: E402


@pytest.fixture(autouse=True)
def fake_local_models(monkeypatch):
    """Never load or download a real model in tests."""
    engine = FakeEngine()
    for key in stt_models.LOCAL_MODELS:
        monkeypatch.setitem(stt_models.FACTORIES, key, lambda cfg, k, progress, e=engine: e)
    return engine


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c
