"""tests/test_note_resolver.py — note resolver proposal and safety coverage.

NO from __future__ import annotations (project-wide constraint).
"""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlmodel import SQLModel, Session as DBSession, create_engine

import ironlog.models  # register tables
from ironlog.generation.skeleton import _effective_exercise_order
from ironlog.models.enums import LiftCategory, NoteClass, ProgressionMode
from ironlog.models.library import BandPair, Movement, MovementState
from ironlog.models.program import Program, ProgramDay, Tier, TierExercise, TierKind
from ironlog.models.session import Note, Session as WorkoutSession
from ironlog.notes.resolver import resolve_note


def _engine():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return engine


def _persist(db, *objs):
    for obj in objs:
        db.add(obj)
    db.commit()
    for obj in objs:
        db.refresh(obj)


def _note(action_type, proposed_change, *, session_id=None):
    return Note(
        session_id=session_id,
        text="resolver test note",
        classification=NoteClass.CONFIG_CHANGE,
        classification_meta={
            "action_type": action_type,
            "proposed_change": proposed_change,
            "confidence": 0.9,
            "rationale": "test",
        },
    )


def _seed_resolver_program(db):
    program = Program(name="Resolver Phase", phase="P1", duration_weeks=4)
    _persist(db, program)

    d1 = ProgramDay(program_id=program.id, day_index=1, day_role="D1 Upper Push")
    d2 = ProgramDay(program_id=program.id, day_index=2, day_role="D2 Lower A")
    _persist(db, d1, d2)

    d1_t1 = Tier(program_day_id=d1.id, tier_label="T1", tier_order=1, tier_kind=TierKind.T1_STRAIGHT)
    d1_gs = Tier(program_day_id=d1.id, tier_label="GS1", tier_order=2, tier_kind=TierKind.GIANT_SET)
    d1_t3 = Tier(program_day_id=d1.id, tier_label="T3", tier_order=3, tier_kind=TierKind.ACCESSORY)
    d2_t1 = Tier(program_day_id=d2.id, tier_label="T1", tier_order=1, tier_kind=TierKind.T1_STRAIGHT)
    d2_t2 = Tier(program_day_id=d2.id, tier_label="T2", tier_order=2, tier_kind=TierKind.ACCESSORY)
    _persist(db, d1_t1, d1_gs, d1_t3, d2_t1, d2_t2)

    movements = {
        "bench": Movement(
            name="Bench Press [PB]",
            base_name="Bench Press",
            progression_mode=ProgressionMode.LADDER,
            load_floor=45.0,
        ),
        "incline": Movement(name="Incline Bench [PB]", base_name="Incline Bench"),
        "hip_thrust": Movement(
            name="Hip Thrust [HIP_THRUST]",
            base_name="Hip Thrust",
            lift_category=LiftCategory.HIP_THRUST,
            progression_mode=ProgressionMode.COMPOSITE,
        ),
        "single_arm_row": Movement(name="Single Arm Row [DB]", base_name="Single Arm Row"),
        "knee_raise": Movement(
            name="Knee Raises [CAPTAIN]",
            base_name="Knee Raises",
            progression_mode=ProgressionMode.PROTOCOL,
        ),
        "meadows_row": Movement(name="Meadows Row [DB]", base_name="Meadows Row"),
        "shared_row": Movement(
            name="Shared Cable Row [FT]",
            base_name="Shared Cable Row",
            progression_mode=ProgressionMode.LADDER,
        ),
    }
    _persist(db, *movements.values())

    tes = {
        "bench": TierExercise(
            tier_id=d1_t1.id,
            slot_id="d1_t1",
            movement_id=movements["bench"].id,
            exercise_order=1,
            tier_role="anchor",
        ),
        "single_arm_row": TierExercise(
            tier_id=d1_gs.id,
            slot_id="d1_gs1a",
            movement_id=movements["single_arm_row"].id,
            exercise_order=1,
            tier_role="free",
        ),
        "knee_raise": TierExercise(
            tier_id=d1_gs.id,
            slot_id="d1_gs1b",
            movement_id=movements["knee_raise"].id,
            exercise_order=2,
            tier_role="free",
        ),
        "meadows_row": TierExercise(
            tier_id=d1_gs.id,
            slot_id="d1_gs1c",
            movement_id=movements["meadows_row"].id,
            exercise_order=3,
            tier_role="free",
        ),
        "shared_d1": TierExercise(
            tier_id=d1_t3.id,
            slot_id="d1_t3a",
            movement_id=movements["shared_row"].id,
            exercise_order=1,
            tier_role="semi",
        ),
        "hip_thrust": TierExercise(
            tier_id=d2_t1.id,
            slot_id="d2_t1",
            movement_id=movements["hip_thrust"].id,
            exercise_order=1,
            tier_role="anchor",
        ),
        "shared_d2": TierExercise(
            tier_id=d2_t2.id,
            slot_id="d2_t2a",
            movement_id=movements["shared_row"].id,
            exercise_order=1,
            tier_role="semi",
        ),
    }
    _persist(db, *tes.values())

    band = BandPair(label="#0 Orange", bottom_lb=18.0, peak_lb=45.0, usable=True)
    _persist(db, band)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    states = {
        "bench": MovementState(
            movement_id=movements["bench"].id,
            day_id="D1 Upper Push",
            current_load=185.0,
            confirmed_at=now,
        ),
        "hip_thrust": MovementState(
            movement_id=movements["hip_thrust"].id,
            day_id="D2 Lower A",
            current_load=205.0,
            ht_plates=205.0,
            ht_band_config=[band.id],
            confirmed_at=now,
        ),
        "shared_d1": MovementState(
            movement_id=movements["shared_row"].id,
            day_id="D1 Upper Push",
            current_load=100.0,
            confirmed_at=now - timedelta(days=2),
        ),
        "shared_d2": MovementState(
            movement_id=movements["shared_row"].id,
            day_id="D2 Lower A",
            current_load=110.0,
            confirmed_at=now - timedelta(days=1),
        ),
    }
    _persist(db, *states.values())

    d1_session = WorkoutSession(date=date(2026, 7, 1), day_role="D1 Upper Push", phase="P1")
    _persist(db, d1_session)

    return {
        "movements": movements,
        "tes": tes,
        "states": states,
        "sessions": {"d1": d1_session},
    }


