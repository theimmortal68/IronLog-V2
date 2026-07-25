"""test_ht_unification.py — Spec 50: D2/D5 unified Hip Thrust progression.
"""
from sqlmodel import select

from ironlog.generation.assembler import assemble
from ironlog.generation.context import resolve_context
from ironlog.generation.fallback import program_selections
from ironlog.generation.loop import commit_session
from ironlog.generation.skeleton import lay_skeleton
from ironlog.models.library import Movement, MovementState, HtProgressionState
from ironlog.models.program import ProgramDay, Tier, TierExercise

from tests.test_ht_composite_wiring import _first_ht_working_set, _stage_clean_ht_advance

def test_unified_ht_shared_read(gen_db_calibrated):
    gen_db = gen_db_calibrated
    wk = lambda d: (d.year, d.isocalendar()[1])

    # 1. Seed TierExercise.unified_ht_group="main" on D2's and D5's HT slots
    ht_mv = gen_db.exec(
        select(Movement).where(Movement.name == "Hip Thrust [HIP_THRUST]")
    ).one()

    d2_slot = gen_db.exec(
        select(TierExercise).join(Tier).join(ProgramDay)
        .where(ProgramDay.day_role == "D2 Lower A", TierExercise.movement_id == ht_mv.id)
    ).one()
    d5_slot = gen_db.exec(
        select(TierExercise).join(Tier).join(ProgramDay)
        .where(ProgramDay.day_role == "D5 Lower B", TierExercise.movement_id == ht_mv.id)
    ).one()

    d2_slot.unified_ht_group = "main"
    d5_slot.unified_ht_group = "main"
    gen_db.add(d2_slot)
    gen_db.add(d5_slot)

    # 2. Seed one HtProgressionState row
    ht_state = HtProgressionState(
        movement_id=ht_mv.id,
        unified_ht_group="main",
        ht_plates=180.0,
        ht_band_config=[1, 2],
    )
    gen_db.add(ht_state)
    gen_db.commit()

    # Generate D2
    sk2 = lay_skeleton("D2 Lower A", gen_db)
    ctx2 = resolve_context("D2 Lower A", sk2, gen_db, wk)
    assembled2 = assemble(program_selections(sk2), sk2, ctx2, gen_db)
    ht2 = _first_ht_working_set(assembled2)

    # Generate D5
    sk5 = lay_skeleton("D5 Lower B", gen_db)
    ctx5 = resolve_context("D5 Lower B", sk5, gen_db, wk)
    assembled5 = assemble(program_selections(sk5), sk5, ctx5, gen_db)
    ht5 = _first_ht_working_set(assembled5)

    # Assert both read from the shared row
    assert ht2.target_plates == 180.0
    assert ht2.band_config == [1, 2]
    assert ht5.target_plates == 180.0
    assert ht5.band_config == [1, 2]


def test_unified_ht_shared_advance(gen_db_calibrated):
    gen_db = gen_db_calibrated
    wk = lambda d: (d.year, d.isocalendar()[1])

    ht_mv = gen_db.exec(
        select(Movement).where(Movement.name == "Hip Thrust [HIP_THRUST]")
    ).one()

    d2_slot = gen_db.exec(
        select(TierExercise).join(Tier).join(ProgramDay)
        .where(ProgramDay.day_role == "D2 Lower A", TierExercise.movement_id == ht_mv.id)
    ).one()
    d5_slot = gen_db.exec(
        select(TierExercise).join(Tier).join(ProgramDay)
        .where(ProgramDay.day_role == "D5 Lower B", TierExercise.movement_id == ht_mv.id)
    ).one()
    d2_slot.unified_ht_group = "main"
    d5_slot.unified_ht_group = "main"
    gen_db.add(d2_slot)
    gen_db.add(d5_slot)

    ht_state = HtProgressionState(
        movement_id=ht_mv.id,
        unified_ht_group="main",
        ht_plates=180.0,
        ht_band_config=[1, 2],
    )
    gen_db.add(ht_state)
    gen_db.commit()

    # Log a clean 3x8 on D2
    _stage_clean_ht_advance(gen_db, ht_mv.id, "D2 Lower A", 180.0, [1, 2], wk)

    # run_analysis should have staged pending on HtProgressionState
    gen_db.refresh(ht_state)
    assert ht_state.pending_ht_plates is not None
    assert ht_state.pending_ht_band_config is not None

    # Generate + commit D2
    sk2 = lay_skeleton("D2 Lower A", gen_db)
    ctx2 = resolve_context("D2 Lower A", sk2, gen_db, wk)
    assembled2 = assemble(program_selections(sk2), sk2, ctx2, gen_db)
    commit_session(
        assembled2, gen_db,
        approval_mode="auto", prompt={}, selections_dict={}, clamps=[], repairs=[], fallback_used=False
    )

    gen_db.refresh(ht_state)
    advanced_plates = ht_state.ht_plates
    advanced_config = ht_state.ht_band_config
    assert advanced_plates != 180.0 or advanced_config != [1, 2] # it advanced
    assert ht_state.pending_ht_plates is None

    # Generate D5, verify it prescribes the advanced value
    sk5 = lay_skeleton("D5 Lower B", gen_db)
    ctx5 = resolve_context("D5 Lower B", sk5, gen_db, wk)
    assembled5 = assemble(program_selections(sk5), sk5, ctx5, gen_db)
    ht5 = _first_ht_working_set(assembled5)
    
    assert ht5.target_plates == advanced_plates
    assert ht5.band_config == advanced_config


