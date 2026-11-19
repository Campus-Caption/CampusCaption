import wave
from pathlib import Path

import pytest

WAV = Path(__file__).parent / "data" / "speech_en.wav"


def new_session(client, **settings):
    r = client.post("/api/captions/session", json={"settings": settings} if settings else None)
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def test_engines_listed_without_login_and_keys_never_leak(client):
    data = client.get("/api/captions/engines").json()
    assert [e["id"] for e in data["engines"]] == ["local", "openai", "mistral"]
    assert data["polish"]["available"] is True
    assert "test-openai-key" not in str(data) and "test-anthropic-key" not in str(data)


def test_no_auth_routes_remain(client):
    for path in ("/login", "/register", "/api/auth/me", "/api/users"):
        assert client.get(path).status_code == 404


def test_pages_load(client):
    assert client.get("/").status_code == 200
    assert client.get("/overlay").status_code == 200
    assert client.get("/api/health").json()["status"] == "ok"


def test_session_settings_switch_and_validation(client):
    sid = new_session(client)
    r = client.patch(f"/api/captions/session/{sid}/settings", json={"engine": "openai", "polish": True})
    assert r.status_code == 200 and r.json()["settings"]["engine"] == "openai" and r.json()["settings"]["polish"] is True
    assert client.patch(f"/api/captions/session/{sid}/settings", json={"language": "klingon!"}).status_code in (400, 422)
    r = client.patch(f"/api/captions/session/{sid}/settings", json={"engine": "local", "local_model": "whisper-small"})
    assert r.status_code == 200
    pub = client.get(f"/api/captions/session/{sid}")
    assert pub.status_code == 200 and "test-" not in pub.text


def test_unknown_session_is_404(client):
    assert client.get("/api/captions/session/nope").status_code == 404
    assert client.post("/api/captions/session/nope/clear").status_code == 404


def test_missing_key_marks_engine_unavailable(client, monkeypatch):
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "mistral_api_key", "", raising=False)
    r = client.post("/api/captions/session", json={"settings": {"engine": "mistral"}})
    assert r.status_code == 409


def test_ingest_streams_real_speech_to_listeners(client):
    sid = new_session(client)
    with wave.open(str(WAV)) as w:
        pcm = w.readframes(w.getnframes())
    pcm = b"\x00\x00" * 8000 + pcm + b"\x00\x00" * 24000  # lead-in and a long pause so the sentence is cut

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
        final = None
        for _ in range(60):
            ev = lis.receive_json()
            if ev.get("type") == "final":
                final = ev
                break
        assert final is not None and final["text"]

    assert client.get(f"/api/captions/session/{sid}/transcript?format=json").json()
    for fmt in ("txt", "vtt", "srt"):
        assert client.get(f"/api/captions/session/{sid}/transcript?format={fmt}").status_code == 200
    assert client.get(f"/api/captions/session/{sid}/transcript?format=exe").status_code in (400, 422)


def test_only_one_microphone_per_session(client):
    sid = new_session(client)
    with client.websocket_connect(f"/api/captions/ws/ingest/{sid}") as first:
        assert first.receive_json()["type"] == "ready"
        with client.websocket_connect(f"/api/captions/ws/ingest/{sid}") as second:
            with pytest.raises(Exception):
                second.receive_json()


def test_clear_removes_history_and_in_progress_sentence(client):
    from app.main import app
    sid = new_session(client)
    s = app.state.captions.get(sid)
    s._on_event({"type": "final", "utterance": 1, "text": "Hello there.", "start": 0.0, "end": 1.0, "latency": 0.1}, 0)
    s._on_event({"type": "partial", "utterance": 2, "committed": "half a", "tentative": "sentence"}, 0)
    assert "Hello there." in s.latest_text()
    assert client.post(f"/api/captions/session/{sid}/clear").status_code == 200
    assert s.latest_text() == ""
    # the sentence that was being spoken when Clear was pressed must not come back
    s._on_event({"type": "partial", "utterance": 2, "committed": "half a sentence", "tentative": "again"}, 0)
    s._on_event({"type": "final", "utterance": 2, "text": "half a sentence again.", "start": 1.0, "end": 2.0, "latency": 0.1}, 0)
    assert s.latest_text() == ""
    # new speech afterwards shows up normally
    s._on_event({"type": "final", "utterance": 3, "text": "Next one.", "start": 2.0, "end": 3.0, "latency": 0.1}, 0)
    assert s.latest_text() == "Next one."
