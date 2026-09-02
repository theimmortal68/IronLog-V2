"""
program.py — the program definition layer (the evolving-seed prior).

DEFINITION tables (static, what a program *is*):
    Program, ProgramDay, Tier, TierExercise, MesoRotation, WeekParityRotation

These are seeded once per training block and read by the generation layer
(skeleton.py) to provide the program prior for each generated session.

NO from __future__ import annotations (project-wide constraint).
"""
from datetime import date, datetime
from enum import Enum
from typing import Optional

from sqlalchemy import Column, JSON, REAL, Text, UniqueConstraint, text
from sqlmodel import Field, SQLModel

from .enums import KneeModality, OverrideType  # noqa: F401 — used in TierExercise/SlotMovementOverride column types


class TierKind(str, Enum):
    T1_STRAIGHT = "T1_STRAIGHT"
    GIANT_SET   = "GIANT_SET"
    PAIR        = "PAIR"
    ACCESSORY   = "ACCESSORY"


class Program(SQLModel, table=True):
    """A training block definition (e.g. Phase 1 Post-HGC)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    phase: str
    duration_weeks: int
    started_at: Optional[datetime] = None               # event-fact (Fork 3)
    ended_at: Optional[datetime] = None


class ProgramDay(SQLModel, table=True):
    """One calendar day in the program (training or rest)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    program_id: int = Field(foreign_key="program.id")
    day_index: int        # 1=Mon … 7=Sun
    day_role: str         # "D1 Upper Push", "D2 Lower A", "" for rest days
    is_rest: bool = False
    warmup_config: Optional[dict] = Field(default=None, sa_column=Column(JSON))


class MissedDayRecord(SQLModel, table=True):
    """One row per detected missed training day (append-only history,
    NOT a singleton). status is mutated in place as the athlete acts
    on it."""
    id: Optional[int] = Field(default=None, primary_key=True)
    program_day_id: int = Field(foreign_key="programday.id", index=True)
    week_start_date: date       # Monday of the missed week
    detected_at: datetime = Field(default_factory=datetime.utcnow)
    status: str = "PENDING"     # PENDING | ACKNOWLEDGED | RESCHEDULED | RESOLVED
    resolved_at: Optional[datetime] = None


class DayFinisher(SQLModel, table=True):
    """One EMOM finisher assigned to a non-rest program day."""
    id: Optional[int] = Field(default=None, primary_key=True)
    program_day_id: int = Field(foreign_key="programday.id")
    movement_id: int = Field(foreign_key="movement.id")
    duration_minutes: int
    params: dict = Field(default_factory=dict, sa_column=Column(JSON))


class FinisherLog(SQLModel, table=True):
    """One row per finisher actually performed -- the finisher's answer to
    SetLog. Captures what was actually used (weight/resistance), since
    DayFinisher.params is a static, seed-time-only config with no per-session
    adjustability. Exactly one of actual_weight_lb / actual_resistance_level
    is expected to be set per finisher type (weight-based movements like
    kb_swing/heavy_farmer_carry/sandbag_load_to_utility_seat vs. resistance-
    based ones like sled_push); jump_rope logs neither (it already has its
    own rope_ladder/duration_ladder progression via MovementState, unrelated
    to this table).
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="session.id", index=True)
    movement_id: int = Field(foreign_key="movement.id", index=True)
    actual_weight_lb: Optional[float] = Field(default=None, sa_column=Column(REAL))
    actual_resistance_level: Optional[int] = None
    notes: Optional[str] = Field(default=None, sa_column=Column(Text))
    performed_at: datetime = Field(default_factory=datetime.utcnow)


class Tier(SQLModel, table=True):
    """One training tier within a program day (T1, T2 GS, GS1, …)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    program_day_id: int = Field(foreign_key="programday.id")
    paired_tier_id: Optional[int] = Field(default=None, foreign_key="tier.id", index=True)
    tier_label: str       # "T1", "T2 GS", "GS1", …
    tier_order: int       # 1-based ordering within the day
    tier_kind: TierKind
    rest_seconds: Optional[int] = None
    rounds: int = 1
    shoe: Optional[str] = None    # display-only footwear label ("Metcon 9", "Adipower II")


