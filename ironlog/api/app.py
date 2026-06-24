"""
app.py — a small FastAPI surface over the engine.

Run it from the repo root (after seeding):

    uvicorn ironlog.api.app:app --reload

Then open http://127.0.0.1:8000/docs for the interactive API.
These few routes show the pattern; the full route set grows from here.
"""
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from ..db import engine
from ..engine import next_set_load
from ..models import BandPair, Equipment, FeedbackTap, Movement, Phase, PhasePolicy

app = FastAPI(title="IronLog V2", version="0.1.0")


def get_session():
    with Session(engine) as session:
        yield session


@app.get("/movements", response_model=List[Movement])
def list_movements(session: Session = Depends(get_session)):
    return session.exec(select(Movement)).all()


@app.get("/movements/{movement_id}", response_model=Movement)
def get_movement(movement_id: int, session: Session = Depends(get_session)):
    m = session.get(Movement, movement_id)
    if not m:
        raise HTTPException(404, "movement not found")
    return m


@app.get("/phase-policy/{phase}", response_model=PhasePolicy)
def get_phase_policy(phase: Phase, session: Session = Depends(get_session)):
    p = session.exec(select(PhasePolicy).where(PhasePolicy.phase == phase)).first()
    if not p:
        raise HTTPException(404, "phase policy not found")
    return p


@app.get("/bands/usable", response_model=List[BandPair])
def usable_bands(session: Session = Depends(get_session)):
    return session.exec(select(BandPair).where(BandPair.usable == True)).all()  # noqa: E712


class NextSetRequest(BaseModel):
    movement_id: int
    current_load: float
    tap: FeedbackTap
    tier: int = 0


class NextSetResponse(BaseModel):
    suggested_load: float


@app.post("/autoregulate/next-set", response_model=NextSetResponse)
def autoregulate_next_set(req: NextSetRequest, session: Session = Depends(get_session)):
    """The between-set loop: given the tap on a working set, suggest the next
    set's load — grid-aligned to the equipment, clamped to floor and cap."""
    m = session.get(Movement, req.movement_id)
    if not m:
        raise HTTPException(404, "movement not found")
    eq: Optional[Equipment] = session.get(Equipment, m.load_equipment_id) if m.load_equipment_id else None
    step = m.min_step or (eq.min_step if eq else 2.5) or 2.5
    floor = m.load_floor if m.load_floor is not None else (eq.load_floor if eq else None)
    suggested = next_set_load(
        current_load=req.current_load, tap=req.tap, ladder=m.increment_ladder,
        tier=req.tier, floor=floor, step=step, cap=m.cap)
    return NextSetResponse(suggested_load=suggested)
