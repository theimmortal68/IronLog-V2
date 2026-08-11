"""tests/test_knee_raise_retype_migration.py — Fix C live-DB migration.

`ironlog.generation.knee_raise_retype.retype_knee_raise` is the one-shot fix
for an EXISTING (already-seeded, pre-fix) live DB: a from-scratch DB now seeds
`Face-Up Incline Knee Raise` correctly (ironlog/seed.py + baseline_seed.py),
but a live DB seeded before this fix still carries the old mis-typed rows
(progression_mode=LADDER, no assist_ladder, MovementState.current_load in lb).

These tests start from `gen_db` (which already seeds the CORRECT post-fix
state) and manually revert it back to the pre-fix "live bug" shape, to prove
the migration repairs exactly that state — and that it is idempotent and
scoped only to this one movement.

NO from __future__ import annotations (project-wide constraint).
"""
from sqlmodel import select

from ironlog.generation.knee_raise_retype import retype_knee_raise
from ironlog.models.enums import ProgressionMode
from ironlog.models.library import Movement, MovementState

KNEE_RAISE = "Face-Up Incline Knee Raise"


def _revert_to_pre_fix_bug_state(db):
    """Simulate the live (pre-fix) DB: LADDER movement, no assist_ladder,
    MovementState carrying current_load in lb instead of assist_level."""
    mv = db.exec(select(Movement).where(Movement.name == KNEE_RAISE)).one()
    mv.progression_mode = ProgressionMode.LADDER
    mv.assist_ladder = None
    db.add(mv)

    for st in db.exec(select(MovementState).where(MovementState.movement_id == mv.id)).all():
        st.current_load = st.assist_level
        st.assist_level = None
        db.add(st)
    db.commit()
    return mv.id


def test_migration_fixes_movement_and_states_from_bug_state(gen_db):
    """2026-08-10 (STAB maintenance-block redesign): Face-Up Incline Knee
    Raise dropped out of D1 entirely (T2 GS turnover), so only D4's seeded
    MovementState exists to migrate/assert against now."""
    from ironlog.generation.baseline_seed import seed_movement_baselines
    seed_movement_baselines(gen_db)

    mv_id = _revert_to_pre_fix_bug_state(gen_db)
    # Confirm we actually reproduced the bug before fixing it.
    bugged = gen_db.exec(select(Movement).where(Movement.id == mv_id)).one()
    assert bugged.progression_mode == ProgressionMode.LADDER
    assert bugged.assist_ladder is None
    bugged_states = gen_db.exec(
        select(MovementState).where(MovementState.movement_id == mv_id)
    ).all()
    assert bugged_states, "expected seeded D4 state to migrate"
    for st in bugged_states:
        assert st.current_load is not None
        assert st.assist_level is None

    counts = retype_knee_raise(gen_db)
    assert counts["movement_changed"] == 1
    assert counts["states_changed"] == len(bugged_states)

    fixed = gen_db.exec(select(Movement).where(Movement.id == mv_id)).one()
    assert fixed.progression_mode == ProgressionMode.ASSISTED
    assert fixed.assist_ladder == [25, 20, 15, 10, 5, 0]

    fixed_states = {
        st.day_id: st
        for st in gen_db.exec(select(MovementState).where(MovementState.movement_id == mv_id)).all()
    }
    assert fixed_states["D4 Upper Pull"].assist_level == 10.0
    assert fixed_states["D4 Upper Pull"].current_load is None


def test_migration_is_idempotent(gen_db):
    from ironlog.generation.baseline_seed import seed_movement_baselines
    seed_movement_baselines(gen_db)
    _revert_to_pre_fix_bug_state(gen_db)

    first = retype_knee_raise(gen_db)
    assert first["movement_changed"] == 1
    assert first["states_changed"] > 0

    second = retype_knee_raise(gen_db)
    assert second["movement_changed"] == 0, "re-running must not report a change"
    assert second["states_changed"] == 0, "already-migrated states must be left alone"

    # Values are stable across the second run. (2026-08-10: D1 no longer
    # carries this movement -- STAB maintenance-block T2 GS turnover -- so
    # only D4's state is checked here.)
    mv = gen_db.exec(select(Movement).where(Movement.name == KNEE_RAISE)).one()
    assert mv.progression_mode == ProgressionMode.ASSISTED
    assert mv.assist_ladder == [25, 20, 15, 10, 5, 0]
    d4 = gen_db.exec(select(MovementState).where(
        MovementState.movement_id == mv.id, MovementState.day_id == "D4 Upper Pull",
    )).one()
    assert d4.assist_level == 10.0
    assert d4.current_load is None


def test_migration_does_not_touch_nordic_or_reverse_nordic(gen_db):
    from ironlog.generation.baseline_seed import seed_movement_baselines
    seed_movement_baselines(gen_db)

    before = {
        name: (m.progression_mode, m.assist_ladder)
        for name, m in (
            (n, gen_db.exec(select(Movement).where(Movement.name == n)).one())
            for n in ("Nordic Curl [GHR]", "Reverse Nordic Curl [GHR]")
        )
    }
    before_states = {
        st.id: (st.assist_level, st.current_load)
        for m_name in before
        for st in gen_db.exec(
            select(MovementState).where(
                MovementState.movement_id == gen_db.exec(
                    select(Movement).where(Movement.name == m_name)
                ).one().id
            )
        ).all()
    }

    _revert_to_pre_fix_bug_state(gen_db)
    retype_knee_raise(gen_db)

    for name, (mode, ladder) in before.items():
        mv = gen_db.exec(select(Movement).where(Movement.name == name)).one()
        assert mv.progression_mode == mode
        assert mv.assist_ladder == ladder

    for m_name in before:
        mv = gen_db.exec(select(Movement).where(Movement.name == m_name)).one()
        for st in gen_db.exec(
            select(MovementState).where(MovementState.movement_id == mv.id)
        ).all():
            assert (st.assist_level, st.current_load) == before_states[st.id]


def test_migration_halts_if_movement_missing(gen_db):
    """Halt-and-flag: never silently no-op on an unexpected DB shape."""
    import pytest

    mv = gen_db.exec(select(Movement).where(Movement.name == KNEE_RAISE)).one()
    gen_db.delete(mv)
    gen_db.commit()

    with pytest.raises(ValueError, match="HALT-AND-FLAG"):
        retype_knee_raise(gen_db)
