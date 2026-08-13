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
from datetime import date

from sqlmodel import func, select

from ironlog.generation.assembler import AssembledSession
from ironlog.generation.fallback import program_selections
from ironlog.generation.loop import generate_session, commit_session, is_clean
from ironlog.generation.proposer import StubProposer, Selections
from ironlog.generation.repair import RepairOutcome
from ironlog.generation.skeleton import lay_skeleton
from ironlog.models.enums import SessionStatus
from ironlog.models.library import E1rmHistory, MovementState
from ironlog.models.session import Session as IronSession
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

    stalled_session_db has consecutive_failed_progressions=2 on Better Fly
    Sagittal Lat Pulldown [FT] (d1_t3e, tier_role=free) → detect_stall fires →
    movement in weak_point_hints → slot_has_deviation_signal True →
    should_invoke_llm True → proposer called once. (2026-08-10: fixture moved
    off Seated Cable Row [FT]/d1_t4a -- STAB maintenance-block redesign
    removed D1's T4 GS tier entirely. 2026-08-13: moved off Lat Prayer
    [ANDREONI + FT]/d1_t3c -- replaced by Better Fly Sagittal Lat Pulldown at
    fresh slot d1_t3e, athlete directive.)
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


# ---------------------------------------------------------------------------
# is_clean — quiet-path and exhausted-path coverage
# ---------------------------------------------------------------------------

def _stub_assembled() -> AssembledSession:
    """Minimal AssembledSession for is_clean tests (no DB needed)."""
    sess = IronSession(date=date(2026, 1, 1), day_role="D1 Upper Push", phase="CUT",
                       status=SessionStatus.PLANNED)
    return AssembledSession(session=sess, prospective_current_loads={})


def test_is_clean_quiet_path_attempts_zero():
    """is_clean returns True for a quiet-week program emission (attempts=0, clamps=0).

    generate_session's quiet path (§3A no-LLM branch) returns
    RepairOutcome(attempts=0, clamps_applied=0, exhausted=False, assembled=<session>).
    The OLD check (attempts == 1) returns False, making a pristine deterministic
    emission appear unclean — it would be routed for human approval instead of
    auto-approved.  The fix (attempts <= 1) covers both the quiet path (0) and
    the first-try-clean LLM path (1).

    This test FAILS before the fix (is_clean returns False for attempts=0) and
    PASSES after (attempts <= 1 → True).
    """
    assembled = _stub_assembled()
    quiet_outcome = RepairOutcome(
        assembled=assembled, attempts=0, clamps_applied=0, exhausted=False,
    )
    assert is_clean(quiet_outcome) is True, (
        "quiet-week deterministic emission (attempts=0, clamps=0) must be clean. "
        "Fix: change attempts == 1 to attempts <= 1 in is_clean."
    )


def test_is_clean_first_try_clean_still_true():
    """is_clean still returns True for a first-try-clean LLM outcome (attempts=1)."""
    assembled = _stub_assembled()
    first_try = RepairOutcome(
        assembled=assembled, attempts=1, clamps_applied=0, exhausted=False,
    )
    assert is_clean(first_try) is True, "first-try-clean (attempts=1) must remain clean"


def test_is_clean_exhausted_or_fallback_is_never_clean():
    """is_clean returns False for exhausted outcomes and fallback (assembled=None)."""
    assembled = _stub_assembled()
    exhausted = RepairOutcome(
        assembled=None, attempts=3, clamps_applied=0, exhausted=True,
    )
    assert is_clean(exhausted) is False, "exhausted outcome must not be clean"

    fallback = RepairOutcome(
        assembled=None, attempts=0, clamps_applied=0, exhausted=False,
    )
    assert is_clean(fallback) is False, "assembled=None outcome must not be clean"

    with_clamps = RepairOutcome(
        assembled=assembled, attempts=1, clamps_applied=2, exhausted=False,
    )
    assert is_clean(with_clamps) is False, "outcome with clamps applied must not be clean"


# ---------------------------------------------------------------------------
# FIX 1 — provenance end-to-end: GenerationLog rows must be non-empty
# ---------------------------------------------------------------------------

def test_provenance_non_empty_after_generate_and_commit(gen_db):
    """FIX 1 (§10 replayability): generate_session threads real provenance so that
    a subsequent commit_session produces a GenerationLog row with non-empty
    prompt_json AND selections_json.

    The quiet-week path (meso-1, no signals) returns a RepairOutcome whose
    prompt and selections_dict fields are populated from build_context_payload
    and the serialised Selections.  commit_session must write those through to
    the DB row.
    """
    from ironlog.models.library import GenerationLog
    from sqlmodel import select

    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    sk = lay_skeleton("D1 Upper Push", gen_db)
    stub = StubProposer(program_selections(sk))
    outcome = generate_session("D1 Upper Push", gen_db, stub, wk)

    assert outcome.prompt is not None and outcome.prompt, (
        "outcome.prompt must be non-empty after generate_session"
    )
    assert outcome.selections_dict is not None and outcome.selections_dict, (
        "outcome.selections_dict must be non-empty after generate_session"
    )

    committed = commit_session(
        outcome.assembled,
        gen_db,
        approval_mode="human",
        prompt=outcome.prompt,
        selections_dict=outcome.selections_dict,
        clamps=outcome.clamps or [],
        repairs=outcome.rejections,
        fallback_used=outcome.exhausted,
    )

    logs = gen_db.exec(
        select(GenerationLog).where(GenerationLog.session_id == committed.id)
    ).all()
    assert len(logs) == 1, "exactly one GenerationLog row must be written per commit"
    log = logs[0]
    assert log.prompt_json, "prompt_json must be non-empty (§10 replayability)"
    assert log.selections_json, "selections_json must be non-empty (§10 replayability)"
    # The selections must reflect the actual program emission: has ordering + slots
    assert "ordering" in log.selections_json, "selections_json must have ordering key"
    assert "slots" in log.selections_json, "selections_json must have slots key"
    assert log.approval_mode == "human"
