# Logging Round-Trip Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the 1.0-beta loop — let the user *log* a generated session (per-set tap, offline) so `run_analysis` can adapt the next one.

**Architecture:** Offline-first capture. The client holds the in-session state in a durable local store (Room), writing each tapped set before the UI advances; at completion it sends ONE atomic, retryable, idempotent batch to the server, which validates taps, writes SetLogs/surveys/notes, flips the session PLANNED→COMPLETED, and fires `run_analysis`. Two repos (server `IronLog-V2`, client `IronLog-V2-Client`) joined by a locked endpoint contract; the server phase is built-and-tested-stable before the client phase begins.

**Tech Stack:** Server — Python 3 / FastAPI / SQLModel / pytest (run on myflix). Client — Kotlin / Jetpack Compose / Ktor / kotlinx.serialization / **Room** (new) / JUnit + coroutines-test.

## Global Constraints

- **NO `from __future__ import annotations`** in any server file (project-wide).
- **BUILD-AND-TEST-ONLY.** Never run `python -m ironlog.seed`; never write the prod `ironlog.db`. Server tests use an in-memory SQLite engine only.
- **Two-writer boundary.** Logging writes `SetLog`, `ExerciseSurvey`, `Note`, and `Session.status`/`Session.notes` ONLY. It MUST NOT write `MovementState.current_load` (generation's field) or any `run_analysis` outcome field (`e1rm`, `calibration_status`, counters, `E1rmHistory`, `Session.analyzed_at`). `run_analysis` is invoked, not reimplemented.
- **Mandatory tap.** `feedback_tap` is required on every working set (`SetRole` in {`WORKING`, `TOP`, `BACKOFF`}). Enforced at BOTH ends: client UI can't advance a working set without it; server `/submit` rejects (HTTP 422) a batch containing a tapless working SetLog.
- **Migration authoring rule** (only if a schema change is needed — none is expected; all tables exist): single-statement-atomic OR idempotent (`IF NOT EXISTS`), and extend the migration-parity test.
- **Server tests run on myflix ONLY:** `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q'`. Baseline before this plan: **224 passing.**
- **Client tests run via the Android/Gradle path** in `~/projects/IronLog-V2-Client`.

## THE ENDPOINT CONTRACT (the server↔client crossing artifact — locked)

Two repos, two build paths, can't co-test. These three shapes are the contract. The server defines them as Pydantic models (Task 1); the client mirrors them as `@Serializable` Kotlin DTOs (Task 5) **field-for-field, snake_case**. Any change to this block is a contract change that touches both repos.

**`POST /sessions/{session_id}/submit` — request body `SubmitRequest`:**
```
SubmitRequest:
  set_logs:  SetLogIn[]            # all logged sets for the session
  surveys:   ExerciseSurveyIn[]    # optional; [] if none
  notes:     NoteIn[]              # optional; [] if none

SetLogIn:
  planned_set_id: int | null       # null = unplanned/extra set
  movement_id:    int
  set_index:      int
  set_role:       str              # "WORKING"|"TOP"|"BACKOFF"|"RAMP"|"WARMUP"|"AMRAP" — drives tap enforcement
  is_warmup:      bool
  actual_load:    float | null
  actual_reps:    int | null
  feedback_tap:   str | null       # "TOO_EASY"|"ON_TARGET"|"TOO_HARD"; REQUIRED when set_role in {WORKING,TOP,BACKOFF}
  rpe_numeric:    float | null
  actual_unassisted_reps: int | null
  actual_assisted_reps:   int | null
  actual_plates:  float | null
  band_pair_id:   int | null
  felt_peak:      float | null

ExerciseSurveyIn:
  movement_id:    int
  sticking_point: str | null
  asymmetry_flag: bool | null
  technique_flag: bool | null

NoteIn:
  movement_id: int | null
  text:        str
  # classification omitted — server stores it unclassified (JOURNAL); classify/apply deferred

SubmitResponse:
  session_id:       int
  status:           str            # always "COMPLETED" after a successful submit
  set_logs_written: int
  already_completed: bool          # true on an idempotent retry no-op
```

**`GET /sessions/{session_id}` — response `SessionDetailResponse`:**
```
SessionDetailResponse:
  id: int
  date: str                        # ISO date
  day_role: str
  phase: str
  status: str
  groups: GroupOut[]               # ordered by order_index

GroupOut:
  id: int
  order_index: int
  group_type: str                  # "STRAIGHT"|"GIANT_SET"
  rounds: int
  rest_seconds: int | null
  label: str | null
  exercises: ExerciseOut[]         # ordered by order_index

ExerciseOut:
  id: int
  movement_id: int
  movement_name: str               # joined from Movement.name (the UI needs it)
  order_index: int
  scheme: str
  objective: str
  planned_sets: PlannedSetOut[]    # ordered by set_index

PlannedSetOut:
  id: int
  set_index: int
  set_role: str
  is_warmup: bool
  target_load: float | null
  target_reps_low: int | null
  target_reps_high: int | null
  target_rpe: float | null
  target_unassisted_reps: int | null
  target_assisted_reps: int | null
  target_plates: float | null
  band_pair_id: int | null
  target_felt_peak: float | null
```

**`GET /sessions/today` — response: `SessionDetailResponse | null`.** Deterministic selection: among sessions with `status == PLANNED` and `analyzed_at is null`, return the one with the greatest `id` (most-recently approved). Zero matches → HTTP 200 with body `null` (client shows "generate one"). Never 404 for the empty case (empty is a valid state, not an error).

---

# PHASE 1 — SERVER (built-and-tested-stable before Phase 2 begins)

### Task 1: Lock the endpoint contract (Pydantic schemas)

**Files:**
- Create: `ironlog/api/schemas_capture.py`
- Test: `tests/test_capture_schemas.py`

**Interfaces:**
- Consumes: `ironlog/models/enums.py` (`FeedbackTap`, `SetRole`).
- Produces: `SetLogIn`, `ExerciseSurveyIn`, `NoteIn`, `SubmitRequest`, `SubmitResponse`, `PlannedSetOut`, `ExerciseOut`, `GroupOut`, `SessionDetailResponse` — the exact shapes in THE ENDPOINT CONTRACT above. Tasks 2/3 import these; Task 5 mirrors them in Kotlin.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_capture_schemas.py
from ironlog.api.schemas_capture import SubmitRequest, SetLogIn, SessionDetailResponse


def test_submitrequest_parses_minimal_working_set():
    req = SubmitRequest.model_validate({
        "set_logs": [{
            "planned_set_id": 10, "movement_id": 3, "set_index": 0,
            "set_role": "WORKING", "is_warmup": False,
            "actual_load": 100.0, "actual_reps": 8, "feedback_tap": "ON_TARGET",
        }],
        "surveys": [], "notes": [],
    })
    assert req.set_logs[0].feedback_tap == "ON_TARGET"
    assert req.set_logs[0].planned_set_id == 10
    assert req.surveys == [] and req.notes == []


def test_setlogin_allows_null_planned_set_and_optional_fields():
    s = SetLogIn.model_validate({
        "planned_set_id": None, "movement_id": 3, "set_index": 1,
        "set_role": "WARMUP", "is_warmup": True,
    })
    assert s.planned_set_id is None
    assert s.feedback_tap is None and s.actual_load is None


def test_session_detail_response_nests_groups_exercises_sets():
    resp = SessionDetailResponse.model_validate({
        "id": 1, "date": "2026-07-01", "day_role": "D1 Upper Push",
        "phase": "P1", "status": "PLANNED",
        "groups": [{
            "id": 1, "order_index": 0, "group_type": "STRAIGHT", "rounds": 1,
            "rest_seconds": 180, "label": None,
            "exercises": [{
                "id": 1, "movement_id": 3, "movement_name": "Bench Press [PB]",
                "order_index": 0, "scheme": "TOPSET_BACKOFF", "objective": "PROGRESS",
                "planned_sets": [{
                    "id": 10, "set_index": 0, "set_role": "TOP", "is_warmup": False,
                    "target_load": 100.0, "target_reps_low": 5, "target_reps_high": 8,
                    "target_rpe": 8.0,
                }],
            }],
        }],
    })
    assert resp.groups[0].exercises[0].movement_name == "Bench Press [PB]"
    assert resp.groups[0].exercises[0].planned_sets[0].set_role == "TOP"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest tests/test_capture_schemas.py -q'`
Expected: FAIL — `ModuleNotFoundError: ironlog.api.schemas_capture`.

- [ ] **Step 3: Write the schemas**

```python
# ironlog/api/schemas_capture.py
"""Capture-layer API contract (the server<->client crossing artifact).

These shapes are mirrored field-for-field by the Android client's Kotlin DTOs.
Any change here is a contract change that touches both repos.
"""
from typing import List, Optional

from pydantic import BaseModel


class SetLogIn(BaseModel):
    planned_set_id: Optional[int] = None
    movement_id: int
    set_index: int
    set_role: str
    is_warmup: bool = False
    actual_load: Optional[float] = None
    actual_reps: Optional[int] = None
    feedback_tap: Optional[str] = None
    rpe_numeric: Optional[float] = None
    actual_unassisted_reps: Optional[int] = None
    actual_assisted_reps: Optional[int] = None
    actual_plates: Optional[float] = None
    band_pair_id: Optional[int] = None
    felt_peak: Optional[float] = None


class ExerciseSurveyIn(BaseModel):
    movement_id: int
    sticking_point: Optional[str] = None
    asymmetry_flag: Optional[bool] = None
    technique_flag: Optional[bool] = None


class NoteIn(BaseModel):
    movement_id: Optional[int] = None
    text: str


class SubmitRequest(BaseModel):
    set_logs: List[SetLogIn]
    surveys: List[ExerciseSurveyIn] = []
    notes: List[NoteIn] = []


class SubmitResponse(BaseModel):
    session_id: int
    status: str
    set_logs_written: int
    already_completed: bool


class PlannedSetOut(BaseModel):
    id: int
    set_index: int
    set_role: str
    is_warmup: bool
    target_load: Optional[float] = None
    target_reps_low: Optional[int] = None
    target_reps_high: Optional[int] = None
    target_rpe: Optional[float] = None
    target_unassisted_reps: Optional[int] = None
    target_assisted_reps: Optional[int] = None
    target_plates: Optional[float] = None
    band_pair_id: Optional[int] = None
    target_felt_peak: Optional[float] = None


class ExerciseOut(BaseModel):
    id: int
    movement_id: int
    movement_name: str
    order_index: int
    scheme: str
    objective: str
    planned_sets: List[PlannedSetOut]


class GroupOut(BaseModel):
    id: int
    order_index: int
    group_type: str
    rounds: int
    rest_seconds: Optional[int] = None
    label: Optional[str] = None
    exercises: List[ExerciseOut]


class SessionDetailResponse(BaseModel):
    id: int
    date: str
    day_role: str
    phase: str
    status: str
    groups: List[GroupOut]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest tests/test_capture_schemas.py -q'`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add ironlog/api/schemas_capture.py tests/test_capture_schemas.py
git commit -m "feat(api): lock the capture endpoint contract (Pydantic schemas)"
```

---

### Task 2: `POST /sessions/{id}/submit` — atomic, idempotent, tap-validated

**Files:**
- Modify: `ironlog/api/app.py` (add the endpoint + helpers; near the v0.6 endpoints, ~line 188+)
- Test: `tests/test_submit_endpoint.py`

**Interfaces:**
- Consumes: `schemas_capture.SubmitRequest`/`SubmitResponse` (Task 1); `ironlog/models/session.py` (`Session`, `SetLog`, `ExerciseSurvey`, `Note`); `ironlog/models/enums.py` (`SessionStatus`, `SetRole`, `FeedbackTap`, `NoteClass`); `app._week_keyer`; `ironlog.persistence.run_analysis.run_analysis`, `already_analyzed`.
- Produces: `POST /sessions/{session_id}/submit` returning `SubmitResponse`. The set of `SetRole` values requiring a tap: `{TOP, BACKOFF, WORKING}` (the working-set roles).

**Behavior (from spec §1/§2):** in one DB transaction — (1) load session (404 if missing); (2) if already `COMPLETED`, return a complete no-op `SubmitResponse(already_completed=True)` WITHOUT writing or re-analyzing (lost-ack idempotency); (3) validate every working SetLog (`set_role` in {WORKING,TOP,BACKOFF}) has a non-null `feedback_tap` — else 422, write nothing; (4) write SetLogs, surveys, notes (notes stored with `classification=NoteClass.JOURNAL`, `confirmed=False`, `applied=False`); (5) set `status=COMPLETED`; (6) `run_analysis(session_id, db, _week_keyer)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_submit_endpoint.py
from datetime import date
from fastapi.testclient import TestClient
from sqlmodel import Session as DbSession, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from ironlog.api.app import app, get_session
from ironlog.models.session import Session, SetLog
from ironlog.models.enums import SessionStatus
import ironlog.models  # ensure all tables registered


def _client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    def _override():
        with DbSession(engine) as s:
            yield s
    app.dependency_overrides[get_session] = _override
    return TestClient(app), engine


def _planned_session(engine):
    with DbSession(engine) as s:
        ws = Session(date=date(2026, 7, 1), day_role="D1 Upper Push", phase="P1",
                     status=SessionStatus.PLANNED)
        s.add(ws); s.commit(); s.refresh(ws)
        return ws.id


def test_submit_writes_setlogs_and_completes():
    client, engine = _client()
    sid = _planned_session(engine)
    body = {"set_logs": [{"planned_set_id": None, "movement_id": 3, "set_index": 0,
                          "set_role": "WORKING", "is_warmup": False,
                          "actual_load": 100.0, "actual_reps": 8,
                          "feedback_tap": "ON_TARGET"}],
            "surveys": [], "notes": []}
    r = client.post(f"/sessions/{sid}/submit", json=body)
    assert r.status_code == 200
    assert r.json()["status"] == "COMPLETED"
    assert r.json()["set_logs_written"] == 1
    with DbSession(engine) as s:
        assert s.get(Session, sid).status == SessionStatus.COMPLETED
        assert len(s.exec(select(SetLog).where(SetLog.session_id == sid)).all()) == 1
    app.dependency_overrides.clear()


def test_submit_rejects_working_set_without_tap_422_and_writes_nothing():
    client, engine = _client()
    sid = _planned_session(engine)
    body = {"set_logs": [{"planned_set_id": None, "movement_id": 3, "set_index": 0,
                          "set_role": "WORKING", "is_warmup": False,
                          "actual_load": 100.0, "actual_reps": 8,
                          "feedback_tap": None}],   # tapless working set
            "surveys": [], "notes": []}
    r = client.post(f"/sessions/{sid}/submit", json=body)
    assert r.status_code == 422
    with DbSession(engine) as s:
        assert s.exec(select(SetLog).where(SetLog.session_id == sid)).all() == []
        assert s.get(Session, sid).status == SessionStatus.PLANNED   # untouched
    app.dependency_overrides.clear()


def test_submit_idempotent_lost_ack_retry_writes_nothing_new():
    """The real-world path: submit succeeds, ack lost, client retries."""
    client, engine = _client()
    sid = _planned_session(engine)
    body = {"set_logs": [{"planned_set_id": None, "movement_id": 3, "set_index": 0,
                          "set_role": "WORKING", "is_warmup": False,
                          "actual_load": 100.0, "actual_reps": 8,
                          "feedback_tap": "ON_TARGET"}],
            "surveys": [], "notes": []}
    r1 = client.post(f"/sessions/{sid}/submit", json=body)
    assert r1.status_code == 200 and r1.json()["already_completed"] is False
    r2 = client.post(f"/sessions/{sid}/submit", json=body)   # lost-ack retry
    assert r2.status_code == 200 and r2.json()["already_completed"] is True
    with DbSession(engine) as s:
        # exactly ONE SetLog — no duplicate from the retry
        assert len(s.exec(select(SetLog).where(SetLog.session_id == sid)).all()) == 1
    app.dependency_overrides.clear()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest tests/test_submit_endpoint.py -q'`
Expected: FAIL — 404 (endpoint not defined).

- [ ] **Step 3: Implement the endpoint**

Add to `ironlog/api/app.py` (imports: `from .schemas_capture import SubmitRequest, SubmitResponse`; `from ..models.session import SetLog, ExerciseSurvey, Note`; `from ..models.enums import SetRole, NoteClass`; `Session`/`SessionStatus`/`run_analysis`/`already_analyzed`/`_week_keyer` already present):

```python
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
            feedback_tap=sl.feedback_tap, rpe_numeric=sl.rpe_numeric,
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

    run_analysis(session_id, db, _week_keyer)

    written = len(db.exec(select(SetLog).where(SetLog.session_id == session_id)).all())
    return SubmitResponse(session_id=session_id, status=SessionStatus.COMPLETED.value,
                          set_logs_written=written, already_completed=False)
```

Note: `SetLog.feedback_tap` is typed `Optional[FeedbackTap]`; passing the string value is coerced by SQLModel's enum column. If the implementer finds coercion fails, map `sl.feedback_tap` through `FeedbackTap(sl.feedback_tap)` (and `SetRole`) — keep the validation logic identical.

- [ ] **Step 4: Run tests to verify they pass**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest tests/test_submit_endpoint.py -q'`
Expected: PASS (3 passed).

- [ ] **Step 5: Verify the two-writer boundary held + full suite**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && grep -n "current_load\|\.e1rm" ironlog/api/app.py; .venv/bin/pytest -q 2>&1 | tail -2'`
Expected: the grep shows no new `current_load`/`e1rm` write in the submit handler (only the v0.6 `commit_session` references elsewhere); suite ~227 passing, 0 failed.

- [ ] **Step 6: Commit**

```bash
git add ironlog/api/app.py tests/test_submit_endpoint.py
git commit -m "feat(api): POST /sessions/{id}/submit — atomic idempotent tap-validated batch + run_analysis"
```

---

### Task 3: `GET /sessions/{id}` + `GET /sessions/today`

**Files:**
- Modify: `ironlog/api/app.py`
- Test: `tests/test_session_read_endpoints.py`

**Interfaces:**
- Consumes: `schemas_capture.SessionDetailResponse` and its nested Out models (Task 1); `Session`, `ExerciseGroup`, `PlannedExercise`, `PlannedSet`; `Movement` (for `movement_name`); `SessionStatus`.
- Produces: `GET /sessions/{session_id}` → `SessionDetailResponse` (404 if missing); `GET /sessions/today` → `Optional[SessionDetailResponse]` (most-recent PLANNED+unanalyzed by max id; `null` if none).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_session_read_endpoints.py
from datetime import date
from fastapi.testclient import TestClient
from sqlmodel import Session as DbSession, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from ironlog.api.app import app, get_session
from ironlog.models.session import (Session, ExerciseGroup, PlannedExercise, PlannedSet)
from ironlog.models.enums import SessionStatus, GroupType, Scheme, Objective, SetRole
from ironlog.models.library import Movement
import ironlog.models


def _client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    def _override():
        with DbSession(engine) as s:
            yield s
    app.dependency_overrides[get_session] = _override
    return TestClient(app), engine


def _full_session(engine, status=SessionStatus.PLANNED):
    with DbSession(engine) as s:
        mv = Movement(name="Bench Press [PB]", base_name="Bench Press")
        s.add(mv); s.commit(); s.refresh(mv)
        ws = Session(date=date(2026, 7, 1), day_role="D1 Upper Push", phase="P1", status=status)
        s.add(ws); s.commit(); s.refresh(ws)
        g = ExerciseGroup(session_id=ws.id, order_index=0, group_type=GroupType.STRAIGHT, rounds=1)
        s.add(g); s.commit(); s.refresh(g)
        pe = PlannedExercise(group_id=g.id, movement_id=mv.id, order_index=0,
                             scheme=Scheme.TOPSET_BACKOFF, objective=Objective.PROGRESS)
        s.add(pe); s.commit(); s.refresh(pe)
        s.add(PlannedSet(planned_exercise_id=pe.id, set_index=0, set_role=SetRole.TOP,
                         target_load=100.0, target_reps_low=5, target_reps_high=8))
        s.commit()
        return ws.id


def test_get_session_returns_full_graph_with_movement_name():
    client, engine = _client()
    sid = _full_session(engine)
    r = client.get(f"/sessions/{sid}")
    assert r.status_code == 200
    body = r.json()
    assert body["day_role"] == "D1 Upper Push"
    ex = body["groups"][0]["exercises"][0]
    assert ex["movement_name"] == "Bench Press [PB]"
    assert ex["planned_sets"][0]["set_role"] == "TOP"
    app.dependency_overrides.clear()


def test_get_session_404_when_missing():
    client, engine = _client()
    assert client.get("/sessions/999").status_code == 404
    app.dependency_overrides.clear()


def test_today_returns_null_when_no_planned_session():
    client, engine = _client()
    r = client.get("/sessions/today")
    assert r.status_code == 200 and r.json() is None
    app.dependency_overrides.clear()


def test_today_returns_most_recent_planned_when_multiple():
    client, engine = _client()
    first = _full_session(engine)
    second = _full_session(engine)
    r = client.get("/sessions/today")
    assert r.status_code == 200
    assert r.json()["id"] == max(first, second)
    app.dependency_overrides.clear()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest tests/test_session_read_endpoints.py -q'`
Expected: FAIL — 404 / missing route.

- [ ] **Step 3: Implement the reads**

Add to `ironlog/api/app.py` (imports: the Out models from `schemas_capture`; `ExerciseGroup, PlannedExercise, PlannedSet` from `..models.session`; `Movement` from `..models.library`):

```python
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
                scheme=pe.scheme.value, objective=pe.objective.value, planned_sets=sets_out,
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
```

Route order: define `/sessions/today` BEFORE `/sessions/{session_id}` so "today" isn't captured as an id path param. (FastAPI matches in definition order; `{session_id}: int` would 422 on "today", but explicit ordering is clearer — put `today` first.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest tests/test_session_read_endpoints.py -q'`
Expected: PASS (4 passed).

- [ ] **Step 5: Full suite (server phase complete)**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q 2>&1 | tail -2'`
Expected: ~231 passing, 0 failed. **The server phase is now built-and-tested-stable; the contract (Task 1) is locked. Phase 2 may begin.**

- [ ] **Step 6: Commit**

```bash
git add ironlog/api/app.py tests/test_session_read_endpoints.py
git commit -m "feat(api): GET /sessions/{id} (full graph) + GET /sessions/today (deterministic)"
```

---

# PHASE 2 — CLIENT (`~/projects/IronLog-V2-Client`; against the locked contract)

> All client tasks reference THE ENDPOINT CONTRACT block above verbatim. The client is a **separate repo** — work it on its own branch `feat/logging-capture` there.

### Task 4: Room durable store + the process-death keystone gate

**Files (in `IronLog-V2-Client`):**
- Modify: `gradle/libs.versions.toml` (Room version + libs), `app/build.gradle.kts` (Room deps + KSP plugin)
- Create: `app/src/main/java/com/jauschua/ironlogv2/data/local/CaptureEntities.kt`, `CaptureDao.kt`, `CaptureDatabase.kt`
- Test: `app/src/test/java/com/jauschua/ironlogv2/data/local/CaptureDurabilityTest.kt` (Robolectric, real Room on a temp file — survives a DB instance close+reopen, which simulates process death where only on-disk state survives)

**Interfaces:**
- Produces: `SetLogDraft` (entity, fields mirror `SetLogIn` + `session_id` + a local autoincrement `draft_id`), `CaptureDao` (`suspend fun insertSetLog(d: SetLogDraft)`, `suspend fun setLogsForSession(sessionId: Int): List<SetLogDraft>`, `suspend fun clearSession(sessionId: Int)`), `CaptureDatabase`. Task 5 consumes these.

**The durability requirement (spec §5/§6 keystone):** the DAO write is a `suspend` function that **returns only after the row is committed to disk**. The test proves recovery from on-disk state after closing the DB instance (the only state that survives a real process kill). Room's `@Insert suspend` commits before returning by design — the test LOCKS that guarantee so a future refactor to fire-and-forget fails here.

- [ ] **Step 1: Add Room deps**

In `gradle/libs.versions.toml` add (under `[versions]`): `room = "2.6.1"`, `ksp = "2.0.21-1.0.25"` (match the project's Kotlin version — the implementer confirms the KSP/Kotlin pairing from the existing `kotlin` version). Under `[libraries]`: `room-runtime`, `room-ktx`, `room-compiler`. Under `[plugins]`: `ksp`. In `app/build.gradle.kts` add `alias(libs.plugins.ksp)` to plugins, and `implementation(libs.room.runtime)`, `implementation(libs.room.ktx)`, `ksp(libs.room.compiler)`, `testImplementation(libs.robolectric)` to dependencies.

- [ ] **Step 2: Write the failing durability test**

```kotlin
// app/src/test/java/com/jauschua/ironlogv2/data/local/CaptureDurabilityTest.kt
package com.jauschua.ironlogv2.data.local

import android.content.Context
import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import java.io.File

@RunWith(RobolectricTestRunner::class)
class CaptureDurabilityTest {
    private fun dbFile() = File.createTempFile("capture-test", ".db").apply { delete() }

    @Test fun setLogs_survive_db_instance_recreation() = runBlocking {
        val ctx = ApplicationProvider.getApplicationContext<Context>()
        val file = dbFile()
        // First "process": write three taps, each commits before returning.
        var db = Room.databaseBuilder(ctx, CaptureDatabase::class.java, file.absolutePath).build()
        repeat(3) { i ->
            db.captureDao().insertSetLog(
                SetLogDraft(sessionId = 7, plannedSetId = i, movementId = 3, setIndex = i,
                    setRole = "WORKING", isWarmup = false, actualLoad = 100.0,
                    actualReps = 8, feedbackTap = "ON_TARGET"))
        }
        db.close()  // simulate process death — only on-disk state survives
        // Second "process": reopen, assert all three recovered.
        db = Room.databaseBuilder(ctx, CaptureDatabase::class.java, file.absolutePath).build()
        val recovered = db.captureDao().setLogsForSession(7)
        assertEquals(3, recovered.size)
        assertEquals("ON_TARGET", recovered.first().feedbackTap)
        db.close()
    }
}
```

- [ ] **Step 3: Run test — fails to compile (entities/dao/db missing)**

Run (in `~/projects/IronLog-V2-Client`): `./gradlew testDebugUnitTest --tests "*CaptureDurabilityTest*"`
Expected: FAIL — unresolved `SetLogDraft`/`CaptureDatabase`.

- [ ] **Step 4: Implement entities, DAO, database**

```kotlin
// CaptureEntities.kt
package com.jauschua.ironlogv2.data.local
import androidx.room.Entity
import androidx.room.PrimaryKey

@Entity(tableName = "setlog_draft")
data class SetLogDraft(
    @PrimaryKey(autoGenerate = true) val draftId: Long = 0,
    val sessionId: Int,
    val plannedSetId: Int?,
    val movementId: Int,
    val setIndex: Int,
    val setRole: String,
    val isWarmup: Boolean,
    val actualLoad: Double? = null,
    val actualReps: Int? = null,
    val feedbackTap: String? = null,
    val rpeNumeric: Double? = null,
    val actualUnassistedReps: Int? = null,
    val actualAssistedReps: Int? = null,
    val actualPlates: Double? = null,
    val bandPairId: Int? = null,
    val feltPeak: Double? = null,
)

@Entity(tableName = "survey_draft")
data class SurveyDraft(
    @PrimaryKey(autoGenerate = true) val draftId: Long = 0,
    val sessionId: Int,
    val movementId: Int,
    val stickingPoint: String? = null,
    val asymmetryFlag: Boolean? = null,
    val techniqueFlag: Boolean? = null,
)

@Entity(tableName = "note_draft")
data class NoteDraft(
    @PrimaryKey(autoGenerate = true) val draftId: Long = 0,
    val sessionId: Int,
    val movementId: Int? = null,
    val text: String,
)
```

```kotlin
// CaptureDao.kt
package com.jauschua.ironlogv2.data.local
import androidx.room.Dao
import androidx.room.Insert
import androidx.room.Query

@Dao
interface CaptureDao {
    @Insert suspend fun insertSetLog(d: SetLogDraft)
    @Insert suspend fun insertSurvey(d: SurveyDraft)
    @Insert suspend fun insertNote(d: NoteDraft)

    @Query("SELECT * FROM setlog_draft WHERE sessionId = :sessionId ORDER BY draftId")
    suspend fun setLogsForSession(sessionId: Int): List<SetLogDraft>
    @Query("SELECT * FROM survey_draft WHERE sessionId = :sessionId ORDER BY draftId")
    suspend fun surveysForSession(sessionId: Int): List<SurveyDraft>
    @Query("SELECT * FROM note_draft WHERE sessionId = :sessionId ORDER BY draftId")
    suspend fun notesForSession(sessionId: Int): List<NoteDraft>

    @Query("DELETE FROM setlog_draft WHERE sessionId = :sessionId")
    suspend fun clearSetLogs(sessionId: Int)
    @Query("DELETE FROM survey_draft WHERE sessionId = :sessionId")
    suspend fun clearSurveys(sessionId: Int)
    @Query("DELETE FROM note_draft WHERE sessionId = :sessionId")
    suspend fun clearNotes(sessionId: Int)
}
```

```kotlin
// CaptureDatabase.kt
package com.jauschua.ironlogv2.data.local
import androidx.room.Database
import androidx.room.RoomDatabase

@Database(entities = [SetLogDraft::class, SurveyDraft::class, NoteDraft::class],
          version = 1, exportSchema = false)
abstract class CaptureDatabase : RoomDatabase() {
    abstract fun captureDao(): CaptureDao
}
```

- [ ] **Step 5: Run the durability test — passes**

Run: `./gradlew testDebugUnitTest --tests "*CaptureDurabilityTest*"`
Expected: PASS — three set-logs recovered after DB close+reopen (commit-before-return proven).

- [ ] **Step 6: Commit (in the client repo)**

```bash
git add gradle/libs.versions.toml app/build.gradle.kts \
        app/src/main/java/com/jauschua/ironlogv2/data/local/ \
        app/src/test/java/com/jauschua/ironlogv2/data/local/CaptureDurabilityTest.kt
git commit -m "feat(capture): Room durable draft store + process-death survival gate"
```

---

### Task 5: Capture DTOs + `CaptureRepo` (load prescription, batch submit, offline retry)

**Files:**
- Create: `app/src/main/java/com/jauschua/ironlogv2/data/api/dto/CaptureModels.kt`, `app/src/main/java/com/jauschua/ironlogv2/data/repo/CaptureRepo.kt`
- Test: `app/src/test/java/com/jauschua/ironlogv2/data/repo/CaptureRepoTest.kt` (Ktor MockEngine + in-memory Room)

**Interfaces:**
- Consumes: `ApiClient`, `runCatchingApi`, `FeedbackTap`; `CaptureDao` + drafts (Task 4); the contract DTOs (this task).
- Produces: Kotlin DTOs `SetLogIn`, `ExerciseSurveyIn`, `NoteIn`, `SubmitRequest`, `SubmitResponse`, `SessionDetailResponse`(+`GroupOut`,`ExerciseOut`,`PlannedSetOut`) mirroring the contract; `CaptureRepo(apiClient, dao)` with `suspend fun today(): Result<SessionDetailResponse?>`, `suspend fun logSet(d: SetLogDraft)`, `suspend fun submit(sessionId: Int): Result<SubmitResponse>` (reads drafts → builds `SubmitRequest` → POSTs → on success clears local). Consumed by Task 6.

- [ ] **Step 1: Write the DTOs** (mirror THE ENDPOINT CONTRACT field-for-field, snake_case)

```kotlin
// CaptureModels.kt
package com.jauschua.ironlogv2.data.api.dto
import kotlinx.serialization.Serializable

@Serializable data class SetLogIn(
    val planned_set_id: Int? = null, val movement_id: Int, val set_index: Int,
    val set_role: String, val is_warmup: Boolean,
    val actual_load: Double? = null, val actual_reps: Int? = null,
    val feedback_tap: String? = null, val rpe_numeric: Double? = null,
    val actual_unassisted_reps: Int? = null, val actual_assisted_reps: Int? = null,
    val actual_plates: Double? = null, val band_pair_id: Int? = null, val felt_peak: Double? = null,
)
@Serializable data class ExerciseSurveyIn(
    val movement_id: Int, val sticking_point: String? = null,
    val asymmetry_flag: Boolean? = null, val technique_flag: Boolean? = null,
)
@Serializable data class NoteIn(val movement_id: Int? = null, val text: String)
@Serializable data class SubmitRequest(
    val set_logs: List<SetLogIn>, val surveys: List<ExerciseSurveyIn> = emptyList(),
    val notes: List<NoteIn> = emptyList(),
)
@Serializable data class SubmitResponse(
    val session_id: Int, val status: String, val set_logs_written: Int, val already_completed: Boolean,
)
@Serializable data class PlannedSetOut(
    val id: Int, val set_index: Int, val set_role: String, val is_warmup: Boolean,
    val target_load: Double? = null, val target_reps_low: Int? = null,
    val target_reps_high: Int? = null, val target_rpe: Double? = null,
    val target_unassisted_reps: Int? = null, val target_assisted_reps: Int? = null,
    val target_plates: Double? = null, val band_pair_id: Int? = null, val target_felt_peak: Double? = null,
)
@Serializable data class ExerciseOut(
    val id: Int, val movement_id: Int, val movement_name: String, val order_index: Int,
    val scheme: String, val objective: String, val planned_sets: List<PlannedSetOut>,
)
@Serializable data class GroupOut(
    val id: Int, val order_index: Int, val group_type: String, val rounds: Int,
    val rest_seconds: Int? = null, val label: String? = null, val exercises: List<ExerciseOut>,
)
@Serializable data class SessionDetailResponse(
    val id: Int, val date: String, val day_role: String, val phase: String,
    val status: String, val groups: List<GroupOut>,
)
```

- [ ] **Step 2: Write the failing repo test**

```kotlin
// CaptureRepoTest.kt — MockEngine asserts the submit payload + clear-on-success
package com.jauschua.ironlogv2.data.repo
import androidx.test.core.app.ApplicationProvider
import androidx.room.Room
import android.content.Context
import com.jauschua.ironlogv2.data.api.ApiClient
import com.jauschua.ironlogv2.data.local.CaptureDatabase
import com.jauschua.ironlogv2.data.local.SetLogDraft
import io.ktor.client.engine.mock.MockEngine
import io.ktor.client.engine.mock.respond
import io.ktor.http.*
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

@RunWith(RobolectricTestRunner::class)
class CaptureRepoTest {
    private fun db() = Room.inMemoryDatabaseBuilder(
        ApplicationProvider.getApplicationContext<Context>(), CaptureDatabase::class.java).build()

    @Test fun submit_builds_payload_from_drafts_and_clears_on_success() = runBlocking {
        var capturedBody: String? = null
        val engine = MockEngine { req ->
            capturedBody = (req.body as io.ktor.http.content.TextContent).text
            respond("""{"session_id":7,"status":"COMPLETED","set_logs_written":1,"already_completed":false}""",
                HttpStatusCode.OK, headersOf(HttpHeaders.ContentType, "application/json"))
        }
        val dao = db().captureDao()
        dao.insertSetLog(SetLogDraft(sessionId = 7, plannedSetId = 10, movementId = 3,
            setIndex = 0, setRole = "WORKING", isWarmup = false, actualLoad = 100.0,
            actualReps = 8, feedbackTap = "ON_TARGET"))
        val repo = CaptureRepo(ApiClient(engine = engine), dao)

        val res = repo.submit(7)
        assertTrue(res.isSuccess)
        assertTrue(capturedBody!!.contains("\"feedback_tap\":\"ON_TARGET\""))
        assertTrue(capturedBody!!.contains("\"planned_set_id\":10"))
        assertEquals(0, dao.setLogsForSession(7).size)   // cleared on success
    }
}
```

- [ ] **Step 3: Run test — fails (CaptureRepo missing)**

Run: `./gradlew testDebugUnitTest --tests "*CaptureRepoTest*"`
Expected: FAIL — unresolved `CaptureRepo`.

- [ ] **Step 4: Implement `CaptureRepo`**

```kotlin
// CaptureRepo.kt
package com.jauschua.ironlogv2.data.repo
import com.jauschua.ironlogv2.data.api.ApiClient
import com.jauschua.ironlogv2.data.api.dto.*
import com.jauschua.ironlogv2.data.api.runCatchingApi
import com.jauschua.ironlogv2.data.local.CaptureDao
import com.jauschua.ironlogv2.data.local.SetLogDraft
import io.ktor.client.call.body
import io.ktor.client.request.*
import io.ktor.http.*

class CaptureRepo(private val apiClient: ApiClient, private val dao: CaptureDao) {

    suspend fun today(): Result<SessionDetailResponse?> = runCatchingApi {
        apiClient.http.get("/sessions/today").body()
    }

    suspend fun session(id: Int): Result<SessionDetailResponse> = runCatchingApi {
        apiClient.http.get("/sessions/$id").body()
    }

    /** Per-set durable write (commits before returning — Room @Insert suspend). */
    suspend fun logSet(d: SetLogDraft) = dao.insertSetLog(d)

    /** Batch submit. Idempotent + retryable: on success, clear local drafts. */
    suspend fun submit(sessionId: Int): Result<SubmitResponse> = runCatchingApi {
        val setLogs = dao.setLogsForSession(sessionId).map {
            SetLogIn(planned_set_id = it.plannedSetId, movement_id = it.movementId,
                set_index = it.setIndex, set_role = it.setRole, is_warmup = it.isWarmup,
                actual_load = it.actualLoad, actual_reps = it.actualReps,
                feedback_tap = it.feedbackTap, rpe_numeric = it.rpeNumeric,
                actual_unassisted_reps = it.actualUnassistedReps,
                actual_assisted_reps = it.actualAssistedReps, actual_plates = it.actualPlates,
                band_pair_id = it.bandPairId, felt_peak = it.feltPeak)
        }
        val surveys = dao.surveysForSession(sessionId).map {
            ExerciseSurveyIn(it.movementId, it.stickingPoint, it.asymmetryFlag, it.techniqueFlag)
        }
        val notes = dao.notesForSession(sessionId).map { NoteIn(it.movementId, it.text) }
        val resp: SubmitResponse = apiClient.http.post("/sessions/$sessionId/submit") {
            contentType(ContentType.Application.Json)
            setBody(SubmitRequest(setLogs, surveys, notes))
        }.body()
        dao.clearSetLogs(sessionId); dao.clearSurveys(sessionId); dao.clearNotes(sessionId)
        resp
    }
}
```

Offline retry (gate #5): `runCatchingApi` already returns a failed `Result` on a network error (the submit isn't cleared, drafts remain), so the caller retries the same `submit(sessionId)` when connectivity returns; the server's idempotency makes a re-send safe. No extra queue needed for beta — the durable drafts ARE the queue.

- [ ] **Step 5: Run test — passes**

Run: `./gradlew testDebugUnitTest --tests "*CaptureRepoTest*"`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/src/main/java/com/jauschua/ironlogv2/data/api/dto/CaptureModels.kt \
        app/src/main/java/com/jauschua/ironlogv2/data/repo/CaptureRepo.kt \
        app/src/test/java/com/jauschua/ironlogv2/data/repo/CaptureRepoTest.kt
git commit -m "feat(capture): contract DTOs + CaptureRepo (load/log/submit, clear-on-success)"
```

---

### Task 6: `CaptureViewModel` — write-before-advance + mandatory-tap (client)

**Files:**
- Modify: `app/src/main/java/com/jauschua/ironlogv2/di/AppContainer.kt` (provide `CaptureDatabase` + `CaptureRepo`)
- Create: `app/src/main/java/com/jauschua/ironlogv2/ui/screens/capture/CaptureViewModel.kt`
- Test: `app/src/test/java/com/jauschua/ironlogv2/ui/capture/CaptureViewModelTest.kt`

**Interfaces:**
- Consumes: `CaptureRepo` (Task 5), `SetLogDraft`, `UiState`, the contract DTOs. `AppContainer` gains `val captureDb` + `val captureRepo` (mirror the existing `by lazy` pattern; `CaptureDatabase` built with `Room.databaseBuilder(appContext, …, "capture.db")` — `AppContainer` must take the `Context`; update `IronLogV2Application` to pass it).
- Produces: `CaptureViewModel` with `logWorkingSet(...)` (the write-before-advance entry point) and `finish()` (submit).

**The two client gates here:**
- **Mandatory tap (gate #2 client half):** `logWorkingSet` for a working role rejects a null tap — it sets a UI error and does NOT advance (no Room write, no index increment).
- **Write-before-advance (gate #5/durability):** `logWorkingSet` is a `suspend`/coroutine that **awaits `repo.logSet(draft)` (which commits to Room) BEFORE** mutating the UI state to the next set. The test asserts the Room row exists at the moment the state shows "advanced."

- [ ] **Step 1: Write the failing tests**

```kotlin
// CaptureViewModelTest.kt
package com.jauschua.ironlogv2.ui.capture
import androidx.test.core.app.ApplicationProvider
import androidx.room.Room
import android.content.Context
import com.jauschua.ironlogv2.data.api.ApiClient
import com.jauschua.ironlogv2.data.local.CaptureDatabase
import com.jauschua.ironlogv2.data.repo.CaptureRepo
import com.jauschua.ironlogv2.ui.screens.capture.CaptureViewModel
import io.ktor.client.engine.mock.MockEngine
import io.ktor.client.engine.mock.respond
import io.ktor.http.*
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.test.*
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

@RunWith(RobolectricTestRunner::class)
class CaptureViewModelTest {
    private fun deps(): Pair<CaptureRepo, CaptureDatabase> {
        val db = Room.inMemoryDatabaseBuilder(
            ApplicationProvider.getApplicationContext<Context>(), CaptureDatabase::class.java)
            .allowMainThreadQueries().build()
        val engine = MockEngine { respond(
            """{"session_id":7,"status":"COMPLETED","set_logs_written":1,"already_completed":false}""",
            HttpStatusCode.OK, headersOf(HttpHeaders.ContentType, "application/json")) }
        return CaptureRepo(ApiClient(engine = engine), db.captureDao()) to db
    }

    @Test fun working_set_without_tap_is_rejected_and_not_persisted() = runBlocking {
        val (repo, db) = deps()
        val vm = CaptureViewModel(repo, sessionId = 7)
        vm.logWorkingSet(plannedSetId = 10, movementId = 3, setIndex = 0, setRole = "WORKING",
            actualLoad = 100.0, actualReps = 8, tap = null)
        assertNotNull(vm.uiError.value)                       // rejected
        assertEquals(0, db.captureDao().setLogsForSession(7).size)  // nothing written
    }

    @Test fun working_set_is_committed_to_room_before_advance() = runBlocking {
        val (repo, db) = deps()
        val vm = CaptureViewModel(repo, sessionId = 7)
        vm.logWorkingSet(plannedSetId = 10, movementId = 3, setIndex = 0, setRole = "WORKING",
            actualLoad = 100.0, actualReps = 8, tap = "ON_TARGET")
        // After the suspend returns, the durable row exists AND the VM advanced.
        assertEquals(1, db.captureDao().setLogsForSession(7).size)
        assertEquals(1, vm.nextSetIndex.value)
    }
}
```

- [ ] **Step 2: Run tests — fail (CaptureViewModel missing)**

Run: `./gradlew testDebugUnitTest --tests "*CaptureViewModelTest*"`
Expected: FAIL — unresolved `CaptureViewModel`.

- [ ] **Step 3: Implement the ViewModel**

```kotlin
// CaptureViewModel.kt
package com.jauschua.ironlogv2.ui.screens.capture
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.jauschua.ironlogv2.data.local.SetLogDraft
import com.jauschua.ironlogv2.data.repo.CaptureRepo
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

private val TAP_REQUIRED = setOf("WORKING", "TOP", "BACKOFF")

class CaptureViewModel(
    private val repo: CaptureRepo,
    private val sessionId: Int,
) : ViewModel() {

    private val _uiError = MutableStateFlow<String?>(null)
    val uiError: StateFlow<String?> = _uiError.asStateFlow()
    private val _nextSetIndex = MutableStateFlow(0)
    val nextSetIndex: StateFlow<Int> = _nextSetIndex.asStateFlow()
    private val _submitResult = MutableStateFlow<String?>(null)
    val submitResult: StateFlow<String?> = _submitResult.asStateFlow()

    /** Write-before-advance: awaits the durable Room commit BEFORE advancing the UI.
     *  Mandatory tap: a working set without a tap is rejected (no write, no advance). */
    suspend fun logWorkingSet(plannedSetId: Int?, movementId: Int, setIndex: Int,
                              setRole: String, actualLoad: Double?, actualReps: Int?,
                              tap: String?, isWarmup: Boolean = false) {
        if (setRole in TAP_REQUIRED && tap == null) {
            _uiError.value = "Tap required before continuing"
            return
        }
        _uiError.value = null
        repo.logSet(SetLogDraft(sessionId = sessionId, plannedSetId = plannedSetId,
            movementId = movementId, setIndex = setIndex, setRole = setRole,
            isWarmup = isWarmup, actualLoad = actualLoad, actualReps = actualReps,
            feedbackTap = tap))            // suspends until committed
        _nextSetIndex.value = setIndex + 1 // advance ONLY after the commit returns
    }

    fun finish() {
        viewModelScope.launch {
            repo.submit(sessionId)
                .onSuccess { _submitResult.value = it.status }
                .onFailure { _submitResult.value = "RETRY" }   // drafts persist; retry-safe
        }
    }
}
```

(The `Factory` companion mirrors `AutoregulateViewModel.Factory`; `sessionId` is passed via the screen — the implementer wires it through the nav arg or a setter, consistent with the existing nav pattern.)

- [ ] **Step 4: Wire `AppContainer`** — add `captureDb`/`captureRepo` (needs `Context`):

```kotlin
// AppContainer.kt — add ctor param + lazies
class AppContainer(private val appContext: android.content.Context) {
    val apiClient: ApiClient by lazy { ApiClient() }
    val libraryRepo: LibraryRepo by lazy { LibraryRepo(apiClient) }
    val autoregRepo: AutoregRepo by lazy { AutoregRepo(apiClient) }
    val captureDb: com.jauschua.ironlogv2.data.local.CaptureDatabase by lazy {
        androidx.room.Room.databaseBuilder(appContext,
            com.jauschua.ironlogv2.data.local.CaptureDatabase::class.java, "capture.db").build()
    }
    val captureRepo: com.jauschua.ironlogv2.data.repo.CaptureRepo by lazy {
        com.jauschua.ironlogv2.data.repo.CaptureRepo(apiClient, captureDb.captureDao())
    }
    val autoregPrefill: MutableStateFlow<Int?> = MutableStateFlow(null)
}
```
Update `IronLogV2Application` to construct `AppContainer(this)`.

- [ ] **Step 5: Run tests — pass**

Run: `./gradlew testDebugUnitTest --tests "*CaptureViewModelTest*"`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
git add app/src/main/java/com/jauschua/ironlogv2/ui/screens/capture/CaptureViewModel.kt \
        app/src/main/java/com/jauschua/ironlogv2/di/AppContainer.kt \
        app/src/main/java/com/jauschua/ironlogv2/IronLogV2Application.kt \
        app/src/test/java/com/jauschua/ironlogv2/ui/capture/CaptureViewModelTest.kt
git commit -m "feat(capture): CaptureViewModel — write-before-advance + mandatory-tap; DI wiring"
```

---

### Task 7: `CaptureScreen` + nav wiring

**Files:**
- Create: `app/src/main/java/com/jauschua/ironlogv2/ui/screens/capture/CaptureScreen.kt`
- Modify: `app/src/main/java/com/jauschua/ironlogv2/ui/Nav.kt` (add the capture destination + entry)

**Interfaces:**
- Consumes: `CaptureViewModel` (Task 6), `SessionDetailResponse` (loaded via `repo.today()`/`repo.session(id)`), `UiState`. Mirrors `AutoregulateScreen` Compose structure.

**Behavior:** on entry, load today's session (`repo.today()`); render groups → exercises → planned sets as the "do this" prescription; for each working set show load/reps inputs + the three-state tap (`TOO_EASY`/`ON_TARGET`/`TOO_HARD`) and a "Log set" button that calls `vm.logWorkingSet(...)` in a coroutine (the button is disabled / shows the error until a tap is selected — the UI half of mandatory-tap); a "Finish" button calls `vm.finish()`. Empty `today()` (null) → show "No planned session — generate one."

- [ ] **Step 1: Implement the screen** (Compose, mirroring `AutoregulateScreen` — load via `LaunchedEffect`, `UiState` when-branches, Material3 components; the "Log set" `Button` `enabled = selectedTap != null`; collect `vm.uiError`/`vm.submitResult` for feedback). Full composable per the existing screen's idiom.

- [ ] **Step 2: Wire nav** — add a `Capture` route to `Nav.kt` and an entry point (a nav action or a bottom-nav item, consistent with the existing destinations), constructing `CaptureViewModel` via its `Factory` from `app.container.captureRepo`.

- [ ] **Step 3: Build the app (compile + lint gate)**

Run (in `~/projects/IronLog-V2-Client`): `./gradlew :app:assembleDebug`
Expected: BUILD SUCCESSFUL — the screen compiles and is reachable from nav.

- [ ] **Step 4: Full client unit-test run**

Run: `./gradlew testDebugUnitTest`
Expected: all capture tests green (durability, repo, viewmodel) + existing tests unaffected.

- [ ] **Step 5: Commit**

```bash
git add app/src/main/java/com/jauschua/ironlogv2/ui/screens/capture/CaptureScreen.kt \
        app/src/main/java/com/jauschua/ironlogv2/ui/Nav.kt
git commit -m "feat(capture): CaptureScreen (prescription render + per-set tap) + nav wiring"
```

---

## Autoregulation during capture (spec §5 — resolved with zero new server work)

The "suggested load" for each working set is the planned `target_load`, which is **already present** in `SessionDetailResponse.groups[].exercises[].planned_sets[].target_load` (Task 3). So the **offline suggestion is the prescription itself** — no computation, always available. The **online refinement** (a tap-adjusted next load) reuses the *existing* `/autoregulate/next-set` endpoint via the already-built `AutoregRepo` (no new server endpoint, no new contract): the capture screen (Task 7) may, when online, call `autoregRepo.nextSet(...)` after a tap to show a refined suggestion, falling back to `target_load` on failure/offline. This is an optional display refinement in Task 7; if it expands the screen scope, it is deferrable because `target_load` is already a valid suggestion — the loop closes without it.

## Named-gate → task map (the "make drift impossible" gates)

| Gate | Where it's enforced + tested |
|---|---|
| 1. PROCESS-DEATH survival (keystone) | Task 4 `CaptureDurabilityTest` — DB close+reopen recovery (on-disk-only state) |
| 2. Mandatory tap both ends | Server: Task 2 `test_submit_rejects_working_set_without_tap_422`. Client: Task 6 `working_set_without_tap_is_rejected` + Task 7 disabled button |
| 3. Submit idempotency (lost-ack) | Task 2 `test_submit_idempotent_lost_ack_retry_writes_nothing_new` |
| 4. Planned-vs-logged delta | Task 2 (SetLog carries `planned_set_id`) + run_analysis fired; `test_submit_writes_setlogs_and_completes` |
| 5. Offline submit retry | Task 5 `CaptureRepo.submit` (drafts persist on failure = the queue; idempotent re-send) |

## Routing plan (delegation)

Server tasks build via codex/gemini repo-aware delegation (`repo="/home/jstout/projects/IronLog-V2"`) with Claude Code subagents applying + running tests on myflix. Client tasks build via Claude Code subagents on the Android/Gradle path (`~/projects/IronLog-V2-Client`). All under subagent-driven-development (fresh implementer + reviewer per task).

```
- Task 1 (contract schemas)        → server delegate (mechanical, complete code in plan)
- Task 2 (/submit endpoint)        → server delegate (the load-bearing idempotency/tap logic)
- Task 3 (read endpoints)          → server delegate
- Task 4 (Room + durability gate)  → client subagent (Gradle/Room + the keystone test)
- Task 5 (DTOs + CaptureRepo)      → client subagent
- Task 6 (CaptureViewModel)        → client subagent (write-before-advance + tap)
- Task 7 (CaptureScreen + nav)     → client subagent
```

**Delegation ratio: 7/7 tasks delegated (100%).** Tier A: orchestration, per-task review gates, the contract as the crossing artifact, and the final whole-branch review across both repos.
