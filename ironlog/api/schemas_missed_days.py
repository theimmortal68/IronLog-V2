"""Missed-day records API contract."""
from datetime import date, datetime
from pydantic import BaseModel

class MissedDayRecordOut(BaseModel):
    id: int
    program_day_id: int
    day_role: str
    week_start_date: date
    detected_at: datetime
    status: str
