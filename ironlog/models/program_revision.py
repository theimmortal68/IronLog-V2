"""
program_revision.py — immutable revision snapshots of a program definition.

ProgramRevision and its child tables (ProgramRevisionDay, ProgramRevisionTier,
ProgramRevisionExercise, ProgramRevisionMesoRotation, ProgramRevisionParityRotation,
ProgramRevisionEquipmentRequirement) are materialized copies of the live authoring
tables, frozen at publish time. Once created, they are never mutated.

See docs/adr/0001-program-library-architecture.md for the full architecture
decision and rationale (Option B: immutable normalized revision tables).

NO from __future__ import annotations (project-wide constraint).
"""
from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import Column, JSON, UniqueConstraint
from sqlmodel import Field, SQLModel

from .enums import KneeModality  # noqa: F401 — used in ProgramRevisionExercise column type
from .program import TierKind  # noqa: F401 — used in ProgramRevisionTier column type


class RevisionOrigin(str, Enum):
    """How the revision came into existence.

    PUBLISHED: created by a real publish action; fully authoritative from the
        moment of publication.
    LEGACY_RECONSTRUCTION: materialized after the fact from live-table state,
        to give pre-migration history a linkable revision for continuity; does
        NOT assert that generation was actually pinned to this exact state at
        the time.
    """
    PUBLISHED = "PUBLISHED"
    LEGACY_RECONSTRUCTION = "LEGACY_RECONSTRUCTION"


class SlotRole(str, Enum):
    """Slot-requirement typing (ADR "Slot requirement typing" / roadmap Phase 6–8).

    ANCHOR: exact movement required, no substitution.
    SEMI_ANCHOR: movement, or an approved substitution family.
    ADAPTIVE_SLOT: required movement pattern, muscle/weak-point constraints,
        allowed program role, prohibited characteristics.

    Structural only in this phase — no resolver/scorer consumes these yet.
    """
    ANCHOR = "ANCHOR"
    SEMI_ANCHOR = "SEMI_ANCHOR"
    ADAPTIVE_SLOT = "ADAPTIVE_SLOT"


class ProgramRevision(SQLModel, table=True):
    """One frozen, executable version of a Program (e.g. 'APEX Bridge r7')."""
    __table_args__ = (UniqueConstraint("program_id", "revision_number"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    program_id: int = Field(foreign_key="program.id", index=True)
    revision_number: int     # monotonic per program_id, starting at 1
    revision_origin: RevisionOrigin
    prescription_hash: str
    topology_hash: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ProgramRevisionDay(SQLModel, table=True):
    """Revision-scoped mirror of ProgramDay."""
    id: Optional[int] = Field(default=None, primary_key=True)
    program_revision_id: int = Field(foreign_key="programrevision.id", index=True)
    day_index: int
    day_role: str
    is_rest: bool = False
    warmup_config: Optional[dict] = Field(default=None, sa_column=Column(JSON))


class ProgramRevisionTier(SQLModel, table=True):
    """Revision-scoped mirror of Tier."""
    id: Optional[int] = Field(default=None, primary_key=True)
    program_revision_day_id: int = Field(foreign_key="programrevisionday.id", index=True)
    paired_program_revision_tier_id: Optional[int] = Field(
        default=None, foreign_key="programrevisiontier.id",
    )
    tier_label: str
    tier_order: int
    tier_kind: TierKind
    rest_seconds: Optional[int] = None
    rounds: int = 1
    shoe: Optional[str] = None


class ProgramRevisionExercise(SQLModel, table=True):
    """Revision-scoped mirror of TierExercise, plus forward-compat slot-role/constraints."""
    id: Optional[int] = Field(default=None, primary_key=True)
    program_revision_tier_id: int = Field(foreign_key="programrevisiontier.id", index=True)
    slot_id: str
    movement_id: int = Field(foreign_key="movement.id")
    exercise_order: int
    tier_role: str
    pattern: Optional[str] = None
    knee_modality: Optional[KneeModality] = None
    rep_low: Optional[int] = None
    rep_high: Optional[int] = None
    duration_low_seconds: Optional[int] = None
    duration_high_seconds: Optional[int] = None
    rpe_cap: Optional[float] = None
    scheme: Optional[str] = None
    unified_ht_group: Optional[str] = None
    derived_from_unified_group: Optional[str] = None
    derive_ratio: Optional[float] = None
    # Forward-compatibility (ADR "Slot requirement typing") — structural only, not
    # consumed by anything yet.  Do not implement a resolver/scorer for these in this task.
    slot_role: SlotRole = SlotRole.ANCHOR
    slot_constraints: Optional[dict] = Field(default=None, sa_column=Column(JSON))


class ProgramRevisionMesoRotation(SQLModel, table=True):
    """Revision-scoped mirror of MesoRotation.

    NOTE: deliberately no mesocycle_id column. A one-time production cutover
    (scripts/migrate_phase_to_periodization.py) mutated existing rows to set
    mesocycle_id for live-lookup optimization, but did not change meso_number
    semantics. No code creates divergent content keyed on mesocycle_id.
    Therefore, meso_number remains the authoritative content key for what a
    revision should freeze, and mesocycle_id is omitted here.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    program_revision_exercise_id: int = Field(
        foreign_key="programrevisionexercise.id", index=True,
    )
    meso_number: int
    movement_id: int = Field(foreign_key="movement.id")
    rep_low: Optional[int] = None
    rep_high: Optional[int] = None


class ProgramRevisionParityRotation(SQLModel, table=True):
    """Revision-scoped mirror of MicrocycleParityRotation (WeekParityRotation)."""
    __table_args__ = (
        UniqueConstraint("program_revision_exercise_id", "week_parity"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    program_revision_exercise_id: int = Field(
        foreign_key="programrevisionexercise.id", index=True,
    )
    week_parity: str   # "A" or "B"
    movement_id: int = Field(foreign_key="movement.id")
    rep_low: Optional[int] = None
    rep_high: Optional[int] = None


class ProgramRevisionEquipmentRequirement(SQLModel, table=True):
    """Structural stub only (ADR "Equipment/Program-Requirement Separation" +
    roadmap Phase 4).  No resolver/consumer in this task — just prove the
    revision schema can express a requirement without a future breaking migration.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    program_revision_exercise_id: int = Field(
        foreign_key="programrevisionexercise.id", index=True,
    )
    requirement_expr: dict = Field(sa_column=Column(JSON))
