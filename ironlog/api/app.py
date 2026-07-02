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
import os
import uuid as _uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from ..db import engine
from ..engine import next_set_load
from ..models import (
    BandPair, Equipment, FeedbackTap, Movement, NoteClass, Phase, PhasePolicy,
    SessionStatus, SetLog, ExerciseSurvey, Note, SetRole,
)
from .schemas_capture import (SubmitRequest, SubmitResponse,
                               SessionDetailResponse, GroupOut, ExerciseOut, PlannedSetOut)
from .schemas_wizard import (
    StartProgramResponse, WizardMovement, WizardResolveRequest,
    WizardResolveResponse, WizardStateResponse,
)
from ..persistence.run_analysis import already_analyzed, run_analysis
from ..generation.loop import commit_session, generate_session
from ..generation.load_trust import compute_load_trust, load_field_for_mode
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


def _make_proposer(sk):
    """Proposer factory: live GeminiProposer when configured, else deterministic Stub.

    Selection logic (graceful — never crashes):
      - IRONLOG_FORCE_STUB set  -> StubProposer (kill-switch, always wins).
      - GEMINI_API_KEY set AND httpx importable -> GeminiProposer (reads the key
        from env).  A live propose failure degrades to fallback in repair.py, never
        a 500.
      - otherwise (no key, or httpx missing) -> StubProposer (the deterministic
        program prior).

    GeminiProposer + httpx are imported lazily so app import never requires httpx.
    """
    if os.environ.get("IRONLOG_FORCE_STUB"):
        return StubProposer(program_selections(sk))
    if os.environ.get("GEMINI_API_KEY"):
        try:
            import httpx  # noqa: F401, PLC0415
        except ImportError:
            return StubProposer(program_selections(sk))
        from ..generation.gemini import GeminiProposer  # lazy: app import stays httpx-free
        return GeminiProposer()
    return StubProposer(program_selections(sk))


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
    proposer = _make_proposer(sk)
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


# ---------------------------------------------------------------------------
# Capture write path (logging round-trip)
# ---------------------------------------------------------------------------

_TAP_REQUIRED_ROLES = {SetRole.WORKING, SetRole.TOP, SetRole.BACKOFF}


@app.post("/sessions/{session_id}/submit", response_model=SubmitResponse)
def submit_session(session_id: int, req: SubmitRequest, db: Session = Depends(get_session)):
    """Atomic offline-batch completion: validate taps -> write SetLogs/surveys/
    notes -> PLANNED->COMPLETED -> run_analysis. Idempotent on session_id."""
    from ..models.session import Session as WorkoutSession
    ws = db.get(WorkoutSession, session_id)
    if ws is None:
        raise HTTPException(404, "session not found")

    # Idempotency (lost-ack retry is the norm): already COMPLETED -> complete no-op.
    if ws.status == SessionStatus.COMPLETED:
        existing = db.exec(select(SetLog).where(SetLog.session_id == session_id)).all()
        return SubmitResponse(session_id=session_id, status=ws.status.value,
                              set_logs_written=len(existing), already_completed=True)

    # Validate mandatory tap on working sets BEFORE any write.
    for sl in req.set_logs:
        if sl.set_role in {r.value for r in _TAP_REQUIRED_ROLES} and sl.feedback_tap is None:
            raise HTTPException(422, f"working set (role={sl.set_role}, index={sl.set_index}) "
                                     "missing feedback_tap")

    for sl in req.set_logs:
        db.add(SetLog(
            planned_set_id=sl.planned_set_id, session_id=session_id,
            movement_id=sl.movement_id, set_index=sl.set_index,
            actual_load=sl.actual_load, actual_reps=sl.actual_reps,
            feedback_tap=FeedbackTap(sl.feedback_tap) if sl.feedback_tap is not None else None,
            rpe_numeric=sl.rpe_numeric,
            is_warmup=sl.is_warmup,
            actual_unassisted_reps=sl.actual_unassisted_reps,
            actual_assisted_reps=sl.actual_assisted_reps,
            actual_plates=sl.actual_plates, band_pair_id=sl.band_pair_id,
            felt_peak=sl.felt_peak,
        ))
    for sv in req.surveys:
        db.add(ExerciseSurvey(session_id=session_id, movement_id=sv.movement_id,
                              sticking_point=sv.sticking_point,
                              asymmetry_flag=sv.asymmetry_flag,
                              technique_flag=sv.technique_flag))
    for nt in req.notes:
        db.add(Note(session_id=session_id, movement_id=nt.movement_id, text=nt.text,
                    classification=NoteClass.JOURNAL, confirmed=False, applied=False))

    ws.status = SessionStatus.COMPLETED
    db.add(ws)
    db.commit()

    # Fire the analyze-at-log seam (v0.6 two-writer boundary: run_analysis owns
    # current_load; this handler never writes it).  The SetLog write committed
    # above — a run_analysis failure does NOT lose the logged workout (the write
    # is already durable; /log can re-run analysis idempotently via analyzed_at).
    # In production EngineState + MovementState always exist; if run_analysis
    # genuinely fails here that should surface as a real error, not a silent 200.
    run_analysis(session_id, db, _week_keyer)

    written = len(db.exec(select(SetLog).where(SetLog.session_id == session_id)).all())
    return SubmitResponse(session_id=session_id, status=SessionStatus.COMPLETED.value,
                          set_logs_written=written, already_completed=False)


