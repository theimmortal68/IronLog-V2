"""test_ht_d6_derived.py — Spec 52: D6 Hip Thrust derived from unified group.
"""
from datetime import date
from sqlmodel import select

from ironlog.engine.band_composite import Band, ht_next_setup, ht_scaled_setup
from ironlog.generation.assembler import assemble
from ironlog.generation.context import resolve_context
from ironlog.generation.fallback import program_selections
from ironlog.generation.loop import commit_session
from ironlog.generation.skeleton import lay_skeleton
from ironlog.models.enums import FeedbackTap, GroupType, Objective, ProgressionRule, Scheme, SessionStatus, SetRole
from ironlog.models.library import BandPair, Movement, MovementState, HtProgressionState
from ironlog.models.program import ProgramDay, Tier, TierExercise
from ironlog.models.session import ExerciseGroup, PlannedExercise, PlannedSet, Session as IronSession, SetLog
from ironlog.persistence.run_analysis import run_analysis

from tests.test_ht_composite_wiring import _stage_clean_ht_advance

wk = lambda d: (d.year, d.isocalendar()[1])

def _stage_dirty_ht_session(db, movement_id, day_role, plates, config, week_keyer):
    session = IronSession(
        date=date(2026, 7, 20),
        day_role=day_role,
        phase="CUT",
        status=SessionStatus.COMPLETED,
    )
    db.add(session)
    db.flush()

    group = ExerciseGroup(
        session_id=session.id,
        order_index=0,
        group_type=GroupType.STRAIGHT,
        label="T1",
    )
    db.add(group)
    db.flush()

    exercise = PlannedExercise(
        group_id=group.id,
        movement_id=movement_id,
        order_index=0,
        scheme=Scheme.STRAIGHT,
        objective=Objective.PROGRESS,
    )
    db.add(exercise)
    db.flush()

    for i in range(3):
        planned_set = PlannedSet(
            planned_exercise_id=exercise.id,
            set_index=i,
            set_role=SetRole.WORKING,
            target_reps_low=8,
            target_reps_high=8,
            target_rpe=8.0,
            target_plates=plates,
            band_config=list(config),
        )
        db.add(planned_set)
        db.flush()
        db.add(SetLog(
            planned_set_id=planned_set.id,
            session_id=session.id,
            movement_id=movement_id,
            set_index=i,
            actual_reps=5,  # Short reps = dirty/no-advance
            feedback_tap=FeedbackTap.ON_TARGET,
            actual_plates=plates,
            is_warmup=False,
        ))
    db.commit()
    run_analysis(session.id, db, week_keyer)


def test_d6_derived_from_unified_advancement(gen_db_calibrated):
    gen_db = gen_db_calibrated
    
    ht_mv = gen_db.exec(
        select(Movement).where(Movement.name == "Hip Thrust [HIP_THRUST]")
    ).one()

    # Seed D2 unified
    d2_slot = gen_db.exec(
        select(TierExercise).join(Tier).join(ProgramDay)
        .where(ProgramDay.day_role == "D2 Lower A", TierExercise.movement_id == ht_mv.id)
    ).one()
    d2_slot.unified_ht_group = "main"
    gen_db.add(d2_slot)

    # Seed D6 derived
    d6_slot = gen_db.exec(
        select(TierExercise).join(Tier).join(ProgramDay)
        .where(ProgramDay.day_role == "D6 Weak Points", TierExercise.movement_id == ht_mv.id)
    ).one()
    d6_slot.derived_from_unified_group = "main"
    d6_slot.derive_ratio = 0.8
    gen_db.add(d6_slot)

    # Seed unified state
    unified_state = HtProgressionState(
        movement_id=ht_mv.id,
        unified_ht_group="main",
        ht_plates=180.0,
        ht_band_config=[1, 2],
    )
    gen_db.add(unified_state)
    
    # Seed D6 state
    ms_d6 = MovementState(
        movement_id=ht_mv.id,
        day_id="D6 Weak Points",
        current_load=135.0,
        ht_plates=135.0,
        ht_band_config=[1]
    )
    gen_db.add(ms_d6)
    gen_db.commit()

    # Log clean D2 session
    _stage_clean_ht_advance(gen_db, ht_mv.id, "D2 Lower A", 180.0, [1, 2], wk)
    
    # Generate & commit D2
    sk2 = lay_skeleton("D2 Lower A", gen_db)
    ctx2 = resolve_context("D2 Lower A", sk2, gen_db, wk)
    assembled2 = assemble(program_selections(sk2), sk2, ctx2, gen_db)
    commit_session(
        assembled2, gen_db,
        approval_mode="auto", prompt={}, selections_dict={}, clamps=[], repairs=[], fallback_used=False
    )

    gen_db.refresh(unified_state)
    new_plates = unified_state.ht_plates
    new_config = unified_state.ht_band_config
    
    band_pairs = gen_db.exec(select(BandPair)).all()
    inventory = [Band(bp.id, bp.bottom_lb, bp.peak_lb, bp.usable) for bp in band_pairs]
    peak_by_id = {bp.id: bp.peak_lb for bp in band_pairs}
    new_unified_peak = new_plates + sum(peak_by_id[bid] for bid in new_config)
    expected_d6_plates, expected_d6_config = ht_scaled_setup(new_unified_peak * 0.8, inventory)

    gen_db.refresh(ms_d6)
    assert ms_d6.ht_plates == expected_d6_plates
    assert ms_d6.ht_band_config == expected_d6_config