def test_d6_ht_is_not_unified(gen_db_calibrated):
    gen_db = gen_db_calibrated
    wk = lambda d: (d.year, d.isocalendar()[1])

    ht_mv = gen_db.exec(
        select(Movement).where(Movement.name == "Hip Thrust [HIP_THRUST]")
    ).one()

    # D6 should NOT be unified
    d6_slot = gen_db.exec(
        select(TierExercise).join(Tier).join(ProgramDay)
        .where(ProgramDay.day_role == "D6 Weak Points", TierExercise.movement_id == ht_mv.id)
    ).one()
    assert d6_slot.unified_ht_group is None

    # Ensure day-scoped MovementState exists
    ms_d6 = gen_db.exec(
        select(MovementState)
        .where(MovementState.movement_id == ht_mv.id, MovementState.day_id == "D6 Weak Points")
    ).first()
    if ms_d6:
        ms_d6.ht_plates = 135.0
        ms_d6.ht_band_config = [1]
        gen_db.add(ms_d6)
        gen_db.commit()
    else:
        # Fallback if gen_db doesn't seed D6 day-scoped row yet
        st = MovementState(movement_id=ht_mv.id, day_id="D6 Weak Points", current_load=135.0, ht_plates=135.0, ht_band_config=[1])
        gen_db.add(st)
        gen_db.commit()

    # Generate D6
    sk6 = lay_skeleton("D6 Weak Points", gen_db)
    ctx6 = resolve_context("D6 Weak Points", sk6, gen_db, wk)
    assembled6 = assemble(program_selections(sk6), sk6, ctx6, gen_db)
    
    # Assert uses own day-scoped row
    ht6 = _first_ht_working_set(assembled6)
    assert ht6.target_plates == 135.0
    assert ht6.band_config == [1]

    # Advance D6
    _stage_clean_ht_advance(gen_db, ht_mv.id, "D6 Weak Points", 135.0, [1], wk)

    # run_analysis staged pending on the MovementState row
    ms_d6 = gen_db.exec(
        select(MovementState)
        .where(MovementState.movement_id == ht_mv.id, MovementState.day_id == "D6 Weak Points")
    ).one()
    assert ms_d6.pending_ht_plates is not None

    sk6_commit = lay_skeleton("D6 Weak Points", gen_db)
    ctx6_commit = resolve_context("D6 Weak Points", sk6_commit, gen_db, wk)
    assembled6_commit = assemble(program_selections(sk6_commit), sk6_commit, ctx6_commit, gen_db)
    commit_session(
        assembled6_commit, gen_db,
        approval_mode="auto", prompt={}, selections_dict={}, clamps=[], repairs=[], fallback_used=False
    )
    
    gen_db.refresh(ms_d6)
    assert ms_d6.ht_plates != 135.0 or ms_d6.ht_band_config != [1]
    assert ms_d6.pending_ht_plates is None
