"""tests/test_note_classifier.py — mocked HTTP, no live Gemini call."""
import json
import pytest

from ironlog.generation.gemini import ProposerError
from ironlog.models.enums import NoteClass
from ironlog.notes.classify import NoteClassifier


class _FakeResp:
    def __init__(self, payload):
        self._p = payload
    def json(self):
        return self._p
    def raise_for_status(self):
        pass


class _FakeHTTP:
    def __init__(self, payload, boom=False):
        self._p = payload
        self.boom = boom
        self.last = None
    def post(self, url, **kw):
        self.last = (url, kw)
        if self.boom:
            raise RuntimeError("network down")
        return _FakeResp(self._p)


def _envelope(obj):
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(obj)}]}}]}


def test_config_change_extracts_proposed_change():
    obj = {"classification": "CONFIG_CHANGE",
           "proposed_change": {"movement": "Bench Press", "action": "switch", "params": "to incline"},
           "confidence": 0.9, "rationale": "asks to switch bench to incline"}
    clf = NoteClassifier("k", http=_FakeHTTP(_envelope(obj)))
    r = clf.classify("switch flat bench to incline")
    assert r.classification == NoteClass.CONFIG_CHANGE
    assert r.proposed_change["movement"] == "Bench Press"
    assert r.confidence == 0.9


@pytest.mark.parametrize("cls", ["TRANSIENT_FLAG", "PROGRAMMING_REQUEST", "JOURNAL"])
def test_other_classes_parse(cls):
    obj = {"classification": cls, "proposed_change": None, "confidence": 0.7, "rationale": "x"}
    clf = NoteClassifier("k", http=_FakeHTTP(_envelope(obj)))
    r = clf.classify("some note")
    assert r.classification == NoteClass(cls)
    assert r.proposed_change is None


def test_request_sets_structured_output_schema():
    obj = {"classification": "JOURNAL", "confidence": 0.5, "rationale": "log"}
    http = _FakeHTTP(_envelope(obj))
    NoteClassifier("k", http=http).classify("felt good")
    _, kw = http.last
    gc = kw["json"]["generationConfig"]
    assert gc["responseMimeType"] == "application/json"
    assert "responseJsonSchema" in gc


def test_bad_classification_value_raises():
    obj = {"classification": "NONSENSE", "confidence": 0.5, "rationale": "x"}
    with pytest.raises(ProposerError):
        NoteClassifier("k", http=_FakeHTTP(_envelope(obj))).classify("x")


def test_http_failure_propagates_as_exception():
    # classify() does not swallow — the caller (classify_session_notes) degrades.
    with pytest.raises(RuntimeError):
        NoteClassifier("k", http=_FakeHTTP({}, boom=True)).classify("x")


def test_missing_key_raises_value_error(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ValueError):
        NoteClassifier()
