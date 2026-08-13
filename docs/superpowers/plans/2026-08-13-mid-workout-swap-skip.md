# Mid-Workout Exercise Swap/Skip Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the athlete swap or skip an exercise's remaining sets mid-workout, from the Android app, without needing a backend edit.

**Architecture:** Two additive DB columns (`PlannedSet.is_skipped`, `PlannedExercise.tier_exercise_id`) plus three new FastAPI endpoints (skip, swap, substitutes) on the server (`~/projects/IronLog-V2`), and a small overflow-menu UI + repo/viewmodel wiring on the Android client (`~/projects/IronLog-V2-Client`). Server tasks land, build, test, and deploy first; client tasks depend on the new endpoints existing.

**Tech Stack:** Python 3.14 / FastAPI / SQLModel / pytest (server); Kotlin / Jetpack Compose / Ktor / JUnit (client).

## Global Constraints

- NO `from __future__ import annotations` in any server `.py` file touched (project-wide constraint, breaks SQLModel `Relationship()` type resolution).
- Migrations are forward-only numbered `.sql` files in `~/projects/IronLog-V2/deploy/migrations/`, tracked via `ironlog/migrate.py` — never edit an already-applied migration file.
- Server DTOs in `ironlog/api/schemas_capture.py` are mirrored field-for-field by Kotlin DTOs in `IronLog-V2-Client/.../data/api/dto/CaptureModels.kt` — any field added to one must be added to the other in the same task where practical, or the very next task if sequencing requires it (Task 2 vs Task 5 here).
- Server runs via `uvicorn ironlog.api.app:app --host 0.0.0.0` (not localhost) so the phone client can reach it — do not change this.
- Every server task's tests run via `~/projects/IronLog-V2/.venv/bin/pytest -q` (or a `-k`/path-scoped subset while iterating, full suite before merge).
- Every client task's build/tests run via `~/projects/IronLog-V2-Client/gradlew :app:assembleDebug` and `:app:testDebugUnitTest`.
- **Task 1 (the schema migration) requires a [HUMAN GATE] before its worktree is dispatched** — DB schema changes are on CLAUDE.md's Forbidden Without Pause list. Stop and get explicit user authorization before creating Task 1's worktree, even though the rest of this plan can be prepared in parallel.

---

### Task 1: Schema migration — `PlannedSet.is_skipped` + `PlannedExercise.tier_exercise_id`

**[HUMAN GATE — DB schema change. Do not dispatch this task's worktree without explicit user authorization, per CLAUDE.md's Forbidden Without Pause list.]**

**Files:**
- Create: `deploy/migrations/040_planned_set_skip_and_exercise_slot.sql`
- Modify: `ironlog/models/session.py` (`PlannedSet`, `PlannedExercise` classes)
- Test: `tests/test_migrations.py`

**Interfaces:**
- Produces: `PlannedSet.is_skipped: bool` (default `False`), `PlannedExercise.tier_exercise_id: Optional[int]` (default `None`, FK to `tierexercise.id`). Every later task in this plan reads/writes these two fields by exactly these names.

- [ ] **Step 1: Write the migration**

```sql
-- 040_planned_set_skip_and_exercise_slot.sql — mid-workout swap/skip support.
-- is_skipped: a not-yet-logged PlannedSet the athlete chose to skip mid-session
-- (no SetLog is ever written for it). tier_exercise_id: the program slot that
-- generated this PlannedExercise, persisted so a "make permanent" swap can
-- attach a SlotMovementOverride to the right slot (previously this link
-- existed only in-memory during generation and was discarded — see
-- assembler.py's _build_exercise tier_exercise_id parameter). Both additive,
-- nullable/defaulted so existing rows are unaffected. ADD COLUMN is atomic
-- in SQLite.
ALTER TABLE plannedset ADD COLUMN is_skipped BOOLEAN NOT NULL DEFAULT 0;
ALTER TABLE plannedexercise ADD COLUMN tier_exercise_id INTEGER REFERENCES tierexercise(id);
```

- [ ] **Step 2: Update the SQLModel classes to match**

In `ironlog/models/session.py`, find `class PlannedExercise` (around line 52) and add the new field after `movement_id`:

```python
class PlannedExercise(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    group_id: int = Field(foreign_key="exercisegroup.id", index=True)
    movement_id: int = Field(foreign_key="movement.id", index=True)
    tier_exercise_id: Optional[int] = Field(default=None, foreign_key="tierexercise.id")
    order_index: int
    scheme: Scheme
    objective: Objective

    group: Optional[ExerciseGroup] = Relationship(back_populates="exercises")
    planned_sets: List["PlannedSet"] = Relationship(back_populates="planned_exercise")
```

Find `class PlannedSet` (around line 64) and add `is_skipped` after `is_warmup`:

```python
class PlannedSet(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    planned_exercise_id: int = Field(foreign_key="plannedexercise.id", index=True)
    set_index: int
    set_role: SetRole
    is_warmup: bool = False
    is_skipped: bool = False

    target_load: Optional[float] = None
    # ...(rest of the class unchanged)
```

- [ ] **Step 3: Write a migration regression test**

