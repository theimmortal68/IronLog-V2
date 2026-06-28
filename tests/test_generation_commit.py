"""test_generation_commit.py — Task 9: approval gate + commit-at-approve (gate d).

Named tests:
  1. test_commit_writes_current_load_assemble_does_not  (NAMED GATE d)
  2. test_is_clean_zero_repairs_zero_clamps
  3. test_is_clean_false_when_repairs_applied
  4. test_is_clean_false_when_clamps_applied
  5. test_is_clean_false_when_exhausted
  6. test_is_clean_false_when_assembled_none
  7. test_generation_log_row_written

Reconciliations vs brief:
- cold_start_selections → program_selections (real function name in fallback.py)
- No `from tests._gen_fixtures import gen_db` import: fixture auto-discovered from conftest.py
- .one() before assemble replaced with .first() + None-guard: fresh gen_db has no
  MovementState rows for D1 Upper Push movements (only Back Squat [PB] is seeded).
  commit_session uses get-or-create so .one() is valid AFTER commit.

NO from __future__ import annotations (project-wide constraint).
gen_db fixture auto-discovered from conftest.py.
"""
from ironlog.generation.assembler import assemble
from ironlog.generation.context import resolve_context
from ironlog.generation.fallback import program_selections
from ironlog.generation.loop import commit_session, is_clean
from ironlog.generation.repair import RepairOutcome
from ironlog.generation.skeleton import lay_skeleton
from ironlog.models.library import GenerationLog, MovementState
from sqlmodel import select


# ---------------------------------------------------------------------------
# NAMED GATE d
# ---------------------------------------------------------------------------

def test_commit_writes_current_load_assemble_does_not(gen_db):
    """NAMED GATE d: assemble computes-but-doesn't-write; commit writes."""
    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    sk = lay_skeleton("D1 Upper Push", gen_db)
    ctx = resolve_context("D1 Upper Push", sk, gen_db, wk)
    sel = program_selections(sk)  # brief used cold_start_selections; real name
    assembled = assemble(sel, sk, ctx, gen_db)
    target_mid = next(iter(assembled.prospective_current_loads))

    # Before commit: state may not exist in a fresh gen_db for D1 Upper Push
    # movements (only Back Squat [PB] is seeded with a MovementState).
    before_st = gen_db.exec(
        select(MovementState).where(MovementState.movement_id == target_mid)
    ).first()
    before = before_st.current_load if before_st is not None else None

    # assemble must NOT have written current_load
    assert before is None or before != assembled.prospective_current_loads[target_mid]

    saved = commit_session(
        assembled, gen_db,
        approval_mode="auto", prompt={},
        selections_dict={}, clamps=[], repairs=[], fallback_used=False,
    )
    # commit_session is the SOLE writer: row must now exist and carry the load
    after = gen_db.exec(
        select(MovementState).where(MovementState.movement_id == target_mid)
    ).one().current_load
    assert after == assembled.prospective_current_loads[target_mid]
    assert saved.id is not None
    assert saved.approved_at is not None


# ---------------------------------------------------------------------------
# is_clean
# ---------------------------------------------------------------------------

def _outcome(assembled, attempts, clamps, exhausted=False):
    return RepairOutcome(
        assembled=assembled,
        attempts=attempts,
        clamps_applied=clamps,
        rejections=[],
        exhausted=exhausted,
    )


def test_is_clean_zero_repairs_zero_clamps():
    # any non-None assembled, one attempt, zero clamps → clean
    assert is_clean(_outcome("sentinel", 1, 0)) is True


def test_is_clean_false_when_repairs_applied():
    # attempts > 1 means at least one repair round
    assert is_clean(_outcome("sentinel", 2, 0)) is False


def test_is_clean_false_when_clamps_applied():
    assert is_clean(_outcome("sentinel", 1, 1)) is False


def test_is_clean_false_when_exhausted():
    assert is_clean(_outcome(None, 3, 0, exhausted=True)) is False


def test_is_clean_false_when_assembled_none():
    # fallback (no valid assembled) is never clean
    assert is_clean(_outcome(None, 1, 0)) is False


# ---------------------------------------------------------------------------
# provenance row
# ---------------------------------------------------------------------------

def test_generation_log_row_written(gen_db):
    """commit_session must write a GenerationLog provenance row (Fork 7d)."""
    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    sk = lay_skeleton("D1 Upper Push", gen_db)
    ctx = resolve_context("D1 Upper Push", sk, gen_db, wk)
    sel = program_selections(sk)
    assembled = assemble(sel, sk, ctx, gen_db)

    saved = commit_session(
        assembled, gen_db,
        approval_mode="auto",
        prompt={"k": "v"},
        selections_dict={"slots": []},
        clamps=[], repairs=[], fallback_used=False,
    )
    logs = gen_db.exec(
        select(GenerationLog).where(GenerationLog.session_id == saved.id)
    ).all()
    assert len(logs) == 1
    log = logs[0]
    assert log.approval_mode == "auto"
    assert log.fallback_used is False
    assert log.prompt_json == {"k": "v"}
