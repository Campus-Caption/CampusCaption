"""#39: two microphones (e.g. lecturer lav mic + audience mic) in one session, captions tagged by mic."""
import numpy as np
import pytest
from starlette.websockets import WebSocketDisconnect

from app.stt import recognizers
from tests.fakes import pcm16, silence, speech


class EnergyVad:
    def prob(self, frame: np.ndarray) -> float:
        return 0.95 if float(np.sqrt(np.mean(frame ** 2))) > 0.02 else 0.02


def final(text, utt=1):
    return {"type": "final", "utterance": utt, "text": text, "start": 0.0, "end": 1.0, "latency": 0.1}


@pytest.fixture()
def session(client):
    from app.main import app
    sid = client.post("/api/captions/session").json()["session_id"]
    return sid, app.state.captions.get(sid)


def test_both_mics_connect_at_once_and_a_third_is_refused(client, session):
    sid, _ = session
    with client.websocket_connect(f"/api/captions/ws/ingest/{sid}?mic=1") as m1, \
         client.websocket_connect(f"/api/captions/ws/ingest/{sid}?mic=2") as m2:
        assert m1.receive_json()["type"] == "ready"
        assert m2.receive_json()["type"] == "ready"
        with client.websocket_connect(f"/api/captions/ws/ingest/{sid}?mic=3") as m3:
            with pytest.raises(WebSocketDisconnect) as exc:
                m3.receive_json()
            assert exc.value.code == 4422


def test_two_mics_stream_into_one_feed_with_speaker_tags(client, session, monkeypatch):
    monkeypatch.setattr(recognizers, "make_vad", EnergyVad)
    sid, s = session
    audio = pcm16(np.concatenate([silence(0.3), speech(2.0), silence(1.5)]))
    with client.websocket_connect(f"/api/captions/ws/listen/{sid}") as lis:
        assert lis.receive_json()["type"] == "snapshot"
        with client.websocket_connect(f"/api/captions/ws/ingest/{sid}?mic=1") as m1, \
             client.websocket_connect(f"/api/captions/ws/ingest/{sid}?mic=2") as m2:
            for ws in (m1, m2):
                assert ws.receive_json()["type"] == "ready"
                ws.send_json({"type": "start"})
                assert ws.receive_json() == {"type": "started", "mic": ws is m1 and 1 or 2}
            for i in range(0, len(audio), 3200):
                m1.send_bytes(audio[i:i + 3200])
            m1.send_json({"type": "stop"})
            while m1.receive_json()["type"] != "stopped":
                pass
        events = []
        for _ in range(200):
            ev = lis.receive_json()
            events.append(ev)
            if ev.get("type") == "final" and ev["text"]:
                break
    assert s.multi_mic
    assert any(e.get("type") == "settings" and e["settings"]["multi_mic"] for e in events)
    finals = [e for e in events if e.get("type") == "final" and e["text"]]
    assert finals and finals[0]["speaker"] == 1


def test_captions_from_each_mic_keep_their_own_ids_and_labels(client, session):
    sid, s = session
    s.mics_used.update({1, 2})
    s._on_event(final("Welcome to the lecture."), s.channel(1), 1)
    s._on_event(final("Can you repeat the formula?"), s.channel(2), 1)  # mic 2 also starts at utterance 1
    hist = [u.public() for u in s.history]
    assert [h["speaker"] for h in hist] == [1, 2]
    assert len({h["utterance"] for h in hist}) == 2, "both mics' first sentence must not share an id"
    text = client.get(f"/api/captions/session/{sid}/transcript").text
    assert text == "Lecturer: Welcome to the lecture.\nAudience: Can you repeat the formula?\n"


def test_speaker_labels_can_be_renamed(client, session):
    sid, s = session
    r = client.patch(f"/api/captions/session/{sid}/settings", json={"speakers": ["Prof.", "  Frage  "]})
    assert r.status_code == 200 and r.json()["settings"]["speakers"] == ["Prof.", "Frage"]
    r = client.patch(f"/api/captions/session/{sid}/settings", json={"speakers": ["", ""]})
    assert r.json()["settings"]["speakers"] == ["Lecturer", "Audience"]
    assert client.patch(f"/api/captions/session/{sid}/settings", json={"speakers": ["a", "b", "c"]}).status_code == 422


def test_one_mic_picking_up_the_other_is_not_captioned_twice(session):
    _, s = session
    s.mics_used.update({1, 2})
    s._on_event(final("Today we talk about backpropagation."), s.channel(1), 1)
    # the audience mic hears the lecturer too
    s._on_event(final("today we talk about back propagation"), s.channel(2), 1)
    s._on_event(final("Why is the gradient negative?", 2), s.channel(2), 1)
    assert [u.text for u in s.history] == ["Today we talk about backpropagation.", "Why is the gradient negative?"]


def test_single_mic_transcript_has_no_labels(client, session):
    sid, s = session
    s.mics_used.add(1)
    s._on_event(final("Just me today."), s.channel(1), 1)
    assert client.get(f"/api/captions/session/{sid}/transcript").text == "Just me today.\n"


def test_live_lines_of_both_mics_are_kept_apart(session):
    _, s = session
    s._on_event({"type": "partial", "utterance": 1, "committed": "lecturer", "tentative": "talks"}, s.channel(1), 1)
    s._on_event({"type": "partial", "utterance": 1, "committed": "student", "tentative": "asks"}, s.channel(2), 1)
    live = s.snapshot()["live"]
    assert {lv["speaker"] for lv in live} == {1, 2}
    assert "lecturer talks" in s.latest_text() and "student asks" in s.latest_text()
