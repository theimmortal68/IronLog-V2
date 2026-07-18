"""Readiness API contract."""
from datetime import date
from typing import Optional
from pydantic import BaseModel

class DailyReadinessOut(BaseModel):
    date: date
    bodyweight: Optional[float] = None
    resting_hr: Optional[float] = None
    sleep_ok: Optional[bool] = None
    subjective_ok: Optional[bool] = None

class DailyReadinessIn(BaseModel):
    bodyweight: Optional[float] = None
    resting_hr: Optional[float] = None
    sleep_ok: Optional[bool] = None
    subjective_ok: Optional[bool] = None

class ConfirmPhaseRequest(BaseModel):
    to_phase: str
