"""tests/test_live_gemini_wiring.py — live GeminiProposer wiring.

Covers:
  1. Factory selection (_make_proposer): key-set -> GeminiProposer; no key -> Stub;
     IRONLOG_FORCE_STUB -> Stub even with a key. (monkeypatch env; NO network call.)
  2. THE ROBUSTNESS TEST (load-bearing): a proposer that always raises ProposerError
     on a deviation-signal path must degrade to the deterministic fallback_session —
     generate_session returns a valid candidate (assembled not None) and does NOT
     propagate the exception. This proves a live Gemini outage degrades to the
     program, never a 500.

NO from __future__ import annotations (project-wide constraint).
gen_db / stalled_session_db fixtures auto-discovered from conftest.py.
"""
from ironlog.api.app import _make_proposer
from ironlog.generation.gemini import GeminiProposer, ProposerError
from ironlog.generation.loop import generate_session
from ironlog.generation.proposer import StubProposer
from ironlog.generation.skeleton import lay_skeleton


# ---------------------------------------------------------------------------
# 1. Factory selection — key-else-stub, with FORCE_STUB kill-switch
# ---------------------------------------------------------------------------

def test_factory_returns_gemini_when_key_set(monkeypatch, gen_db):
    """GEMINI_API_KEY set (+ httpx present on server) -> GeminiProposer."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("IRONLOG_FORCE_STUB", raising=False)
    sk = lay_skeleton("D1 Upper Push", gen_db)
    proposer = _make_proposer(sk)
    assert isinstance(proposer, GeminiProposer), (
        "key present + httpx importable must yield a live GeminiProposer"
    )


def test_factory_returns_stub_when_no_key(monkeypatch, gen_db):
    """No GEMINI_API_KEY -> deterministic StubProposer (never crash)."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("IRONLOG_FORCE_STUB", raising=False)
    sk = lay_skeleton("D1 Upper Push", gen_db)
    proposer = _make_proposer(sk)
    assert isinstance(proposer, StubProposer), "no key must fall back to StubProposer"


def test_factory_force_stub_overrides_key(monkeypatch, gen_db):
    """IRONLOG_FORCE_STUB kill-switch -> StubProposer even with a key set."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("IRONLOG_FORCE_STUB", "1")
    sk = lay_skeleton("D1 Upper Push", gen_db)
    proposer = _make_proposer(sk)
    assert isinstance(proposer, StubProposer), (
        "IRONLOG_FORCE_STUB must force StubProposer even when a key is present"
    )


# ---------------------------------------------------------------------------
# 2. THE ROBUSTNESS TEST (load-bearing) — live propose failure -> fallback
# ---------------------------------------------------------------------------

class _RaisingProposer:
    """A proposer whose .propose() always raises ProposerError (simulates a live
    Gemini outage / transport failure)."""
    def __init__(self):
        self.calls = 0

    def propose(self, payload):
        self.calls += 1
        raise ProposerError("simulated live Gemini outage")


def test_generate_session_falls_back_when_proposer_raises(stalled_session_db):
    """A live propose failure on a deviation-signal path must degrade to the
    deterministic fallback_session — generate_session returns a valid candidate
    and does NOT propagate the exception.

    stalled_session_db is a signal path (should_invoke_llm True) so the proposer
    IS called; if the propose call were left unwrapped in repair.py, the
    ProposerError would propagate and this test would ERROR instead of pass.
    """
    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    raising = _RaisingProposer()
    outcome = generate_session("D1 Upper Push", stalled_session_db, raising, wk)

    assert raising.calls >= 1, "signal path must have called the proposer"
    assert outcome.assembled is not None, (
        "a live propose failure must degrade to the deterministic fallback "
        "(assembled session), never a 500"
    )
    assert outcome.exhausted is True, (
        "repair exhausted via propose-failures; loop.py then attaches the fallback"
    )
