from datetime import date
from sqlmodel import SQLModel, Session as DBSession, create_engine
import pytest

from ironlog.models.program import Program, ProgramDay, Tier, TierExercise, TierKind
from ironlog.models.library import Movement
from ironlog.models.session import Note, Session as WorkoutSession
from ironlog.notes.apply import resolve_slot, SlotResolutionError, AmbiguousSlotError
import ironlog.models


def _engine():
    e = create_engine("sqlite://")
    SQLModel.metadata.create_all(e)
    return e


def _program_with_bench_slot(db):
    prog = Program(name="Phase 1", phase="P1", duration_weeks=4); db.add(prog); db.commit(); db.refresh(prog)
    day = ProgramDay(program_id=prog.id, day_index=0, day_role="D1 Upper Push")
    db.add(day); db.commit(); db.refresh(day)
    tier = Tier(program_day_id=day.id, tier_label="T1", tier_order=1, tier_kind=TierKind.T1_STRAIGHT)
    db.add(tier); db.commit(); db.refresh(tier)
    bench = Movement(name="Bench Press [PB]", base_name="Bench Press"); db.add(bench); db.commit(); db.refresh(bench)
    te = TierExercise(tier_id=tier.id, slot_id="d1_t1", movement_id=bench.id, exercise_order=1, tier_role="anchor")
    db.add(te); db.commit(); db.refresh(te)
    return te, bench


def _note(db, movement_id, day_role="D1 Upper Push"):
    ws = WorkoutSession(date=date(2026, 7, 1), day_role=day_role, phase="P1")
    db.add(ws); db.commit(); db.refresh(ws)
    n = Note(session_id=ws.id, movement_id=movement_id, text="switch to incline")
    db.add(n); db.commit(); db.refresh(n)
    return n


def test_resolve_slot_finds_the_tier_exercise():
    db = DBSession(_engine())
    te, bench = _program_with_bench_slot(db)
    n = _note(db, bench.id)
    assert resolve_slot(n, db).id == te.id


def test_resolve_slot_no_match_raises():
    db = DBSession(_engine())
    _program_with_bench_slot(db)
    n = _note(db, movement_id=99999)
    with pytest.raises(SlotResolutionError):
        resolve_slot(n, db)


def test_resolve_slot_ambiguous_raises():
    db = DBSession(_engine())
    te, bench = _program_with_bench_slot(db)
    # a second TierExercise in the same day with the same movement
    tier2 = db.get(Tier, te.tier_id)
    db.add(TierExercise(tier_id=tier2.id, slot_id="d1_t1b", movement_id=bench.id, exercise_order=2, tier_role="semi"))
    db.commit()
    n = _note(db, bench.id)
    with pytest.raises(AmbiguousSlotError):
        resolve_slot(n, db)
