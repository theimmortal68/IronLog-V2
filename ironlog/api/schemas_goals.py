"""Goal settings API contract."""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel

class GoalSettingsOut(BaseModel):
    target_bodyweight: float
    target_bodyweight_tolerance: float
    target_body_fat_pct: Optional[float] = None
    target_body_fat_pct_tolerance: Optional[float] = None
    updated_at: datetime

class GoalSettingsIn(BaseModel):
    target_bodyweight: Optional[float] = None
    target_bodyweight_tolerance: Optional[float] = None
    target_body_fat_pct: Optional[float] = None
    target_body_fat_pct_tolerance: Optional[float] = None