# ---------------------------------------------------------------------------
# Capture read path (logging round-trip — Task 3)
# ---------------------------------------------------------------------------

def _serialize_session(ws, db) -> SessionDetailResponse:
    from ..models.session import Session as WorkoutSession  # noqa
    groups_out = []
    groups = sorted(ws.groups, key=lambda g: g.order_index)
    for g in groups:
        ex_out = []
        for pe in sorted(g.exercises, key=lambda e: e.order_index):
            mv = db.get(Movement, pe.movement_id)
            sets_out = [PlannedSetOut(
                id=ps.id, set_index=ps.set_index, set_role=ps.set_role.value,
                is_warmup=ps.is_warmup, target_load=ps.target_load,
                target_reps_low=ps.target_reps_low, target_reps_high=ps.target_reps_high,
                target_rpe=ps.target_rpe, target_unassisted_reps=ps.target_unassisted_reps,
                target_assisted_reps=ps.target_assisted_reps, target_plates=ps.target_plates,
                band_pair_id=ps.band_pair_id, target_felt_peak=ps.target_felt_peak,
            ) for ps in sorted(pe.planned_sets, key=lambda x: x.set_index)]
            ex_out.append(ExerciseOut(
                id=pe.id, movement_id=pe.movement_id,
                movement_name=(mv.name if mv else ""), order_index=pe.order_index,
                scheme=pe.scheme.value, objective=pe.objective.value,
                unilateral=(mv.unilateral if mv else False),
                planned_sets=sets_out,
            ))
        groups_out.append(GroupOut(
            id=g.id, order_index=g.order_index, group_type=g.group_type.value,
            rounds=g.rounds, rest_seconds=g.rest_seconds, label=g.label, exercises=ex_out,
        ))
    return SessionDetailResponse(
        id=ws.id, date=ws.date.isoformat(), day_role=ws.day_role, phase=ws.phase,
        status=ws.status.value, groups=groups_out,
    )


@app.get("/sessions/today", response_model=Optional[SessionDetailResponse])
def get_today_session(db: Session = Depends(get_session)):
    """Most-recently-approved PLANNED, unanalyzed session (greatest id). null if none."""
    from ..models.session import Session as WorkoutSession
    ws = db.exec(
        select(WorkoutSession)
        .where(WorkoutSession.status == SessionStatus.PLANNED)
        .where(WorkoutSession.analyzed_at.is_(None))
        .order_by(WorkoutSession.id.desc())
    ).first()
    return _serialize_session(ws, db) if ws else None


