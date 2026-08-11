"""tests/test_assembler_fidelity.py — Task 3: assembler honors seeded TierExercise
rep targets / rpe_cap and propagates Tier.rest_seconds onto ExerciseGroup, instead
of hardcoding reps (old: WORKING 8-12, TOP 3-5, BACKOFF 5-8) and RPE from the
phase-policy band. Also confirms Movement.unilateral surfaces via /sessions/{id}
serialization.

NO from __future__ import annotations (project-wide constraint).
"""
from sqlmodel import select

from ironlog.api.app import _make_proposer, _serialize_session, _week_keyer
from ironlog.generation.loop import generate_session
from ironlog.generation.skeleton import lay_skeleton
from ironlog.models.enums import GroupType
from ironlog.models.library import Movement


def _movement_id(db, name):
    return db.exec(select(Movement).where(Movement.name == name)).one().id


def _exercise_and_group(sess, movement_id):
    for g in sess.groups:
        for ex in g.exercises:
            if ex.movement_id == movement_id:
                return ex, g
    raise AssertionError(f"movement_id {movement_id} not found in assembled session")


def _proposer_for(day_role, db):
    """Build a proposer for the quiet-week path. _make_proposer(sk) falls back to
    StubProposer(program_selections(sk)) in the absence of GEMINI_API_KEY, and
    program_selections(sk) requires a real Skeleton (sk.adaptive_slots) — passing
    None would crash at construction time. generate_session's quiet path never
    actually calls the proposer (should_invoke_llm gates it out), but the
    proposer object must not raise just from being built, so we pass a real
    skeleton here."""
    sk = lay_skeleton(day_role, db)
    return _make_proposer(sk)


def test_reps_and_rest_from_bench_anchor(gen_db):
    """Bench (d1_t1, T1 anchor, STRAIGHT scheme) -> 3 WORKING sets at 4/6
    (2026-08-10 STAB maintenance-block T1 rep-range drop, YAML reconciliation),
    group rest_seconds == 120 (from the Tier), not the old hardcoded 8-12."""
    out = generate_session("D1 Upper Push", gen_db, _proposer_for("D1 Upper Push", gen_db), _week_keyer)
    assert out.exhausted is False
    sess = out.assembled.session

    bench_id = _movement_id(gen_db, "Bench Press [PB]")
    ex, group = _exercise_and_group(sess, bench_id)

    assert len(ex.planned_sets) == 3
    for ps in ex.planned_sets:
        assert (ps.target_reps_low, ps.target_reps_high) == (4, 6)
    assert group.group_type == GroupType.STRAIGHT
    assert group.rest_seconds == 120


def test_rest_seconds_propagated_giant_set(gen_db):
    """D1's T2 GS tier (giant-set, 3 exercises) carries the Tier's rest_seconds
    (90) on the ExerciseGroup — previously always None."""
    out = generate_session("D1 Upper Push", gen_db, _proposer_for("D1 Upper Push", gen_db), _week_keyer)
    sess = out.assembled.session

    giant_groups = [g for g in sess.groups if g.group_type == GroupType.GIANT_SET]
    assert giant_groups, "D1 Upper Push must assemble at least one giant-set group"
    for g in giant_groups:
        assert g.rest_seconds is not None, "rest_seconds must no longer be None"
    # T2 GS specifically (first giant group in tier order) is 90.
    assert giant_groups[0].rest_seconds == 90


def test_rpe_from_reverse_hyper_recovery_cap(gen_db):
    """RevHyper-Recovery (d6_g2a, TierExercise.rpe_cap=6.0) -> every WORKING set's
    target_rpe == 6.0, not the phase-policy band default."""
    out = generate_session("D6 Weak Points", gen_db, _proposer_for("D6 Weak Points", gen_db), _week_keyer)
    assert out.exhausted is False
    sess = out.assembled.session

    rhr_id = _movement_id(gen_db, "Reverse Hyper Recovery [REV_HYPER]")
    ex, group = _exercise_and_group(sess, rhr_id)

    assert ex.planned_sets, "Reverse Hyper Recovery must assemble sets"
    for ps in ex.planned_sets:
        assert ps.target_rpe == 6.0
    assert group.group_type == GroupType.GIANT_SET
    assert group.rest_seconds == 90   # Reverse Hyper Recovery moved GS3→GS2 (rest 90)


def test_unilateral_surfaces_in_session_detail(gen_db):
    """Movement.unilateral surfaces per-exercise in the /sessions/{id} serialization.
    Bulgarian Split Squat (D5 T2 GS, seeded unilateral=True) must read unilateral
    True in the serialized ExerciseOut; RDL (unilateral=False) must read False."""
    out = generate_session("D5 Lower B", gen_db, _proposer_for("D5 Lower B", gen_db), _week_keyer)
    assert out.exhausted is False
    sess = out.assembled.session
    gen_db.add(sess)
    gen_db.commit()
    gen_db.refresh(sess)

    detail = _serialize_session(sess, gen_db)
    by_mid = {ex.movement_id for g in detail.groups for ex in g.exercises}
    bss_id = _movement_id(gen_db, "Bulgarian Split Squat [DB]")
    assert bss_id in by_mid

    bss_ex = next(ex for g in detail.groups for ex in g.exercises if ex.movement_id == bss_id)
    assert bss_ex.unilateral is True

    rdl_id = _movement_id(gen_db, "RDL [PB]")
    rdl_ex = next(ex for g in detail.groups for ex in g.exercises if ex.movement_id == rdl_id)
    assert rdl_ex.unilateral is False
