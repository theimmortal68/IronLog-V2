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
scope marker on /generate: "main-work-only; warmups/Z2 per program doc,
not yet in-app".
"""
import logging
import os
import uuid as _uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlmodel import Session, col, select

from ..db import engine
from ..engine import next_set_load
from ..integrations.withings import sync_withings_measurements
from ..integrations.withings_auth import build_authorize_url, exchange_code_for_tokens
from ..models import (
    BandPair, Equipment, FeedbackTap, Movement, NoteClass, Phase, PhasePolicy,
    SessionStatus, SetLog, ExerciseSurvey, Note, SetRole,
)
from ..notes.classify import classify_session_notes
from .schemas_capture import (SubmitRequest, SubmitResponse,
                               SessionDetailResponse, GroupOut, ExerciseOut, PlannedSetOut)
from .schemas_wizard import (
    StartProgramResponse, WizardMovement, WizardResolveRequest,
    WizardResolveResponse, WizardStateResponse,
)
from .schemas_readiness import DailyReadinessIn, DailyReadinessOut, ConfirmPhaseRequest
from ..models.library import EngineState, DailyReadiness, WithingsCredentials
from ..persistence.ht_refine import refine_from_logged_ht
from ..persistence.run_analysis import already_analyzed, run_analysis
from ..generation.assembler import build_finisher_payload, build_warmup_payload
from ..generation.loop import commit_session, generate_session
from ..generation.load_trust import compute_load_trust, load_field_for_mode
from ..generation.skeleton import lay_skeleton
from ..generation.fallback import program_selections
from ..generation.proposer import StubProposer
from ..generation.repair import RepairOutcome
from ..notes.resolver import resolve_note

app = FastAPI(title="IronLog V2", version="0.1.0")
logger = logging.getLogger(__name__)

# In-memory candidate store (single-server MVP; not shared across restarts).
# Key: candidate_id (UUID str). Value: RepairOutcome from generate_session.
_candidates: Dict[str, RepairOutcome] = {}


def get_session():
    with Session(engine) as session:
        yield session


async def _sync_withings_measurements_background():
    try:
        from ..db import engine as db_engine

        with Session(db_engine) as db:
            await sync_withings_measurements(db)
    except Exception:
        logger.exception("Withings background sync failed")


@app.post("/integrations/withings/webhook")
async def withings_webhook(
    background_tasks: BackgroundTasks,
    userid: str = Form(...),
    appli: str = Form(...),
):
    """Withings POSTs application/x-www-form-urlencoded {userid, appli}
    on a measurement notification. The payload is only a change signal;
    it is not trusted as data. Schedule a sync and acknowledge
    immediately."""
    background_tasks.add_task(_sync_withings_measurements_background)
    return {"status": "accepted"}


@app.post("/integrations/withings/sync-now")
async def withings_sync_now(db: Session = Depends(get_session)):
    """Manual on-demand Withings sync. Returns the sync summary directly."""
    try:
        return await sync_withings_measurements(db)
    except RuntimeError as exc:
        status_code = 400 if "not yet authorized" in str(exc) else 502
        raise HTTPException(status_code, str(exc)) from exc


@app.get("/integrations/withings/authorize")
def withings_authorize():
    client_id = os.environ.get("WITHINGS_CLIENT_ID")
    redirect_uri = os.environ.get("WITHINGS_REDIRECT_URI")
    if not client_id:
        raise HTTPException(500, "WITHINGS_CLIENT_ID is not configured")
    if not redirect_uri:
        raise HTTPException(500, "WITHINGS_REDIRECT_URI is not configured")
    url = build_authorize_url(client_id, redirect_uri, _uuid.uuid4().hex)
    return RedirectResponse(url)


@app.get("/integrations/withings/callback")
async def withings_callback(code: str, db: Session = Depends(get_session)):
    client_id = os.environ.get("WITHINGS_CLIENT_ID")
    client_secret = os.environ.get("WITHINGS_CLIENT_SECRET")
    redirect_uri = os.environ.get("WITHINGS_REDIRECT_URI")
    missing = [
        name for name, value in (
            ("WITHINGS_CLIENT_ID", client_id),
            ("WITHINGS_CLIENT_SECRET", client_secret),
            ("WITHINGS_REDIRECT_URI", redirect_uri),
        )
        if not value
    ]
    if missing:
        raise HTTPException(
            500,
            "Withings OAuth is not configured: " + ", ".join(missing),
        )

    try:
        tokens = await exchange_code_for_tokens(
            client_id,
            client_secret,
            code,
            redirect_uri,
        )
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc

    now = datetime.utcnow()
    token_expires_at = now + timedelta(seconds=int(tokens["expires_in"]))
    credentials = db.get(WithingsCredentials, 1)
    if credentials is None:
        credentials = WithingsCredentials(
            id=1,
            access_token=tokens["access_token"],
            refresh_token=tokens["refresh_token"],
            token_expires_at=token_expires_at,
            updated_at=now,
        )
    else:
        credentials.access_token = tokens["access_token"]
        credentials.refresh_token = tokens["refresh_token"]
        credentials.token_expires_at = token_expires_at
        credentials.updated_at = now

    db.add(credentials)
    db.commit()
    return {"status": "authorized"}


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
    preview: Optional[SessionDetailResponse] = None


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

    The 'scope' marker is always present: main-work only; warmups/Z2
    are per program doc and not yet in-app (deferred to v0.7).
    """
    sk = lay_skeleton(req.day_role, db)
    proposer = _make_proposer(sk)
    outcome = generate_session(req.day_role, db, proposer, _week_keyer)
    candidate_id = str(_uuid.uuid4())
    _candidates[candidate_id] = outcome
    preview = None
    if outcome.assembled is not None:
        preview = _serialize_session(
            outcome.assembled.session,
            db,
            warmup=outcome.assembled.warmup,
            finisher=outcome.assembled.finisher,
        )
    return GenerateResponse(
        candidate_id=candidate_id,
        day_role=req.day_role,
        exhausted=outcome.exhausted,
        attempts=outcome.attempts,
        scope=(
            "main-work-only; warmups/Z2 per program doc, not yet in-app"
        ),
        preview=preview,
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
def submit_session(session_id: int, req: SubmitRequest, background_tasks: BackgroundTasks,
                   db: Session = Depends(get_session)):
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

    # Single-band HT felt-peak refinement (Task 5): a logged HT set that used
    # exactly one band is a clean signal for that band's true peak resistance.
    # Runs after the SetLog write above so it sees this session's felt_peak
    # rows; independent of run_analysis (BandPair.peak_lb is inventory
    # calibration, not current_load/ht_plates/ht_band_config).
    refine_from_logged_ht(session_id, db)
    result = run_analysis(session_id, db, _week_keyer)
    
    available_phase = result.phase_transition_available.value if result.phase_transition_available else None
    
    # Store it in EngineState so the confirm-phase endpoint can validate against it
    engine_state = db.exec(select(EngineState)).one()
    engine_state.pending_phase_transition = available_phase
    db.add(engine_state)
    db.commit()

    background_tasks.add_task(classify_session_notes, session_id)

    written = len(db.exec(select(SetLog).where(SetLog.session_id == session_id)).all())
    return SubmitResponse(
        session_id=session_id, 
        status=SessionStatus.COMPLETED.value,
        set_logs_written=written, 
        already_completed=False,
        phase_transition_available=available_phase,
    )


# ---------------------------------------------------------------------------
# Notes review path (note-confirm, Task 3)
# ---------------------------------------------------------------------------

class ProposalOut(BaseModel):
    tier_exercise_id: int
    day_role: str
    slot_label: str
    override_type: str
    override_movement_id: Optional[int] = None
    load_delta: Optional[float] = None
    load_absolute: Optional[float] = None
    rep_low: Optional[int] = None
    rep_high: Optional[int] = None
    override_order: Optional[float] = None
    valid: bool = True
    validation_note: Optional[str] = None
    summary: str = ""


class NoteReviewOut(BaseModel):
    id: int
    session_id: Optional[int] = None
    movement_id: Optional[int] = None
    created_at: str
    text: str
    classification: str
    proposed_change: Optional[dict] = None
    confidence: Optional[float] = None
    action_type: Optional[str] = None
    resolved_proposals: List[ProposalOut] = []


@app.get("/notes/review", response_model=List[NoteReviewOut])
def get_notes_review(db: Session = Depends(get_session)):
    """Unconfirmed change-proposals (CONFIG_CHANGE / PROGRAMMING_REQUEST), newest first."""
    from ..models.session import Note
    rows = db.exec(
        select(Note).where(
            Note.confirmed == False,  # noqa: E712
            col(Note.classification).in_([NoteClass.CONFIG_CHANGE, NoteClass.PROGRAMMING_REQUEST]),
        ).order_by(col(Note.id).desc())
    ).all()
    out = []
    for n in rows:
        meta = n.classification_meta or {}
        proposed_change = meta.get("proposed_change")
        action_type = meta.get("action_type")
        resolved_proposals = []
        if (
            n.classification in (NoteClass.CONFIG_CHANGE, NoteClass.PROGRAMMING_REQUEST)
            and action_type is not None
            and action_type != "OTHER"
            and proposed_change is not None
        ):
            try:
                resolved_proposals = [
                    ProposalOut(**vars(proposal))
                    for proposal in resolve_note(n, db)
                ]
            except Exception:
                resolved_proposals = []
        out.append(NoteReviewOut(
            id=n.id, session_id=n.session_id, movement_id=n.movement_id,
            created_at=n.created_at.isoformat(), text=n.text,
            classification=n.classification.value,
            proposed_change=proposed_change, confidence=meta.get("confidence"),
            action_type=action_type, resolved_proposals=resolved_proposals))
    return out


@app.post("/notes/{note_id}/confirm")
def confirm_note(note_id: int, db: Session = Depends(get_session)):
    from ..models.session import Note
    n = db.get(Note, note_id)
    if n is None:
        raise HTTPException(404, "note not found")
    n.confirmed = True
    # applied=True too: confirm is a terminal action (like apply/dismiss). Without
    # it the note leaves the /notes/review inbox (filtered on confirmed==False) but
    # stays applied==False, which context.py keys on to flag the movement to the
    # proposer forever. All three terminal actions resolve the note → stop flagging.
    n.applied = True
    db.add(n); db.commit()
    return {"id": note_id, "confirmed": True}


@app.post("/notes/{note_id}/dismiss")
def dismiss_note(note_id: int, db: Session = Depends(get_session)):
    from ..models.session import Note
    n = db.get(Note, note_id)
    if n is None:
        raise HTTPException(404, "note not found")
    n.classification = NoteClass.JOURNAL
    n.applied = True
    db.add(n); db.commit()
    return {"id": note_id, "dismissed": True}


# ---------------------------------------------------------------------------
# Note-apply path (live-state slot override — Task 3)
# ---------------------------------------------------------------------------

class ApplyNoteRequest(BaseModel):
    tier_exercise_id: int
    override_type: str
    override_movement_id: Optional[int] = None
    load_delta: Optional[float] = None
    load_absolute: Optional[float] = None
    rep_low: Optional[int] = None
    rep_high: Optional[int] = None
    override_order: Optional[float] = None


@app.post("/notes/{note_id}/apply")
def apply_note(note_id: int, req: ApplyNoteRequest, db: Session = Depends(get_session)):
    from ..models.session import Note
    from ..notes.apply import apply_override, SlotResolutionError
    n = db.get(Note, note_id)
    if n is None:
        raise HTTPException(404, "note not found")
    try:
        ov = apply_override(
            n, req.tier_exercise_id, req.override_type, db,
            override_movement_id=req.override_movement_id,
            load_delta=req.load_delta, load_absolute=req.load_absolute,
            rep_low=req.rep_low, rep_high=req.rep_high,
            override_order=req.override_order)
    except SlotResolutionError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": ov.id, "tier_exercise_id": ov.tier_exercise_id,
            "override_type": ov.override_type.value,
            "override_movement_id": ov.override_movement_id,
            "load_delta": ov.load_delta, "load_absolute": ov.load_absolute,
            "rep_low": ov.rep_low, "rep_high": ov.rep_high,
            "override_order": ov.override_order, "note_id": note_id}


