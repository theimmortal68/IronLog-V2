"""
session.py — the session / set-log / capture layer.

Implements the spec's two ideas:
  * PLANNED vs LOGGED: every set exists twice — what the engine prescribed
    (PlannedSet) and what happened (SetLog). The delta is the signal.
  * The capture fix: `SetLog.feedback_tap` is the per-set signal and is NOT
    nullable on working sets (enforced at the API layer), and `is_warmup` is a
    real column — never inferred from the exercise name.
"""
from datetime import date, datetime
from typing import List, Optional

from sqlmodel import Field, Relationship, SQLModel
from sqlalchemy import Column, JSON

from .enums import (
    FeedbackTap, GroupType, NoteClass, Objective, Scheme, SessionStatus, SetRole,
)


class Session(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    date: date
    day_role: str                                  # "Upper A" / "Lower A" / ...
    phase: str                                     # snapshot of EngineState phase
    status: SessionStatus = SessionStatus.PLANNED
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    approved_at: Optional[datetime] = None
    signature: dict = Field(default_factory=dict, sa_column=Column(JSON))
    rationale: Optional[str] = None
    notes: Optional[str] = None

    groups: List["ExerciseGroup"] = Relationship(back_populates="session")


class ExerciseGroup(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="session.id", index=True)
    order_index: int
    group_type: GroupType
    rounds: int = 1                                # giant sets = 3
    rest_seconds: Optional[int] = None
    label: Optional[str] = None

    session: Optional[Session] = Relationship(back_populates="groups")
    exercises: List["PlannedExercise"] = Relationship(back_populates="group")


class PlannedExercise(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    group_id: int = Field(foreign_key="exercisegroup.id", index=True)
    movement_id: int = Field(foreign_key="movement.id", index=True)
    order_index: int
    scheme: Scheme
    objective: Objective

    group: Optional[ExerciseGroup] = Relationship(back_populates="exercises")
    planned_sets: List["PlannedSet"] = Relationship(back_populates="planned_exercise")


class PlannedSet(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    planned_exercise_id: int = Field(foreign_key="plannedexercise.id", index=True)
    set_index: int
    set_role: SetRole
    is_warmup: bool = False                        # real flag (the fix)

    target_load: Optional[float] = None
    target_reps_low: Optional[int] = None
    target_reps_high: Optional[int] = None
    target_rpe: Optional[float] = None

    # assisted (rep-ratio)
    target_unassisted_reps: Optional[int] = None
    target_assisted_reps: Optional[int] = None

    # HT composite
    target_plates: Optional[float] = None
    band_pair_id: Optional[int] = Field(default=None, foreign_key="bandpair.id")
    target_felt_peak: Optional[float] = None

    planned_exercise: Optional[PlannedExercise] = Relationship(back_populates="planned_sets")


class SetLog(SQLModel, table=True):
    """What actually happened. feedback_tap is mandatory on working sets."""
    id: Optional[int] = Field(default=None, primary_key=True)
    planned_set_id: Optional[int] = Field(default=None, foreign_key="plannedset.id")
    session_id: int = Field(foreign_key="session.id", index=True)
    movement_id: int = Field(foreign_key="movement.id", index=True)
    set_index: int
    performed_at: datetime = Field(default_factory=datetime.utcnow)

    actual_load: Optional[float] = None
    actual_reps: Optional[int] = None
    feedback_tap: Optional[FeedbackTap] = None     # required on WORKING/TOP/BACKOFF
    rpe_numeric: Optional[float] = None            # optional finer grain
    is_warmup: bool = False

    actual_unassisted_reps: Optional[int] = None
    actual_assisted_reps: Optional[int] = None
    actual_plates: Optional[float] = None
    band_pair_id: Optional[int] = Field(default=None, foreign_key="bandpair.id")
    felt_peak: Optional[float] = None


class ExerciseSurvey(SQLModel, table=True):
    """Post-exercise sticking-point read, captured while recall is fresh."""
    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="session.id", index=True)
    movement_id: int = Field(foreign_key="movement.id", index=True)
    performed_at: datetime = Field(default_factory=datetime.utcnow)
    sticking_point: Optional[str] = None           # code from the taxonomy
    asymmetry_flag: Optional[bool] = None
    technique_flag: Optional[bool] = None


class Note(SQLModel, table=True):
    """Freeform channel; classified server-side; config changes need confirm."""
    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: Optional[int] = Field(default=None, foreign_key="session.id")
    movement_id: Optional[int] = Field(default=None, foreign_key="movement.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    text: str
    classification: Optional[NoteClass] = None
    confirmed: bool = False
    applied: bool = False


class StickingPointTaxonomy(SQLModel, table=True):
    """Per-lift survey options (data-driven so it's editable)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    lift_category: str = Field(index=True)
    option_code: str
    order_index: int = 0
