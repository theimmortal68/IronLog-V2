"""
app.py — a small FastAPI surface over the engine.

Run it from the repo root (after seeding):

    uvicorn ironlog.api.app:app --reload

Then open http://127.0.0.1:8000/docs for the interactive API.
These few routes show the pattern; the full route set grows from here.

v0.6 additions (Task 10):
  POST /sessions/{session_id}/log    — guard + run_analysis (idempotency gate b)
  POST /generate                     — §3A conditional gate (gate f), returns candidate
  POST /sessions/{candidate_id}/approve — commit_session (sole current_load writer)

The generate endpoint uses StubProposer for the beta build (LLM adapter is Task 11).
Candidates are stored in a module-level dict (_candidates); cleared on restart.
scope marker on /generate: "main-work-only; warmups/finishers/Z2 per program doc,
not yet in-app".
"""
import uuid as _uuid
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from ..db import engine
from ..engine import next_set_load
from ..models import BandPair, Equipment, FeedbackTap, Movement, Phase, PhasePolicy
from ..persistence.run_analysis import already_analyzed, run_analysis
from ..generation.loop import commit_session, generate_session
from ..generation.skeleton import lay_skeleton
from ..generation.fallback import program_selections
from ..generation.proposer import StubProposer
from ..generation.repair import RepairOutcome

app = FastAPI(title="IronLog V2", version="0.1.0")

# In-memory candidate store (single-server MVP; not shared across restarts).
# Key: candidate_id (UUID str). Value: RepairOutcome from generate_session.
_candidates: Dict[str, RepairOutcome] = {}


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


# ---------------------------------------------------------------------------
# v0.6 generation spine endpoints (Task 10)
# ---------------------------------------------------------------------------

class LogSessionResponse(BaseModel):
    session_id: int
    already_analyzed: bool
    message: str


class GenerateRequest(BaseModel):
    day_role: str


class GenerateResponse(BaseModel):
    candidate_id: str
    day_role: str
    exhausted: bool
    attempts: int
    scope: str


class ApproveResponse(BaseModel):
    session_id: int


def _week_keyer(d):
    """Default week keyer: ISO (year, week_number)."""
    iso = d.isocalendar()
    return (iso[0], iso[1])


@app.post("/sessions/{session_id}/log", response_model=LogSessionResponse)
def log_session(session_id: int, db: Session = Depends(get_session)):
    """Post-session loop: run analysis on a logged session (idempotent).

    Idempotency guard (GATE b): if analysis already ran (E1rmHistory row exists),
    this is a no-op — returns already_analyzed=True without re-running analysis.
    """
    from ..models.session import Session as WorkoutSession
    ws = db.exec(select(WorkoutSession).where(WorkoutSession.id == session_id)).first()
    if ws is None:
        raise HTTPException(404, "session not found")
    if already_analyzed(session_id, db):
        return LogSessionResponse(
            session_id=session_id,
            already_analyzed=True,
            message="already analyzed — no-op",
        )
    run_analysis(session_id, db, _week_keyer)
    return LogSessionResponse(
        session_id=session_id,
        already_analyzed=False,
        message="analysis complete",
    )


@app.post("/generate", response_model=GenerateResponse)
def generate(req: GenerateRequest, db: Session = Depends(get_session)):
    """Generate a session candidate via the §3A conditional gate.

    Quiet week (no deviation signals) → deterministic program emission; no LLM call.
    Signal present → StubProposer (beta; real LLM adapter is Task 11).
    Candidate stored in _candidates[candidate_id]; nothing written to DB until approve.
    Regenerate = call this endpoint again (returns a new candidate_id).

    The 'scope' marker is always present: main-work only; warmups/finishers/Z2
    are per program doc and not yet in-app (deferred to v0.7).
    """
    sk = lay_skeleton(req.day_role, db)
    proposer = StubProposer(program_selections(sk))
    outcome = generate_session(req.day_role, db, proposer, _week_keyer)
    candidate_id = str(_uuid.uuid4())
    _candidates[candidate_id] = outcome
    return GenerateResponse(
        candidate_id=candidate_id,
        day_role=req.day_role,
        exhausted=outcome.exhausted,
        attempts=outcome.attempts,
        scope=(
            "main-work-only; warmups/finishers/Z2 per program doc, not yet in-app"
        ),
    )


@app.post("/sessions/{candidate_id}/approve", response_model=ApproveResponse)
def approve_session(candidate_id: str, db: Session = Depends(get_session)):
    """Approve a generated candidate: commit_session writes it to the DB.

    commit_session is the SOLE writer of current_load (Fork 7c / two-writer boundary).
    The candidate is consumed from _candidates on approval (one approval per candidate).
    """
    outcome = _candidates.pop(candidate_id, None)
    if outcome is None:
        raise HTTPException(404, "candidate not found — call /generate first")
    if outcome.assembled is None:
        raise HTTPException(422, "candidate is exhausted — no valid session assembled")
    committed = commit_session(
        outcome.assembled,
        db,
        approval_mode="human",
        prompt=outcome.prompt or {},
        selections_dict=outcome.selections_dict or {},
        clamps=outcome.clamps or [],
        repairs=outcome.rejections,
        fallback_used=outcome.exhausted,
    )
    return ApproveResponse(session_id=committed.id)
