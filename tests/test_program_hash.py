"""Regression tests for deterministic Program hash utilities.

NO from __future__ import annotations (project-wide constraint).
"""
from sqlmodel import SQLModel, Session as DbSession, create_engine, select

from ironlog.engine.program_hash import (
    compute_program_prescription_hash,
    compute_slot_topology_hash,
)
from ironlog.models.enums import KneeModality
from ironlog.models.program import Program, ProgramDay, Tier, TierExercise, TierKind
import ironlog.models  # noqa: F401 - register all tables


def _engine():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return engine


def _warmup_config(reordered=False):
    if reordered:
        return {
            "items": [
                {"reps": 5, "sets": 1, "name": "floor_slides"},
                {"seconds": 90, "name": "jump_rope"},
            ],
            "activation": {"sets": 1, "name": "waiters_carry", "seconds_per_side": 20},
            "movement_flow_seconds": 90,
        }
    return {
        "movement_flow_seconds": 90,
        "activation": {"name": "waiters_carry", "seconds_per_side": 20, "sets": 1},
        "items": [
            {"name": "floor_slides", "sets": 1, "reps": 5},
            {"name": "jump_rope", "seconds": 90},
        ],
    }


def _seed_program(db, reordered=False):
    program = Program(name="Hash Test", phase="P1", duration_weeks=4)
    db.add(program)
    db.flush()

    day_specs = [
        {
            "day_index": 1,
            "day_role": "D1 Upper Push",
            "is_rest": False,
            "warmup_config": _warmup_config(reordered),
            "tiers": [
                {
                    "tier_label": "T1",
                    "tier_order": 1,
                    "tier_kind": TierKind.T1_STRAIGHT,
                    "rest_seconds": 180,
                    "rounds": 1,
                    "exercises": [
                        {
                            "slot_id": "d1_t1",
                            "movement_id": 101,
                            "exercise_order": 1,
                            "tier_role": "anchor",
                            "pattern": "press",
                            "rep_low": 5,
                            "rep_high": 8,
                            "rpe_cap": 8.0,
                            "scheme": "DOUBLE_PROGRESSION",
                        },
                        {
                            "slot_id": "d1_t1b",
                            "movement_id": 102,
                            "exercise_order": 2,
                            "tier_role": "semi",
                            "pattern": "row",
                            "rep_low": 8,
                            "rep_high": 10,
                            "scheme": "STRAIGHT",
                        },
                    ],
                },
                {
                    "tier_label": "T2 GS",
                    "tier_order": 2,
                    "tier_kind": TierKind.GIANT_SET,
                    "rest_seconds": 90,
                    "rounds": 3,
                    "exercises": [
                        {
                            "slot_id": "d1_t2a",
                            "movement_id": 103,
                            "exercise_order": 1,
                            "tier_role": "free",
                            "pattern": "lateral_raise",
                            "rep_low": 12,
                            "rep_high": 15,
                            "scheme": "STRAIGHT",
                        },
                        {
                            "slot_id": "d1_t2b",
                            "movement_id": 104,
                            "exercise_order": 2,
                            "tier_role": "free",
                            "pattern": "tib_raise",
                            "knee_modality": KneeModality.TIB,
                            "duration_low_seconds": 20,
                            "duration_high_seconds": 30,
                            "scheme": "STRAIGHT",
                        },
                    ],
                },
            ],
        },
        {
            "day_index": 2,
            "day_role": "",
            "is_rest": True,
            "warmup_config": None,
            "tiers": [],
        },
        {
            "day_index": 3,
            "day_role": "D3 Lower",
            "is_rest": False,
            "warmup_config": None,
            "tiers": [
                {
                    "tier_label": "T1",
                    "tier_order": 1,
                    "tier_kind": TierKind.T1_STRAIGHT,
                    "rest_seconds": 150,
                    "rounds": 1,
                    "exercises": [
                        {
                            "slot_id": "d3_t1",
                            "movement_id": 105,
                            "exercise_order": 1,
                            "tier_role": "anchor",
                            "pattern": "hinge",
                            "rep_low": 6,
                            "rep_high": 8,
                            "scheme": "TOPSET_BACKOFF",
                            "unified_ht_group": "main",
                        },
                        {
                            "slot_id": "d3_t1b",
                            "movement_id": 106,
                            "exercise_order": 2,
                            "tier_role": "semi",
                            "pattern": "hinge",
                            "rep_low": 8,
                            "rep_high": 10,
                            "scheme": "STRAIGHT",
                            "derived_from_unified_group": "main",
                            "derive_ratio": 0.8,
                        },
                    ],
                },
            ],
        },
    ]

    day_order = list(reversed(day_specs)) if reordered else day_specs
    for day_spec in day_order:
        day = ProgramDay(
            program_id=program.id,
            day_index=day_spec["day_index"],
            day_role=day_spec["day_role"],
            is_rest=day_spec["is_rest"],
            warmup_config=day_spec["warmup_config"],
        )
        db.add(day)
        db.flush()

        tier_specs = list(reversed(day_spec["tiers"])) if reordered else day_spec["tiers"]
        for tier_spec in tier_specs:
            tier = Tier(
                program_day_id=day.id,
                tier_label=tier_spec["tier_label"],
                tier_order=tier_spec["tier_order"],
                tier_kind=tier_spec["tier_kind"],
                rest_seconds=tier_spec["rest_seconds"],
                rounds=tier_spec["rounds"],
            )
            db.add(tier)
            db.flush()

            exercise_specs = (
                list(reversed(tier_spec["exercises"]))
                if reordered
                else tier_spec["exercises"]
            )
            for exercise_spec in exercise_specs:
                db.add(TierExercise(tier_id=tier.id, **exercise_spec))

    db.commit()
    db.refresh(program)
    return program