@app.get("/programs/{program_id}/slots")
def get_program_slots(program_id: int, db: Session = Depends(get_session)):
    """List the program's exercise slots (day/tier/movement/current rep target)
    — the read surface the client uses to send an explicit apply target instead
    of relying on the server to infer a slot from a note."""
    from ..models.program import ProgramDay, Tier, TierExercise

    day_ids = db.exec(
        select(ProgramDay.id).where(ProgramDay.program_id == program_id)
    ).all()
    out = []
    for day_id in day_ids:
        day = db.get(ProgramDay, day_id)
        tiers = db.exec(select(Tier).where(Tier.program_day_id == day_id)).all()
        for tier in tiers:
            tes = db.exec(
                select(TierExercise).where(TierExercise.tier_id == tier.id)
                .order_by(TierExercise.exercise_order)
            ).all()
            for te in tes:
                mv = db.get(Movement, te.movement_id)
                out.append({
                    "tier_exercise_id": te.id,
                    "slot_id": te.slot_id,
                    "day_role": day.day_role,
                    "tier_label": tier.tier_label,
                    "movement_id": te.movement_id,
                    "movement_name": (mv.name if mv else None),
                    "current_rep_low": te.rep_low,
                    "current_rep_high": te.rep_high,
                })
    return out


@app.get("/overrides")
def list_overrides(db: Session = Depends(get_session)):
    from ..models.program import SlotMovementOverride, TierExercise, Tier, ProgramDay
    from ..models.enums import OverrideType
    rows = db.exec(select(SlotMovementOverride).where(
        SlotMovementOverride.active == True).order_by(SlotMovementOverride.id.desc())).all()  # noqa: E712
    out = []
    for ov in rows:
        te = db.get(TierExercise, ov.tier_exercise_id)
        tier = db.get(Tier, te.tier_id) if te else None
        day = db.get(ProgramDay, tier.program_day_id) if tier else None
        frm = db.get(Movement, te.movement_id) if te else None
        note = db.get(Note, ov.source_note_id)
        row = {"id": ov.id, "day_role": (day.day_role if day else None),
               "tier_label": (tier.tier_label if tier else None),
               "slot_id": (te.slot_id if te else None),
               "override_type": ov.override_type.value,
               "movement_name": (frm.name if frm else None),
               "source_note_id": ov.source_note_id,
               "source_note_text": (note.text if note else None)}
        if ov.override_type == OverrideType.MOVEMENT:
            to = db.get(Movement, ov.override_movement_id)
            row["to_movement_name"] = (to.name if to else None)
        elif ov.override_type == OverrideType.LOAD:
            row["load_delta"] = ov.load_delta
            row["load_absolute"] = ov.load_absolute
        elif ov.override_type == OverrideType.REPS:
            row["rep_low"] = ov.rep_low
            row["rep_high"] = ov.rep_high
        elif ov.override_type == OverrideType.REORDER:
            row["override_order"] = ov.override_order
        out.append(row)
    return out


