from datetime import date, datetime, time, timedelta
from typing import Optional

from sqlmodel import Session, select

from ..models.library import EngineState
from ..models.program import MissedDayRecord, ProgramDay
from ..models.session import Session as WorkoutSession

GRACE_HOURS = 6
OPEN_STATUSES = ("PENDING", "RESCHEDULED", "ACKNOWLEDGED")


def resolve_day_date(day_index: int, week_start: date) -> date:
    """day_index is 1=Mon...7=Sun. week_start must be a Monday. Returns
    the actual calendar date for that day_index within that week."""
    return week_start + timedelta(days=day_index - 1)


def _current_week_start(as_of: date) -> date:
    """Monday of the week containing as_of."""
    return as_of - timedelta(days=as_of.weekday())


def _matching_session_exists(db: Session, day_role: str, week_start: date) -> bool:
    week_end = resolve_day_date(7, week_start)
    session = db.exec(
        select(WorkoutSession).where(
            WorkoutSession.date >= week_start,
            WorkoutSession.date <= week_end,
            WorkoutSession.day_role == day_role,
        )
    ).first()
    return session is not None


def check_missed_days(db: Session, as_of: Optional[datetime] = None) -> dict:
    """Detects newly-missed days for the active program's current week,
    and auto-resolves previously PENDING/RESCHEDULED/ACKNOWLEDGED records
    where a matching session now exists. as_of defaults to
    datetime.utcnow() if not given. Returns a summary dict, e.g.
    {"newly_missed": N, "resolved": N}."""
    as_of = as_of or datetime.utcnow()
    summary = {"newly_missed": 0, "resolved": 0}

    engine_state = db.exec(
        select(EngineState).where(EngineState.id == 1)
    ).one_or_none()
    if engine_state is None or engine_state.active_program_id is None:
        return summary

    week_start = _current_week_start(as_of.date())
    program_days = db.exec(
        select(ProgramDay).where(
            ProgramDay.program_id == engine_state.active_program_id,
            ProgramDay.is_rest == False,  # noqa: E712
        )
    ).all()

    for program_day in program_days:
        day_date = resolve_day_date(program_day.day_index, week_start)
        missed_cutoff = datetime.combine(day_date, time.min) + timedelta(
            days=1,
            hours=GRACE_HOURS,
        )
        if as_of < missed_cutoff:
            continue
        if _matching_session_exists(db, program_day.day_role, week_start):
            continue

        existing = db.exec(
            select(MissedDayRecord).where(
                MissedDayRecord.program_day_id == program_day.id,
                MissedDayRecord.week_start_date == week_start,
            )
        ).first()
        if existing is not None:
            continue

        db.add(MissedDayRecord(
            program_day_id=program_day.id,
            week_start_date=week_start,
            status="PENDING",
        ))
        summary["newly_missed"] += 1

    missed_records = db.exec(
        select(MissedDayRecord).where(
            MissedDayRecord.status.in_(OPEN_STATUSES)
        )
    ).all()
    for record in missed_records:
        program_day = db.get(ProgramDay, record.program_day_id)
        if program_day is None:
            continue
        if not _matching_session_exists(db, program_day.day_role, record.week_start_date):
            continue

        record.status = "RESOLVED"
        record.resolved_at = as_of
        db.add(record)
        summary["resolved"] += 1

    db.commit()
    return summary