Add to `tests/test_migrations.py` (follow the file's existing pattern — check an existing test in that file for the exact `sqlite3`/engine setup it uses before writing this one; the assertion below is the contract regardless of harness style):

```python
def test_040_adds_is_skipped_and_tier_exercise_id(tmp_path):
    from sqlalchemy import create_engine, text
    from ironlog.migrate import apply_pending

    db_path = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{db_path}")
    # Bring the DB up to the schema state 040 depends on first.
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE plannedset (id INTEGER PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE tierexercise (id INTEGER PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE plannedexercise (id INTEGER PRIMARY KEY)"))
    applied = apply_pending(engine)
    assert "040_planned_set_skip_and_exercise_slot" in applied
    with engine.connect() as conn:
        cols_ps = {row[1] for row in conn.execute(text("PRAGMA table_info(plannedset)"))}
        cols_pe = {row[1] for row in conn.execute(text("PRAGMA table_info(plannedexercise)"))}
    assert "is_skipped" in cols_ps
    assert "tier_exercise_id" in cols_pe
```

- [ ] **Step 4: Run the test**

Run: `~/projects/IronLog-V2/.venv/bin/pytest tests/test_migrations.py -v -k 040`
Expected: PASS

- [ ] **Step 5: Run the full suite to confirm nothing else broke**

Run: `~/projects/IronLog-V2/.venv/bin/pytest -q`
Expected: all tests pass (baseline was 701 passed as of 2026-08-13; this task adds one more).

- [ ] **Step 6: Commit**

```bash
git add deploy/migrations/040_planned_set_skip_and_exercise_slot.sql ironlog/models/session.py tests/test_migrations.py
git commit -m "feat(schema): add PlannedSet.is_skipped and PlannedExercise.tier_exercise_id"
```

**Deploy note (not part of this task's worktree — handled at merge time per CLAUDE.md's Deploy Gate):** this migration must run on the live server (`ironlog/migrate.py` runs automatically via the systemd `ExecStartPre`, so a normal `sudo systemctl restart ironlogv2` after merging applies it) — Class 2 (schema/data migration), always a [HUMAN GATE] restart per CLAUDE.md's Deploy Gate table, backup-first.

---

### Task 2: Server — skip endpoint

**Files:**
- Modify: `ironlog/api/app.py` (new endpoint, near the existing `/sessions/{session_id}/submit` and capture-read-path endpoints around line 401-510)
- Modify: `ironlog/api/schemas_capture.py` (`PlannedSetOut.is_skipped` field)
- Test: `tests/test_capture_skip_swap.py` (new file)

**Interfaces:**
- Consumes: `PlannedSet.is_skipped` (Task 1).
- Produces: `POST /sessions/{session_id}/exercises/{exercise_id}/skip` → `ExerciseOut` (existing schema, now carrying `is_skipped` per set). Task 6 (client skip UX) calls this exact path and shape.

- [ ] **Step 1: Add `is_skipped` to the response schema**

In `ironlog/api/schemas_capture.py`, `PlannedSetOut` (around line 54):

```python
class PlannedSetOut(BaseModel):
    id: int
    set_index: int
    set_role: str
    is_warmup: bool
    is_skipped: bool = False
    target_load: Optional[float] = None
    target_reps_low: Optional[int] = None
    target_reps_high: Optional[int] = None
    target_rpe: Optional[float] = None
    target_unassisted_reps: Optional[int] = None
    target_assisted_reps: Optional[int] = None
    target_plates: Optional[float] = None
    band_pair_id: Optional[int] = None
    target_felt_peak: Optional[float] = None
    band_config: Optional[List[int]] = None
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_capture_skip_swap.py`:

```python
"""tests/test_capture_skip_swap.py — mid-workout skip/swap endpoints (Tasks 2-4).

Uses the real `gen_db` fixture (conftest.py). NO from __future__ import
annotations (project-wide constraint).
"""
from datetime import date

from fastapi.testclient import TestClient
from sqlmodel import select

from ironlog.api.app import app, get_session
from ironlog.models.library import Movement
from ironlog.models.session import (
    ExerciseGroup, PlannedExercise, PlannedSet, Session as WorkoutSession,
)
from ironlog.models.enums import GroupType, Objective, Scheme, SessionStatus, SetRole


def _make_planned_session(db, movement_name="Bench Press [PB]"):
    mv = db.exec(select(Movement).where(Movement.name == movement_name)).one()
    ws = WorkoutSession(date=date.today(), day_role="D1 Upper Push",
                        phase="STAB", status=SessionStatus.PLANNED)
    db.add(ws); db.commit(); db.refresh(ws)
    grp = ExerciseGroup(session_id=ws.id, order_index=0, group_type=GroupType.STRAIGHT, rounds=1)
    db.add(grp); db.commit(); db.refresh(grp)
    pe = PlannedExercise(group_id=grp.id, movement_id=mv.id, order_index=0,
                         scheme=Scheme.STRAIGHT, objective=Objective.PROGRESS)
    db.add(pe); db.commit(); db.refresh(pe)
    for i in range(3):
        db.add(PlannedSet(planned_exercise_id=pe.id, set_index=i, set_role=SetRole.WORKING,
                          target_load=100.0, target_reps_low=4, target_reps_high=6))
    db.commit()
    return ws, pe


def test_skip_marks_only_unlogged_sets_and_is_idempotent(gen_db):
    ws, pe = _make_planned_session(gen_db)
    app.dependency_overrides[get_session] = lambda: gen_db
    client = TestClient(app)

    resp = client.post(f"/sessions/{ws.id}/exercises/{pe.id}/skip")
    assert resp.status_code == 200
    body = resp.json()
    assert all(s["is_skipped"] for s in body["planned_sets"])

    # Idempotent: calling again on an already-fully-skipped exercise is a no-op, still 200.
    resp2 = client.post(f"/sessions/{ws.id}/exercises/{pe.id}/skip")
    assert resp2.status_code == 200
    assert all(s["is_skipped"] for s in resp2.json()["planned_sets"])
    app.dependency_overrides.clear()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `~/projects/IronLog-V2/.venv/bin/pytest tests/test_capture_skip_swap.py -v`
Expected: FAIL with `404 Not Found` (the endpoint doesn't exist yet) or a connection/route error.

- [ ] **Step 4: Add the endpoint**

In `ironlog/api/app.py`, add near the other `/sessions/{session_id}/...` capture endpoints (after `submit_session`, before the "Notes review path" section comment around line 471):

```python
@app.post("/sessions/{session_id}/exercises/{exercise_id}/skip", response_model=ExerciseOut)
def skip_exercise(session_id: int, exercise_id: int, db: Session = Depends(get_session)):
    """Mark every not-yet-logged PlannedSet under this exercise as skipped.

    Idempotent. Already-logged sets (a SetLog row referencing them exists) are
    left untouched -- this only affects sets nobody has logged yet.
    """
    from ..models.session import PlannedExercise as PE, PlannedSet as PS
    pe = db.get(PE, exercise_id)
    if pe is None or pe.group.session_id != session_id:
        raise HTTPException(404, "exercise not found in this session")
    logged_planned_set_ids = {
        sl.planned_set_id for sl in db.exec(
            select(SetLog).where(SetLog.session_id == session_id)
        ).all() if sl.planned_set_id is not None
    }
    for ps in pe.planned_sets:
        if ps.id not in logged_planned_set_ids:
            ps.is_skipped = True
            db.add(ps)
    db.commit()
    db.refresh(pe)
    return _serialize_exercise(pe, db)
```

This calls a small serialization helper factored out of the existing `_serialize_session`'s per-exercise loop (around line 700-720) rather than duplicating it. Add this helper just above `_serialize_session`:

```python
def _serialize_exercise(pe, db) -> ExerciseOut:
    mv = db.get(Movement, pe.movement_id)
    sets_out = [PlannedSetOut(
        id=ps.id, set_index=ps.set_index, set_role=ps.set_role.value,
        is_warmup=ps.is_warmup, is_skipped=ps.is_skipped, target_load=ps.target_load,
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
    return ExerciseOut(
        id=pe.id, movement_id=pe.movement_id, movement_name=(mv.name if mv else ""),
        order_index=pe.order_index, scheme=pe.scheme.value, objective=pe.objective.value,
        unit_hint=unit_hint, unilateral=(mv.unilateral if mv else False),
        planned_sets=sets_out,
    )
```

Then simplify `_serialize_session`'s per-exercise loop (around line 720-732) to call `_serialize_exercise(pe, db)` instead of duplicating the same fields, and update the `PlannedSetOut(...)` construction inside it to also pass `is_skipped=ps.is_skipped` if you keep the loop separate — prefer the single shared helper to avoid the two call sites drifting.

- [ ] **Step 5: Run test to verify it passes**

Run: `~/projects/IronLog-V2/.venv/bin/pytest tests/test_capture_skip_swap.py -v`
Expected: PASS

- [ ] **Step 6: Run the full suite**

Run: `~/projects/IronLog-V2/.venv/bin/pytest -q`
Expected: all pass (no regression from the `_serialize_session` refactor — this is the risk point a reviewer should focus on).

- [ ] **Step 7: Commit**

```bash
git add ironlog/api/app.py ironlog/api/schemas_capture.py tests/test_capture_skip_swap.py
git commit -m "feat(capture): mid-workout skip-exercise endpoint"
```

**Review routing:** this task touches session-completion-adjacent logic and refactors the shared serialization path used by every session read — route to Opus review per CLAUDE.md's Review Gate (non-trivial logic touching a shared surface), not review-exempt.

---

### Task 3: Server — swap endpoint

**Files:**
- Modify: `ironlog/generation/assembler.py` (new `prescribe_swap_sets` function)
- Modify: `ironlog/generation/context.py` (import check only — `resolve_context` already exists, no changes expected unless the import surfaces a circular-import issue, in which case resolve by importing inside the function body, not at module level)
- Modify: `ironlog/api/app.py` (new endpoint + request schema)
- Modify: `ironlog/api/schemas_capture.py` (`SwapExerciseRequest`)
- Test: `tests/test_capture_skip_swap.py` (append)

**Interfaces:**
- Consumes: `PlannedExercise.tier_exercise_id` (Task 1), `_serialize_exercise` (Task 2).
- Produces: `POST /sessions/{session_id}/exercises/{exercise_id}/swap` → `ExerciseOut`. Task 6 (client swap UX) calls this exact path and body shape `{new_movement_id: int, make_permanent: bool}`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_capture_skip_swap.py`:

```python
def test_swap_recomputes_remaining_sets_and_updates_movement(gen_db):
    ws, pe = _make_planned_session(gen_db, movement_name="Bench Press [PB]")
    new_mv = gen_db.exec(select(Movement).where(Movement.name == "Incline DB Press [DB + BENCH]")).one()
    app.dependency_overrides[get_session] = lambda: gen_db
    client = TestClient(app)

    resp = client.post(
        f"/sessions/{ws.id}/exercises/{pe.id}/swap",
        json={"new_movement_id": new_mv.id, "make_permanent": False},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["movement_id"] == new_mv.id
    assert body["movement_name"] == "Incline DB Press [DB + BENCH]"
    assert len(body["planned_sets"]) == 3
    app.dependency_overrides.clear()


def test_swap_leaves_already_logged_setlog_movement_id_untouched(gen_db):
    ws, pe = _make_planned_session(gen_db, movement_name="Bench Press [PB]")
    old_mv_id = pe.movement_id
    logged_set = pe.planned_sets[0]
    gen_db.add(SetLog(planned_set_id=logged_set.id, session_id=ws.id, movement_id=old_mv_id,
                      set_index=0, actual_load=100.0, actual_reps=5,
                      feedback_tap=FeedbackTap.RIGHT_AT_LIMIT))
    gen_db.commit()
    new_mv = gen_db.exec(select(Movement).where(Movement.name == "Incline DB Press [DB + BENCH]")).one()
    app.dependency_overrides[get_session] = lambda: gen_db
    client = TestClient(app)

    client.post(f"/sessions/{ws.id}/exercises/{pe.id}/swap",
               json={"new_movement_id": new_mv.id, "make_permanent": False})

    row = gen_db.exec(select(SetLog).where(SetLog.planned_set_id == logged_set.id)).one()
    assert row.movement_id == old_mv_id
    app.dependency_overrides.clear()


def test_swap_make_permanent_writes_slot_override(gen_db):
    from ironlog.models.program import SlotMovementOverride
    from ironlog.models.enums import OverrideType

    ws, pe = _make_planned_session(gen_db, movement_name="Bench Press [PB]")
    te = gen_db.exec(select(TierExercise).where(TierExercise.slot_id == "d1_t1")).first()
    pe.tier_exercise_id = te.id
    gen_db.add(pe); gen_db.commit()
    new_mv = gen_db.exec(select(Movement).where(Movement.name == "Incline DB Press [DB + BENCH]")).one()
    app.dependency_overrides[get_session] = lambda: gen_db
    client = TestClient(app)

    resp = client.post(f"/sessions/{ws.id}/exercises/{pe.id}/swap",
                       json={"new_movement_id": new_mv.id, "make_permanent": True})
    assert resp.status_code == 200
    ov = gen_db.exec(select(SlotMovementOverride).where(
        SlotMovementOverride.tier_exercise_id == te.id,
        SlotMovementOverride.override_type == OverrideType.MOVEMENT,
        SlotMovementOverride.active == True)).first()  # noqa: E712
    assert ov is not None
    assert ov.override_movement_id == new_mv.id
    app.dependency_overrides.clear()


def test_swap_make_permanent_without_tier_exercise_id_409s(gen_db):
    ws, pe = _make_planned_session(gen_db, movement_name="Bench Press [PB]")
    assert pe.tier_exercise_id is None
    new_mv = gen_db.exec(select(Movement).where(Movement.name == "Incline DB Press [DB + BENCH]")).one()
    app.dependency_overrides[get_session] = lambda: gen_db
    client = TestClient(app)

    resp = client.post(f"/sessions/{ws.id}/exercises/{pe.id}/swap",
                       json={"new_movement_id": new_mv.id, "make_permanent": True})
    assert resp.status_code == 409
    app.dependency_overrides.clear()
```

Add the two additional imports these tests need at the top of the file: `from ironlog.models.session import SetLog` and `from ironlog.models.enums import FeedbackTap`, `from ironlog.models.program import TierExercise`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `~/projects/IronLog-V2/.venv/bin/pytest tests/test_capture_skip_swap.py -v -k swap`
Expected: FAIL (404, no swap endpoint yet).

- [ ] **Step 3: Add `prescribe_swap_sets` to assembler.py**

In `ironlog/generation/assembler.py`, add near `_build_exercise` (the function it reuses):

```python
def prescribe_swap_sets(new_movement: Movement, day_role: str,
                        tier_exercise_id: Optional[int],
                        rep_low: Optional[int], rep_high: Optional[int],
                        rpe_cap: Optional[float], db: DBSession) -> List[PlannedSet]:
    """Build a fresh 3-set prescription for `new_movement`, matching the exact
    per-set structure (target_load / target_reps / HT plates+bands / etc.)
    _build_exercise produces during full-session generation -- reused here
    for a mid-session swap so one exercise's remaining sets can be
    recomputed without regenerating the whole session.

    Deliberately calls _build_exercise with is_anchor=False: ramp sets are
    always logged first (set_index -3..-1, before any WORKING set), so a
    mid-exercise swap never needs to regenerate them even if the swapped
    slot was originally an anchor.

    Returns transient PlannedSet objects (not persisted) in set_index order
    (0, 1, 2) -- the caller matches them onto the session's existing
    not-yet-logged rows by set_index and copies over the target_* fields;
    it does NOT create or delete PlannedSet rows.
    """
    from .context import resolve_context
    from .skeleton import lay_skeleton

    sk = lay_skeleton(day_role, db)
    week_keyer = lambda d: (d.isocalendar()[0], d.isocalendar()[1])  # noqa: E731
    ctx = resolve_context(day_role, sk, db, week_keyer)
    band_inventory = [Band(bp.id, bp.bottom_lb, bp.peak_lb, bp.usable)
                      for bp in db.exec(select(BandPair)).all()]
    ex = _build_exercise(
        new_movement, 0, ctx, db, prospective={}, day_role=day_role,
        is_anchor=False, rep_low=rep_low, rep_high=rep_high, rpe_cap=rpe_cap,
        band_inventory=band_inventory, prospective_ht={}, prospective_ht_unified={},
        tier_exercise_id=tier_exercise_id,
    )
    return ex.planned_sets
```

- [ ] **Step 4: Add the swap request schema**

In `ironlog/api/schemas_capture.py`, add near `SubmitRequest`:

```python
class SwapExerciseRequest(BaseModel):
    new_movement_id: int
    make_permanent: bool = False
```

- [ ] **Step 5: Add the swap endpoint**

In `ironlog/api/app.py`, add directly after `skip_exercise` (Task 2):

```python
@app.post("/sessions/{session_id}/exercises/{exercise_id}/swap", response_model=ExerciseOut)
def swap_exercise(session_id: int, exercise_id: int, req: SwapExerciseRequest,
                  db: Session = Depends(get_session)):
    """Replace the movement filling this exercise's remaining (not logged,
    not skipped) sets. Already-logged SetLog rows keep their own
    movement_id, captured at log time -- untouched here.
    """
    from ..generation.assembler import prescribe_swap_sets
    from ..models.session import PlannedExercise as PE
    from ..models.program import SlotMovementOverride, TierExercise as TE
    from ..models.enums import OverrideType

    pe = db.get(PE, exercise_id)
    if pe is None or pe.group.session_id != session_id:
        raise HTTPException(404, "exercise not found in this session")
    new_mv = db.get(Movement, req.new_movement_id)
    if new_mv is None or new_mv.status != Status.ACTIVE:
        raise HTTPException(422, "new_movement_id must reference an ACTIVE movement")
    if req.make_permanent and pe.tier_exercise_id is None:
        raise HTTPException(409, "this exercise has no tier_exercise_id (legacy session) "
                                 "-- cannot make a permanent program change from it")

    logged_planned_set_ids = {
        sl.planned_set_id for sl in db.exec(
            select(SetLog).where(SetLog.session_id == session_id)
        ).all() if sl.planned_set_id is not None
    }
    remaining = sorted(
        [ps for ps in pe.planned_sets
         if ps.id not in logged_planned_set_ids and not ps.is_skipped],
        key=lambda ps: ps.set_index,
    )
    te = db.get(TE, pe.tier_exercise_id) if pe.tier_exercise_id is not None else None
    rep_low = te.rep_low if te else (remaining[0].target_reps_low if remaining else None)
    rep_high = te.rep_high if te else (remaining[0].target_reps_high if remaining else None)
    rpe_cap = te.rpe_cap if te else None

    fresh_sets = prescribe_swap_sets(new_mv, pe.group.session.day_role, pe.tier_exercise_id,
                                     rep_low, rep_high, rpe_cap, db)
    fresh_by_index = {s.set_index: s for s in fresh_sets}
    for ps in remaining:
        fresh = fresh_by_index.get(ps.set_index)
        if fresh is None:
            continue
        ps.target_load = fresh.target_load
        ps.target_reps_low = fresh.target_reps_low
        ps.target_reps_high = fresh.target_reps_high
        ps.target_rpe = fresh.target_rpe
        ps.target_unassisted_reps = fresh.target_unassisted_reps
        ps.target_assisted_reps = fresh.target_assisted_reps
        ps.target_plates = fresh.target_plates
        ps.band_pair_id = fresh.band_pair_id
        ps.target_felt_peak = fresh.target_felt_peak
        ps.band_config = fresh.band_config
        db.add(ps)

    pe.movement_id = new_mv.id
    db.add(pe)

    if req.make_permanent:
        existing = db.exec(select(SlotMovementOverride).where(
            SlotMovementOverride.tier_exercise_id == pe.tier_exercise_id,
            SlotMovementOverride.override_type == OverrideType.MOVEMENT,
            SlotMovementOverride.active == True)).first()  # noqa: E712
        if existing is not None:
            existing.active = False
            db.add(existing)
        db.add(SlotMovementOverride(
            tier_exercise_id=pe.tier_exercise_id, override_type=OverrideType.MOVEMENT,
            override_movement_id=new_mv.id,
        ))

    db.commit()
    db.refresh(pe)
    return _serialize_exercise(pe, db)
```

Check the top of `ironlog/api/app.py` for an existing `Status` import (from `..models.enums` or `..models.library`) — if not already imported at module level, add it to the existing enum-import line rather than a fresh import statement.

- [ ] **Step 6: Run tests to verify they pass**

Run: `~/projects/IronLog-V2/.venv/bin/pytest tests/test_capture_skip_swap.py -v -k swap`
Expected: PASS (4 tests)

- [ ] **Step 7: Run the full suite**

Run: `~/projects/IronLog-V2/.venv/bin/pytest -q`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add ironlog/generation/assembler.py ironlog/api/app.py ironlog/api/schemas_capture.py tests/test_capture_skip_swap.py
git commit -m "feat(capture): mid-workout swap-exercise endpoint (today-only or permanent)"
```

**Review routing:** mandatory Opus review per CLAUDE.md's Review Gate — this is non-trivial logic (reuses generation internals outside their normal call path) and can be wrong while still building (e.g. a subtly wrong `set_index` match would silently prescribe garbage). Not review-exempt.

---

### Task 4: Server — substitutes endpoint

**Files:**
- Modify: `ironlog/api/app.py`
- Test: `tests/test_capture_skip_swap.py` (append)

**Interfaces:**
- Produces: `GET /movements/substitutes/{movement_id}` → `List[Movement]` (existing `Movement` response model already used by `GET /movements`). Task 6 (client swap picker) calls this exact path for the "suggested substitutes" list.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_capture_skip_swap.py`:

```python
def test_substitutes_share_primary_muscle_and_exclude_self(gen_db):
    bench = gen_db.exec(select(Movement).where(Movement.name == "Bench Press [PB]")).one()
    app.dependency_overrides[get_session] = lambda: gen_db
    client = TestClient(app)

    resp = client.get(f"/movements/substitutes/{bench.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert all(m["primary_muscle"] == bench.primary_muscle.value for m in body)
    assert all(m["id"] != bench.id for m in body)
    assert all(m["status"] == "ACTIVE" for m in body)
    app.dependency_overrides.clear()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/projects/IronLog-V2/.venv/bin/pytest tests/test_capture_skip_swap.py -v -k substitutes`
Expected: FAIL (404)

- [ ] **Step 3: Add the endpoint**

In `ironlog/api/app.py`, near the existing `GET /movements/{movement_id}` endpoint (around line 201):

```python
@app.get("/movements/substitutes/{movement_id}", response_model=List[Movement])
def movement_substitutes(movement_id: int, db: Session = Depends(get_session)):
    """ACTIVE movements sharing this movement's primary_muscle, excluding
    itself -- powers the swap picker's 'suggested substitutes' list.
    """
    mv = db.get(Movement, movement_id)
    if mv is None:
        raise HTTPException(404, "movement not found")
    if mv.primary_muscle is None:
        return []
    return db.exec(select(Movement).where(
        Movement.primary_muscle == mv.primary_muscle,
        Movement.id != movement_id,
        Movement.status == Status.ACTIVE,
    ).order_by(Movement.name)).all()
```

This route MUST be registered before `GET /movements/{movement_id}` in file order, or FastAPI will match `/movements/substitutes/5` against the `{movement_id}` path param of the plain movement-lookup route first (since `substitutes` would parse as an invalid int and 422, not silently misroute -- but placing it first avoids relying on that and matches the plain reading of the URL). If `/movements/{movement_id}` already appears earlier in the file, move this new route above it.

- [ ] **Step 4: Run test to verify it passes**

Run: `~/projects/IronLog-V2/.venv/bin/pytest tests/test_capture_skip_swap.py -v -k substitutes`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `~/projects/IronLog-V2/.venv/bin/pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add ironlog/api/app.py tests/test_capture_skip_swap.py
git commit -m "feat(capture): movement-substitutes endpoint for the swap picker"
```

**Review routing:** review-exempt (additive read-only endpoint, no state mutation, straightforward filter query) — log the routing reason in the merge commit per CLAUDE.md's Review Gate.

**Deploy note:** Tasks 2-4 are Class 1 (code-only restart) per CLAUDE.md's Deploy Gate — bundle them into one `sudo systemctl restart ironlogv2` + smoke-check (`curl` the new endpoints) after Task 4 merges, following the active-use check first.

---

### Task 5: Client — DTOs + repo wiring for skip/swap

**Files:**
- Modify: `~/projects/IronLog-V2-Client/app/src/main/java/com/jauschua/ironlogv2/data/api/dto/CaptureModels.kt`
- Modify: `~/projects/IronLog-V2-Client/app/src/main/java/com/jauschua/ironlogv2/data/repo/CaptureRepo.kt`
- Test: `~/projects/IronLog-V2-Client/app/src/test/java/com/jauschua/ironlogv2/data/repo/CaptureRepoSwapSkipTest.kt` (new file)

**Interfaces:**
- Consumes: `POST /sessions/{id}/exercises/{id}/skip`, `POST /sessions/{id}/exercises/{id}/swap`, `GET /movements/substitutes/{id}` (Tasks 2-4).
- Produces: `CaptureRepo.skipExercise(sessionId, exerciseId): Result<ExerciseOut>`, `CaptureRepo.swapExercise(sessionId, exerciseId, newMovementId, makePermanent): Result<ExerciseOut>`, `CaptureRepo.substitutesFor(movementId): Result<List<MovementSummary>>`. Task 6 (UI) calls these exact signatures.

- [ ] **Step 1: Add `is_skipped` to `PlannedSetOut` and the new DTOs**

In `CaptureModels.kt`, update `PlannedSetOut`:

```kotlin
@Serializable data class PlannedSetOut(
    val id: Int, val set_index: Int, val set_role: String, val is_warmup: Boolean,
    val is_skipped: Boolean = false,
    val target_load: Double? = null, val target_reps_low: Int? = null,
    val target_reps_high: Int? = null, val target_rpe: Double? = null,
    val target_unassisted_reps: Int? = null, val target_assisted_reps: Int? = null,
    val target_plates: Double? = null, val band_pair_id: Int? = null, val target_felt_peak: Double? = null,
    val band_config: List<Int>? = null,
)
```

Add near the bottom of the file:

```kotlin
@Serializable data class SwapExerciseRequest(
    val new_movement_id: Int, val make_permanent: Boolean = false,
)
@Serializable data class MovementSummary(
    val id: Int, val name: String, val primary_muscle: String? = null, val status: String,
)
```

(`MovementSummary` is a trimmed view of the server's full `Movement` model — check `~/projects/IronLog-V2/ironlog/models/library.py`'s `Movement` class for the exact JSON field names `id`/`name`/`primary_muscle`/`status` serialize as, in case any differ from the Python attribute name; adjust field names here to match if the server uses a custom serializer alias.)

- [ ] **Step 2: Write the failing repo test**

Create `CaptureRepoSwapSkipTest.kt` (follow the existing test-doubling pattern used by other `CaptureRepo*Test.kt` files in the same directory — check `CaptureRepoTest.kt` for how it fakes `ApiClient`/`HttpClient` before writing this):

```kotlin
package com.jauschua.ironlogv2.data.repo

import com.jauschua.ironlogv2.data.api.dto.ExerciseOut
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class CaptureRepoSwapSkipTest {

    @Test
    fun `skipExercise posts to the skip endpoint and returns the updated exercise`() = runTest {
        // Arrange a repo whose ApiClient's mock engine returns a canned ExerciseOut
        // for POST /sessions/7/exercises/3/skip -- follow this file's sibling
        // CaptureRepoTest.kt for the exact MockEngine/ApiClient construction pattern.
        // Assert: result.isSuccess, and the request path/method match exactly.
    }

    @Test
    fun `swapExercise posts new_movement_id and make_permanent in the request body`() = runTest {
        // Arrange similarly for POST /sessions/7/exercises/3/swap; assert the
        // captured request body deserializes to SwapExerciseRequest(new_movement_id = 42,
        // make_permanent = true) when swapExercise(7, 3, 42, true) is called.
    }

    @Test
    fun `substitutesFor gets the substitutes endpoint for the given movement id`() = runTest {
        // Arrange for GET /movements/substitutes/42; assert result.isSuccess with
        // the canned list of MovementSummary returned unchanged.
    }
}
```

Fill in each test body using the exact MockEngine/ApiClient construction already established by `CaptureRepoTest.kt` in this directory (read that file first — do not invent a different test-doubling approach for this sibling file).

- [ ] **Step 3: Run tests to verify they fail**

Run: `~/projects/IronLog-V2-Client/gradlew :app:testDebugUnitTest --tests "com.jauschua.ironlogv2.data.repo.CaptureRepoSwapSkipTest"`
Expected: FAIL (compile error — the repo functions don't exist yet).

- [ ] **Step 4: Add the repo functions**

In `CaptureRepo.kt`, add after `submit`:

```kotlin
    suspend fun skipExercise(sessionId: Int, exerciseId: Int): Result<ExerciseOut> = runCatchingApi {
        apiClient.http.post("/sessions/$sessionId/exercises/$exerciseId/skip").body()
    }

    suspend fun swapExercise(
        sessionId: Int, exerciseId: Int, newMovementId: Int, makePermanent: Boolean,
    ): Result<ExerciseOut> = runCatchingApi {
        apiClient.http.post("/sessions/$sessionId/exercises/$exerciseId/swap") {
            contentType(ContentType.Application.Json)
            setBody(SwapExerciseRequest(newMovementId, makePermanent))
        }.body()
    }

    suspend fun substitutesFor(movementId: Int): Result<List<MovementSummary>> = runCatchingApi {
        apiClient.http.get("/movements/substitutes/$movementId").body()
    }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `~/projects/IronLog-V2-Client/gradlew :app:testDebugUnitTest --tests "com.jauschua.ironlogv2.data.repo.CaptureRepoSwapSkipTest"`
Expected: PASS

- [ ] **Step 6: Run the full client unit test suite**

Run: `~/projects/IronLog-V2-Client/gradlew :app:testDebugUnitTest`
Expected: all pass, no regressions.

- [ ] **Step 7: Commit**

```bash
cd ~/projects/IronLog-V2-Client
git add app/src/main/java/com/jauschua/ironlogv2/data/api/dto/CaptureModels.kt \
       app/src/main/java/com/jauschua/ironlogv2/data/repo/CaptureRepo.kt \
       app/src/test/java/com/jauschua/ironlogv2/data/repo/CaptureRepoSwapSkipTest.kt
git commit -m "feat(capture): repo wiring for mid-workout skip/swap/substitutes"
```

**Review routing:** review-exempt (mechanical DTO/repo additions mirroring an already-reviewed server contract, test-covered) — log the routing reason in the merge commit.

---

### Task 6: Client — capture cursor + overflow-menu UX

**Files:**
- Modify: `~/projects/IronLog-V2-Client/app/src/main/java/com/jauschua/ironlogv2/ui/screens/capture/CaptureViewModel.kt`
- Modify: `~/projects/IronLog-V2-Client/app/src/main/java/com/jauschua/ironlogv2/ui/screens/capture/CaptureScreen.kt`
- Create: `~/projects/IronLog-V2-Client/app/src/main/java/com/jauschua/ironlogv2/ui/screens/capture/ExerciseActionsMenu.kt`
- Create: `~/projects/IronLog-V2-Client/app/src/main/java/com/jauschua/ironlogv2/ui/screens/capture/SwapExerciseSheet.kt`
- Test: `~/projects/IronLog-V2-Client/app/src/test/java/com/jauschua/ironlogv2/ui/capture/CaptureScreenLogicTest.kt` (append — this file already covers `flattenPrescription`/cursor-logic pure functions per its name)

**Interfaces:**
- Consumes: `CaptureRepo.skipExercise` / `.swapExercise` / `.substitutesFor` (Task 5), `PlannedSetOut.is_skipped` (Task 5's DTO update).
- Produces: `CaptureViewModel.skipExercise(exerciseId: Int)`, `CaptureViewModel.swapExercise(exerciseId: Int, newMovementId: Int, makePermanent: Boolean)`, `CaptureViewModel.loadSubstitutes(movementId: Int): List<MovementSummary>` — called by the new Compose UI in this task.

- [ ] **Step 1: Write the failing cursor test**

Append to `CaptureScreenLogicTest.kt` (match its existing style — check the file for how it constructs `GroupOut`/`ExerciseOut`/`PlannedSetOut` fixtures before writing new ones):

```kotlin
@Test
fun `flattenPrescription-derived resume cursor skips is_skipped sets`() {
    val skippedSet = PlannedSetOut(id = 1, set_index = 0, set_role = "WORKING", is_warmup = false,
        is_skipped = true)
    val nextSet = PlannedSetOut(id = 2, set_index = 1, set_role = "WORKING", is_warmup = false,
        is_skipped = false)
    val exercise = ExerciseOut(id = 10, movement_id = 100, movement_name = "Bench Press [PB]",
        order_index = 0, scheme = "STRAIGHT", objective = "PROGRESS",
        planned_sets = listOf(skippedSet, nextSet))
    val group = GroupOut(id = 1, order_index = 0, group_type = "STRAIGHT", rounds = 1,
        exercises = listOf(exercise))

    val flattened = flattenPrescription(listOf(group))
    val resumeSet = flattened.firstOrNull { !it.is_skipped }
    assertEquals(2, resumeSet?.id)
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/projects/IronLog-V2-Client/gradlew :app:testDebugUnitTest --tests "*CaptureScreenLogicTest*"`
Expected: this specific test FAILS only if `is_skipped` isn't on the fixture constructor yet — since Task 5 already added the field with a default, this test should actually PASS once written (it exercises existing `flattenPrescription` unchanged, just proving the filter idiom the next step wires into `load()`). Confirm it passes as a sanity check on the fixture shape before proceeding — this step exists to catch a DTO mismatch early, not to prove new production code.

- [ ] **Step 3: Wire skip-awareness into the resume cursor and cursor-advance in CaptureViewModel.kt**

In `CaptureViewModel.kt`'s `load()` (around line 234-239), change the `resumeSet` computation:

```kotlin
                        val resumeSet = flattenedPrescription.firstOrNull { ps ->
                            if (ps.is_skipped) return@firstOrNull false
                            val rows = rowCountByPlannedSetId[ps.id] ?: 0
                            val fullyLogged = if (ps.id in unilateralSetIds) rows >= 2 else rows >= 1
                            !fullyLogged
                        }
```

In `logWorkingSet` (around line 366-368), change the cursor-advance to skip over `is_skipped` entries:

```kotlin
                        val completedPlannedSetId = plannedSetId
                        val idx = flattenedPrescription.indexOfFirst { it.id == completedPlannedSetId }
                        _currentPlannedSetId.value = flattenedPrescription
                            .drop(idx + 1)
                            .firstOrNull { !it.is_skipped }
                            ?.id
```

Add the two new ViewModel methods after `logWorkingSet` (or near `editLoggedSet`, whichever the file groups logging-related methods closer to):

```kotlin
    suspend fun skipExercise(exerciseId: Int) {
        repo.skipExercise(sessionId, exerciseId)
            .onSuccess { updated ->
                applyUpdatedExercise(updated)
            }
            .onFailure { e ->
                _uiError.value = (e as? IronLogException)?.error?.humanMessage() ?: e.message
                    ?: "Failed to skip exercise"
            }
    }

    suspend fun swapExercise(exerciseId: Int, newMovementId: Int, makePermanent: Boolean) {
        repo.swapExercise(sessionId, exerciseId, newMovementId, makePermanent)
            .onSuccess { updated ->
                applyUpdatedExercise(updated)
            }
            .onFailure { e ->
                _uiError.value = (e as? IronLogException)?.error?.humanMessage() ?: e.message
                    ?: "Failed to swap exercise"
            }
    }

    suspend fun loadSubstitutes(movementId: Int): List<MovementSummary> =
        repo.substitutesFor(movementId).getOrDefault(emptyList())

    /**
     * Patches [updated] into the current session's group/exercise tree by exercise id,
     * re-derives [flattenedPrescription] and the cursor (skip-aware, same rule as [load]'s
     * resumeSet), and re-emits [UiState.Success] with the patched session -- avoids a full
     * network re-fetch after a skip/swap.
     */
    private fun applyUpdatedExercise(updated: ExerciseOut) {
        val current = (_state.value as? UiState.Success)?.session ?: return
        val patchedGroups = current.groups.map { g ->
            g.copy(exercises = g.exercises.map { if (it.id == updated.id) updated else it })
        }
        val patchedSession = current.copy(groups = patchedGroups)
        flattenedPrescription = flattenPrescription(patchedGroups)
        unilateralSetIds = unilateralPlannedSetIds(patchedGroups)
        restContextBySetId = restContextByPlannedSetId(patchedGroups)
        roundSetIdsBySetId = roundPlannedSetIdsBySetId(patchedGroups)
        lastSetIdByGroup = lastSetIdByGroup(patchedGroups)
        if (_currentPlannedSetId.value != null &&
            flattenedPrescription.none { it.id == _currentPlannedSetId.value }) {
            _currentPlannedSetId.value = flattenedPrescription.firstOrNull { !it.is_skipped }?.id
        }
        _state.value = UiState.Success(patchedSession)
    }
```

Check `GroupOut` and `SessionDetailResponse`'s `@Serializable data class` declarations in `CaptureModels.kt` — Kotlin data classes get `.copy()` for free, so the `g.copy(...)` / `current.copy(...)` calls above work without any additional code, but confirm neither class has a custom `equals`/non-data-class override that would break this (it doesn't, per Task 5's review of that file).

- [ ] **Step 4: Add the overflow menu composable**

Create `ExerciseActionsMenu.kt`:

```kotlin
package com.jauschua.ironlogv2.ui.screens.capture

import androidx.compose.foundation.layout.size
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.unit.dp

/**
 * Small overflow (⋮) menu shown next to an exercise name in [CaptureScreen], offering
 * Swap/Skip for that exercise's remaining sets. Only rendered by the caller when the
 * exercise has at least one not-yet-logged, not-yet-skipped set (nothing to act on
 * otherwise) -- this composable itself does not gate visibility.
 */
@Composable
fun ExerciseActionsMenu(
    onSwap: () -> Unit,
    onSkip: () -> Unit,
) {
    var expanded by remember { mutableStateOf(false) }
    IconButton(onClick = { expanded = true }) {
        Icon(Icons.Filled.MoreVert, contentDescription = "Exercise actions")
    }
    DropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
        DropdownMenuItem(text = { Text("Swap exercise") }, onClick = { expanded = false; onSwap() })
        DropdownMenuItem(text = { Text("Skip remaining sets") }, onClick = { expanded = false; onSkip() })
    }
}
```

- [ ] **Step 5: Add the swap bottom sheet**

Create `SwapExerciseSheet.kt`:

```kotlin
package com.jauschua.ironlogv2.ui.screens.capture

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.clickable
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Row
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.jauschua.ironlogv2.data.api.dto.MovementSummary

/**
 * Two-step swap picker: pick a replacement movement (suggested substitutes list,
 * or search a fetched full-library list), then choose today-only vs permanent.
 * [onConfirm] fires once with the final (movementId, makePermanent) choice.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SwapExerciseSheet(
    substitutes: List<MovementSummary>,
    fullLibrary: List<MovementSummary>,
    onConfirm: (movementId: Int, makePermanent: Boolean) -> Unit,
    onDismiss: () -> Unit,
) {
    var query by remember { mutableStateOf("") }
    var selected by remember { mutableStateOf<MovementSummary?>(null) }
    var makePermanent by remember { mutableStateOf(false) }

    ModalBottomSheet(onDismissRequest = onDismiss) {
        Column(modifier = Modifier.padding(16.dp)) {
            if (selected == null) {
                Text("Suggested substitutes")
                LazyColumn {
                    items(substitutes) { m ->
                        Text(m.name, modifier = Modifier
                            .fillMaxWidth()
                            .clickable { selected = m }
                            .padding(vertical = 8.dp))
                    }
                }
                OutlinedTextField(
                    value = query, onValueChange = { query = it },
                    label = { Text("Search full library") },
                    modifier = Modifier.fillMaxWidth(),
                )
                val filtered = fullLibrary.filter { it.name.contains(query, ignoreCase = true) }
                LazyColumn {
                    items(filtered) { m ->
                        Text(m.name, modifier = Modifier
                            .fillMaxWidth()
                            .clickable { selected = m }
                            .padding(vertical = 8.dp))
                    }
                }
            } else {
                Text("Swap to ${selected!!.name}")
                Row {
                    RadioButton(selected = !makePermanent, onClick = { makePermanent = false })
                    Text("Today only")
                }
                Row {
                    RadioButton(selected = makePermanent, onClick = { makePermanent = true })
                    Text("Update program going forward")
                }
                Button(onClick = { onConfirm(selected!!.id, makePermanent) }) {
                    Text("Confirm swap")
                }
            }
        }
    }
}
```

- [ ] **Step 6: Wire the menu into CaptureScreen.kt**

In `CaptureScreen.kt`, both exercise-name `Text(text = displayMovementName(exercise.movement_name), ...)` call sites (GIANT_SET branch around line 497, STRAIGHT branch around line 612) need an `ExerciseActionsMenu` placed next to them, gated on the exercise having a not-yet-logged-not-skipped set remaining. Wrap each `Text(...)` in a `Row` and add the menu, calling into the ViewModel via `scope.launch`:

```kotlin
                                            Row {
                                                Text(
                                                    text = displayMovementName(exercise.movement_name),
                                                    style = MaterialTheme.typography.bodyLarge,
                                                    modifier = Modifier.padding(start = 8.dp)
                                                )
                                                val hasRemaining = exercise.planned_sets.any {
                                                    it.id !in loggedSetActuals.keys.map { k -> k.first } && !it.is_skipped
                                                }
                                                if (hasRemaining) {
                                                    ExerciseActionsMenu(
                                                        onSwap = { swapSheetExerciseId = exercise.id },
                                                        onSkip = {
                                                            scope.launch { vm.skipExercise(exercise.id) }
                                                        },
                                                    )
                                                }
                                            }
```

This is the same shape at both call sites — apply it to both, adjusting only the surrounding indentation to match each branch's existing nesting. `swapSheetExerciseId` is new `CaptureScreen` composable-level state (`var swapSheetExerciseId by remember { mutableStateOf<Int?>(null) }`, declared once near the top of `SessionContent`) that drives showing `SwapExerciseSheet` when non-null; when the sheet's `onConfirm` fires, call `scope.launch { vm.swapExercise(exerciseId, movementId, makePermanent); swapSheetExerciseId = null }` and populate its `substitutes`/`fullLibrary` params from `vm.loadSubstitutes(movementId)` (suggested) and the existing movements-list repo call already used elsewhere in the app for the Movements tab (reuse that repo/viewmodel call rather than adding a second one — check `MovementsViewModel` or equivalent for the existing "fetch all ACTIVE movements" call before adding a new one).

- [ ] **Step 7: Run the full client build + test suite**

Run: `~/projects/IronLog-V2-Client/gradlew :app:assembleDebug :app:testDebugUnitTest`
Expected: BUILD SUCCESSFUL, all tests pass.

- [ ] **Step 8: Commit**

```bash
cd ~/projects/IronLog-V2-Client
git add app/src/main/java/com/jauschua/ironlogv2/ui/screens/capture/CaptureViewModel.kt \
       app/src/main/java/com/jauschua/ironlogv2/ui/screens/capture/CaptureScreen.kt \
       app/src/main/java/com/jauschua/ironlogv2/ui/screens/capture/ExerciseActionsMenu.kt \
       app/src/main/java/com/jauschua/ironlogv2/ui/screens/capture/SwapExerciseSheet.kt \
       app/src/test/java/com/jauschua/ironlogv2/ui/capture/CaptureScreenLogicTest.kt
git commit -m "feat(capture): mid-workout swap/skip overflow menu + cursor skip-awareness"
```

**Review routing:** mandatory Opus review per CLAUDE.md's Review Gate — touches the capture cursor (real athlete-data-logging stakes, same category as the giant-set rendering fix earlier this session) and giant-set-adjacent rendering. Not review-exempt.

---

### Task 7: End-to-end verification on a live giant set

**Files:** none (verification only, no code changes expected — if verification surfaces a bug, fix it in the relevant task's file and note the fix in that task's commit, not a new task).

- [ ] **Step 1: Deploy the server changes**

Confirm Tasks 1-4 are merged to `main` on `~/projects/IronLog-V2`, migration applied (`ironlogv2` service restarted per the Task 1 deploy note and Tasks 2-4's Class-1 bundle), and the three new endpoints respond:

```bash
ssh myflix "curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8000/movements/substitutes/1"
```

Expected: `200`

- [ ] **Step 2: Build and install the client**

Confirm Tasks 5-6 are merged to `main` on `~/projects/IronLog-V2-Client`, then:

```bash
cd ~/projects/IronLog-V2-Client && ./gradlew :app:assembleDebug
adb -s <phone-wireless-adb-address> install -r app/build/outputs/apk/debug/app-debug.apk
```

(Verify the current wireless-adb address first — it changes on reconnect.)

- [ ] **Step 3: Manual smoke test on a live D4 T2 GS (or any current giant set)**

1. Start today's session on the phone, navigate to a giant set (e.g. D4's T2 GS: Stryker Pad CSR Barbell / PureTorque Pro Rotation / Better Fly Cable Pullover).
2. Log the first round's sets for all three exercises normally.
3. Before round 2, tap the overflow menu on one exercise (e.g. PureTorque Pro Rotation) and **Skip remaining sets**. Confirm: that exercise's remaining rounds show "Skipped," the other two continue rotating for rounds 2-3, and the capture cursor advances directly between the two remaining exercises without stopping on the skipped one.
4. On a different exercise, tap **Swap exercise**, pick a suggested substitute, choose "Today only," confirm. Confirm: remaining rounds show the new movement's name and its own load (or needs-cal prompt if uncalibrated), while already-logged round-1 sets for the original movement still show the original movement's name/data in history.
5. Finish and submit the session. Confirm submit succeeds (skipped sets don't block completion) and the session shows correctly in history afterward (`GET /sessions/{id}` — skipped sets present with `is_skipped=true`, no phantom `SetLog` rows for them).

- [ ] **Step 4: Write the completion report**

Per CLAUDE.md's Completion Report template, saved to `~/project-ops/reports/`, covering both repos' commits, delegation ratio, review findings, and the Task 7 manual verification results.
