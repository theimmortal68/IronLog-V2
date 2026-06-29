# First-Run Wizard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Take the structure-seeded program from "no trustworthy loads" to "configured + trainable" — a wizard that sets working loads, backed by a single computed load-trust function shared by generation, the wizard endpoint, and the completion gate.

**Architecture:** One `compute_load_trust(movement, state, db, as_of)` is the keystone — generation's load resolver, the `GET /wizard/state` endpoint, and the wizard's completion gate all derive trust from it, so they cannot disagree (finishing the wizard = clean generation, by construction). Trust is derived from event-facts (`current_load`/`assist_level`, `SetLog.performed_at`, `MovementState.confirmed_at`); never a stored verdict. Two repos (server `IronLog-V2`, client `IronLog-V2-Client`) joined by a locked endpoint contract; server built-and-tested-stable before the client wizard screen.

**Tech Stack:** Server — Python/FastAPI/SQLModel/pytest (on myflix). Client — Kotlin/Compose/Ktor/kotlinx.serialization (existing patterns from the logging chunk).

## Global Constraints

- **NO `from __future__ import annotations`** (server, project-wide).
- **Trust is COMPUTED, never stored** — `compute_load_trust` derives UNKNOWN/STALE/FRESH every call from facts. `confirmed_at` is an **event-fact** (a timestamp, like `SetLog.performed_at`), NOT a verdict/boolean. The existing `MovementState.calibration_status` (e1rm-trust: INHERITED/CALIBRATING/MEASURED) is a **different axis** — do NOT touch it or conflate load-trust into it.
- **Presence check is `IS NULL`, never falsy/`== 0`.** `assist_level = 0` (unassisted pull-ups) is a VALID FRESH state, NOT needs-calibration. Bodyweight (PROTOCOL/CONDITIONING/NONE) movements need no load → always FRESH, never asked, never block.
- **One shared function:** generation, the wizard-state endpoint, and the completion gate MUST call the same `compute_load_trust` — no per-surface reimplementation (the §7.2 can't-disagree test enforces this).
- **Migrations are additive-nullable**, applied via `apply_pending` (migrate-forward on the just-seeded live DB, NOT a reseed); extend the parity test (`test_chain_matches_create_all`). One ALTER per migration file (matches 005/006).
- **Two-repo contract:** the endpoint DTO/response shapes are the locked crossing artifact; server built-and-tested-stable before the client wizard tasks; client DTOs mirror server Pydantic field-for-field (snake_case).
- **Server tests on myflix ONLY:** `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q ...'`. Client via Gradle in `~/projects/IronLog-V2-Client`. Baseline: server 236, client 20.
- **BUILD-AND-TEST-ONLY for tests** (in-memory SQLite). The migration applies to the live seeded DB as a separate deploy step (Task 9), gated by the parity test.

## THE ENDPOINT CONTRACT (server↔client crossing artifact — locked)

**`GET /programs/{program_id}/wizard-state` → `WizardStateResponse`** (the program's load-config state — `compute_load_trust` rendered per movement):
```
WizardStateResponse:
  program_id: int
  program_name: str
  needs_attention_count: int          # UNKNOWN + unconfirmed-STALE (the "N left")
  ready_to_start: bool                # needs_attention_count == 0
  movements: WizardMovement[]          # active-program movements that NEED a load (bodyweight excluded)
WizardMovement:
  movement_id: int
  movement_name: str
  load_field: str                     # "current_load" | "assist_level"
  trust: str                          # "UNKNOWN" | "STALE" | "FRESH"
  prefill_value: float | null          # current value for STALE/FRESH; null for UNKNOWN
  unit_hint: str | null               # "lb" | "assist" (display aid)
```

**`POST /programs/{program_id}/wizard-resolve` → `WizardResolveResponse`** (batch-write resolved loads):
```
WizardResolveRequest:
  resolutions: WizardResolution[]      # only movements the user touched
WizardResolution:
  movement_id: int
  value: float                        # entered/confirmed load → into load_field
WizardResolveResponse:
  resolved: int
  needs_attention_count: int          # recomputed after the write
  ready_to_start: bool
```
POST writes, per resolution: the canonical load field (`current_load`|`assist_level` per mode) + `confirmed_at = now`. Stamps `confirmed_at` ONLY on the movements in `resolutions` (touched) — never on untouched-FRESH movements.

**`POST /programs/{program_id}/start` → `StartProgramResponse`** (gate: must be `ready_to_start`):
```
StartProgramResponse:
  program_id: int
  started: bool                       # false + reason if not ready_to_start
  active: bool                        # active_program_id now == program_id
```
Sets `EngineState.active_program_id = program_id` + `Program.started_at = now`. (Generating the first session is a follow-on `/generate` call, unchanged from v0.6.)

---

# PHASE 1 — SERVER (built-and-tested-stable before Phase 2)

### Task 1: Schema + migrations (the additive-nullable columns) + parity

**Files:**
- Modify: `ironlog/models/library.py` (`MovementState.confirmed_at`, `EngineState.active_program_id`), `ironlog/models/program.py` (`Program.started_at`, `Program.ended_at`)
- Create: `deploy/migrations/007_movementstate_confirmed_at.sql`, `008_enginestate_active_program_id.sql`, `009_program_started_ended_at.sql`
- Test: extend `tests/test_migrations.py`

**Interfaces:**
- Produces: `MovementState.confirmed_at: Optional[datetime]`, `EngineState.active_program_id: Optional[int]` (FK→program.id), `Program.started_at: Optional[datetime]`, `Program.ended_at: Optional[datetime]`. Consumed by Tasks 2/4/5.

- [ ] **Step 1: Add the model fields**

```python
# library.py — MovementState (add, near current_load):
    confirmed_at: Optional[datetime] = None      # event-fact: when user last vouched for this load (Fork 2)
# library.py — EngineState (add):
    active_program_id: Optional[int] = Field(default=None, foreign_key="program.id")  # single-active pointer (Fork 3)
# program.py — Program (add):
    started_at: Optional[datetime] = None        # event-fact (Fork 3)
    ended_at: Optional[datetime] = None
```

- [ ] **Step 2: Write the migrations** (one ALTER per file, matching 005/006 style)

```sql
-- 007_movementstate_confirmed_at.sql
ALTER TABLE movementstate ADD COLUMN confirmed_at DATETIME;
```
```sql
-- 008_enginestate_active_program_id.sql
ALTER TABLE enginestate ADD COLUMN active_program_id INTEGER REFERENCES program(id);
```
```sql
-- 009_program_started_ended_at.sql
ALTER TABLE program ADD COLUMN started_at DATETIME;
ALTER TABLE program ADD COLUMN ended_at DATETIME;
```
(009 has two ALTERs — same table, atomic-enough; if the runner requires strictly one statement, split into 009/010. Confirm against `apply_pending`'s statement handling — it executes the file; SQLite runs multiple `;`-separated statements via `executescript`. If `apply_pending` uses single-statement `execute`, split the file.)

- [ ] **Step 3: Run parity red→green**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest tests/test_migrations.py -q'`
Expected: `test_chain_matches_create_all` passes — the chain (000→009) with the new columns == `create_all`. (It auto-discovers the new migration files; the new model columns must appear in both the model and a migration, or parity fails.) Full suite tail: ~236 + parity.

- [ ] **Step 4: Commit**
```bash
git add ironlog/models/library.py ironlog/models/program.py deploy/migrations/00*.sql tests/test_migrations.py
git commit -m "feat(wizard): additive-nullable schema (confirmed_at, active_program_id, started_at/ended_at) + migrations 007-009"
```

---

### Task 2: `compute_load_trust` — the shared keystone

**Files:**
- Create: `ironlog/generation/load_trust.py`
- Test: `tests/test_load_trust.py`

**Interfaces:**
- Consumes: `MovementState` (incl. `confirmed_at` from Task 1), `Movement` (`progression_mode`, `start_ratio`, `derived_from_id`), `SetLog` (recency), `ProgressionMode`.
- Produces: `LoadTrust` (enum: `UNKNOWN`/`STALE`/`FRESH`), `LoadTrustResult` (dataclass: `trust: LoadTrust`, `value: Optional[float]`, `load_field: Optional[str]`), `compute_load_trust(movement, state, db, as_of) -> LoadTrustResult`, and `load_field_for_mode(mode) -> Optional[str]`. **This is the single shared function** Tasks 3/4/5 all call.

**Behavior:** (1) per-mode load field — LADDER/COMPOSITE→`current_load`, ASSISTED→`assist_level`, PROTOCOL/CONDITIONING/NONE→None (bodyweight: always FRESH, value None, never needs-calibration). (2) value resolution (mirrors `resolve_start_load` MINUS the floor): the load field present (`IS NOT NULL`) → use it; else derived-ratio (`start_ratio` + `derived_from_id` with a resolvable anchor e1rm) → `start_ratio * anchor.e1rm`; else → UNKNOWN. (3) presence is `IS NULL`, never `== 0`. (4) recency = `max(last working SetLog.performed_at, confirmed_at)`; FRESH if within 30 days of `as_of`, else STALE.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_load_trust.py
from datetime import datetime, timedelta, timezone
from sqlmodel import Session as DbSession, SQLModel, create_engine
from sqlmodel.pool import StaticPool
from ironlog.generation.load_trust import compute_load_trust, LoadTrust, load_field_for_mode
from ironlog.models.library import Movement, MovementState
from ironlog.models.session import SetLog
from ironlog.models.enums import ProgressionMode, FeedbackTap
import ironlog.models

NOW = datetime(2026, 7, 1, tzinfo=timezone.utc)

def _db():
    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(e); return e

def _mv(s, **kw):
    m = Movement(name=kw.pop("name","M"), base_name="M", progression_mode=kw.pop("mode", ProgressionMode.LADDER), **kw)
    s.add(m); s.commit(); s.refresh(m); return m

def test_ladder_no_current_load_is_unknown():
    e=_db()
    with DbSession(e) as s:
        m=_mv(s); 
        r=compute_load_trust(m, None, s, NOW)
        assert r.trust==LoadTrust.UNKNOWN and r.value is None and r.load_field=="current_load"

def test_ladder_present_recent_is_fresh():
    e=_db()
    with DbSession(e) as s:
        m=_mv(s); st=MovementState(movement_id=m.id, current_load=205.0, confirmed_at=NOW-timedelta(days=5))
        s.add(st); s.commit()
        r=compute_load_trust(m, st, s, NOW)
        assert r.trust==LoadTrust.FRESH and r.value==205.0

def test_ladder_present_old_is_stale():
    e=_db()
    with DbSession(e) as s:
        m=_mv(s); st=MovementState(movement_id=m.id, current_load=205.0, confirmed_at=NOW-timedelta(days=40))
        s.add(st); s.commit()
        assert compute_load_trust(m, st, s, NOW).trust==LoadTrust.STALE

def test_recency_uses_last_working_setlog_not_just_confirmed():
    e=_db()
    with DbSession(e) as s:
        m=_mv(s); st=MovementState(movement_id=m.id, current_load=205.0, confirmed_at=NOW-timedelta(days=40))
        s.add(st); s.commit()
        s.add(SetLog(session_id=1, movement_id=m.id, set_index=0, is_warmup=False,
                     feedback_tap=FeedbackTap.ON_TARGET, performed_at=NOW-timedelta(days=3)))
        s.commit()
        # logged 3d ago → fresh despite confirmed 40d ago (max of the two)
        assert compute_load_trust(m, st, s, NOW).trust==LoadTrust.FRESH

def test_bodyweight_protocol_always_fresh_never_calibration():
    e=_db()
    with DbSession(e) as s:
        m=_mv(s, mode=ProgressionMode.PROTOCOL)
        r=compute_load_trust(m, None, s, NOW)
        assert r.trust==LoadTrust.FRESH and r.load_field is None   # no load to set, never blocks

def test_assisted_null_is_unknown_but_zero_is_fresh():
    e=_db()
    with DbSession(e) as s:
        m=_mv(s, mode=ProgressionMode.ASSISTED)
        # assist_level IS NULL → unknown
        st_null=MovementState(movement_id=m.id, assist_level=None, confirmed_at=NOW)
        s.add(st_null); s.commit()
        assert compute_load_trust(m, st_null, s, NOW).trust==LoadTrust.UNKNOWN
        # assist_level == 0 (unassisted) → VALID fresh, NOT unknown
        st_null.assist_level=0.0; s.add(st_null); s.commit()
        r=compute_load_trust(m, st_null, s, NOW)
        assert r.trust==LoadTrust.FRESH and r.value==0.0 and r.load_field=="assist_level"
```

- [ ] **Step 2: Run red** — `ssh myflix '… pytest tests/test_load_trust.py -q'` → FAIL (module missing).

- [ ] **Step 3: Implement**

```python
# ironlog/generation/load_trust.py
"""compute_load_trust — the single shared load-trustworthiness function.

Used by generation's resolver, the wizard-state endpoint, AND the completion
gate, so they cannot disagree. Trust is DERIVED every call from event-facts
(current_load/assist_level, SetLog.performed_at, MovementState.confirmed_at) —
never a stored verdict. NO from __future__ import annotations.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional

from sqlmodel import Session, select

from ..models.enums import ProgressionMode
from ..models.library import Movement, MovementState
from ..models.session import SetLog

STALE_AFTER_DAYS = 30


class LoadTrust(str, Enum):
    UNKNOWN = "UNKNOWN"   # no real load → needs-calibration, refuse to prescribe
    STALE = "STALE"       # real load but recency > 30d → prescribe-with-confirm
    FRESH = "FRESH"       # real load, recent → use as-is


@dataclass
class LoadTrustResult:
    trust: LoadTrust
    value: Optional[float]
    load_field: Optional[str]   # "current_load" | "assist_level" | None (bodyweight)


def load_field_for_mode(mode: ProgressionMode) -> Optional[str]:
    if mode in (ProgressionMode.LADDER, ProgressionMode.COMPOSITE):
        return "current_load"
    if mode == ProgressionMode.ASSISTED:
        return "assist_level"
    return None   # PROTOCOL / CONDITIONING / NONE → bodyweight, no load


def _resolve_value(movement, state, db, field):
    """current_load/assist_level present (IS NOT NULL) -> use it; else derived-ratio
    anchor -> start_ratio * anchor.e1rm; else None (UNKNOWN). NO floor fallback."""
    if state is not None:
        v = getattr(state, field)
        if v is not None:          # IS NULL check — assist_level == 0 is a real value
            return v
    if field == "current_load" and movement.start_ratio is not None and movement.derived_from_id is not None:
        anchor = db.exec(select(MovementState).where(
            MovementState.movement_id == movement.derived_from_id)).first()
        if anchor is not None and anchor.e1rm is not None:
            return movement.start_ratio * anchor.e1rm
    return None


def _recency(movement, state, db) -> Optional[datetime]:
    last = db.exec(
        select(SetLog.performed_at)
        .where(SetLog.movement_id == movement.id)
        .where(SetLog.is_warmup == False)            # noqa: E712 — working sets only
        .order_by(SetLog.performed_at.desc())
    ).first()
    candidates = [t for t in (last, getattr(state, "confirmed_at", None) if state else None) if t is not None]
    return max(candidates) if candidates else None


def compute_load_trust(movement: Movement, state: Optional[MovementState],
                       db: Session, as_of: datetime) -> LoadTrustResult:
    field = load_field_for_mode(movement.progression_mode)
    if field is None:
        return LoadTrustResult(LoadTrust.FRESH, None, None)   # bodyweight: always fresh, never asked
    value = _resolve_value(movement, state, db, field)
    if value is None:
        return LoadTrustResult(LoadTrust.UNKNOWN, None, field)
    rec = _recency(movement, state, db)
    if rec is None or (as_of - rec) > timedelta(days=STALE_AFTER_DAYS):
        return LoadTrustResult(LoadTrust.STALE, value, field)
    return LoadTrustResult(LoadTrust.FRESH, value, field)
```

(Implementer note: ensure `as_of` and the stored datetimes are comparable — both tz-aware or both naive; match the project's existing `datetime.utcnow()` (naive) convention or normalize. Resolve the tz consistency and add a test if the existing data is naive.)

- [ ] **Step 4: Run green** — all 6 tests pass. Full suite tail.

- [ ] **Step 5: Commit** — `feat(wizard): compute_load_trust shared keystone (computed trust; IS-NULL-not-zero; bodyweight-always-fresh; derived-ratio value)`

---

### Task 3: `resolve_start_load` → needs-calibration via `compute_load_trust` (generation surface)

**Files:** Modify `ironlog/generation/assembler.py` (`resolve_start_load` + its caller at line ~119); Test: `tests/test_assembler_needs_calibration.py`

**Interfaces:** Consumes `compute_load_trust` (Task 2). `resolve_start_load` is refactored to delegate to `compute_load_trust`; UNKNOWN → a needs-calibration signal (NOT a floor). The assembler caller handles UNKNOWN (the slot is flagged needs-calibration; the rest of the session assembles).

- [ ] **Step 1: Write the failing test** — an un-configured LADDER movement (no MovementState) does NOT resolve to its floor:

```python
# tests/test_assembler_needs_calibration.py  (sketch — implementer reconciles to assembler API)
def test_unconfigured_movement_is_needs_calibration_not_floor():
    # Bench (load_floor=45), no MovementState → generation flags needs-calibration, NOT load=45
    # assert the resolved slot's trust is UNKNOWN / load is None (needs-calibration), NOT 45.0
    ...
```

- [ ] **Step 2: Run red.**
- [ ] **Step 3: Implement** — `resolve_start_load` calls `compute_load_trust`; on `FRESH`/`STALE` return `.value`; on `UNKNOWN` return a sentinel/`None` (needs-calibration) and have the caller flag the slot rather than assemble a floor. Remove the `return movement.load_floor … else 0.0` fallback (that line IS the bug). Keep the derived-ratio path (now inside `compute_load_trust`).
- [ ] **Step 4: Run green** + full suite (the cold-start/program-emit tests from v0.6 may now surface needs-calibration where they previously got floors — reconcile those tests to the new honest behavior: an unconfigured movement is needs-calibration, and the existing tests should seed a load or assert needs-calibration, NOT expect a floor).
- [ ] **Step 5: Commit** — `feat(wizard): generation flags needs-calibration for unconfigured loads (drop floor fallback; via compute_load_trust)`

---

### Task 4: `GET /programs/{id}/wizard-state` (wizard surface) + lay_skeleton active-program scoping

**Files:** Create `ironlog/api/schemas_wizard.py` (the contract DTOs); Modify `ironlog/api/app.py` (endpoint) + `ironlog/generation/skeleton.py` (`lay_skeleton` scopes `ProgramDay` to a program_id); Test: `tests/test_wizard_state_endpoint.py`

**Interfaces:** Consumes `compute_load_trust` (Task 2). Produces the contract Pydantic models (`WizardStateResponse`/`WizardMovement` per THE ENDPOINT CONTRACT) + `GET /programs/{program_id}/wizard-state`. `lay_skeleton(day_role, db, program_id=None)` gains optional program scoping (defaults to active or the single program for back-compat).

- [ ] Step 1: Write failing tests — a program with one configured (FRESH) + one unconfigured (UNKNOWN) movement returns `needs_attention_count=1`, the UNKNOWN movement listed with `prefill_value=null`, bodyweight movements EXCLUDED, `ready_to_start=false`. The endpoint's per-movement trust equals `compute_load_trust` (shared-function check).
- [ ] Step 2: red.
- [ ] Step 3: implement — the endpoint enumerates the program's distinct movements (via TierExercises + MesoRotations), calls `compute_load_trust` per movement, excludes bodyweight (load_field None), builds `WizardStateResponse`. `lay_skeleton` scopes ProgramDay by program_id.
- [ ] Step 4: green + full suite.
- [ ] Step 5: Commit — `feat(wizard): GET wizard-state (compute_load_trust per program movement) + lay_skeleton program scoping`

---

### Task 5: `POST wizard-resolve` + `POST programs/{id}/start` (write + gate surfaces) + the spine test

**Files:** Modify `ironlog/api/app.py` (+ `schemas_wizard.py` for the resolve/start DTOs); Test: `tests/test_wizard_resolve_and_start.py`

**Interfaces:** Consumes `compute_load_trust` (gate) + the Task-1 columns. Produces `POST /programs/{id}/wizard-resolve` (batch write) + `POST /programs/{id}/start` (gate + activate).

- [ ] Step 1: Write failing tests:
  - **resolve writes load + confirmed_at, ONLY on touched** (§7.3): resolve movement A (UNKNOWN→value) → A.current_load set + A.confirmed_at stamped; a FRESH untouched movement B's confirmed_at is UNCHANGED. (Catches stamp-everything.)
  - **assisted resolve writes assist_level** (per load_field), not current_load.
  - **completion gate**: `/start` returns `started=false` while `needs_attention_count>0`; after resolving all, `ready_to_start=true`, `/start` sets `active_program_id` + `started_at`.
  - **the spine / can't-disagree (§7.2)**: after the wizard gate clears (all FRESH), generation (resolve_start_load via compute_load_trust) returns real loads with ZERO needs-calibration — wizard-finishing ⇒ clean-generation. Assert the wizard-state trust for a movement == what generation's resolver sees (same function).
- [ ] Step 2: red.
- [ ] Step 3: implement — resolve: for each `WizardResolution`, write the canonical load field (per `load_field_for_mode`) + `confirmed_at=now`, stamping ONLY the movements in the request; recompute `needs_attention_count`. start: guard `ready_to_start` (all program movements FRESH via compute_load_trust), set `EngineState.active_program_id` + `Program.started_at`.
- [ ] Step 4: green + full suite (the **server phase is now built-and-tested-stable**; the contract is real).
- [ ] Step 5: Commit — `feat(wizard): POST wizard-resolve (confirmed_at only-on-touched) + start (gate + activate); spine can't-disagree test`

---

# PHASE 2 — CLIENT (`~/projects/IronLog-V2-Client`; branch `feat/wizard`; against the locked contract)

### Task 6: Wizard DTOs + `WizardRepo`
Mirror `schemas_wizard.py` field-for-field (snake_case `@Serializable` DTOs); `WizardRepo(apiClient)` with `state(programId)`, `resolve(programId, resolutions)`, `start(programId)` (the `runCatchingApi { http.get/post().body() }` pattern from `AutoregRepo`/`CaptureRepo`). Test (MockEngine): resolve serializes `{movement_id, value}[]`; state parses trust/prefill. Commit.

### Task 7: `WizardViewModel`
The needs-attention list: load `state(programId)`, hold per-movement entry, the live `needs_attention_count` ("N left"), resolve (batch), enable Start when `ready_to_start`. Mirror `CaptureViewModel`/`AutoregulateViewModel` (UiState, Factory via AppContainer). Test: filling an UNKNOWN decrements N-left; Start disabled until 0. Commit.

### Task 8: `WizardScreen` + nav
Compose screen rendering the three trust states (UNKNOWN→empty field, STALE→prefilled-to-confirm, FRESH→collapsed/summarized), the "N left" counter, Start button (enabled at 0) → calls `start` → navigates to today/overview. Assisted movements show an assist input (not a weight field). Mirror `CaptureScreen`/`AutoregulateScreen`; wire into `Nav.kt`/`MainActivity`. Gate: `./gradlew :app:assembleDebug` SUCCESSFUL + `testDebugUnitTest` green. Commit.

---

# DEPLOY (after both phases merge — gated, separate from build)

### Task 9: Apply migrations 007-009 to the live seeded DB
**Not a build task — a deploy step, run like the structure seed.** Stop `ironlogv2.service` → `apply_pending(engine)` (migrate-forward; additive-nullable, the 108 movements/program/EngineState undisturbed) → verify the columns exist + parity → restart. The just-seeded DB (2026-06-29, 0 sessions, MovementState empty) takes the additive columns cleanly. Reversible (the seed is reproducible). Run on myflix.

---

## Named-gate → task map
| Gate (§7) | Task |
|---|---|
| 1 compute_load_trust correctness (incl. assisted null-vs-zero, bodyweight-always-fresh) | Task 2 |
| 2 shared-function can't-disagree (the spine) | Task 5 |
| 3 confirmed_at only-on-touched | Task 5 |
| 4 needs-calibration not floor (Bench≠45) | Task 3 |
| 5 single-active structural (lay_skeleton scopes) | Task 4 + 5 |
| 6 completion-gate ⇒ clean generation | Task 5 |
| 7 carryover persists (global MovementState) | Task 4/5 (covered by compute_load_trust over global state) |
| 8 two-repo contract | Task 6 |
| 9 migration parity (new columns) | Task 1 |

## Routing plan
Server Tasks 1-5 + deploy 9: codex/gemini repo-aware delegation + Claude Code subagents apply/test on myflix. Client Tasks 6-8: Claude Code subagents, Android/Gradle. All under subagent-driven-development (fresh implementer + reviewer per task), server-stable-before-client.
```
- Task 1 schema+migrations    → server delegate
- Task 2 compute_load_trust   → server delegate (KEYSTONE — build first, well-tested, shared)
- Task 3 resolve_start_load   → server delegate
- Task 4 wizard-state + scope → server delegate
- Task 5 resolve+start+spine  → server delegate
- Task 6 DTOs+WizardRepo      → client subagent
- Task 7 WizardViewModel      → client subagent
- Task 8 WizardScreen+nav     → client subagent
- Task 9 deploy migrations    → Tier A ops (gated, on myflix)
```
**Delegation ratio: 8/9 tasks delegated (89%).** Tier A: orchestration, per-task review gates, the keystone-built-once-and-shared check, the deploy ops step, the final whole-branch review.