def test_d6_suppresses_independent_advance(gen_db_calibrated):
    gen_db = gen_db_calibrated
    
    ht_mv = gen_db.exec(
        select(Movement).where(Movement.name == "Hip Thrust [HIP_THRUST]")
    ).one()

    d6_slot = gen_db.exec(
        select(TierExercise).join(Tier).join(ProgramDay)
        .where(ProgramDay.day_role == "D6 Weak Points", TierExercise.movement_id == ht_mv.id)
    ).one()
    d6_slot.derived_from_unified_group = "main"
    d6_slot.derive_ratio = 0.8
    gen_db.add(d6_slot)

    ms_d6 = MovementState(
        movement_id=ht_mv.id,
        day_id="D6 Weak Points",
        current_load=135.0,
        ht_plates=135.0,
        ht_band_config=[1]
    )
    gen_db.add(ms_d6)
    gen_db.commit()

    # Log clean D6 session
    _stage_clean_ht_advance(gen_db, ht_mv.id, "D6 Weak Points", 135.0, [1], wk)

    gen_db.refresh(ms_d6)
    assert ms_d6.pending_ht_plates is None
    assert ms_d6.active_rule == ProgressionRule.RULE_DRIVEN.value


def test_d6_no_touch_on_dirty_unified_session(gen_db_calibrated):
    gen_db = gen_db_calibrated
    
    ht_mv = gen_db.exec(
        select(Movement).where(Movement.name == "Hip Thrust [HIP_THRUST]")
    ).one()

    d2_slot = gen_db.exec(
        select(TierExercise).join(Tier).join(ProgramDay)
        .where(ProgramDay.day_role == "D2 Lower A", TierExercise.movement_id == ht_mv.id)
    ).one()
    d2_slot.unified_ht_group = "main"
    gen_db.add(d2_slot)

    d6_slot = gen_db.exec(
        select(TierExercise).join(Tier).join(ProgramDay)
        .where(ProgramDay.day_role == "D6 Weak Points", TierExercise.movement_id == ht_mv.id)
    ).one()
    d6_slot.derived_from_unified_group = "main"
    d6_slot.derive_ratio = 0.8
    gen_db.add(d6_slot)

    unified_state = HtProgressionState(
        movement_id=ht_mv.id,
        unified_ht_group="main",
        ht_plates=180.0,
        ht_band_config=[1, 2],
    )
    gen_db.add(unified_state)
    
    ms_d6 = MovementState(
        movement_id=ht_mv.id,
        day_id="D6 Weak Points",
        current_load=135.0,
        ht_plates=135.0,
        ht_band_config=[1]
    )
    gen_db.add(ms_d6)
    gen_db.commit()

    # Log dirty D2 session
    _stage_dirty_ht_session(gen_db, ht_mv.id, "D2 Lower A", 180.0, [1, 2], wk)
    
    sk2 = lay_skeleton("D2 Lower A", gen_db)
    ctx2 = resolve_context("D2 Lower A", sk2, gen_db, wk)
    assembled2 = assemble(program_selections(sk2), sk2, ctx2, gen_db)
    commit_session(
        assembled2, gen_db,
        approval_mode="auto", prompt={}, selections_dict={}, clamps=[], repairs=[], fallback_used=False
    )

    gen_db.refresh(ms_d6)
    assert ms_d6.ht_plates == 135.0
    assert ms_d6.ht_band_config == [1]
