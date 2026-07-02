# Today / Generate + History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the app self-sufficient for daily use — an in-app generate→review→approve flow (new Today tab) plus a History screen showing past sessions' logged actuals, backed by three additive read-only server endpoints.

**Architecture:** Server-stable-before-client. The server gains a candidate `preview` on `/generate` (serialized from the in-memory, uncommitted candidate — Fork 7c intact) and three read endpoints (`GET /sessions`, `GET /sessions/{id}/logs`, `GET /programs/{id}/days`). The client adds a `GenerateRepo`, a **Today** home tab (state machine: pick-day → generate → review preview → approve → Capture), and a **History** screen. The new Pydantic↔Kotlin DTOs are the crossing artifact.

**Tech Stack:** Python/FastAPI/SQLModel (server, pytest on myflix); Kotlin/Compose + Ktor + kotlinx.serialization (client, gradlew on workstation).

## Global Constraints

- Server: **NO `from __future__ import annotations`.**
- All new endpoints are **READ-only**; the two-writer boundary is intact; **NO schema change → NO migration.**
- Server tests run on myflix only: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q'`. Baseline: current `main` (post-in-gym-merge).
- Client builds on workstation: `~/projects/IronLog-V2-Client/gradlew :app:assembleDebug`; install `adb -s 192.168.1.17:36231 install -r app/build/outputs/apk/debug/app-debug.apk`.
- `SERVER_BASE_URL=http://192.168.1.7:8000` is a local-uncommitted client change — **leave it, do not stage it.**
- Two-repo: finish + test S1–S4 (server) before starting C1–C3 (client). DTOs are the crossing artifact.
- Follow existing test fixtures: server tests mirror the FastAPI `TestClient` + seeded-DB setup used in `tests/test_generation_*.py` / `tests/test_capture_*.py`; client unit tests mirror `app/src/test/.../CaptureScreenLogicTest.kt`.

---

## Task 1 (S1): Candidate preview on `/generate`

**Files:**
- Modify: `ironlog/api/app.py` — `_serialize_session` (line 321, add provisional-id fallback), `GenerateResponse` (line 129, add `preview`), `generate` handler (line 197, populate `preview`).
- Test: `tests/test_generate_preview.py` (new).

**Interfaces:**
- Consumes: `_serialize_session(ws, db) -> SessionDetailResponse` (existing); `outcome.assembled: AssembledSession` with `.session: WorkoutSession` whose `.groups`→`.exercises`→`.planned_sets` relationships are populated in-memory (pre-commit, so `.id` is `None`).
- Produces: `GenerateResponse.preview: Optional[SessionDetailResponse]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_generate_preview.py
from ironlog.models.session import Session as WorkoutSession
from sqlmodel import select

def test_generate_returns_preview_matching_session_shape(client):
    # `client` + a seeded startable program is the same fixture the other
    # generation tests use (a TestClient over a seeded DB). Follow that setup.
    resp = client.post("/generate", json={"day_role": "D1 Upper Push"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["exhausted"] is False
    preview = body["preview"]
    assert preview is not None
    # Same top-level fields as SessionDetailResponse:
    assert set(preview.keys()) >= {"id", "date", "day_role", "phase", "status", "groups"}
    assert preview["day_role"] == "D1 Upper Push"
    assert len(preview["groups"]) >= 1
    # Provisional ids are present ints (display-only), sets carry targets:
    g0 = preview["groups"][0]
    assert isinstance(g0["id"], int)
    s0 = g0["exercises"][0]["planned_sets"][0]
    assert isinstance(s0["id"], int)
    assert "target_load" in s0 and "target_reps_low" in s0

def test_generate_does_not_persist_a_session(client, db_session):
    # /generate must write NOTHING to the DB (Fork 7c). `db_session` is the
    # test's Session handle (same fixture the capture tests use).
    before = db_session.exec(select(WorkoutSession)).all()
    client.post("/generate", json={"day_role": "D1 Upper Push"})
    after = db_session.exec(select(WorkoutSession)).all()
    assert len(after) == len(before)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest tests/test_generate_preview.py -q'`
