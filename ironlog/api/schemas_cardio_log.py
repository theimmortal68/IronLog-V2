"""Cardio-log API contract."""
from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel

class CardioLogCreate(BaseModel):
    date: date
    duration_minutes: int
    avg_hr: Optional[int] = None
    modality: str  # "WALK" | "TREADMILL"
    incline_pct: Optional[float] = None
    backward_walk_done: bool = False

class CardioLogOut(BaseModel):
    id: int
    date: date
    duration_minutes: int
    avg_hr: Optional[int]
    modality: str
    incline_pct: Optional[float]
    backward_walk_done: bool
    created_at: datetime

class CardioWeeklySummaryOut(BaseModel):
    count: int
    target: int
    week_start: date
