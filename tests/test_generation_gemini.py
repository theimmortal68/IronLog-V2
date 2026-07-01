"""tests/test_generation_gemini.py — NAMED GATE a: mocked HTTP, no live API call."""
import json
import pytest
from ironlog.generation.gemini import GeminiProposer, ProposerError, _default_http_client


class _FakeResp:
    def __init__(self, payload):
        self._p = payload
        self.status_code = 200

    def json(self):
        return self._p

    def raise_for_status(self):
        pass


class _FakeHTTP:
    def __init__(self, payload):
        self._p = payload
        self.last = None

    def post(self, url, **kw):
        self.last = (url, kw)
        return _FakeResp(self._p)


def _gemini_envelope(obj):
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(obj)}]}}]}


def test_conforming_response_parses_to_selections():
    obj = {
        "ordering": ["s1"],
        "slots": [{"slot_id": "s1", "movement_id": 7}],
        "rationale": "ok",
    }
    http = _FakeHTTP(_gemini_envelope(obj))
    sel = GeminiProposer("k", http=http).propose({"day_role": "D1 Upper Push", "slots": []})
    assert sel.slots[0].movement_id == 7
    # the request must set structured-output + the schema (gate a)
    _, kw = http.last
    body = kw["json"]["generationConfig"]
    assert body["responseMimeType"] == "application/json"
    assert "responseJsonSchema" in body or "responseSchema" in body


def test_nonconforming_response_is_rejected():
    bad = {"ordering": ["s1"], "rationale": "x"}  # missing required 'slots'
    http = _FakeHTTP(_gemini_envelope(bad))
    with pytest.raises(ProposerError):
        GeminiProposer("k", http=http).propose({"slots": []})


def test_missing_candidates_raises():
    http = _FakeHTTP({})  # no 'candidates' key
    with pytest.raises(ProposerError):
        GeminiProposer("k", http=http).propose({"slots": []})


def test_invalid_json_in_text_raises():
    bad_envelope = {"candidates": [{"content": {"parts": [{"text": "not json {{"}]}}]}
    http = _FakeHTTP(bad_envelope)
    with pytest.raises(ProposerError):
        GeminiProposer("k", http=http).propose({"slots": []})


def test_api_key_from_env(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "env-key")
    obj = {
        "ordering": [],
        "slots": [],
        "rationale": "empty",
    }
    http = _FakeHTTP(_gemini_envelope(obj))
    # Should not raise even though no api_key arg — reads from env
    sel = GeminiProposer(http=http).propose({"slots": []})
    assert sel.rationale == "empty"
    _, kw = http.last
    assert kw["headers"]["x-goog-api-key"] == "env-key"


def test_default_http_client_has_generous_timeout():
    """_default_http_client() must carry a read timeout >= 30s.

    Dynamic thinking (thinkingBudget=-1) takes ~7s typical; the old httpx
    default of 5s caused systematic ReadTimeout.  This asserts the fix holds.
    No live network call is made.
    """
    c = _default_http_client()
    assert c.timeout.read is not None, "read timeout must be set (not None / infinite)"
    assert c.timeout.read >= 30, (
        f"read timeout {c.timeout.read}s is too short for dynamic thinking responses"
    )