Expected: FAIL — `preview` KeyError / is `None` (field doesn't exist yet).

- [ ] **Step 3: Add provisional-id fallback to `_serialize_session`**

The serializer already walks the relationship graph; only the ids break for an uncommitted candidate. Assign display-only ids when `.id is None`, unique for sets:

```python
def _serialize_session(ws, db) -> SessionDetailResponse:
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
            ) for ps in sorted(pe.planned_sets, key=lambda x: x.set_index)]
            ex_out.append(ExerciseOut(
                id=(pe.id if pe.id is not None else ei), movement_id=pe.movement_id,
                movement_name=(mv.name if mv else ""), order_index=pe.order_index,
                scheme=pe.scheme.value, objective=pe.objective.value,
                unilateral=(mv.unilateral if mv else False), planned_sets=sets_out,
            ))
        groups_out.append(GroupOut(
            id=(g.id if g.id is not None else gi), order_index=g.order_index,
            group_type=g.group_type.value, rounds=g.rounds, rest_seconds=g.rest_seconds,
            label=g.label, exercises=ex_out,
        ))
    return SessionDetailResponse(
        id=(ws.id if ws.id is not None else 0), date=ws.date.isoformat(),
        day_role=ws.day_role, phase=ws.phase, status=ws.status.value, groups=groups_out,
    )
```

- [ ] **Step 4: Add `preview` to `GenerateResponse` and populate it in `generate`**

```python
class GenerateResponse(BaseModel):
    candidate_id: str
    day_role: str
    exhausted: bool
    attempts: int
    scope: str
    preview: Optional[SessionDetailResponse] = None
```

In the `generate` handler (after `_candidates[candidate_id] = outcome`), build the preview from the in-memory candidate:

```python
    preview = None
    if outcome.assembled is not None:
        preview = _serialize_session(outcome.assembled.session, db)
    return GenerateResponse(
        candidate_id=candidate_id, day_role=req.day_role,
        exhausted=outcome.exhausted, attempts=outcome.attempts,
        scope="main-work-only; warmups/finishers/Z2 per program doc, not yet in-app",
        preview=preview,
    )
```

Confirm `SessionDetailResponse` is imported in `app.py` (it already is — used as a `response_model`). `_serialize_session` receiving an in-memory `WorkoutSession` works because it only walks relationships + `db.get(Movement, ...)`; it never queries by session id.

- [ ] **Step 5: Run tests to verify they pass**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest tests/test_generate_preview.py -q'`
Expected: PASS (both tests).

- [ ] **Step 6: Run full suite (no regressions)**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q'`
Expected: all green (the serializer change is backward-compatible — real ids still pass through).

- [ ] **Step 7: Commit**

```bash
git -C ~/projects/IronLog-V2 add ironlog/api/app.py tests/test_generate_preview.py
git -C ~/projects/IronLog-V2 commit -m "feat(api): candidate preview on /generate (in-memory serialize, no DB write)"
```

---

## Task 2 (S2): `GET /sessions` — completed-session list

**Files:**
- Modify: `ironlog/api/app.py` — add `SessionSummary` model + `GET /sessions` handler (place near the other `/sessions/*` reads, before the `/sessions/{session_id}` route so it isn't shadowed).
- Test: `tests/test_sessions_list.py` (new).

**Interfaces:**
- Produces: `GET /sessions -> List[SessionSummary]` where `SessionSummary = {id: int, date: str, day_role: str, phase: str, status: str}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sessions_list.py
def test_sessions_lists_only_completed_newest_first(client, make_completed_session):
    # make_completed_session(day_role, date) is a helper you add mirroring the
    # capture tests' session setup: create a session, submit it to COMPLETED.
    a = make_completed_session("D1 Upper Push", "2026-07-01")
    b = make_completed_session("D5 Lower B", "2026-07-02")
    resp = client.get("/sessions")
    assert resp.status_code == 200
    rows = resp.json()
    ids = [r["id"] for r in rows]
    # newest (highest id) first; only COMPLETED present
    assert ids == sorted(ids, reverse=True)
    assert all(r["status"] == "COMPLETED" for r in rows)
    assert {a, b}.issubset(set(ids))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest tests/test_sessions_list.py -q'`
Expected: FAIL — 404/route missing.

- [ ] **Step 3: Add the model + handler**

```python
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
```

Place this handler **above** `@app.get("/sessions/{session_id}")` so `/sessions` isn't captured by the `{session_id}` path.

- [ ] **Step 4: Run test to verify it passes**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest tests/test_sessions_list.py -q'`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git -C ~/projects/IronLog-V2 add ironlog/api/app.py tests/test_sessions_list.py
git -C ~/projects/IronLog-V2 commit -m "feat(api): GET /sessions — completed-session list (newest-first)"
```

---

## Task 3 (S3): `GET /sessions/{id}/logs` — logged actuals

**Files:**
- Modify: `ironlog/api/app.py` — add `LoggedSet` + `LoggedSetsResponse` models + handler.
- Test: `tests/test_session_logs.py` (new).

**Interfaces:**
- Produces: `GET /sessions/{id}/logs -> LoggedSetsResponse` where
  `LoggedSet = {movement_id, movement_name, set_index, reps: Optional[int], load: Optional[float], tap: Optional[str], is_warmup}`
  and `LoggedSetsResponse = {session_id, date, day_role, logs: List[LoggedSet]}`. 404 if the session doesn't exist.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_session_logs.py
def test_session_logs_returns_actuals(client, make_completed_session_with_logs):
    # helper submits a session with known SetLogs (e.g. Bench 165x8 tap ON_TARGET).
    sid = make_completed_session_with_logs()
    resp = client.get(f"/sessions/{sid}/logs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == sid
    assert len(body["logs"]) >= 1
    first = body["logs"][0]
    assert set(first.keys()) == {
        "movement_id", "movement_name", "set_index", "reps", "load", "tap", "is_warmup"}
    assert first["movement_name"]  # joined from Movement

def test_session_logs_404_for_missing(client):
    resp = client.get("/sessions/999999/logs")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest tests/test_session_logs.py -q'`
Expected: FAIL — route missing.

- [ ] **Step 3: Add the models + handler**

```python
class LoggedSet(BaseModel):
    movement_id: int
    movement_name: str
    set_index: int
    reps: Optional[int] = None
    load: Optional[float] = None
    tap: Optional[str] = None
    is_warmup: bool


class LoggedSetsResponse(BaseModel):
    session_id: int
    date: str
    day_role: str
    logs: List[LoggedSet]


@app.get("/sessions/{session_id}/logs", response_model=LoggedSetsResponse)
def get_session_logs(session_id: int, db: Session = Depends(get_session)):
    """Logged actuals (SetLogs) for a session, in log order; client groups by movement."""
    from ..models.session import Session as WorkoutSession, SetLog
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
        ))
    return LoggedSetsResponse(
        session_id=session_id, date=ws.date.isoformat(), day_role=ws.day_role, logs=logs)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest tests/test_session_logs.py -q'`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git -C ~/projects/IronLog-V2 add ironlog/api/app.py tests/test_session_logs.py
git -C ~/projects/IronLog-V2 commit -m "feat(api): GET /sessions/{id}/logs — logged actuals"
```

---

## Task 4 (S4): `GET /programs/{id}/days` — training day_roles

**Files:**
- Modify: `ironlog/api/app.py` — add handler (reuse the `ProgramDay` enumeration pattern from `_program_movement_ids`).
- Test: `tests/test_program_days.py` (new).

**Interfaces:**
- Produces: `GET /programs/{id}/days -> List[str]` — training `day_role`s in `day_index` order, excluding rest days (`is_rest` true or `day_role == ""`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_program_days.py
def test_program_days_lists_training_days_in_order(client, seeded_program_id):
    resp = client.get(f"/programs/{seeded_program_id}/days")
    assert resp.status_code == 200
    days = resp.json()
    assert days == ["D1 Upper Push", "D2 Lower A", "D4 Upper B/Pull", "D5 Lower B", "D6 Weak Points"]
    assert "" not in days  # rest days excluded
```

(Adjust the expected list to the seeded program's exact `day_role` strings if they differ — read them from the seed.)

- [ ] **Step 2: Run test to verify it fails**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest tests/test_program_days.py -q'`
Expected: FAIL — route missing.

- [ ] **Step 3: Add the handler**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest tests/test_program_days.py -q'`
Expected: PASS.

- [ ] **Step 5: Full suite + commit**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q'` → all green.

```bash
git -C ~/projects/IronLog-V2 add ironlog/api/app.py tests/test_program_days.py
git -C ~/projects/IronLog-V2 commit -m "feat(api): GET /programs/{id}/days — training day_roles for the picker"
```

**→ Server phase complete. Do not start the client until the full server suite is green.**

---

## Task 5 (C1): Client DTOs + `GenerateRepo`

**Files:**
- Modify: `app/src/main/java/com/jauschua/ironlogv2/data/api/dto/CaptureModels.kt` — add DTOs.
- Create: `app/src/main/java/com/jauschua/ironlogv2/data/repo/GenerateRepo.kt`.
- Modify: `app/src/main/java/com/jauschua/ironlogv2/di/AppContainer.kt` — expose `generateRepo`.

**Interfaces:**
- Produces: `GenerateRepo.generate(dayRole): Result<GenerateResponse>`, `.approve(candidateId): Result<ApproveResponse>`, `.programDays(programId): Result<List<String>>`, `.pastSessions(): Result<List<SessionSummary>>`, `.sessionLogs(id): Result<LoggedSetsResponse>`.

- [ ] **Step 1: Add DTOs (field-for-field with the Pydantic models)**

```kotlin
// append to CaptureModels.kt — preview reuses the existing SessionDetailResponse
@Serializable data class GenerateRequest(val day_role: String)
@Serializable data class GenerateResponse(
    val candidate_id: String, val day_role: String, val exhausted: Boolean,
    val attempts: Int, val scope: String, val preview: SessionDetailResponse? = null,
)
@Serializable data class ApproveResponse(val session_id: Int)
@Serializable data class SessionSummary(
    val id: Int, val date: String, val day_role: String, val phase: String, val status: String,
)
@Serializable data class LoggedSet(
    val movement_id: Int, val movement_name: String, val set_index: Int,
    val reps: Int? = null, val load: Double? = null, val tap: String? = null, val is_warmup: Boolean,
)
@Serializable data class LoggedSetsResponse(
    val session_id: Int, val date: String, val day_role: String, val logs: List<LoggedSet>,
)
```

- [ ] **Step 2: Create `GenerateRepo` (mirror `WizardRepo`)**

```kotlin
// GenerateRepo.kt
package com.jauschua.ironlogv2.data.repo

import com.jauschua.ironlogv2.data.api.ApiClient
import com.jauschua.ironlogv2.data.api.dto.*
import com.jauschua.ironlogv2.data.api.runCatchingApi
import io.ktor.client.call.body
import io.ktor.client.request.get
import io.ktor.client.request.post
import io.ktor.client.request.setBody
import io.ktor.http.ContentType
import io.ktor.http.contentType

class GenerateRepo(private val apiClient: ApiClient) {
    suspend fun generate(dayRole: String): Result<GenerateResponse> = runCatchingApi {
        apiClient.http.post("/generate") {
            contentType(ContentType.Application.Json); setBody(GenerateRequest(dayRole))
        }.body()
    }
    suspend fun approve(candidateId: String): Result<ApproveResponse> = runCatchingApi {
        apiClient.http.post("/sessions/$candidateId/approve") {
            contentType(ContentType.Application.Json)
        }.body()
    }
    suspend fun programDays(programId: Int): Result<List<String>> = runCatchingApi {
        apiClient.http.get("/programs/$programId/days").body()
    }
    suspend fun pastSessions(): Result<List<SessionSummary>> = runCatchingApi {
        apiClient.http.get("/sessions").body()
    }
    suspend fun sessionLogs(id: Int): Result<LoggedSetsResponse> = runCatchingApi {
        apiClient.http.get("/sessions/$id/logs").body()
    }
}
```

- [ ] **Step 3: Expose it in `AppContainer`** (mirror how `captureRepo`/`wizardRepo` are constructed — `val generateRepo by lazy { GenerateRepo(apiClient) }`).

- [ ] **Step 4: Build to verify it compiles**

Run: `~/projects/IronLog-V2-Client/gradlew :app:assembleDebug`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 5: Commit**

```bash
git -C ~/projects/IronLog-V2-Client add -A
git -C ~/projects/IronLog-V2-Client commit -m "feat(client): Today/History DTOs + GenerateRepo"
```

---

## Task 6 (C2): Today tab (state machine + screen + nav)

**Files:**
- Create: `app/src/main/java/com/jauschua/ironlogv2/ui/screens/today/TodayViewModel.kt`, `TodayScreen.kt`.
- Modify: `app/src/main/java/com/jauschua/ironlogv2/ui/MainActivity.kt` — `Routes.TODAY`, add to `TABS` (leftmost), `startDestination = Routes.TODAY`, `composable(Routes.TODAY){...}`; wire "Continue"→`Routes.CAPTURE`, History link→`Routes.HISTORY`.
- Test: `app/src/test/java/com/jauschua/ironlogv2/ui/today/TodayLogicTest.kt` (new).

**Interfaces:**
- Consumes: `GenerateRepo` (Task 5), `CaptureRepo.today()`, `Routes.DEFAULT_PROGRAM_ID`.
- Produces: a `TodayUiState` sealed hierarchy the screen renders.

- [ ] **Step 1: Write the failing test for the state machine**

Model the state as a pure sealed type so its transitions are unit-testable without Compose:

```kotlin
// TodayLogicTest.kt
import com.jauschua.ironlogv2.ui.screens.today.GenerateOutcomeKind
import com.jauschua.ironlogv2.ui.screens.today.classifyGenerate
import org.junit.Assert.assertEquals
import org.junit.Test

class TodayLogicTest {
    @Test fun nonexhausted_generate_with_preview_is_reviewable() {
        assertEquals(GenerateOutcomeKind.REVIEWABLE, classifyGenerate(exhausted = false, hasPreview = true))
    }
    @Test fun exhausted_generate_is_error() {
        assertEquals(GenerateOutcomeKind.ERROR, classifyGenerate(exhausted = true, hasPreview = false))
    }
    @Test fun nonexhausted_but_null_preview_is_error() {
        assertEquals(GenerateOutcomeKind.ERROR, classifyGenerate(exhausted = false, hasPreview = false))
    }
}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `~/projects/IronLog-V2-Client/gradlew :app:testDebugUnitTest --tests "*TodayLogicTest"`
Expected: FAIL — unresolved `TodayUiState`.

- [ ] **Step 3: Implement `TodayViewModel` + `TodayUiState`**

`TodayUiState` sealed interface with: `Loading`, `HasPlanned(val session: SessionDetailResponse)`, `NoSession(val days: List<String>)`, `Generating`, `Preview(val candidateId: String, val preview: SessionDetailResponse)`, `GenerateError(val msg: String)`, `Approved(val sessionId: Int)`. Extract the branch decision as a pure top-level classifier (file-level, same package) so it's unit-testable and the VM calls it:

```kotlin
enum class GenerateOutcomeKind { REVIEWABLE, ERROR }

/** A generate result is reviewable only when it did not exhaust AND carries a preview. */
fun classifyGenerate(exhausted: Boolean, hasPreview: Boolean): GenerateOutcomeKind =
    if (!exhausted && hasPreview) GenerateOutcomeKind.REVIEWABLE else GenerateOutcomeKind.ERROR
```

The VM's `generate(dayRole)` calls `classifyGenerate(resp.exhausted, resp.preview != null)`: `REVIEWABLE` → `Preview(resp.candidate_id, resp.preview!!)`; `ERROR` → `GenerateError("No valid session could be generated — try again.")`.

VM methods (coroutines over `viewModelScope`, exposing `StateFlow<TodayUiState>`):
- `load()`: `today()` → non-null → `HasPlanned`; null → `programDays(DEFAULT_PROGRAM_ID)` → `NoSession(days)`.
- `generate(dayRole)`: set `Generating`; `generateRepo.generate(dayRole)` → success + `preview != null` → `Preview(candidate_id, preview)`; exhausted/null → `GenerateError`; `Result.failure` → `GenerateError(humanMessage)`.
- `approve()`: from `Preview`, `approve(candidateId)` → `Approved(session_id)` (screen navigates to Capture); failure → `GenerateError`.
- `regenerate(dayRole)`: re-run `generate(dayRole)`.

- [ ] **Step 4: Implement `TodayScreen`**

Render per state: `Loading` spinner; `HasPlanned` → header + "Continue workout" button (`onContinue` → nav Capture); `NoSession` → a day-picker (simple list/dropdown of `days`) + "Generate" button; `Generating` spinner; `Preview` → a **read-only** render of `preview.groups` (reuse the group/exercise/set target rows — key list items by index `gi/ei/si`, NO input fields, NO log buttons) + "Approve" and "Regenerate" buttons; `GenerateError` → message + "Try again"; `Approved` → `LaunchedEffect` navigates to Capture. Add a "History" text button (→ `onHistory`).

- [ ] **Step 5: Wire nav in `MainActivity`**

Add `Routes.TODAY = "today"`, `Routes.HISTORY = "history"`, `Routes.HISTORY_DETAIL = "history/{id}"` + `historyDetail(id)`. Prepend `Tab(Routes.TODAY, "Today", Icons.Filled.Today)` to `TABS`; set `startDestination = Routes.TODAY`. Add `composable(Routes.TODAY){ TodayScreen(onContinue = { nav.navigate(Routes.CAPTURE){...} }, onHistory = { nav.navigate(Routes.HISTORY) }) }`.

- [ ] **Step 6: Run the unit test + build**

Run: `~/projects/IronLog-V2-Client/gradlew :app:testDebugUnitTest --tests "*TodayLogicTest"` → PASS.
Run: `~/projects/IronLog-V2-Client/gradlew :app:assembleDebug` → BUILD SUCCESSFUL.

- [ ] **Step 7: Commit**

```bash
git -C ~/projects/IronLog-V2-Client add -A
git -C ~/projects/IronLog-V2-Client commit -m "feat(today): Today tab — pick-day → generate → review → approve → Capture"
```

---

## Task 7 (C3): History screen + detail

**Files:**
- Create: `app/src/main/java/com/jauschua/ironlogv2/ui/screens/history/HistoryViewModel.kt`, `HistoryScreen.kt`, `HistoryDetailScreen.kt`.
- Modify: `MainActivity.kt` — `composable(Routes.HISTORY)` + `composable(Routes.HISTORY_DETAIL)`.
- Test: `app/src/test/java/com/jauschua/ironlogv2/ui/history/HistoryLogicTest.kt` (new).

**Interfaces:**
- Consumes: `GenerateRepo.pastSessions()`, `.sessionLogs(id)`.
- Produces: `groupLogsByMovement(logs): List<MovementLogs>` where `MovementLogs = (movementName: String, sets: List<LoggedSet>)`, movements in first-appearance order.

- [ ] **Step 1: Write the failing test for grouping**

```kotlin
// HistoryLogicTest.kt
import com.jauschua.ironlogv2.data.api.dto.LoggedSet
import com.jauschua.ironlogv2.ui.screens.history.groupLogsByMovement
import org.junit.Assert.assertEquals
import org.junit.Test

class HistoryLogicTest {
    private fun log(mid: Int, name: String, si: Int) =
        LoggedSet(mid, name, si, reps = 8, load = 165.0, tap = "ON_TARGET", is_warmup = false)

    @Test fun groups_by_movement_in_first_appearance_order() {
        val logs = listOf(log(4, "Bench", 0), log(7, "Pendlay", 0), log(4, "Bench", 1))
        val grouped = groupLogsByMovement(logs)
        assertEquals(listOf("Bench", "Pendlay"), grouped.map { it.movementName })
        assertEquals(2, grouped[0].sets.size)  // both Bench sets together
    }
}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `~/projects/IronLog-V2-Client/gradlew :app:testDebugUnitTest --tests "*HistoryLogicTest"`
Expected: FAIL — unresolved `groupLogsByMovement`.

- [ ] **Step 3: Implement the pure grouping helper**

```kotlin
data class MovementLogs(val movementName: String, val sets: List<LoggedSet>)

/** Group flat logs by movement, preserving first-appearance order (linked map). */
fun groupLogsByMovement(logs: List<LoggedSet>): List<MovementLogs> {
    val byMovement = LinkedHashMap<Int, MutableList<LoggedSet>>()
    for (l in logs) byMovement.getOrPut(l.movement_id) { mutableListOf() }.add(l)
    return byMovement.values.map { MovementLogs(it.first().movement_name, it.toList()) }
}
```

- [ ] **Step 4: Implement `HistoryViewModel` + screens**

- `HistoryViewModel`: `list()` → `pastSessions()` → `StateFlow<UiState<List<SessionSummary>>>`; `detail(id)` → `sessionLogs(id)` → `StateFlow<UiState<LoggedSetsResponse>>`.
- `HistoryScreen(onOpen: (Int)->Unit)`: LazyColumn of rows "`date` · `day_role`" (tap → `onOpen(id)`); empty-state "No completed sessions yet."
- `HistoryDetailScreen(id)`: loads `sessionLogs(id)`, renders `groupLogsByMovement(logs)` — per movement a header + its sets as "`load`×`reps`" (e.g. "165×8"), read-only. Use `UiState.Loading/Error/Success` like the existing screens.

- [ ] **Step 5: Wire nav** — `composable(Routes.HISTORY){ HistoryScreen(onOpen = { id -> nav.navigate(Routes.historyDetail(id)) }) }` and `composable(Routes.HISTORY_DETAIL, arguments = listOf(navArgument("id"){ type = NavType.IntType })){ entry -> HistoryDetailScreen(entry.arguments!!.getInt("id")) }`.

- [ ] **Step 6: Run the unit test + build**

Run: `~/projects/IronLog-V2-Client/gradlew :app:testDebugUnitTest --tests "*HistoryLogicTest"` → PASS.
Run: `~/projects/IronLog-V2-Client/gradlew :app:assembleDebug` → BUILD SUCCESSFUL.

- [ ] **Step 7: Commit**

```bash
git -C ~/projects/IronLog-V2-Client add -A
git -C ~/projects/IronLog-V2-Client commit -m "feat(history): completed-session history + logged-actuals detail"
```

---

## Verification (Tier A, after all tasks)

- Server: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q'` — full suite green.
- Client: `gradlew :app:assembleDebug` + `adb -s 192.168.1.17:36231 install -r ...`.
- **Phone checklist:** Today tab is the landing tab; with no session it shows the day-picker + Generate; pick a day → Generate → preview shows the right workout (loads/reps) → Approve → lands in Capture with the session → log + submit → History lists the completed session → tap shows logged actuals grouped by movement. Regenerate returns a fresh candidate. Picking a day out of order works.

## Routing Plan

| Task | Repo | Delegate to |
|---|---|---|
| 1 S1 candidate preview | server | Claude Code Agent subagent (apply+test) |
| 2 S2 sessions list | server | Claude Code Agent subagent |
| 3 S3 session logs | server | Claude Code Agent subagent |
| 4 S4 program days | server | Claude Code Agent subagent |
| 5 C1 DTOs + repo | client | Claude Code Agent subagent |
| 6 C2 Today tab | client | Claude Code Agent subagent |
| 7 C3 History | client | Claude Code Agent subagent |
| Integration + phone test | both | Tier A (review each diff, install, verify) |

**Delegation ratio: 7/7 implementation tasks delegated (100%).** Tier A does the acceptance-gate review between tasks, integration wiring if any, and the on-device verification. Codex/Gemini are read-only (can't build/install), so the apply+test substrate is Claude Code subagents (per the established cadence).
