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
# vacated GS1 slot) has zero prior training history. Wide-Grip Pull-up
# [TOWER] (2026-07-26, athlete directive, D4+D6's Pull-up slots switched
# grips from neutral to wide) is a brand new movement with zero prior
# training history on either day's MovementState. Lying Tricep Extension
# [SB] (2026-07-26, new D1 d1_t2d slot filling Pendlay Row Narrow's vacated
# T2 GS slot after its T1b promotion) has zero prior training history.
# PureTorque Pro Rotation (2026-07-26, new D4 d4_t3d slot replacing Dragon
# Flag -- athlete directive) is a load-bearing (LADDER/DOUBLE_PROGRESSION)
# movement newly wired into the program, with zero prior training history
# (it previously sat unused in the library, never programmed on any day).
# Pull-up - Neutral Grip (Paused) [TOWER] (2026-07-26, 3-way pull-up split --
# D1 stays assisted neutral-grip, D4 stays Wide-Grip, D6 gets this new
# variant) is a brand new movement with zero prior training history.
#
# 2026-08-10 (STAB maintenance-block redesign): D1's "Lying Tricep Extension
# [SB]" needs-cal entry is REMOVED -- that movement drops out of D1's wiring
# entirely (T2 GS turnover to Stryker Pad Seated OHP / Matrix Machine
# Preacher Curl / Better Fly Standing Lateral Raise), and every T1/T1b/T2/T3
# slot now seeds from a real Wk1 logged baseline. D1 DOES pick up one
# needs-cal entry though: "Wide-Grip Pull-up [TOWER]" (d1_t3a) is
# PULL_UP_ROLLING_MAX, which is tracked via unassisted_max_rolling, not a
# scalar current_load/assist_level -- it never gets a BASELINES entry
# anywhere in the program (matches D4's Wide-Grip Pull-up below), so it
# reads needs-cal until a real session logs an unassisted rep max, same as
# D4's.
#
# 2026-08-11 (STAB maintenance-block redesign, Task 2): D2's "Lying Leg Curl
# [GHR]" needs-cal entry is REMOVED (not merged) -- that movement drops out
# of D2's wiring entirely (T2 GS turned over to Matrix Machine Sissy Squat /
# Nordic Curl Max [Ares]) and isn't referenced anywhere else in the program,
# so it's no longer generated at all. D2 now picks up FOUR needs-cal
# entries instead -- all four new D2 movements (Matrix Machine Sissy Squat,
# Nordic Curl Max [Ares], Hybrid Board Calf Raise [D2], Ab Trainer Decline
# Sit-up), each brand new with zero prior training history, matching this
# session's established convention (no BASELINES entry for genuinely new
# movements).
#
# 2026-08-11 (STAB maintenance-block redesign, Task 3): D4's "Standing OHP
# [PB]" and "Wide-Grip Pull-up [TOWER]" needs-cal entries are REMOVED -- both
# movements drop out of D4's wiring entirely (T1 -> Seated BTN OHP [PB],
# T1b -> Better Fly Lat Pulldown [FT]; Standing OHP [PB] and Wide-Grip
# Pull-up [TOWER] stay ACTIVE in the library, unwired from D4 -- Wide-Grip
# Pull-up [TOWER] is STILL wired on D1 (d1_t3a), so it stays in D1's
# needs-cal set above, unaffected). D4 now picks up SIX needs-cal entries:
# the four genuinely new T1/T1b/T2 movements (Seated BTN OHP [PB], Better
# Fly Lat Pulldown [FT], Stryker Pad CSR Barbell [PB], Better Fly Cable
# Pullover [FT]) plus the pre-existing Ab Trainer Hanging Leg Raise (new to
# the library, T2) and "Lying Tricep Extension [SB]" (REUSED movement --
# unused since Task 1 dropped it from D1, now wired fresh on D4's T3 at a
# new slot, zero prior history there). "PureTorque Pro Rotation" is
# unchanged (already needs-cal, unaffected by this task).
EXPECTED_NEEDS_CAL = {
    "D1 Upper Push": {"Wide-Grip Pull-up [TOWER]"},
    "D2 Lower A": {
        "Matrix Machine Sissy Squat", "Nordic Curl Max [Ares]",
        "Hybrid Board Calf Raise [D2]", "Ab Trainer Decline Sit-up",
    },
    "D4 Upper Pull": {
        "Seated BTN OHP [PB]", "Better Fly Lat Pulldown [FT]",
        "Stryker Pad CSR Barbell [PB]", "Ab Trainer Hanging Leg Raise",
        "Better Fly Cable Pullover [FT]", "Lying Tricep Extension [SB]",
        "PureTorque Pro Rotation",
    },
    "D6 Weak Points": {"Cable Bicep Curl [FT]", "Pull-up - Neutral Grip (Paused) [TOWER]"},
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
