from sqlmodel import select

from ironlog.generation.assembler import assemble
from ironlog.generation.baseline_seed import seed_movement_baselines
from ironlog.generation.context import resolve_context
from ironlog.generation.fallback import program_selections
from ironlog.generation.loop import commit_session
from ironlog.generation.skeleton import lay_skeleton
from ironlog.models.library import Movement, MovementState

WEEK_KEYER = lambda d: (d.isocalendar()[0], d.isocalendar()[1])  # noqa: E731
DAY_ROLE = "D6 Weak Points"


def _ht_movement(db):
    return db.exec(
        select(Movement).where(Movement.name == "Hip Thrust [HIP_THRUST]")
    ).one()


def _ht_state(db):
    return db.exec(
        select(MovementState).where(
            MovementState.movement_id == _ht_movement(db).id,
            MovementState.day_id == DAY_ROLE,
        )
    ).one()


def _assemble_day(db):
    skeleton = lay_skeleton(DAY_ROLE, db)
    ctx = resolve_context(DAY_ROLE, skeleton, db, WEEK_KEYER)
    return assemble(program_selections(skeleton), skeleton, ctx, db)


def _ht_sets(assembled, movement_id):
    return [
        ps
        for group in assembled.session.groups
        for ex in group.exercises
        if ex.movement_id == movement_id
        for ps in ex.planned_sets
    ]


def test_assemble_holds_ht_setup_when_no_pending_advance(gen_db):
    seed_movement_baselines(gen_db)
    movement = _ht_movement(gen_db)
    state = _ht_state(gen_db)
    assert state.ht_plates == 155.0
    assert state.pending_ht_plates is None

    assembled = _assemble_day(gen_db)

    assert assembled.prospective_ht_setups[movement.id] == (155.0, [1])
    ht_sets = _ht_sets(assembled, movement.id)
    assert ht_sets
    assert all(ps.target_plates == 155.0 for ps in ht_sets)
    assert all(ps.band_config == [1] for ps in ht_sets)

    commit_session(
        assembled,
        gen_db,
        approval_mode="auto",
        prompt={},
        selections_dict={},
        clamps=[],
        repairs=[],
        fallback_used=False,
    )

    after = _ht_state(gen_db)
    assert after.ht_plates == 155.0
    assert after.ht_band_config == [1]
    assert after.pending_ht_plates is None
    assert after.pending_ht_band_config is None


def test_commit_applies_pending_ht_setup_once_and_clears_it(gen_db):
    seed_movement_baselines(gen_db)
    movement = _ht_movement(gen_db)
    state = _ht_state(gen_db)
    state.pending_ht_plates = 160.0
    state.pending_ht_band_config = [1]
    gen_db.add(state)
    gen_db.commit()

    assembled = _assemble_day(gen_db)

    assert assembled.prospective_ht_setups[movement.id] == (160.0, [1])
    ht_sets = _ht_sets(assembled, movement.id)
    assert ht_sets
    assert all(ps.target_plates == 155.0 for ps in ht_sets)

    commit_session(
        assembled,
        gen_db,
        approval_mode="auto",
        prompt={},
        selections_dict={},
        clamps=[],
        repairs=[],
        fallback_used=False,
    )

    after = _ht_state(gen_db)
    assert after.ht_plates == 160.0
    assert after.ht_band_config == [1]
    assert after.pending_ht_plates is None
    assert after.pending_ht_band_config is None
