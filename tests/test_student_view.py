"""#22 #24 #26: students follow on their phones — room code / QR, the /watch page, scrollback from the start."""
import re

import pytest

from app.core import config
from app.core.config import Settings, get_settings
from app.stt import manager as manager_module
from app.stt.manager import CODE_ALPHABET

SECRET = "s3cret-for-tests"
CODE_RE = re.compile(rf"^[{CODE_ALPHABET}]{{3}}-[{CODE_ALPHABET}]{{3}}$")


def new_session(client, headers=None):
    r = client.post("/api/captions/session", headers=headers or {})
    assert r.status_code == 200, r.text
    return r.json()


def session_obj(sid):
    from app.main import app
    return app.state.captions.get(sid)


def say(s, n):
    """n finished sentences on mic 1, ids 1..n."""
    for i in range(1, n + 1):
        s._on_event({"type": "final", "utterance": i, "text": f"Sentence {i}.", "start": 0.0, "end": 1.0,
                     "latency": 0.1}, s.channel(1), 1)


# ------------------------------------------------------------------ room code
def test_session_gets_a_readable_room_code(client):
    data = new_session(client)
    assert CODE_RE.match(data["code"]), data["code"]
    assert data["join_url"].endswith("/j/" + data["code"].replace("-", ""))
    # the public snapshot (overlay) carries it too, so the projector can show it
    assert client.get(f"/api/captions/session/{data['session_id']}").json()["code"] == data["code"].replace("-", "")


def test_codes_are_unique_among_open_sessions(client, monkeypatch):
    from app.main import app
    taken = session_obj(new_session(client)["session_id"]).code
    picks = iter(taken + "AAAAAA")  # the first draw collides with the open session, the second does not
    monkeypatch.setattr(manager_module.secrets, "choice", lambda alphabet: next(picks))
    assert app.state.captions._new_code() == "AAAAAA"


@pytest.mark.parametrize("variant", [str.lower, lambda c: c[:3] + "-" + c[3:], lambda c: f" {c[:3]} {c[3:]} "])
def test_join_accepts_lowercase_and_dashes(client, variant):
    data = new_session(client)
    code = data["code"].replace("-", "")
    r = client.get(f"/api/captions/join/{variant(code)}")
    assert r.status_code == 200 and r.json() == {"session_id": data["session_id"], "code": data["code"]}


def test_unknown_or_ended_codes_are_not_found(client):
    assert client.get("/api/captions/join/ZZZZZZ").status_code == 404
    assert client.get("/api/captions/join/x").status_code == 404
    data = new_session(client)
    client.delete(f"/api/captions/session/{data['session_id']}")
    r = client.get(f"/api/captions/join/{data['code']}")
    assert r.status_code == 404 and r.json()["detail"]["code"] == "no_session"


def test_short_link_redirects_to_the_phone_view(client):
    data = new_session(client)
    r = client.get(f"/j/{data['code'].replace('-', '')}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/watch?session={data['session_id']}"
    r = client.get("/j/ZZZZZZ", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/watch?code=ZZZZZZ"
    assert client.get("/watch").status_code == 200


# ------------------------------------------------------------------ QR code
def test_qr_code_is_an_svg(client):
    sid = new_session(client)["session_id"]
    r = client.get(f"/api/captions/session/{sid}/qr.svg")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
    assert r.text.lstrip().startswith("<svg") and r.headers["cache-control"] == "no-store"
    assert client.get("/api/captions/session/nope/qr.svg").status_code == 404


def test_students_never_need_the_lecturer_password(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "lecturer_secret", SECRET, raising=False)
    data = new_session(client, {"X-Lecturer-Secret": SECRET})
    assert client.get(f"/api/captions/join/{data['code']}").status_code == 200
    assert client.get(f"/api/captions/session/{data['session_id']}/qr.svg").status_code == 200
    assert client.get(f"/j/{data['code']}", follow_redirects=False).status_code == 303
    with client.websocket_connect(f"/api/captions/ws/listen/{data['session_id']}?history=all") as ws:
        assert ws.receive_json()["type"] == "snapshot"


def test_join_link_uses_public_url(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "public_url", "http://10.0.0.5:8080/", raising=False)
    data = new_session(client)
    assert data["join_url"] == "http://10.0.0.5:8080/j/" + data["code"].replace("-", "")


# ------------------------------------------------------------------ scrollback
def test_phones_get_the_whole_lecture_projector_gets_the_last_20(client):
    sid = new_session(client)["session_id"]
    say(session_obj(sid), 25)
    with client.websocket_connect(f"/api/captions/ws/listen/{sid}?history=all") as ws:
        snap = ws.receive_json()
        assert [h["utterance"] for h in snap["history"]] == list(range(1, 26))
    with client.websocket_connect(f"/api/captions/ws/listen/{sid}") as ws:
        assert [h["utterance"] for h in ws.receive_json()["history"]] == list(range(6, 26))
    assert len(client.get(f"/api/captions/session/{sid}").json()["history"]) == 20  # polling fallback


def test_clearing_reaches_phones_through_cleared_upto(client):
    sid = new_session(client)["session_id"]
    s = session_obj(sid)
    say(s, 3)
    assert client.get(f"/api/captions/session/{sid}").json()["cleared_upto"] == 0
    assert client.post(f"/api/captions/session/{sid}/clear").status_code == 200
    with client.websocket_connect(f"/api/captions/ws/listen/{sid}?history=all") as ws:
        snap = ws.receive_json()
    assert snap["history"] == [] and snap["cleared_upto"] == 3


# ------------------------------------------------------------------ network settings
def make(**kw):
    return Settings(_env_file=None, **kw)


def test_server_refuses_the_network_without_a_lecturer_password():
    assert make(host="0.0.0.0", lecturer_secret="").bind_error()
    assert make(host="192.168.1.7", lecturer_secret="").bind_error()
    assert make(host="0.0.0.0", lecturer_secret="x").bind_error() is None
    for local in ("127.0.0.1", "localhost", "::1"):
        assert make(host=local, lecturer_secret="").bind_error() is None


def test_public_base_url(monkeypatch):
    monkeypatch.setattr(config, "lan_ip", lambda: "192.168.1.20")
    assert make(public_url="https://captions.example.org/").public_base_url() == "https://captions.example.org"
    assert make(host="127.0.0.1").public_base_url() == ""
    assert make(host="0.0.0.0", port=9000).public_base_url() == "http://192.168.1.20:9000"
    assert make(host="192.168.1.7").public_base_url() == "http://192.168.1.7:8080"
    monkeypatch.setattr(config, "lan_ip", lambda: None)
    assert make(host="0.0.0.0").public_base_url() == ""


def test_console_learns_whether_phones_can_reach_the_server(client, monkeypatch):
    local = {"host": "127.0.0.1:8080"}
    info = client.get("/api/captions/engines", headers=local).json()
    assert info["lan_ready"] is False and info["public_url"] == "http://127.0.0.1:8080"
    monkeypatch.setattr(get_settings(), "public_url", "http://192.168.1.20:8080", raising=False)
    info = client.get("/api/captions/engines", headers=local).json()
    assert info["lan_ready"] is True and info["public_url"] == "http://192.168.1.20:8080"