def test_hashes_are_canonicalized_independent_of_row_and_json_key_order():
    engine = _engine()
    with DbSession(engine) as db:
        program_a = _seed_program(db, reordered=False)
        program_b = _seed_program(db, reordered=True)

        assert compute_program_prescription_hash(program_a) == compute_program_prescription_hash(program_b)
        assert compute_slot_topology_hash(program_a) == compute_slot_topology_hash(program_b)


def test_rep_target_change_updates_prescription_hash_only():
    engine = _engine()
    with DbSession(engine) as db:
        program = _seed_program(db)
        original_prescription_hash = compute_program_prescription_hash(program)
        original_topology_hash = compute_slot_topology_hash(program)

        exercise = db.exec(
            select(TierExercise).where(TierExercise.slot_id == "d1_t1")
        ).one()
        exercise.rep_low = 6
        exercise.rep_high = 9
        db.add(exercise)
        db.commit()

        assert compute_program_prescription_hash(program) != original_prescription_hash
        assert compute_slot_topology_hash(program) == original_topology_hash


def test_day_rest_flip_updates_both_hashes():
    engine = _engine()
    with DbSession(engine) as db:
        program = _seed_program(db)
        original_prescription_hash = compute_program_prescription_hash(program)
        original_topology_hash = compute_slot_topology_hash(program)

        day = db.exec(
            select(ProgramDay).where(
                ProgramDay.program_id == program.id,
                ProgramDay.day_index == 2,
            )
        ).one()
        day.is_rest = False
        db.add(day)
        db.commit()

        assert compute_program_prescription_hash(program) != original_prescription_hash
        assert compute_slot_topology_hash(program) != original_topology_hash


def test_movement_change_updates_prescription_hash_only():
    engine = _engine()
    with DbSession(engine) as db:
        program = _seed_program(db)
        original_prescription_hash = compute_program_prescription_hash(program)
        original_topology_hash = compute_slot_topology_hash(program)

        exercise = db.exec(
            select(TierExercise).where(TierExercise.slot_id == "d1_t2a")
        ).one()
        exercise.movement_id = 999
        db.add(exercise)
        db.commit()

        assert compute_program_prescription_hash(program) != original_prescription_hash
        assert compute_slot_topology_hash(program) == original_topology_hash
