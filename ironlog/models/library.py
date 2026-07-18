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
from datetime import date as _Date  # pydantic rejects a field literally named `date`
                                     # typed `date` with an assigned Field(...) --
                                     # "field name clashing with a type annotation" --
                                     # so DailyReadiness.date uses this local alias
                                     # instead. Every other field here keeps the
                                     # plain `date` import untouched.
from typing import List, Optional

from sqlalchemy import Column, JSON, UniqueConstraint, text
from sqlmodel import Field, Relationship, SQLModel

from .enums import (
    AssistSubtype, AssistUnit, BandCalStatus, CalibrationStatus, EquipPhase,
    KneeModality, LiftCategory, LoadUnit, Muscle, Objective, Phase, ProgressionMode,
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
    peak_lb: float                            # rated/side x5; bottom is x2 (2.5x bottom)
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
    unilateral: bool = Field(default=False, sa_column_kwargs={"server_default": text("0")})
    is_tracked: bool = True
    status: Status = Status.ACTIVE
    knee_modality: Optional[KneeModality] = None       # cross-session knee-frequency classification (v0.3)

    # equipment: the load-bearing item is a real FK (drives floor/step);
    # the full descriptive set is JSON tags.
    load_equipment_id: Optional[int] = Field(default=None, foreign_key="equipment.id")
    equipment_tags: List[str] = Field(default_factory=list, sa_column=Column(JSON))

    # muscle targeting
    primary_muscle: Optional[Muscle] = None
    secondary_muscles: List[str] = Field(default_factory=list, sa_column=Column(JSON))

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
    ramp_eligible: bool = Field(default=False, sa_column_kwargs={"server_default": text("0")})
    rpe_capped: bool = False
    rpe_cap_exempt: bool = False

    # variant relationships
    family: Optional[str] = Field(default=None, index=True)  # shares one baseline
    is_family_anchor: bool = False
    derived_from_id: Optional[int] = Field(default=None, foreign_key="movement.id")
    start_ratio: Optional[float] = None                # e.g. front squat 0.80x

    band_eligible: bool = False                        # HT: uses a band pair
    notes: Optional[str] = None

    # per-movement progression-rule config (progression engine, v0.6+)
    progression_rule: Optional[str] = None
    assist_ladder: Optional[list] = Field(default=None, sa_column=Column(JSON))
    position_ladder: Optional[list] = Field(default=None, sa_column=Column(JSON))
    rep_ladder: Optional[list] = Field(default=None, sa_column=Column(JSON))
    rope_ladder: Optional[list] = Field(default=None, sa_column=Column(JSON))

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
    active_program_id: Optional[int] = Field(default=None, foreign_key="program.id")  # single-active pointer (Fork 3)
    pending_phase_transition: Optional[str] = None


class DailyReadiness(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    date: _Date = Field(index=True, unique=True)
    bodyweight: Optional[float] = None
    bodyweight_source: str = "manual"
    resting_hr: Optional[float] = None
    resting_hr_source: str = "manual"
    sleep_ok: Optional[bool] = None
    subjective_ok: Optional[bool] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class WithingsCredentials(SQLModel, table=True):
    """Singleton (id==1) holding the Withings OAuth2 token pair. access_token
    and refresh_token both rotate automatically as the server calls the
    Withings API, which is why this lives in the DB rather than .env (a
    file would need scripted rewrites on every rotation)."""
    id: Optional[int] = Field(default=1, primary_key=True)
    access_token: str
    refresh_token: str
    token_expires_at: datetime
    last_synced_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class MovementState(SQLModel, table=True):
    """Per-movement dynamic state."""
    __table_args__ = (UniqueConstraint("movement_id", "day_id", name="uq_movementstate_movement_day"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    movement_id: int = Field(foreign_key="movement.id", index=True)
    day_id: Optional[str] = Field(default=None, index=True)  # composite key w/ movement_id (progression engine, v0.6+)

    calibration_status: CalibrationStatus = CalibrationStatus.INHERITED
    e1rm: Optional[float] = None
    e1rm_updated_at: Optional[datetime] = None
    current_load: Optional[float] = None
    current_increment_tier: int = 0                    # index into increment_ladder
    pending_load_delta: Optional[float] = None         # earned load step (advance->load bridge, K2); staged by run_analysis, applied+cleared by commit_session
    current_rep_scheme: Optional[str] = None
    rep_scheme_locked_until: Optional[date] = None
    consecutive_ceiling_sessions: int = 0
    consecutive_failed_progressions: int = Field(default=0, sa_column_kwargs={"server_default": text("0")})  # mirrors ceiling counter; PROGRESS-gated (v0.4)

    confirmed_at: Optional[datetime] = None              # event-fact: when user last vouched for this load (Fork 2)

    # assisted movements
    assist_level: Optional[float] = None               # degrees / cable-lb / reps

    # HT composite
    ht_plates: Optional[float] = None
    ht_band_pair_id: Optional[int] = Field(default=None, foreign_key="bandpair.id")
    ht_felt_peak: Optional[float] = None

    # progression engine (v0.6+)
    consecutive_advance_count: int = Field(default=0, sa_column_kwargs={"server_default": text("0")})
    active_rule: Optional[str] = None
    current_body_position: Optional[str] = None
    current_rep_target: Optional[int] = None            # rep-ladder rule state (Task 3)
    duration_ladder: Optional[list] = Field(default=None, sa_column=Column(JSON))
    current_duration_seconds: Optional[int] = None
    current_rope: Optional[str] = None
    unassisted_max_rolling: Optional[int] = None
    stall_signal: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    ht_band_config: Optional[list] = Field(default=None, sa_column=Column(JSON))  # HT band-composite (Task 1)

    movement: Optional[Movement] = Relationship(back_populates="state")


class E1rmHistory(SQLModel, table=True):
    """Per-session anchor e1RM history (the append log behind MovementState.e1rm).

    One row per analyzed session per movement that had an anchor (a tapped
    working set). Readers: calibration-flip (weekly aggregation) and stall
    detection (PROGRESS-window trend). objective+phase are stamped per row so
    stall's window selection can filter to PROGRESS sessions without re-deriving.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    movement_id: int = Field(foreign_key="movement.id", index=True)
    session_id: int = Field(foreign_key="session.id", index=True)
    e1rm: float
    objective: Objective
    phase: Phase
    anchor_load: float
    anchor_reps: int
    anchor_rpe: float
    computed_at: datetime


class GenerationLog(SQLModel, table=True):
    """Full provenance of a generation (Fork 7d): the injected prompt, the
    model's selections, any clamps/repairs, the approval mode, and whether a
    fallback was used. Replayable audit trail (docs/06 §10)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="session.id", index=True)
    prompt_json: dict = Field(default_factory=dict, sa_column=Column(JSON))
    selections_json: dict = Field(default_factory=dict, sa_column=Column(JSON))
    clamps_json: list = Field(default_factory=list, sa_column=Column(JSON))
    repairs_json: list = Field(default_factory=list, sa_column=Column(JSON))
    approval_mode: str = "auto"            # "human" | "auto"
    fallback_used: bool = False
    committed_at: datetime = Field(default_factory=datetime.utcnow)
