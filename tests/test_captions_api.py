import wave
from pathlib import Path

import pytest

WAV = Path(__file__).parent / "data" / "speech_en.wav"


def new_session(client, **settings):
    r = client.post("/api/captions/session", json={"settings": settings} if settings else None)
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def test_only_local_models_are_offered_and_keys_never_leak(client):
    data = client.get("/api/captions/engines").json()
    assert "engines" not in data
    assert [m["id"] for m in data["models"]] == ["nemotron", "parakeet", "whisper-turbo", "whisper-small", "voxtral"]
    assert "engine" not in data["defaults"] and "glossary" not in data["defaults"]
    assert data["polish"]["available"] is True
    assert "test-anthropic-key" not in str(data)
    text = str(data).lower()
    assert "openai" not in text and "audio is sent" not in text and "api_key" not in text
    assert data["privacy"] == "Audio never leaves this computer."


def test_no_auth_routes_remain(client):
    for path in ("/login", "/register", "/api/auth/me", "/api/users"):
        assert client.get(path).status_code == 404


def test_pages_load(client):
    assert client.get("/").status_code == 200
    assert client.get("/overlay").status_code == 200
    assert client.get("/api/health").json()["status"] == "ok"


def test_session_settings_switch_and_validation(client):
    sid = new_session(client)
    r = client.patch(f"/api/captions/session/{sid}/settings", json={"polish": True})
    assert r.status_code == 200 and r.json()["settings"]["polish"] is True
    assert client.patch(f"/api/captions/session/{sid}/settings", json={"language": "klingon!"}).status_code in (400, 422)
    r = client.patch(f"/api/captions/session/{sid}/settings", json={"local_model": "whisper-small"})
    assert r.status_code == 200 and r.json()["settings"]["local_model"] == "whisper-small"
    assert client.patch(f"/api/captions/session/{sid}/settings", json={"local_model": "gpt-4o"}).status_code == 422
    pub = client.get(f"/api/captions/session/{sid}")
    assert pub.status_code == 200 and "test-" not in pub.text


def test_unknown_session_is_404(client):
    assert client.get("/api/captions/session/nope").status_code == 404
    assert client.post("/api/captions/session/nope/clear").status_code == 404


def test_cloud_engines_and_glossary_are_gone(client):
    """Only local models: an old client asking for a cloud engine or a glossary gets no cloud and no glossary."""
    r = client.post("/api/captions/session", json={"settings": {"engine": "openai", "glossary": ["Kubernetes"]}})
    assert r.status_code == 200
    settings = r.json()["settings"]
    assert "engine" not in settings and "glossary" not in settings
    snap = client.get(f"/api/captions/session/{r.json()['session_id']}").json()
    assert "engine" not in snap["settings"] and "glossary" not in snap["settings"]


def test_transcript_is_plain_text_only(client):
    sid = new_session(client)
    r = client.get(f"/api/captions/session/{sid}/transcript")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    assert r.headers["content-disposition"].endswith('untertitel.txt"')
    # Old format links still get plain text, never VTT/SRT/JSON.
    for fmt in ("vtt", "srt", "json"):
        r = client.get(f"/api/captions/session/{sid}/transcript?format={fmt}")
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
        assert "WEBVTT" not in r.text and "-->" not in r.text


@pytest.mark.skipif(not WAV.exists(), reason="speech sample missing (audio is never committed)")
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

    assert final["text"] in client.get(f"/api/captions/session/{sid}/transcript").text


def test_each_microphone_slot_takes_one_connection(client):
    sid = new_session(client)
    with client.websocket_connect(f"/api/captions/ws/ingest/{sid}") as first:
        assert first.receive_json()["type"] == "ready"
        with client.websocket_connect(f"/api/captions/ws/ingest/{sid}?mic=1") as second:
            with pytest.raises(Exception):
                second.receive_json()


def test_clear_removes_history_and_in_progress_sentence(client):
    from app.main import app
    sid = new_session(client)
    s = app.state.captions.get(sid)
    s._on_event({"type": "final", "utterance": 1, "text": "Hello there.", "start": 0.0, "end": 1.0, "latency": 0.1}, s.channel(1), 1)
    s._on_event({"type": "partial", "utterance": 2, "committed": "half a", "tentative": "sentence"}, s.channel(1), 1)
    assert "Hello there." in s.latest_text()
    assert client.post(f"/api/captions/session/{sid}/clear").status_code == 200
    assert s.latest_text() == ""
    # the sentence that was being spoken when Clear was pressed must not come back
    s._on_event({"type": "partial", "utterance": 2, "committed": "half a sentence", "tentative": "again"}, s.channel(1), 1)
    s._on_event({"type": "final", "utterance": 2, "text": "half a sentence again.", "start": 1.0, "end": 2.0, "latency": 0.1}, s.channel(1), 1)
    assert s.latest_text() == ""
    # new speech afterwards shows up normally
    s._on_event({"type": "final", "utterance": 3, "text": "Next one.", "start": 2.0, "end": 3.0, "latency": 0.1}, s.channel(1), 1)
    assert s.latest_text() == "Next one."


def test_obs_features_are_gone(client):
    """No OBS link, no green/transparent themes, no plain-text polling endpoint, overlay cannot be framed."""
    sid = new_session(client)
    assert client.get(f"/api/captions/session/{sid}/latest").status_code in (404, 405)
    console = client.get("/").text
    assert "copy-obs" not in console and "OBS" not in console
    overlay = client.get("/overlay")
    assert overlay.headers["x-frame-options"] == "DENY"
    js = client.get("/static/js/overlay.js").text
    assert '"green"' not in js and '"transparent"' not in js
