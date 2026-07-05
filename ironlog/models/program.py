"""
program.py — the program definition layer (the evolving-seed prior).

DEFINITION tables (static, what a program *is*):
    Program, ProgramDay, Tier, TierExercise, MesoRotation

These are seeded once per training block and read by the generation layer
(skeleton.py) to provide the program prior for each generated session.

NO from __future__ import annotations (project-wide constraint).
"""
from datetime import datetime
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel

from .enums import KneeModality  # noqa: F401 — used in TierExercise column type


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


class Tier(SQLModel, table=True):
    """One training tier within a program day (T1, T2 GS, GS1, …)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    program_day_id: int = Field(foreign_key="programday.id")
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


class SlotMovementOverride(SQLModel, table=True):
    """Live-state per-slot movement swap (note-driven). lay_skeleton honors an
    active override for a TierExercise, taking precedence over MesoRotation and
    the base movement. Base program is never mutated; revert = active=False."""
    id: Optional[int] = Field(default=None, primary_key=True)
    tier_exercise_id: int = Field(foreign_key="tierexercise.id", index=True)
    override_movement_id: int = Field(foreign_key="movement.id")
    source_note_id: int = Field(foreign_key="note.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    active: bool = True
