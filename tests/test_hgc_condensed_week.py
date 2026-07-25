"""tests/test_hgc_condensed_week.py"""
from sqlmodel import select
from ironlog.models.session import Session as LogSession
from scripts.build_hgc_condensed_week import apply, MINI_SESSIONS
import pytest

def test_hgc_condensed_week_creates_sessions(gen_db_calibrated):
    # Apply script
    apply(gen_db_calibrated)
    
    # Verify sessions
    sessions = gen_db_calibrated.exec(
        select(LogSession).where(LogSession.rationale.startswith("HGC condensed week")).order_by(LogSession.id)
    ).all()
    
    assert len(sessions) == 11
    
    for idx, (expected_date, expected_role, expected_movements) in enumerate(MINI_SESSIONS):
        s = sessions[idx]
        assert s.date == expected_date
        assert s.day_role == expected_role
        assert len(s.groups) == 1
        
        exercises = s.groups[0].exercises
        assert len(exercises) == len(expected_movements)
        
        for ex in exercises:
            assert len(ex.planned_sets) > 0

def test_hgc_condensed_week_is_idempotent(gen_db_calibrated):
    apply(gen_db_calibrated)
    sessions1 = gen_db_calibrated.exec(
        select(LogSession).where(LogSession.rationale.startswith("HGC condensed week"))
    ).all()
    assert len(sessions1) == 11
    
    apply(gen_db_calibrated)
    sessions2 = gen_db_calibrated.exec(
        select(LogSession).where(LogSession.rationale.startswith("HGC condensed week"))
    ).all()
    assert len(sessions2) == 11