@app.get("/sessions/{session_id}", response_model=SessionDetailResponse)
def get_session_detail(session_id: int, db: Session = Depends(get_session)):
    from ..models.session import Session as WorkoutSession
    ws = db.get(WorkoutSession, session_id)
    if ws is None:
        raise HTTPException(404, "session not found")
    return _serialize_session(ws, db)


# ---------------------------------------------------------------------------
# Wizard read path (load-config state — first-run wizard, Task 4)
# ---------------------------------------------------------------------------

_UNIT_HINTS = {"current_load": "lb", "assist_level": "assist"}


def _program_movement_ids(program_id: int, db: Session) -> set:
    """Distinct movement ids the program references (TierExercises across its
    ProgramDays->Tiers, plus any MesoRotation overrides). The single enumeration
    the wizard-state read, the resolve write, and the start gate all share."""
    from ..models.program import MesoRotation, ProgramDay, Tier, TierExercise

    day_ids = db.exec(
        select(ProgramDay.id).where(ProgramDay.program_id == program_id)
    ).all()
    tier_ids = db.exec(
        select(Tier.id).where(Tier.program_day_id.in_(day_ids))
    ).all() if day_ids else []
    te_rows = db.exec(
        select(TierExercise.id, TierExercise.movement_id)
        .where(TierExercise.tier_id.in_(tier_ids))
    ).all() if tier_ids else []
    te_ids = [te_id for te_id, _ in te_rows]

    movement_ids = {mv_id for _, mv_id in te_rows}
    if te_ids:
        movement_ids.update(db.exec(
            select(MesoRotation.movement_id)
            .where(MesoRotation.tier_exercise_id.in_(te_ids))
        ).all())
    return movement_ids


def _needs_attention_count(program_id: int, db: Session, now: datetime) -> int:
    """UNKNOWN + STALE over the program's load-bearing movements, derived via the
    SHARED compute_load_trust. Bodyweight (load_field None) never counts. This IS
    the completion gate's predicate AND the wizard-state counter — one function,
    so the read surface, the write surface, and the gate cannot disagree."""
    from ..models.library import MovementState

    count = 0
    for mv_id in _program_movement_ids(program_id, db):
        mv = db.get(Movement, mv_id)
        if mv is None:
            continue
        state = db.exec(
            select(MovementState).where(MovementState.movement_id == mv_id)
        ).first()
        r = compute_load_trust(mv, state, db, as_of=now)
        if r.load_field is None:          # bodyweight — no load, never asked
            continue
        if r.trust.value in ("UNKNOWN", "STALE"):
            count += 1
    return count


@app.get("/programs/{program_id}/wizard-state", response_model=WizardStateResponse)
def get_wizard_state(program_id: int, db: Session = Depends(get_session)):
    """Render compute_load_trust per program movement (the wizard read surface).

    Enumerates the program's distinct movements (TierExercises + MesoRotations
    across all its ProgramDays->Tiers), derives trust via the SHARED
    compute_load_trust (the spine — wizard and generation cannot disagree), and
    EXCLUDES bodyweight movements (load_field None — no load to ever ask for).
    needs_attention_count = UNKNOWN + STALE; ready_to_start when that is zero.
    Read-only: no DB writes.
    """
    from datetime import datetime

    from ..models.library import MovementState
    from ..models.program import Program

    program = db.get(Program, program_id)
    if program is None:
        raise HTTPException(404, "program not found")

    now = datetime.utcnow()
    movements: List[WizardMovement] = []
    for mv_id in sorted(_program_movement_ids(program_id, db)):
        mv = db.get(Movement, mv_id)
        if mv is None:
            continue
        state = db.exec(
            select(MovementState).where(MovementState.movement_id == mv_id)
        ).first()
        r = compute_load_trust(mv, state, db, as_of=now)
        if r.load_field is None:          # bodyweight — no load, never asked
            continue
        movements.append(WizardMovement(
            movement_id=mv_id,
            movement_name=mv.name,
            load_field=r.load_field,
            trust=r.trust.value,
            prefill_value=r.value,
            unit_hint=_UNIT_HINTS.get(r.load_field),
        ))

    # Count-predicate single-sourced: the needs-attention count comes from the
    # SHARED _needs_attention_count helper, not a local copy of the predicate.
    needs_attention_count = _needs_attention_count(program_id, db, now)
    return WizardStateResponse(
        program_id=program.id,
        program_name=program.name,
        needs_attention_count=needs_attention_count,
        ready_to_start=(needs_attention_count == 0),
        movements=movements,
    )


