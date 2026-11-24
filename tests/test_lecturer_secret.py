"""#16: only the lecturer (who knows LECTURER_SECRET) may start, change, clear, export and end captions."""
import pytest
from starlette.websockets import WebSocketDisconnect

from app.core.config import get_settings

SECRET = "s3cret-for-tests"
OK = {"X-Lecturer-Secret": SECRET}
BAD = {"X-Lecturer-Secret": "wrong"}


@pytest.fixture()
def protected(monkeypatch):
    monkeypatch.setattr(get_settings(), "lecturer_secret", SECRET, raising=False)


def start(client, headers=OK):
    return client.post("/api/captions/session", headers=headers)


def test_engines_report_whether_a_password_is_needed(client, protected):
    data = client.get("/api/captions/engines").json()
    assert data["auth_required"] is True
    assert SECRET not in str(data)


def test_engines_report_no_password_when_unset(client):
    assert client.get("/api/captions/engines").json()["auth_required"] is False


@pytest.mark.parametrize("headers", [{}, BAD])
def test_starting_a_session_needs_the_secret(client, protected, headers):
    r = start(client, headers)
    assert r.status_code == 401 and r.json()["detail"]["code"] == "unauthorized"
    assert start(client).status_code == 200


@pytest.mark.parametrize("headers", [{}, BAD])
def test_every_control_endpoint_rejects_requests_without_the_secret(client, protected, headers):
    sid = start(client).json()["session_id"]
    calls = [
        ("patch", f"/api/captions/session/{sid}/settings", {"json": {"polish": True}}),
        ("post", f"/api/captions/session/{sid}/clear", {}),
        ("get", f"/api/captions/session/{sid}/transcript", {}),
        ("post", "/api/captions/engines/local/parakeet/warm", {}),
        ("delete", f"/api/captions/session/{sid}", {}),
    ]
    for method, url, kw in calls:
        assert getattr(client, method)(url, headers=headers, **kw).status_code == 401, url
    # ... and they still work with it.
    for method, url, kw in calls:
        assert getattr(client, method)(url, headers=OK, **kw).status_code == 200, url


def test_viewer_endpoints_stay_public(client, protected):
    sid = start(client).json()["session_id"]
    assert client.get(f"/api/captions/session/{sid}").status_code == 200
    assert client.get(f"/api/captions/session/{sid}/latest").status_code == 200
    with client.websocket_connect(f"/api/captions/ws/listen/{sid}") as lis:
        assert lis.receive_json()["type"] == "snapshot"
    assert client.get("/overlay").status_code == 200


@pytest.mark.parametrize("first", [{"type": "auth", "secret": "wrong"}, {"type": "start"}, {"type": "auth"}])
def test_microphone_stream_rejects_without_the_secret(client, protected, first):
    sid = start(client).json()["session_id"]
    with client.websocket_connect(f"/api/captions/ws/ingest/{sid}") as ws:
        ws.send_json(first)
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 4401


def test_microphone_stream_accepts_the_secret(client, protected):
    sid = start(client).json()["session_id"]
    with client.websocket_connect(f"/api/captions/ws/ingest/{sid}") as ws:
        ws.send_json({"type": "auth", "secret": SECRET})
        assert ws.receive_json()["type"] == "ready"


def test_without_a_configured_secret_the_auth_message_is_harmless(client):
    sid = start(client, headers={}).json()["session_id"]
    with client.websocket_connect(f"/api/captions/ws/ingest/{sid}") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "auth", "secret": ""})
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"
