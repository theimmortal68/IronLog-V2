from datetime import date
from typing import Any, Dict, List, Optional
from pydantic import BaseModel

class MacrocycleSummaryOut(BaseModel):
    id: int
    goal: str
    status: str

class MesocycleSummaryOut(BaseModel):
    id: int
    template_id: int
    ordinal: Optional[int]
    status: str

class MicrocycleSummaryOut(BaseModel):
    id: int
    ordinal: int
    planned_posture: str
    effective_posture: Optional[str]
    lifecycle_status: str
    drift_status: str
    drift_days: int
    planned_start_date: date
    planned_end_date: date
    actual_start_date: Optional[date]
    actual_completion_date: Optional[date]

class DeloadStateOut(BaseModel):
    active: bool
    trigger_reason: Optional[str]

class ResolverTraceStepOut(BaseModel):
    axis: str
    before: Dict[str, Any]
    after: Dict[str, Any]

class CurrentPlanOut(BaseModel):
    macrocycle: Optional[MacrocycleSummaryOut] = None
    mesocycle: Optional[MesocycleSummaryOut] = None
    microcycle: Optional[MicrocycleSummaryOut] = None
    body_comp_state: Optional[str] = None
    recovery_status: Optional[str] = None
    deload_state: Optional[DeloadStateOut] = None
    resolver_trace: Optional[List[ResolverTraceStepOut]] = None

class MesocycleInstanceOut(BaseModel):
    id: int
    template_id: int
    template_name: str
    ordinal: Optional[int]
    planned_start_date: date
    planned_end_date: date
    actual_start_date: Optional[date]
    actual_end_date: Optional[date]
    status: str

class MacrocycleDetailOut(BaseModel):
    id: int
    goal: str
    planned_start_date: Optional[date]
    planned_end_date: Optional[date]
    status: str
    mesocycles: List[MesocycleInstanceOut]
