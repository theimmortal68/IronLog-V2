"""tests/test_golive_phase1.py — Task 7: go-live orchestration script + verify.

End-to-end gate: seed the Phase-1 calibrated baselines onto the standard gen_db
fixture (103-movement library + Phase-1 program, no reset needed — gen_db is
already fresh), then run verify_all_days() through the REAL generate_session
path for every training day and assert each comes up structurally clean
(loaded_slots > 0) with zero needs-calibration movements.

Uses the real `gen_db` fixture from tests/conftest.py (in-memory DB seeded via
seed.seed() + seed_phase1_program()). NO from __future__ import annotations
(project-wide constraint).
"""


# Movements intentionally shipped with no baseline (needs-calibration is the
# correct starting state, not a seed-completeness gap): Lying Leg Curl
# [GHR] (2026-07-22 Nordic Curl -> Leg Curl swap, D2 d2_t2a; renamed
# 2026-07-26, same Hyper Pro rack equipment as Belt Squat) has no prior
# training history to seed a starting load from. Standing OHP [PB]
# (2026-07-23, new D4 d4_t1_ohp slot closing the program's overhead-press
# gap) is a brand new program movement with zero prior training history
# either. Cable Bicep Curl [FT] (2026-07-26, new D6 d6_g1d slot filling Dips'
# vacated GS1 slot) has zero prior training history.
EXPECTED_NEEDS_CAL = {
    "D2 Lower A": {"Lying Leg Curl [GHR]"},
    "D4 Upper Pull": {"Standing OHP [PB]"},
    "D6 Weak Points": {"Cable Bicep Curl [FT]"},
}


def test_golive_all_days_generate_clean(gen_db):
    from scripts.golive_phase1 import verify_all_days
    from ironlog.generation.baseline_seed import seed_movement_baselines
    seed_movement_baselines(gen_db)
    report = verify_all_days(gen_db)   # returns {day_role: {"loaded_slots": int, "needs_cal": [..]}}
    for role in ("D1 Upper Push", "D2 Lower A", "D4 Upper Pull", "D5 Lower B", "D6 Weak Points"):
        expected = EXPECTED_NEEDS_CAL.get(role, set())
        unexpected = set(report[role]["needs_cal"]) - expected
        assert not unexpected, f"{role} has unexpected needs-calibration slots: {unexpected}"
        assert report[role]["loaded_slots"] > 0


def test_d6_dips_resolves_seeded_assist_level(gen_db):
    """D6 Dips now starts as bodyweight + band assist in its own T1 slot.

    docs/program/phase1-seed-source.yaml gives d6_t1 assist_level=40. The
    go-live baseline must seed that into MovementState.assist_level, not the
    obsolete d6_g1b cable current_load, and generation must prescribe 40 on
    the assisted scalar path.
    """
    from sqlmodel import select

    from ironlog.api.app import _make_proposer, _week_keyer
    from ironlog.generation.baseline_seed import seed_movement_baselines
    from ironlog.generation.loop import generate_session
    from ironlog.generation.skeleton import lay_skeleton
    from ironlog.models.library import Movement, MovementState

    seed_movement_baselines(gen_db)

    sk = lay_skeleton("D6 Weak Points", gen_db)
    proposer = _make_proposer(sk)
    outcome = generate_session("D6 Weak Points", gen_db, proposer, _week_keyer)
    assert outcome.assembled is not None, (
        f"D6 Weak Points: generation exhausted (rejections: {outcome.rejections})"
    )

    dips = gen_db.exec(select(Movement).where(Movement.name == "Dips [TOWER + TUBES]")).one()
    state = gen_db.exec(
        select(MovementState).where(
            MovementState.movement_id == dips.id,
            MovementState.day_id == "D6 Weak Points",
        )
    ).one()
    assert state.assist_level == 40
    assert state.current_load is None

    dips_exercises = [
        ex
        for g in outcome.assembled.session.groups
        for ex in g.exercises
        if ex.movement_id == dips.id
    ]
    assert dips_exercises, "Dips [TOWER + TUBES] did not appear in the generated D6 session"
    for ex in dips_exercises:
        assert ex.planned_sets, "Dips slot has no planned sets"
        for ps in ex.planned_sets:
            assert ps.target_load == 40, (
                f"Dips planned set target_load={ps.target_load!r}, expected 40 "
                "(seeded assist_level from baseline_seed BASELINES['d6_t1'])"
            )
