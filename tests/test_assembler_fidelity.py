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
    """RevHyper-Recovery (TierExercise.rpe_cap=6.0) -> every WORKING set's
    target_rpe == 6.0, not the phase-policy band default.

    2026-08-12 (STAB maintenance-block redesign, Task 5): D6's real d6_g2a
    slot (Reverse Hyper Recovery) drops out of D6's wiring entirely -- GS2
    fully turned over to Better Fly Cable Bicep Curl / Stryker Pad CSR
    Cables / Better Fly Rear Delt Extension, none of which carry a non-
    default rpe_cap. Reverse Hyper Recovery [REV_HYPER] was the ONLY
    rpe_cap != 8.0 example anywhere in the real program, so this test now
    attaches a synthetic GIANT_SET TierExercise (own Tier, tier_order=99,
    matching this file's/test_ht_*.py's established synthetic-slot
    pattern) onto D6's real ProgramDay, reusing the same still-ACTIVE-but-
    unwired Movement and its original rpe_cap=6.0/rest=90 shape, to keep
    testing that the assembler actually reads a non-default rpe_cap rather
    than falling back to the phase-policy band default."""
    from ironlog.models.program import ProgramDay, Tier, TierExercise, TierKind

    rhr_id = _movement_id(gen_db, "Reverse Hyper Recovery [REV_HYPER]")
    pd = gen_db.exec(select(ProgramDay).where(ProgramDay.day_role == "D6 Weak Points")).one()
    tier = Tier(program_day_id=pd.id, tier_label="TEST-RPE", tier_order=99,
                tier_kind=TierKind.GIANT_SET, rounds=3, rest_seconds=90, shoe="Metcon 9")
    gen_db.add(tier)
    gen_db.flush()
    gen_db.add(TierExercise(
        tier_id=tier.id, slot_id="test_rpe_cap_d6_rhr", movement_id=rhr_id,
        exercise_order=1, tier_role="free", pattern="reverse_hyper",
        rep_low=15, rep_high=20, scheme="FIXED", rpe_cap=6.0,
    ))
    gen_db.commit()

    out = generate_session("D6 Weak Points", gen_db, _proposer_for("D6 Weak Points", gen_db), _week_keyer)
    assert out.exhausted is False
    sess = out.assembled.session

    ex, group = _exercise_and_group(sess, rhr_id)

    assert ex.planned_sets, "Reverse Hyper Recovery must assemble sets"
    for ps in ex.planned_sets:
        assert ps.target_rpe == 6.0
    assert group.group_type == GroupType.GIANT_SET
    assert group.rest_seconds == 90   # Reverse Hyper Recovery moved GS3→GS2 (rest 90)


def test_unilateral_surfaces_in_session_detail(gen_db):
    """Movement.unilateral surfaces per-exercise in the /sessions/{id} serialization.
    Matrix Machine Bulgarian Split Squat (D5 T2 GS, seeded unilateral=True) must read
    unilateral True in the serialized ExerciseOut; Reverse Nordic Curl [GHR]
    (D5 T3 GS, unilateral=False) must read False.

    2026-08-12 (STAB maintenance-block redesign, Task 4): repointed from
    Bulgarian Split Squat [DB] (dropped from D5's wiring entirely) to its
    replacement, Nordic Max Bulgarian Split Squat. RDL [PB] (the old
    unilateral=False comparison) is also fully unwired program-wide now
    (D5's T1 anchor is Kickstand RDL [DB], itself unilateral=True) --
    repointed to Reverse Nordic Curl [GHR] (D5 T3 GS, unchanged, still
    unilateral=False by default), keeping both examples within the same
    generated D5 session.

    2026-08-14: repointed again to Matrix Machine Bulgarian Split Squat --
    Nordic Max Bulgarian Split Squat dropped from D5's wiring (Nordic Max
    rig conflict with Nordic Curl Max in the same giant set), replacement
    is also seeded unilateral=True, same test intent holds.
    """
    out = generate_session("D5 Lower B", gen_db, _proposer_for("D5 Lower B", gen_db), _week_keyer)
    assert out.exhausted is False
    sess = out.assembled.session
    gen_db.add(sess)
    gen_db.commit()
    gen_db.refresh(sess)

    detail = _serialize_session(sess, gen_db)
    by_mid = {ex.movement_id for g in detail.groups for ex in g.exercises}
    bss_id = _movement_id(gen_db, "Matrix Machine Bulgarian Split Squat")
    assert bss_id in by_mid

    bss_ex = next(ex for g in detail.groups for ex in g.exercises if ex.movement_id == bss_id)
    assert bss_ex.unilateral is True

    rdl_id = _movement_id(gen_db, "Reverse Nordic Curl [GHR]")
    rdl_ex = next(ex for g in detail.groups for ex in g.exercises if ex.movement_id == rdl_id)
    assert rdl_ex.unilateral is False
