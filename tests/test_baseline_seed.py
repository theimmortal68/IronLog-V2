"""
test_baseline_seed.py — day-scoped MovementState baseline seeding gate.

Verifies seed_movement_baselines() upserts calibrated baselines keyed on
(movement_id, day_id=day_role), so movements shared across days (e.g. the
Hip Thrust composite on D2/D5/D6) keep independent tracks rather than
collapsing onto a single MovementState row.

NO from __future__ import annotations (project-wide constraint).
"""
from sqlmodel import select


def test_baselines_seeded_day_scoped(gen_db):
    from ironlog.generation.baseline_seed import seed_movement_baselines
    from ironlog.models.library import MovementState, Movement, BandPair
    from ironlog.models.program import TierExercise
    seed_movement_baselines(gen_db)
    states = gen_db.exec(select(MovementState)).all()
    by_key = {(s.movement_id, s.day_id): s for s in states}
    te = {t.slot_id: t for t in gen_db.exec(select(TierExercise)).all()}
    # scalar load lands on the right (movement, day)
    d1t1 = te["d1_t1"]
    assert by_key[(d1t1.movement_id, "D1 Upper Push")].current_load == 155
    # HT gets plates + band config = [orange id]
    orange = gen_db.exec(select(BandPair).where(BandPair.label == "#0 Orange")).one()
    d6ht = te["d6_g1c"]
    st = by_key[(d6ht.movement_id, "D6 Weak Points")]
    assert st.ht_plates == 155 and st.ht_band_config == [orange.id]
    # three independent HT tracks exist (D2/D5/D6), NOT one collapsed row.
    # D2 and D5 now share the same baseline value (205), so the guarantee that
    # matters is 3 SEPARATE rows keyed by distinct day_id (not that all three
    # values are distinct) — assert row count/day-keys explicitly, then values.
    ht_rows = [s for s in states if s.ht_plates is not None]
    assert len(ht_rows) == 3
    assert len({s.day_id for s in ht_rows}) == 3
    assert sorted(s.ht_plates for s in ht_rows) == [155, 205, 205]


def test_reset_clears_transactional_keeps_baselines(gen_db, logged_session_id):
    from ironlog.generation.baseline_seed import (
        reset_transactional_and_state, seed_movement_baselines,
    )
    from ironlog.models.library import MovementState
    from ironlog.models.program import TierExercise
    from ironlog.models.session import (
        ExerciseGroup, PlannedExercise, PlannedSet, Session as WorkoutSession,
        SetLog,
    )

    seed_movement_baselines(gen_db)
    # simulate logged state on a seeded baseline row (d1_t1), keyed explicitly
    # so this assertion doesn't depend on MovementState row insertion order
    te = {t.slot_id: t for t in gen_db.exec(select(TierExercise)).all()}
    d1t1 = te["d1_t1"]
    st = gen_db.exec(
        select(MovementState).where(
            MovementState.movement_id == d1t1.movement_id,
            MovementState.day_id == "D1 Upper Push",
        )
    ).one()
    st.e1rm = 300.0
    st.consecutive_advance_count = 4
    st.unassisted_max_rolling = 9
    gen_db.commit()

    # F3 regression guard: the logged_session_id fixture already planted a real
    # generated+committed session with ExerciseGroup/PlannedExercise/PlannedSet
    # scaffolding (+ a SetLog) chained off it. Confirm it's present before reset
    # so the "gone after reset" check below is actually meaningful.
    assert gen_db.exec(select(WorkoutSession)).all() != []
    assert gen_db.exec(select(ExerciseGroup)).all() != []
    assert gen_db.exec(select(PlannedExercise)).all() != []
    assert gen_db.exec(select(PlannedSet)).all() != []

    reset_transactional_and_state(gen_db)

    assert gen_db.exec(select(SetLog)).all() == []
    assert gen_db.exec(select(WorkoutSession)).all() == []
    assert gen_db.exec(select(ExerciseGroup)).all() == []
    assert gen_db.exec(select(PlannedExercise)).all() == []
    assert gen_db.exec(select(PlannedSet)).all() == []
    st2 = gen_db.exec(select(MovementState).where(MovementState.id == st.id)).one()
    assert st2.e1rm is None and st2.consecutive_advance_count == 0 and st2.unassisted_max_rolling is None
    assert st2.current_load is not None or st2.assist_level is not None or st2.ht_plates is not None