@pytest.fixture
def resolver_db():
    with DBSession(_engine()) as db:
        ctx = _seed_resolver_program(db)
        yield db, ctx


def test_swap_note_resolves_candidate_slot_and_target_movement(resolver_db):
    db, ctx = resolver_db
    note = _note(
        "SWAP",
        {"movement": "Bench Press", "action": "switch", "params": "to Incline Bench"},
    )

    proposals = resolve_note(note, db)

    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal.valid is True
    assert proposal.tier_exercise_id == ctx["tes"]["bench"].id
    assert proposal.day_role == "D1 Upper Push"
    assert proposal.slot_label == "T1"
    assert proposal.override_type == "MOVEMENT"
    assert proposal.override_movement_id == ctx["movements"]["incline"].id


def test_non_ht_load_note_with_numeric_delta_is_valid(resolver_db):
    db, ctx = resolver_db
    note = _note(
        "LOAD_INCREASE",
        {"movement": "Bench Press", "action": "bump", "params": "+10"},
    )

    proposal = resolve_note(note, db)[0]

    assert proposal.tier_exercise_id == ctx["tes"]["bench"].id
    assert proposal.override_type == "LOAD"
    assert proposal.load_delta == 10.0
    assert proposal.valid is True
    assert proposal.validation_note is None


def test_ht_load_note_over_bottom_clamp_is_invalid(resolver_db):
    db, ctx = resolver_db
    note = _note(
        "LOAD_INCREASE",
        {"movement": "Hip Thrust", "action": "bump", "params": "+5"},
    )

    proposal = resolve_note(note, db)[0]

    assert proposal.tier_exercise_id == ctx["tes"]["hip_thrust"].id
    assert proposal.override_type == "LOAD"
    assert proposal.load_delta == 5.0
    assert proposal.valid is False
    assert proposal.validation_note is not None
    assert "bottom" in proposal.validation_note.lower()
    assert "clamp" in proposal.validation_note.lower()
    assert "225.0" in proposal.validation_note


def test_reorder_between_two_sibling_movements_computes_between_order(resolver_db):
    db, ctx = resolver_db
    note = _note(
        "REORDER",
        {
            "movement": "Knee Raises",
            "action": "move",
            "params": None,
            "before_movement": "Meadows Row",
            "after_movement": "Single Arm Row",
        },
    )

    proposal = resolve_note(note, db)[0]

    after_order = _effective_exercise_order(db, ctx["tes"]["single_arm_row"])
    before_order = _effective_exercise_order(db, ctx["tes"]["meadows_row"])
    assert proposal.valid is True
    assert proposal.override_type == "REORDER"
    assert after_order < proposal.override_order < before_order
    assert proposal.override_order == pytest.approx((after_order + before_order) / 2.0)


def test_shared_movement_on_two_days_returns_ranked_candidates(resolver_db):
    db, ctx = resolver_db
    note = _note(
        "LOAD_INCREASE",
        {"movement": "Shared Cable Row", "action": "bump", "params": "+2.5"},
        session_id=ctx["sessions"]["d1"].id,
    )

    proposals = resolve_note(note, db)

    assert ctx["states"]["shared_d2"].confirmed_at > ctx["states"]["shared_d1"].confirmed_at
    assert [p.tier_exercise_id for p in proposals] == [
        ctx["tes"]["shared_d1"].id,
        ctx["tes"]["shared_d2"].id,
    ]
    assert [p.day_role for p in proposals] == ["D1 Upper Push", "D2 Lower A"]
    assert [p.load_delta for p in proposals] == [2.5, 2.5]
    assert all(p.valid for p in proposals)


def test_load_note_without_parseable_number_is_invalid(resolver_db):
    db, ctx = resolver_db
    note = _note(
        "LOAD_INCREASE",
        {"movement": "Bench Press", "action": "bump", "params": "heavier next time"},
    )

    proposal = resolve_note(note, db)[0]

    assert proposal.tier_exercise_id == ctx["tes"]["bench"].id
    assert proposal.override_type == "LOAD"
    assert proposal.valid is False
    assert proposal.load_delta is None
    assert proposal.validation_note is not None
    assert "numeric load delta" in proposal.validation_note


def test_other_or_missing_proposed_change_returns_empty_list(resolver_db):
    db, _ctx = resolver_db

    other_note = _note(
        "OTHER",
        {"movement": "Bench Press", "action": "observe", "params": None},
    )
    missing_change_note = _note("LOAD_INCREASE", None)

    assert resolve_note(other_note, db) == []
    assert resolve_note(missing_change_note, db) == []
