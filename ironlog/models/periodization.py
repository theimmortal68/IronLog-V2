"""
periodization.py — long-range macro/meso/microcycle state tables.

This module is schema-only: it defines the planning hierarchy and orthogonal
state axes consumed by later deterministic resolver work.

NO from __future__ import annotations (project-wide constraint).
"""
from datetime import date, datetime
from enum import Enum
from typing import List, Optional

from sqlalchemy import Column, Enum as SAEnum, ForeignKey, Integer, JSON, UniqueConstraint
from sqlmodel import Field, SQLModel


class PlanStatus(str, Enum):
    PLANNED = "PLANNED"
    ACTIVE = "ACTIVE"
    COMPLETE = "COMPLETE"
    ABANDONED = "ABANDONED"


class MacroPlanningState(str, Enum):
    ACTIVE = "ACTIVE"
    AWAITING_NEXT_MESOCYCLE = "AWAITING_NEXT_MESOCYCLE"
    COMPLETE = "COMPLETE"


class MicrocycleLifecycleStatus(str, Enum):
    NOT_STARTED = "NOT_STARTED"
    ACTIVE = "ACTIVE"
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"


class MicrocycleDriftStatus(str, Enum):
    """Design `schedule_state`; the existing DB/API column remains drift_status."""
    ON_TIME = "ON_TIME"
    EXTENDED = "EXTENDED"
    DRIFT_FLAGGED = "DRIFT_FLAGGED"


class MicrocycleSlotType(str, Enum):
    TRAINING = "TRAINING"
    REST = "REST"


class MicrocycleSlotResolution(str, Enum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    SKIPPED = "SKIPPED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class MicrocycleSlotResolutionSource(str, Enum):
    SESSION = "SESSION"
    INFERRED_BOUNDARY = "INFERRED_BOUNDARY"
    USER_EXPLICIT = "USER_EXPLICIT"


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
    planning_state: MacroPlanningState = Field(
        default=MacroPlanningState.ACTIVE,
        sa_column=Column(SAEnum(MacroPlanningState), nullable=False),
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
    program_id: Optional[int] = Field(default=None, foreign_key="program.id")
    ordinal: Optional[int] = None
    planned_start_date: date
    planned_end_date: date
    actual_start_date: Optional[date] = None
    actual_end_date: Optional[date] = None
    program_prescription_hash: Optional[str] = None
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
    slot_topology_hash: Optional[str] = None


class MicrocycleSlot(SQLModel, table=True):
    """Snapshotted planned day slot for a specific active Microcycle."""
    __table_args__ = (
        UniqueConstraint("microcycle_id", "ordinal"),
        UniqueConstraint("microcycle_id", "day_code"),
        UniqueConstraint("session_id"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    microcycle_id: int = Field(foreign_key="microcycle.id")
    ordinal: int
    day_code: str
    day_label: str
    planned_date: date
    slot_type: MicrocycleSlotType = Field(
        sa_column=Column(SAEnum(MicrocycleSlotType), nullable=False),
    )
    resolution: MicrocycleSlotResolution = Field(
        default=MicrocycleSlotResolution.PENDING,
        sa_column=Column(SAEnum(MicrocycleSlotResolution), nullable=False),
    )
    resolution_source: Optional[MicrocycleSlotResolutionSource] = Field(
        default=None,
        sa_column=Column(SAEnum(MicrocycleSlotResolutionSource), nullable=True),
    )
    session_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("session.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    resolved_at: Optional[datetime] = None


class AdvancementLog(SQLModel, table=True):
    """Append-only audit events produced by advancement/reconciliation flows."""
    id: Optional[int] = Field(default=None, primary_key=True)
    reconcile_run_id: Optional[str] = None
    entity_type: str
    entity_id: int
    reason: str
    details_json: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=datetime.utcnow)


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
