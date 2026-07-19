"""Weak-point assessment API contract."""
from typing import List, Optional
from pydantic import BaseModel

class WeakMovementOut(BaseModel):
    movement_id: int
    name: str
    stalled: bool
    lagging: bool
    growth_rate: Optional[float] = None

class MuscleGroupSummaryOut(BaseModel):
    muscle: str
    weak_count: int
    total_count: int
    weak_movements: List[WeakMovementOut]

class WeakPointAssessmentOut(BaseModel):
    muscle_groups: List[MuscleGroupSummaryOut]
    movements: List[WeakMovementOut]
