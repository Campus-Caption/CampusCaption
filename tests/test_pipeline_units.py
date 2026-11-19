"""Pure-logic tests: segmenter, LocalAgreement, text filter, VAD wrapper."""
import wave
from pathlib import Path

import numpy as np
import pytest

from app.stt.agreement import LocalAgreement
from app.stt.segmenter import Cut, Dropped, Segmenter, Started
from app.stt.textfilter import clean
from app.stt.types import FRAME


def run(seg: Segmenter, probs):
    events = []
    for p in probs:
        events += seg.push(np.full(FRAME, p, dtype=np.float32), p)
    return events


def kinds(events):
    return [type(e).__name__ for e in events]


# ------------------------------------------------------------------ segmenter
def test_speech_then_silence_yields_one_utterance():
    seg = Segmenter()
    ev = run(seg, [0.0] * 10 + [0.9] * 40 + [0.0] * 30)
    assert kinds(ev) == ["Started", "Cut"]
    cut = ev[1]
    assert cut.reason == "silence"
    # preroll (10 frames of silence kept = 320 ms cap) + 40 speech + 6 frames tail, far less than the 30 silent frames
    assert 40 * FRAME < len(cut.audio) < 40 * FRAME + 20 * FRAME
    assert not seg.in_speech


def test_started_includes_preroll():
    seg = Segmenter()
    ev = run(seg, [0.0] * 20 + [0.9] * 3)
    assert isinstance(ev[0], Started)
    assert ev[0].sample == (20 + 3 - 10) * FRAME  # 10-frame preroll ends at the frame that triggered the start


def test_short_click_is_dropped():
    seg = Segmenter()
    ev = run(seg, [0.9] * 4 + [0.0] * 40)
    assert kinds(ev) == ["Started", "Dropped"]


def test_short_pause_inside_sentence_does_not_cut():
    seg = Segmenter()
    ev = run(seg, [0.9] * 20 + [0.0] * 8 + [0.9] * 20 + [0.0] * 30)
    assert kinds(ev) == ["Started", "Cut"]


def test_long_speech_is_split_at_a_pause():
    seg = Segmenter(max_utterance_s=6.0)
    # 4 s speech, 0.3 s pause, then speech until the limit is hit
    ev = run(seg, [0.9] * 125 + [0.1] * 10 + [0.9] * 70)
    assert kinds(ev) == ["Started", "Cut", "Started"]
    cut = ev[1]
    assert cut.reason == "max_length"
    assert 125 * FRAME <= len(cut.audio) <= 136 * FRAME  # cut inside the pause, not mid-word
    assert seg.in_speech  # the rest keeps going as the next utterance


def test_flush_closes_open_utterance():
    seg = Segmenter()
    run(seg, [0.9] * 30)
    out = seg.flush()
    assert len(out) == 1 and isinstance(out[0], Cut) and out[0].reason == "flush"
    assert seg.flush() == []


def test_snapshot_only_while_speaking():
    seg = Segmenter()
    assert seg.snapshot() is None
    run(seg, [0.9] * 10)
    assert seg.snapshot() is not None and len(seg.snapshot()) == 10 * FRAME


# ------------------------------------------------------------------ LocalAgreement
def test_agreement_commits_only_confirmed_words():
    la = LocalAgreement()
    assert la.update("Das ist ein") == ("", "Das ist ein")           # nothing confirmed yet
    assert la.update("Das ist ein Test") == ("Das ist ein", "Test")  # first three confirmed twice
    assert la.update("Das ist ein Test der Vorlesung") == ("Das ist ein Test", "der Vorlesung")


def test_agreement_never_rewrites_committed_words():
    la = LocalAgreement()
    la.update("Hallo zusammen und")
    committed, _ = la.update("Hallo zusammen und willkommen")
    assert committed == "Hallo zusammen und"
    # a later hypothesis that disagrees with committed text must not change it
    committed2, tentative = la.update("Hallo Zusammen, und willkommen heute")
    assert committed2.startswith("Hallo zusammen und")
    assert committed2 == "Hallo zusammen und" or committed2.split()[:3] == ["Hallo", "zusammen", "und"]


def test_agreement_ignores_case_and_punctuation_differences():
    la = LocalAgreement()
    la.update("hello world")
    assert la.update("Hello, world again")[0].lower().replace(",", "") == "hello world"


# ------------------------------------------------------------------ text filter
@pytest.mark.parametrize("text", ["Untertitel der Amara.org-Community", "Thanks for watching!", ""])
def test_hallucinations_dropped(text):
    assert clean(text) == ""


def test_normal_sentences_survive():
    assert clean("Thank you. Danke schön!") == "Thank you. Danke schön!"
    assert clean("Vielen Dank, dass Sie da sind.") == "Vielen Dank, dass Sie da sind."


def test_repetition_loop_collapsed():
    assert clean("ja ja ja ja ja ja ja") == "ja"
    assert clean("das ist gut das ist gut das ist gut das ist gut") == "das ist gut"
    assert clean("sehr sehr gut") == "sehr sehr gut"  # normal emphasis kept


# ------------------------------------------------------------------ real VAD (bundled Silero model)
ort = pytest.importorskip("onnxruntime")
from app.stt.vad import SileroVad  # noqa: E402


def _frames(x):
    for i in range(0, len(x) // FRAME * FRAME, FRAME):
        yield x[i:i + FRAME]


def test_silero_quiet_for_silence_and_noise():
    vad = SileroVad()
    rng = np.random.default_rng(0)
    probs = [vad.prob(f) for f in _frames(rng.normal(0, 0.002, 16000).astype(np.float32))]
    assert max(probs) < 0.3


SPEECH = Path(__file__).parent / "data" / "speech_en.wav"


@pytest.mark.skipif(not SPEECH.exists(), reason="speech sample missing")
def test_silero_detects_real_speech_and_segmenter_cuts_two_sentences():
    with wave.open(str(SPEECH)) as w:
        assert w.getframerate() == 16000
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768
    x = np.concatenate([np.zeros(8000, np.float32), x, np.zeros(16000, np.float32)])
    vad, seg, events = SileroVad(), Segmenter(), []
    for f in _frames(x):
        events += seg.push(f, vad.prob(f))
    cuts = [e for e in events if isinstance(e, Cut)]
    assert len(cuts) >= 1
    assert sum(len(c.audio) for c in cuts) > 0.6 * len(x[8000:-16000])
