"""
periodization.py — long-range macro/meso/microcycle state tables.

This module is schema-only: it defines the planning hierarchy and orthogonal
state axes consumed by later deterministic resolver work.

NO from __future__ import annotations (project-wide constraint).
"""
from datetime import date
from enum import Enum
from typing import List, Optional

from sqlalchemy import Column, Enum as SAEnum, JSON, UniqueConstraint
from sqlmodel import Field, SQLModel


class PlanStatus(str, Enum):
    PLANNED = "PLANNED"
    ACTIVE = "ACTIVE"
    COMPLETE = "COMPLETE"
    ABANDONED = "ABANDONED"


class MicrocycleLifecycleStatus(str, Enum):
    NOT_STARTED = "NOT_STARTED"
    ACTIVE = "ACTIVE"
    COMPLETE = "COMPLETE"
    ABORTED = "ABORTED"


class MicrocycleDriftStatus(str, Enum):
    ON_TIME = "ON_TIME"
    EXTENDED = "EXTENDED"
    DRIFT_FLAGGED = "DRIFT_FLAGGED"


class BodyCompStateValue(str, Enum):
    CUT = "CUT"
    MAINTENANCE = "MAINTENANCE"
    GAIN = "GAIN"


class RecoveryStatusValue(str, Enum):
    NORMAL = "NORMAL"
    CAUTION = "CAUTION"
    POOR = "POOR"


class Macrocycle(SQLModel, table=True):
    """Inert long-range planning container."""
    id: Optional[int] = Field(default=None, primary_key=True)
    goal: str
    planned_start_date: Optional[date] = None
    planned_end_date: Optional[date] = None
    actual_start_date: Optional[date] = None
    actual_end_date: Optional[date] = None
    status: PlanStatus = Field(
        default=PlanStatus.PLANNED,
        sa_column=Column(SAEnum(PlanStatus), nullable=False),
    )


class MesocycleTemplate(SQLModel, table=True):
    """Reusable ordered sequence of open-vocabulary training postures."""
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    postures: List[str] = Field(default_factory=list, sa_column=Column(JSON))


class Mesocycle(SQLModel, table=True):
    """Instance of a mesocycle template, optionally owned by a macrocycle."""
    __table_args__ = (UniqueConstraint("macrocycle_id", "ordinal"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    template_id: int = Field(foreign_key="mesocycletemplate.id")
    macrocycle_id: Optional[int] = Field(default=None, foreign_key="macrocycle.id")
    ordinal: Optional[int] = None
    planned_start_date: date
    planned_end_date: date
    actual_start_date: Optional[date] = None
    actual_end_date: Optional[date] = None
    status: PlanStatus = Field(
        default=PlanStatus.PLANNED,
        sa_column=Column(SAEnum(PlanStatus), nullable=False),
    )


class Microcycle(SQLModel, table=True):
    """Calendar-anchored, drift-tolerant planned week within a mesocycle."""
    __table_args__ = (UniqueConstraint("mesocycle_id", "ordinal"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    mesocycle_id: int = Field(foreign_key="mesocycle.id")
    ordinal: int
    planned_start_date: date
    planned_end_date: date
    actual_start_date: Optional[date] = None
    actual_completion_date: Optional[date] = None
    expected_sessions: int
    completed_sessions: int = 0
    lifecycle_status: MicrocycleLifecycleStatus = Field(
        default=MicrocycleLifecycleStatus.NOT_STARTED,
        sa_column=Column(SAEnum(MicrocycleLifecycleStatus), nullable=False),
    )
    drift_status: MicrocycleDriftStatus = Field(
        default=MicrocycleDriftStatus.ON_TIME,
        sa_column=Column(SAEnum(MicrocycleDriftStatus), nullable=False),
    )
    drift_days: int = 0
    planned_posture: str
    effective_posture: Optional[str] = None


class BodyCompState(SQLModel, table=True):
    """Independent body-composition state timeline."""
    id: Optional[int] = Field(default=None, primary_key=True)
    state: BodyCompStateValue = Field(
        sa_column=Column(SAEnum(BodyCompStateValue), nullable=False),
    )
    effective_from: date
    effective_to: Optional[date] = None
    notes: Optional[str] = None


class RecoveryStatus(SQLModel, table=True):
    """Resolved daily readiness status derived from the existing capture pipeline."""
    id: Optional[int] = Field(default=None, primary_key=True)
    as_of_date: date = Field(index=True, unique=True)
    status: RecoveryStatusValue = Field(
        sa_column=Column(SAEnum(RecoveryStatusValue), nullable=False),
    )
    inputs_snapshot: Optional[dict] = Field(default=None, sa_column=Column(JSON))


class DeloadState(SQLModel, table=True):
    """Adaptive deload attribution and lifecycle state."""
    id: Optional[int] = Field(default=None, primary_key=True)
    microcycle_id: Optional[int] = Field(default=None, foreign_key="microcycle.id")
    active: bool = False
    triggered_at: Optional[date] = None
    trigger_reason: Optional[str] = None
    resolved_at: Optional[date] = None