@app.post("/programs/{program_id}/wizard-resolve", response_model=WizardResolveResponse)
def resolve_wizard(program_id: int, req: WizardResolveRequest,
                   db: Session = Depends(get_session)):
    """Batch-write the resolved loads (the wizard's WRITE surface).

    For each WizardResolution: write the CANONICAL load field — load_field_for_mode
    picks current_load (LADDER/COMPOSITE) vs assist_level (ASSISTED) — and stamp
    confirmed_at = now. The §7.3 honesty pin: stamp confirmed_at ONLY on the
    movements in `resolutions` (the ones actually vouched for) — untouched-FRESH
    movements keep their existing confirmed_at. Two-writer boundary: writes ONLY
    the load field + confirmed_at; never e1rm/calibration_status/counters. Then
    recompute needs_attention via the SHARED compute_load_trust.
    """
    from datetime import datetime

    from ..models.library import MovementState
    from ..models.program import Program

    program = db.get(Program, program_id)
    if program is None:
        raise HTTPException(404, "program not found")

    now = datetime.utcnow()
    resolved = 0
    for res in req.resolutions:
        movement = db.get(Movement, res.movement_id)
        if movement is None:
            raise HTTPException(404, f"movement {res.movement_id} not found")
        field = load_field_for_mode(movement.progression_mode)
        if field is None:                 # bodyweight — nothing to set
            continue
        state = db.exec(
            select(MovementState).where(MovementState.movement_id == res.movement_id)
        ).first()
        if state is None:                 # get-or-create per resolved movement
            state = MovementState(movement_id=res.movement_id)
            db.add(state)
        setattr(state, field, res.value)  # write ONLY the canonical load field …
        state.confirmed_at = now          # … + the confirmation event-fact
        resolved += 1

    db.commit()

    needs_attention = _needs_attention_count(program_id, db, datetime.utcnow())
    return WizardResolveResponse(
        resolved=resolved,
        needs_attention_count=needs_attention,
        ready_to_start=(needs_attention == 0),
    )


@app.post("/programs/{program_id}/start", response_model=StartProgramResponse)
def start_program(program_id: int, db: Session = Depends(get_session)):
    """The completion gate + activation. Refuses (started=false, active=false) while
    any program movement is UNKNOWN/STALE (needs_attention_count > 0). When the gate
    clears, set EngineState.active_program_id (the single-active pointer) +
    Program.started_at (event-fact), and report active=true. The gate predicate is
    the SAME compute_load_trust the wizard renders — finishing the wizard guarantees
    a clean start by construction (§7.6)."""
    from datetime import datetime

    from ..models.library import EngineState
    from ..models.program import Program

    program = db.get(Program, program_id)
    if program is None:
        raise HTTPException(404, "program not found")

    now = datetime.utcnow()
    if _needs_attention_count(program_id, db, now) > 0:
        return StartProgramResponse(program_id=program_id, started=False, active=False)

    es = db.get(EngineState, 1)
    if es is None:                        # singleton get-or-create
        es = EngineState(id=1)
        db.add(es)
    es.active_program_id = program_id
    program.started_at = now
    db.commit()

    return StartProgramResponse(program_id=program_id, started=True, active=True)
