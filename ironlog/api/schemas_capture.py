"""Capture-layer API contract (the server<->client crossing artifact).

These shapes are mirrored field-for-field by the Android client's Kotlin DTOs.
Any change here is a contract change that touches both repos.
"""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class SetLogIn(BaseModel):
    planned_set_id: Optional[int] = None
    movement_id: int
    set_index: int
    set_role: str
    is_warmup: bool = False
    actual_load: Optional[float] = None
    actual_reps: Optional[int] = None
    feedback_tap: Optional[str] = None
    rpe_numeric: Optional[float] = None
    actual_unassisted_reps: Optional[int] = None
    actual_assisted_reps: Optional[int] = None
    actual_plates: Optional[float] = None
    band_pair_id: Optional[int] = None
    felt_peak: Optional[float] = None


class ExerciseSurveyIn(BaseModel):
    movement_id: int
    sticking_point: Optional[str] = None
    asymmetry_flag: Optional[bool] = None
    technique_flag: Optional[bool] = None


class NoteIn(BaseModel):
    movement_id: Optional[int] = None
    text: str


class SubmitRequest(BaseModel):
    set_logs: List[SetLogIn]
    surveys: List[ExerciseSurveyIn] = []
    notes: List[NoteIn] = []


class SwapExerciseRequest(BaseModel):
    new_movement_id: int
    make_permanent: bool = False


class SubmitResponse(BaseModel):
    session_id: int
    status: str
    set_logs_written: int
    already_completed: bool
    phase_transition_available: Optional[str] = None


class PlannedSetOut(BaseModel):
    id: int
    set_index: int
    set_role: str
    is_warmup: bool
    is_skipped: bool = False
    target_load: Optional[float] = None
    target_reps_low: Optional[int] = None
    target_reps_high: Optional[int] = None
    target_rpe: Optional[float] = None
    target_unassisted_reps: Optional[int] = None
    target_assisted_reps: Optional[int] = None
    target_plates: Optional[float] = None
    band_pair_id: Optional[int] = None
    target_felt_peak: Optional[float] = None
    band_config: Optional[List[int]] = None


class ExerciseOut(BaseModel):
    id: int
    movement_id: int
    movement_name: str
    unilateral: bool = False
    order_index: int
    scheme: str
    objective: str
    unit_hint: Optional[str] = None
    planned_sets: List[PlannedSetOut]


class GroupOut(BaseModel):
    id: int
    order_index: int
    group_type: str
    rounds: int
    rest_seconds: Optional[int] = None
    label: Optional[str] = None
    shoe: Optional[str] = None
    exercises: List[ExerciseOut]


class WarmupOut(BaseModel):
    movement_flow_seconds: int
    items: List[Dict[str, Any]]
    activation_seconds: int
    items_activation: List[Dict[str, Any]]


class FinisherOut(BaseModel):
    exercise_name: str
    duration_minutes: int
    params: Dict[str, Any]
    current_duration_seconds: Optional[int] = None
    current_rope: Optional[str] = None
    last_logged_weight_lb: Optional[float] = None
    last_logged_resistance_level: Optional[int] = None


class SessionDetailResponse(BaseModel):
    id: int
    date: str
    day_role: str
    phase: str
    status: str
    groups: List[GroupOut]
    warmup: Optional[WarmupOut] = None
    finisher: Optional[FinisherOut] = None