@app.post("/overrides/{override_id}/revert")
def revert_override(override_id: int, db: Session = Depends(get_session)):
    from ..models.program import SlotMovementOverride
    ov = db.get(SlotMovementOverride, override_id)
    if ov is None:
        raise HTTPException(404, "override not found")
    ov.active = False
    db.add(ov); db.commit()
    return {"id": override_id, "active": False}


# ---------------------------------------------------------------------------
# Capture read path (logging round-trip — Task 3)
# ---------------------------------------------------------------------------

def _serialize_session(ws, db, finisher=None, warmup=None) -> SessionDetailResponse:
    """Walk the relationship graph and serialize to SessionDetailResponse.

    Also used for in-memory (uncommitted) generate candidates, where ws.id /
    group.id / exercise.id / planned_set.id are all None (pre-commit). In that
    case, assign display-only provisional int ids (unique per set) so the
    response shape matches the committed (real-id) path exactly — the client
    only shows these; it never round-trips them back to the server, since
    approve() re-derives everything from the stored candidate, not the preview.
    """
    from ..models.session import Session as WorkoutSession  # noqa
    _set_counter = [0]

    def _sid(ps):
        if ps.id is not None:
            return ps.id
        _set_counter[0] += 1
        return _set_counter[0]

    groups_out = []
    groups = sorted(ws.groups, key=lambda g: g.order_index)
    for gi, g in enumerate(groups):
        ex_out = []
        for ei, pe in enumerate(sorted(g.exercises, key=lambda e: e.order_index)):
            mv = db.get(Movement, pe.movement_id)
            sets_out = [PlannedSetOut(
                id=_sid(ps), set_index=ps.set_index, set_role=ps.set_role.value,
                is_warmup=ps.is_warmup, target_load=ps.target_load,
                target_reps_low=ps.target_reps_low, target_reps_high=ps.target_reps_high,
                target_rpe=ps.target_rpe, target_unassisted_reps=ps.target_unassisted_reps,
                target_assisted_reps=ps.target_assisted_reps, target_plates=ps.target_plates,
                band_pair_id=ps.band_pair_id, target_felt_peak=ps.target_felt_peak,
                band_config=ps.band_config,
            ) for ps in sorted(pe.planned_sets, key=lambda x: x.set_index)]
            unit_hint = (
                _UNIT_HINTS.get(load_field_for_mode(mv.progression_mode))
                if mv else None
            )
            ex_out.append(ExerciseOut(
                id=(pe.id if pe.id is not None else ei), movement_id=pe.movement_id,
                movement_name=(mv.name if mv else ""), order_index=pe.order_index,
                scheme=pe.scheme.value, objective=pe.objective.value,
                unit_hint=unit_hint,
                unilateral=(mv.unilateral if mv else False),
                planned_sets=sets_out,
            ))
        groups_out.append(GroupOut(
            id=(g.id if g.id is not None else gi), order_index=g.order_index,
            group_type=g.group_type.value, rounds=g.rounds, rest_seconds=g.rest_seconds,
            label=g.label, shoe=g.shoe, exercises=ex_out,
        ))
    return SessionDetailResponse(
        id=(ws.id if ws.id is not None else 0), date=ws.date.isoformat(),
        day_role=ws.day_role, phase=ws.phase, status=ws.status.value, groups=groups_out,
        warmup=warmup,
        finisher=finisher,
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
    if ws is None:
        return None
    program_day_id = (ws.signature or {}).get("program_day_id")
    warmup = (
        build_warmup_payload(db, program_day_id)
        if program_day_id is not None else None
    )
    finisher = (
        build_finisher_payload(db, program_day_id)
        if program_day_id is not None else None
    )
    return _serialize_session(ws, db, warmup=warmup, finisher=finisher)


class SessionSummary(BaseModel):
    id: int
    date: str
    day_role: str
    phase: str
    status: str


@app.get("/sessions", response_model=List[SessionSummary])
def list_sessions(db: Session = Depends(get_session)):
    """Past COMPLETED sessions, newest-first (for History)."""
    from ..models.session import Session as WorkoutSession
    rows = db.exec(
        select(WorkoutSession)
        .where(WorkoutSession.status == SessionStatus.COMPLETED)
        .order_by(WorkoutSession.id.desc())
    ).all()
    return [SessionSummary(
        id=w.id, date=w.date.isoformat(), day_role=w.day_role,
        phase=w.phase, status=w.status.value,
    ) for w in rows]


@app.get("/sessions/{session_id}", response_model=SessionDetailResponse)
def get_session_detail(session_id: int, db: Session = Depends(get_session)):
    from ..models.session import Session as WorkoutSession
    ws = db.get(WorkoutSession, session_id)
    if ws is None:
        raise HTTPException(404, "session not found")
    program_day_id = (ws.signature or {}).get("program_day_id")
    warmup = (
        build_warmup_payload(db, program_day_id)
        if program_day_id is not None else None
    )
    finisher = (
        build_finisher_payload(db, program_day_id)
        if program_day_id is not None else None
    )
    return _serialize_session(ws, db, warmup=warmup, finisher=finisher)


class LoggedSet(BaseModel):
    movement_id: int
    movement_name: str
    set_index: int
    reps: Optional[int] = None
    load: Optional[float] = None
    tap: Optional[str] = None
    is_warmup: bool
    rpe_numeric: Optional[float] = None
    felt_peak: Optional[float] = None


class SurveyOut(BaseModel):
    movement_id: int
    movement_name: str
    asymmetry_flag: Optional[bool] = None
    technique_flag: Optional[bool] = None
    sticking_point: Optional[str] = None


class NoteOut(BaseModel):
    movement_id: Optional[int] = None
    text: str


class LoggedSetsResponse(BaseModel):
    session_id: int
    date: str
    day_role: str
    logs: List[LoggedSet]
    surveys: List[SurveyOut] = []
    notes: List[NoteOut] = []


@app.get("/sessions/{session_id}/logs", response_model=LoggedSetsResponse)
def get_session_logs(session_id: int, db: Session = Depends(get_session)):
    """Logged actuals (SetLogs) + per-exercise surveys + notes for a session.
    Client groups sets by movement and matches surveys/notes by movement_id."""
    from ..models.session import (
        Session as WorkoutSession, SetLog, ExerciseSurvey, Note)
    ws = db.get(WorkoutSession, session_id)
    if ws is None:
        raise HTTPException(404, "session not found")

    rows = db.exec(
        select(SetLog).where(SetLog.session_id == session_id).order_by(SetLog.id)
    ).all()
    logs = []
    for sl in rows:
        mv = db.get(Movement, sl.movement_id)
        logs.append(LoggedSet(
            movement_id=sl.movement_id, movement_name=(mv.name if mv else ""),
            set_index=sl.set_index, reps=sl.actual_reps, load=sl.actual_load,
            tap=(sl.feedback_tap.value if sl.feedback_tap else None),
            is_warmup=sl.is_warmup,
            rpe_numeric=sl.rpe_numeric, felt_peak=sl.felt_peak,
        ))

    survey_rows = db.exec(
        select(ExerciseSurvey).where(ExerciseSurvey.session_id == session_id)
        .order_by(ExerciseSurvey.id)
    ).all()
    surveys = []
    for sv in survey_rows:
        mv = db.get(Movement, sv.movement_id)
        surveys.append(SurveyOut(
            movement_id=sv.movement_id, movement_name=(mv.name if mv else ""),
            asymmetry_flag=sv.asymmetry_flag, technique_flag=sv.technique_flag,
            sticking_point=sv.sticking_point,
        ))

    note_rows = db.exec(
        select(Note).where(Note.session_id == session_id).order_by(Note.id)
    ).all()
    notes = [NoteOut(movement_id=n.movement_id, text=n.text) for n in note_rows]

    return LoggedSetsResponse(
        session_id=session_id, date=ws.date.isoformat(), day_role=ws.day_role,
        logs=logs, surveys=surveys, notes=notes)


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


@app.get("/programs/{program_id}/days", response_model=List[str])
def get_program_days(program_id: int, db: Session = Depends(get_session)):
    """Training day_roles in order (excludes rest days) — feeds the Today day-picker."""
    from ..models.program import ProgramDay
    rows = db.exec(
        select(ProgramDay)
        .where(ProgramDay.program_id == program_id)
        .order_by(ProgramDay.day_index)
    ).all()
    return [pd.day_role for pd in rows if not pd.is_rest and pd.day_role]


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

# ---------------------------------------------------------------------------
# Readiness endpoints (Task 23)
# ---------------------------------------------------------------------------

@app.get("/readiness/today", response_model=Optional[DailyReadinessOut])
def get_readiness_today(db: Session = Depends(get_session)):
    today = datetime.now().date()
    return db.exec(select(DailyReadiness).where(DailyReadiness.date == today)).first()

@app.post("/readiness", response_model=DailyReadinessOut)
def post_readiness(req: DailyReadinessIn, db: Session = Depends(get_session)):
    today = datetime.now().date()
    row = db.exec(select(DailyReadiness).where(DailyReadiness.date == today)).first()
    
    # exclude_unset ensures only explicitly provided fields overwrite existing data
    update_data = req.dict(exclude_unset=True)
    if row is None:
        row = DailyReadiness(date=today, **update_data)
        db.add(row)
    else:
        for key, value in update_data.items():
            setattr(row, key, value)
        db.add(row)
    db.commit()
    db.refresh(row)
    return row

@app.post("/engine-state/confirm-phase")
def confirm_phase(req: ConfirmPhaseRequest, db: Session = Depends(get_session)):
    engine_state = db.exec(select(EngineState)).one()
    if not engine_state.pending_phase_transition:
        raise HTTPException(400, "No pending phase transition available")
    
    if req.to_phase != engine_state.pending_phase_transition:
        raise HTTPException(
            400,
            f"Requested phase {req.to_phase} does not match pending transition {engine_state.pending_phase_transition}"
        )
    
    engine_state.current_phase = Phase(req.to_phase)
    engine_state.pending_phase_transition = None
    db.add(engine_state)
    db.commit()
    return {"status": "confirmed", "current_phase": engine_state.current_phase.value}