class TierExercise(SQLModel, table=True):
    """One exercise slot within a tier (the program prior for that slot)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    tier_id: int = Field(foreign_key="tier.id")
    slot_id: str          # "d1_t1", "d1_t2a", …
    movement_id: int = Field(foreign_key="movement.id")   # Meso-1 default
    exercise_order: int   # 1-based ordering within the tier
    tier_role: str        # "anchor" | "semi" | "free"
    pattern: Optional[str] = None
    knee_modality: Optional[KneeModality] = None
    rep_low: Optional[int] = None
    rep_high: Optional[int] = None
    rpe_cap: Optional[float] = None
    scheme: Optional[str] = None   # e.g. "TOPSET_BACKOFF", "DOUBLE_PROGRESSION"
    unified_ht_group: Optional[str] = None
    # HT slots conceptually use either unified_ht_group or derived_from_unified_group, never both.
    derived_from_unified_group: Optional[str] = None
    derive_ratio: Optional[float] = None


class MesoRotation(SQLModel, table=True):
    """Per-meso movement override for a TierExercise rotation slot.

    Meso 1 = the TierExercise's own movement_id (no MesoRotation row needed).
    meso_number >= 2 rows override that slot's movement when lay_skeleton
    is called with meso_number matching this row.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    tier_exercise_id: int = Field(foreign_key="tierexercise.id")
    meso_number: int
    movement_id: int = Field(foreign_key="movement.id")
    rep_low: Optional[int] = None
    rep_high: Optional[int] = None


class WeekParityRotation(SQLModel, table=True):
    """Per-slot movement (+ optional rep-target) override keyed by fixed week
    parity, resolved automatically from the current date at generation time
    -- no manual toggle, no note-apply flow. Two rows per rotating slot: one
    week_parity="A", one week_parity="B".

    Precedence in lay_skeleton's slot resolution: an active
    SlotMovementOverride still wins first (explicit live-state swap always
    takes priority), then a matching WeekParityRotation row for the current
    date's parity, then MesoRotation(meso_number), then te.movement_id
    (unchanged fallback order, WeekParityRotation inserted as a new tier).

    rep_low/rep_high are optional: when set, they override the TierExercise's
    own rep_low/rep_high for the SlotSpec/AnchorSpec built for the matched
    week (so two rotating movements can carry genuinely different rep
    targets, not just different movement identities). When left None, the
    TierExercise's own rep_low/rep_high apply unchanged.
    """
    __table_args__ = (UniqueConstraint("tier_exercise_id", "week_parity"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    tier_exercise_id: int = Field(foreign_key="tierexercise.id", index=True)
    week_parity: str  # "A" or "B" -- validated by callers, not a DB constraint
    movement_id: int = Field(foreign_key="movement.id")
    rep_low: Optional[int] = None
    rep_high: Optional[int] = None


class SlotMovementOverride(SQLModel, table=True):
    """General per-slot override — movement swap / load adjust / rep-target
    change (note-driven). lay_skeleton honors an active MOVEMENT override for a
    TierExercise, taking precedence over MesoRotation and the base movement
    (unchanged behavior). The assembler honors an active LOAD/REPS override at
    prescription time (Option-C: it adjusts only the prescribed values, never
    MovementState/current_load). Base program is never mutated; revert = active=False.

    override_movement_id is required (kept NOT NULL to match the existing
    021 schema — additive-only migrations, no column-nullability change): a
    LOAD/REPS row still sets it (harmlessly unused for those types) rather than
    forcing a table rebuild to relax the constraint.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    tier_exercise_id: int = Field(foreign_key="tierexercise.id", index=True)
    override_movement_id: int = Field(foreign_key="movement.id")
    source_note_id: int = Field(foreign_key="note.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    active: bool = True
    override_type: OverrideType = Field(
        default=OverrideType.MOVEMENT,
        sa_column_kwargs={"server_default": text("'MOVEMENT'")},
    )
    load_delta: Optional[float] = None
    load_absolute: Optional[float] = None
    rep_low: Optional[int] = None
    rep_high: Optional[int] = None
    override_order: Optional[float] = Field(default=None, sa_column=Column(REAL))
