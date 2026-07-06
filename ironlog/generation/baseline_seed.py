"""baseline_seed.py — seed MovementState calibrated baselines for the go-live.

Keyed on (movement_id, day_id=day_role). Sets scalar current_load / assist_level,
or HT ht_plates + ht_band_config = [orange band id]. Idempotent upsert on
(movement_id, day_id). NO from __future__ import annotations.
"""
from typing import Dict, Optional

from sqlmodel import Session, delete, select

from ironlog.models.enums import CalibrationStatus, Phase
from ironlog.models.library import (
    BandPair, E1rmHistory, EngineState, GenerationLog, MovementState,
)
from ironlog.models.program import ProgramDay, Tier, TierExercise
from ironlog.models.session import (
    ExerciseGroup, ExerciseSurvey, Note, PlannedExercise, PlannedSet,
    Session as WorkoutSession, SetLog,
)

# slot_id -> ("load"|"assist"|"ht", value, band_label_or_None)
#
# d1_t2c / d4_t2c (Face-Up Incline Knee Raise) are seeded "load" not "assist":
# the movement's progression_mode is LADDER (ironlog/seed.py), so resolve_start_load
# reads current_load, not assist_level. The design doc's "Face-Up-Knee 25°"/"10°"
# values (docs/superpowers/specs/2026-07-04-config-seed-reconciliation-design.md)
# are the movement's own LADDER progression value (incline setting, tracked like
# any other current_load scalar) — seeding them as assist_level left the field
# resolver blind to them (Task 7 go-live verify caught this: both slots came back
# needs-calibration despite having a seeded value in the wrong field).
BASELINES = {
    "d1_t1": ("load", 165, None), "d1_t2a": ("load", 170, None),
    "d1_t2b": ("load", 55, None), "d1_t2c": ("load", 25, None),
    "d1_t3b": ("load", 12.5, None), "d1_t3c": ("load", 60, None),
    "d1_t4a": ("load", 100, None), "d1_t4c": ("load", 10, None),
    "d2_t1": ("load", 260, None), "d2_t1b": ("ht", 205, "#0 Orange"),
    "d2_t2a": ("assist", 20, None), "d2_t2b": ("load", 180, None),
    "d2_t3a": ("load", 25, None), "d2_t3b": ("load", 25, None),
    "d4_t2a": ("load", 35, None), "d4_t2b": ("load", 40, None),
    "d4_t2c": ("load", 10, None), "d4_t3a": ("load", 10, None),
    "d4_t3b": ("load", 70, None),
    "d5_t1": ("load", 255, None), "d5_t1b": ("ht", 205, "#0 Orange"),
    "d5_t2a": ("load", 30, None), "d5_t2b": ("load", 180, None),
    "d5_t2c": ("assist", 25, None), "d5_t3a": ("load", 20, None),
    "d5_t3b": ("assist", 20, None), "d5_t3c": ("load", 30, None),
    "d5_t3d": ("load", 245, None),
    "d6_g1b": ("load", 150, None), "d6_g1c": ("ht", 155, "#0 Orange"),
    "d6_g2a": ("load", 90, None), "d6_g2b": ("load", 30, None),
    "d6_g2c": ("load", 10, None), "d6_g3a": ("load", 30, None),
    "d6_g3b": ("load", 60, None), "d6_g3c": ("load", 105, None),
}


def _day_role_for_tier(db: Session, tier: Tier) -> str:
    pd = db.exec(select(ProgramDay).where(ProgramDay.id == tier.program_day_id)).one()
    return pd.day_role


def _upsert(db: Session, movement_id: int, day_id: str) -> MovementState:
    st = db.exec(
        select(MovementState).where(
            MovementState.movement_id == movement_id,
            MovementState.day_id == day_id,
        )
    ).first()
    if st is None:
        st = MovementState(movement_id=movement_id, day_id=day_id)
        db.add(st)
    return st


def seed_movement_baselines(db: Session) -> None:
    tes = {t.slot_id: t for t in db.exec(select(TierExercise)).all()}
    tiers = {t.id: t for t in db.exec(select(Tier)).all()}
    bands = {b.label: b.id for b in db.exec(select(BandPair)).all()}
    for slot_id, (kind, value, band_label) in BASELINES.items():
        te = tes.get(slot_id)
        if te is None:
            raise ValueError(f"baseline slot_id not seeded: {slot_id}")
        day_id = _day_role_for_tier(db, tiers[te.tier_id])
        st = _upsert(db, te.movement_id, day_id)
        st.calibration_status = CalibrationStatus.MEASURED
        if kind == "load":
            st.current_load = value
        elif kind == "assist":
            st.assist_level = value
        elif kind == "ht":
            band_id = bands.get(band_label)
            if band_id is None:
                raise ValueError(f"band not seeded: {band_label}")
            st.ht_plates = value
            st.ht_band_config = [band_id]
    db.commit()


def reset_transactional_and_state(db: Session) -> None:
    """Wipe logged/transactional data + derived MovementState fields for a
    go-live on a non-fresh DB, WITHOUT touching seeded calibrated baselines
    (current_load / assist_level / ht_plates / ht_band_config / calibration_status).

    Deletes all rows of: SetLog, ExerciseSurvey, Note, GenerationLog,
    PlannedSet, PlannedExercise, ExerciseGroup (session scaffolding),
    WorkoutSession (the `Session` table model — not sqlmodel.Session, the
    db connection), E1rmHistory. Clears MovementState's derived-state fields.
    Resets EngineState.current_phase to CUT (keeps bodyweight).
    """
    # children referencing session_id (or chained via it) first, WorkoutSession
    # (the parent) last: SetLog/ExerciseSurvey/Note/GenerationLog/E1rmHistory
    # reference session_id directly; PlannedSet -> PlannedExercise ->
    # ExerciseGroup -> session_id is the scaffolding chain (must delete
    # leaf-to-root or a non-fresh DB is left with orphaned rows pointing at a
    # deleted session).
    for model in (
        SetLog, ExerciseSurvey, Note, GenerationLog, E1rmHistory,
        PlannedSet, PlannedExercise, ExerciseGroup, WorkoutSession,
    ):
        db.exec(delete(model))

    for st in db.exec(select(MovementState)).all():
        st.e1rm = None
        st.e1rm_updated_at = None
        st.consecutive_ceiling_sessions = 0
        st.consecutive_failed_progressions = 0
        st.consecutive_advance_count = 0
        st.stall_signal = None
        st.active_rule = None
        st.unassisted_max_rolling = None

    es = db.exec(select(EngineState)).first()
    if es is not None:
        es.current_phase = Phase.CUT

    db.commit()
