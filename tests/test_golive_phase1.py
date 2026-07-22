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
# correct starting state, not a seed-completeness gap): Leg Curl [GHR]
# (2026-07-22 Nordic Curl -> Leg Curl swap, D2 d2_t2a) has no prior training
# history to seed a starting load from.
EXPECTED_NEEDS_CAL = {
    "D2 Lower A": {"Leg Curl [GHR]"},
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


def test_d6_dips_resolves_seeded_cable_load(gen_db):
    """C1 regression gate: docs/program/phase1-seed-source.yaml:72 specifies D6
    Dips (d6_g1b) as cable-loaded RPE-8 at 150 lb ("CORRECTED: cable-loaded,
    not BW rep-ladder"), and baseline_seed.BASELINES seeds
    d6_g1b = ("load", 150, None) into MovementState.current_load.

    Before the fix, `Dips [ANDREONI + FT]` was progression_mode=PROTOCOL, so
    load_field_for_mode(PROTOCOL) returns None and compute_load_trust/the
    resolver never reads current_load at all — the seeded 150 was silently
    ignored and generation shipped Dips with target_load=None. verify_all_days
    also treats PROTOCOL as legitimately loadless and skips it, so the old
    go-live report never flagged the gap.

    This test drives the REAL generate_session path (same one verify_all_days
    uses) and asserts the assembled D6 Dips slot actually carries
    target_load == 150 on every planned set — proof the seeded baseline
    resolves, not just that the movement is "structurally loaded" in the
    aggregate sense verify_all_days checks.
    """
    from sqlmodel import select

    from ironlog.api.app import _make_proposer, _week_keyer
    from ironlog.generation.baseline_seed import seed_movement_baselines
    from ironlog.generation.loop import generate_session
    from ironlog.generation.skeleton import lay_skeleton
    from ironlog.models.library import Movement

    seed_movement_baselines(gen_db)

    sk = lay_skeleton("D6 Weak Points", gen_db)
    proposer = _make_proposer(sk)
    outcome = generate_session("D6 Weak Points", gen_db, proposer, _week_keyer)
    assert outcome.assembled is not None, (
        f"D6 Weak Points: generation exhausted (rejections: {outcome.rejections})"
    )

    dips = gen_db.exec(select(Movement).where(Movement.name == "Dips [ANDREONI + FT]")).one()
    dips_exercises = [
        ex
        for g in outcome.assembled.session.groups
        for ex in g.exercises
        if ex.movement_id == dips.id
    ]
    assert dips_exercises, "Dips [ANDREONI + FT] did not appear in the generated D6 session"
    for ex in dips_exercises:
        assert ex.planned_sets, "Dips slot has no planned sets"
        for ps in ex.planned_sets:
            assert ps.target_load == 150, (
                f"Dips planned set target_load={ps.target_load!r}, expected 150 "
                "(seeded cable load from baseline_seed BASELINES['d6_g1b'])"
            )
