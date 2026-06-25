"""
library.py — the library/state layer as SQLModel tables.

Each class with `table=True` becomes a database table AND a Pydantic model your
FastAPI routes can validate/serialize. The design follows the schema spec's split:

    DEFINITION (static, what a lift *is*)   ->  Movement, Equipment, BandPair, PhasePolicy
    STATE      (dynamic, what's true now)   ->  MovementState, EngineState

Two columns hold lists (increment_ladder, equipment_tags). Relational databases
don't store lists in a normal column, so we keep them as JSON. The load-BEARING
equipment is a real foreign key (it governs floor/step); the rest of the tags are
descriptive JSON.
"""

from datetime import date, datetime
from typing import List, Optional

from sqlalchemy import Column, JSON
from sqlmodel import Field, Relationship, SQLModel

from .enums import (
    AssistSubtype, AssistUnit, BandCalStatus, CalibrationStatus, EquipPhase,
    KneeModality, LiftCategory, LoadUnit, Objective, Phase, ProgressionMode,
    Region, Scheme, Status,
)
# ----------------------------------------------------------------------------
# DEFINITION
# ----------------------------------------------------------------------------

class Equipment(SQLModel, table=True):
    """Vocabulary + the hard load floors the validator enforces."""
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    load_floor: Optional[float] = None      # lightest loadable; None = n/a
    min_step: Optional[float] = None         # smallest reachable increment
    load_unit: LoadUnit = LoadUnit.LB
    available_phase: EquipPhase = EquipPhase.P1   # when it joins the gym
    notes: Optional[str] = None

    movements: List["Movement"] = Relationship(back_populates="load_equipment")
class BandPair(SQLModel, table=True):
    """Hip Thrust accommodating-resistance band pair (one per side)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    label: str                                # "#0 Orange" .. "#5 Purple"
    bottom_lb: float
    peak_lb: float                            # ~2.1x bottom (geometry)
    calibration_status: BandCalStatus = BandCalStatus.MODELED
    inspection_date: Optional[date] = None    # wear-gate prompt
    usable: bool = True                       # #5 false: bottom alone > clamp
class Movement(SQLModel, table=True):
    """A lift in the library (static definition)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)        # "Back Squat [PB]"
    base_name: str                                    # "Back Squat" (grouping)
    region: Region = Region.NONE
    lift_category: LiftCategory = LiftCategory.NONE
    is_primary: bool = False
    is_tracked: bool = True
    status: Status = Status.ACTIVE
    knee_modality: Optional[KneeModality] = None       # cross-session knee-frequency classification (v0.3)

    # equipment: the load-bearing item is a real FK (drives floor/step);
    # the full descriptive set is JSON tags.
    load_equipment_id: Optional[int] = Field(default=None, foreign_key="equipment.id")
    equipment_tags: List[str] = Field(default_factory=list, sa_column=Column(JSON))

    # progression
    progression_mode: ProgressionMode = ProgressionMode.LADDER
    assist_subtype: Optional[AssistSubtype] = None
    assist_unit: Optional[AssistUnit] = None
    scheme: Scheme = Scheme.STRAIGHT
    objective_override: Optional[Objective] = None    # None = inherit phase default

    # loading numbers
    increment_ladder: List[float] = Field(default_factory=list, sa_column=Column(JSON))
    min_step: Optional[float] = None
    load_floor: Optional[float] = None
    cap: Optional[float] = None
    rpe_capped: bool = False
    rpe_cap_exempt: bool = False

    # variant relationships
    family: Optional[str] = Field(default=None, index=True)  # shares one baseline
    is_family_anchor: bool = False
    derived_from_id: Optional[int] = Field(default=None, foreign_key="movement.id")
    start_ratio: Optional[float] = None                # e.g. front squat 0.80x

    band_eligible: bool = False                        # HT: uses a band pair
    notes: Optional[str] = None

    load_equipment: Optional[Equipment] = Relationship(back_populates="movements")
    state: Optional["MovementState"] = Relationship(back_populates="movement")
class PhasePolicy(SQLModel, table=True):
    """The loading envelope for each phase (config, one row per phase)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    phase: Phase = Field(index=True, unique=True)
    default_objective: Objective
    rpe_band_low: float
    rpe_band_high: float
    hard_cap: float
    top_set_rpe: float
    progression_attempted: bool
    volume_posture: str
    meaningful_drop_pct: Optional[float] = None        # under-recovery trigger
    meaningful_drop_sessions: Optional[int] = None
# ----------------------------------------------------------------------------
# STATE
# ----------------------------------------------------------------------------

class EngineState(SQLModel, table=True):
    """Global engine state (singleton: id == 1)."""
    id: Optional[int] = Field(default=1, primary_key=True)
    current_phase: Phase = Phase.CALIBRATION
    bodyweight: Optional[float] = None                 # drives CUT->STAB gate
    # STAB -> REBUILD gate flags
    rhr_down: bool = False
    sleep_ok: bool = False
    no_rpe_creep: bool = False
    bw_stable_2wk: bool = False
    strength_bounce: bool = False
    subjective_ok: bool = False
class MovementState(SQLModel, table=True):
    """Per-movement dynamic state."""
    id: Optional[int] = Field(default=None, primary_key=True)
    movement_id: int = Field(foreign_key="movement.id", index=True, unique=True)

    calibration_status: CalibrationStatus = CalibrationStatus.INHERITED
    e1rm: Optional[float] = None
    e1rm_updated_at: Optional[datetime] = None
    current_load: Optional[float] = None
    current_increment_tier: int = 0                    # index into increment_ladder
    current_rep_scheme: Optional[str] = None
    rep_scheme_locked_until: Optional[date] = None
    consecutive_ceiling_sessions: int = 0

    # assisted movements
    assist_level: Optional[float] = None               # degrees / cable-lb / reps

    # HT composite
    ht_plates: Optional[float] = None
    ht_band_pair_id: Optional[int] = Field(default=None, foreign_key="bandpair.id")
    ht_felt_peak: Optional[float] = None

    movement: Optional[Movement] = Relationship(back_populates="state")
