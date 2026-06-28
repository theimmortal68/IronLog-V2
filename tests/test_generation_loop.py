"""test_generation_loop.py — Task 10: orchestrator + conditional gate (NAMED GATES b, e, f).

Named tests:
  test_end_to_end_with_stub_produces_valid_candidate     (end-to-end sanity)
  test_conditional_invocation_quiet_week_no_llm_call     (NAMED GATE f — quiet path)
  test_conditional_invocation_signal_present_calls_llm   (NAMED GATE f — signal path)
  test_analysis_idempotency_no_duplicate_history         (NAMED GATE b)
  test_two_writer_boundary                               (NAMED GATE e)

Reconciliations vs brief:
  - Fixture imports via conftest.py auto-discovery (no explicit import from _gen_fixtures).
  - _CountingProposer defined inline.
  - All day_roles use real program names ("D1 Upper Push").
  - logged_session_id / stalled_session_db defined in conftest.py.

NO from __future__ import annotations (project-wide constraint).
gen_db / logged_session_id / stalled_session_db fixtures auto-discovered from conftest.py.
"""
from sqlmodel import func, select

from ironlog.generation.fallback import program_selections
from ironlog.generation.loop import generate_session, commit_session, is_clean
from ironlog.generation.proposer import StubProposer, Selections
from ironlog.generation.skeleton import lay_skeleton
from ironlog.models.library import E1rmHistory, MovementState
from ironlog.persistence.run_analysis import run_analysis, already_analyzed


# ---------------------------------------------------------------------------
# Local spy helper
# ---------------------------------------------------------------------------

class _CountingProposer:
    """A proposer that counts calls and returns canned valid selections."""
    def __init__(self, canned: Selections):
        self.canned = canned
        self.calls = 0

    def propose(self, payload: dict) -> Selections:
        self.calls += 1
        return self.canned


# ---------------------------------------------------------------------------
# End-to-end sanity
# ---------------------------------------------------------------------------

def test_end_to_end_with_stub_produces_valid_candidate(gen_db):
    """generate_session with a StubProposer produces a non-exhausted candidate."""
    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    sk = lay_skeleton("D1 Upper Push", gen_db)
    stub = StubProposer(program_selections(sk))
    outcome = generate_session("D1 Upper Push", gen_db, stub, wk)
    assert outcome.assembled is not None and not outcome.exhausted


# ---------------------------------------------------------------------------
# NAMED GATE f — conditional invocation
# ---------------------------------------------------------------------------

def test_conditional_invocation_quiet_week_no_llm_call(gen_db):
    """NAMED GATE f (quiet path): meso-1 / no feedback signals → proposer NEVER called.

    gen_db has no stall signals, no open Notes, no novelty_owed entries →
    should_invoke_llm returns False → the §3A gate emits the program deterministically
    without touching the proposer. spy.calls must be 0.
    """
    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    sk = lay_skeleton("D1 Upper Push", gen_db)
    spy = _CountingProposer(program_selections(sk))
    outcome = generate_session("D1 Upper Push", gen_db, spy, wk)
    assert spy.calls == 0, "quiet week must not call the LLM proposer"
    assert outcome.assembled is not None, "program emitted deterministically"
    assert not outcome.exhausted, "quiet-week deterministic path is never exhausted"


def test_conditional_invocation_signal_present_calls_llm(stalled_session_db):
    """NAMED GATE f (signal path): planted stall → should_invoke_llm True → proposer called once.

    stalled_session_db has consecutive_failed_progressions=2 on Pendlay Row - Narrow [OB]
    (d1_t2a, tier_role=semi) → detect_stall fires → movement in weak_point_hints →
    slot_has_deviation_signal True → should_invoke_llm True → proposer called once.
    """
    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    sk = lay_skeleton("D1 Upper Push", stalled_session_db)
    spy = _CountingProposer(program_selections(sk))
    generate_session("D1 Upper Push", stalled_session_db, spy, wk)
    assert spy.calls == 1, "a present stall signal must call the LLM proposer exactly once"


# ---------------------------------------------------------------------------
# NAMED GATE b — analysis idempotency
# ---------------------------------------------------------------------------

def test_analysis_idempotency_no_duplicate_history(gen_db, logged_session_id):
    """NAMED GATE b: re-running run_analysis on a session_id does not append a
    duplicate E1rmHistory row. already_analyzed returns True after the first run.
    """
    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    run_analysis(logged_session_id, gen_db, wk)
    n1 = gen_db.exec(select(func.count()).select_from(E1rmHistory)).one()
    run_analysis(logged_session_id, gen_db, wk)           # re-log (must be no-op)
    n2 = gen_db.exec(select(func.count()).select_from(E1rmHistory)).one()
    assert n1 == n2, "re-analysis must be a no-op — idempotency guard failed"
    assert already_analyzed(logged_session_id, gen_db) is True


# ---------------------------------------------------------------------------
# NAMED GATE e — two-writer boundary
# ---------------------------------------------------------------------------

def test_two_writer_boundary(gen_db, logged_session_id):
    """NAMED GATE e: run_analysis must NOT write current_load on any MovementState.

    Before and after run_analysis, the current_load for every movement must be
    identical. current_load is owned exclusively by commit_session (Fork 7c).
    """
    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    before = {
        s.movement_id: s.current_load
        for s in gen_db.exec(select(MovementState)).all()
    }
    run_analysis(logged_session_id, gen_db, wk)
    after = {
        s.movement_id: s.current_load
        for s in gen_db.exec(select(MovementState)).all()
    }
    assert before == after, (
        "run_analysis wrote current_load — violates two-writer boundary (GATE e)"
    )
