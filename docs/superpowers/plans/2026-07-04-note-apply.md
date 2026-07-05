# Note-Apply Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to execute this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make a confirmed `CONFIG_CHANGE` movement-swap actually take effect as a deterministic live-state override the generator honors — base program never mutated.

**Architecture:** Server-first. New `SlotMovementOverride` table (live-state, mirrors `MesoRotation`); `lay_skeleton` resolves a slot's movement via a shared `_effective_movement_id` helper with precedence **override > meso-rotation > base**; `POST /notes/{id}/apply {target_movement_id}` resolves the slot from the note + creates the override + sets `confirmed/applied`; `dismiss` also sets `applied`; `/overrides` list + revert. Client Review screen gains an Apply→movement-picker flow + an active-swaps list.

**Tech Stack:** Python/FastAPI/SQLModel, pytest (via `ssh myflix`). Client Kotlin/Compose/Ktor.

**Spec:** `docs/superpowers/specs/2026-07-04-note-apply-design.md` (commit 5ae6b3b, main).

## Global Constraints

- Server: **NO `from __future__ import annotations`**; migration `021` is additive (`CREATE TABLE IF NOT EXISTS`) and its DDL must **exactly match SQLModel's SQLite `create_all` output** for the new model (the `tests/test_migrations.py` parity keystone enforces this — derive the DDL from the model, don't hand-guess column types); apply is **deterministic — NO LLM in the apply path**; the base program (`TierExercise.movement_id`) is **never mutated**; Option-C / progression-engine writers untouched (this writes only `Note.applied/confirmed` + `SlotMovementOverride`); full pytest suite (baseline **383**) stays green. Tests run `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q ...'`. BUILD-AND-TEST-ONLY — never touch the live DB / restart the service.
- Client: no new Gradle dependency; `SERVER_BASE_URL` local-uncommitted (never commit `app/build.gradle.kts`).

---

## File Structure
- `ironlog/models/program.py` — MODIFY: add `SlotMovementOverride`.
- `deploy/migrations/021_slot_movement_override.sql` — CREATE.
- `ironlog/notes/apply.py` — CREATE: `resolve_slot`, `apply_override`.
- `ironlog/generation/skeleton.py` — MODIFY: `_effective_movement_id` + wire into both branches.
- `ironlog/api/app.py` — MODIFY: `/notes/{id}/apply`, extend `dismiss`, `/overrides` list + revert.
- Client: `data/api/dto/NotesModels.kt` (+override DTOs), `NotesRepo.kt` (apply/overrides/revert), `ui/screens/review/` (Apply picker + active-swaps).

---

### Task 1: `SlotMovementOverride` model + migration 021 + `resolve_slot`

**Files:**
- Modify: `ironlog/models/program.py`
- Create: `deploy/migrations/021_slot_movement_override.sql`, `ironlog/notes/apply.py`
- Test: `tests/test_note_apply_resolve.py`, `tests/test_migrations.py` (parity auto-covers 021)

**Interfaces:**
- `SlotMovementOverride(id, tier_exercise_id, override_movement_id, source_note_id, created_at, active: bool = True)`.
- `resolve_slot(note, db) -> TierExercise` — raises `SlotResolutionError` (0 matches) / `AmbiguousSlotError` (2+).

- [ ] **Step 1: Write the failing resolve test**

Create `tests/test_note_apply_resolve.py`:

```python
from datetime import date
from sqlmodel import SQLModel, Session as DBSession, create_engine
import pytest

from ironlog.models.program import Program, ProgramDay, Tier, TierExercise
from ironlog.models.enums import TierKind
from ironlog.models.library import Movement
from ironlog.models.session import Note, Session as WorkoutSession
from ironlog.notes.apply import resolve_slot, SlotResolutionError, AmbiguousSlotError
import ironlog.models


def _engine():
    e = create_engine("sqlite://")
    SQLModel.metadata.create_all(e)
    return e


def _program_with_bench_slot(db):
    prog = Program(name="Phase 1"); db.add(prog); db.commit(); db.refresh(prog)
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
```
(Confirm `ProgramDay`'s field names by reading `ironlog/models/program.py` — the test uses `day_index`/`day_role`; adapt to the real fields.)

- [ ] **Step 2: Run to verify it fails**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_note_apply_resolve.py'`
Expected: ImportError (`ironlog.notes.apply` missing) / model missing.

- [ ] **Step 3: Add the model**

In `ironlog/models/program.py`, add (mirroring `MesoRotation`'s style; ensure `datetime` + `Field` imports exist):

```python
class SlotMovementOverride(SQLModel, table=True):
    """Live-state per-slot movement swap (note-driven). lay_skeleton honors an
    active override for a TierExercise, taking precedence over MesoRotation and
    the base movement. Base program is never mutated; revert = active=False."""
    id: Optional[int] = Field(default=None, primary_key=True)
    tier_exercise_id: int = Field(foreign_key="tierexercise.id", index=True)
    override_movement_id: int = Field(foreign_key="movement.id")
    source_note_id: int = Field(foreign_key="note.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    active: bool = True
```

- [ ] **Step 4: Derive + write migration 021 (must match SQLModel DDL)**

Get the exact SQLite DDL SQLModel generates for the new table, then transcribe it into the migration so the parity test passes:
```bash
ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/python -c "
from sqlalchemy.schema import CreateTable
from ironlog.models.program import SlotMovementOverride
from ironlog.db import engine
print(CreateTable(SlotMovementOverride.__table__).compile(engine))"'
```
Create `deploy/migrations/021_slot_movement_override.sql` with a header comment + `CREATE TABLE IF NOT EXISTS slotmovementoverride (...)` whose columns/types **exactly match** the printed DDL (add `IF NOT EXISTS`; keep column order/types/constraints identical). Example shape (verify against the printed DDL — do not trust this verbatim):
```sql
-- 021_slot_movement_override.sql — live-state per-slot movement swap (note-apply).
-- Additive CREATE TABLE; columns match SQLModel create_all output (parity test).
CREATE TABLE IF NOT EXISTS slotmovementoverride (
    id INTEGER NOT NULL PRIMARY KEY,
    tier_exercise_id INTEGER NOT NULL,
    override_movement_id INTEGER NOT NULL,
    source_note_id INTEGER NOT NULL,
    created_at DATETIME NOT NULL,
    active BOOLEAN NOT NULL,
    FOREIGN KEY(tier_exercise_id) REFERENCES tierexercise (id),
    FOREIGN KEY(override_movement_id) REFERENCES movement (id),
    FOREIGN KEY(source_note_id) REFERENCES note (id)
);
CREATE INDEX IF NOT EXISTS ix_slotmovementoverride_tier_exercise_id ON slotmovementoverride (tier_exercise_id);
```

- [ ] **Step 5: Write `resolve_slot` + error types**

Create `ironlog/notes/apply.py`:
```python
"""apply.py — note-apply: resolve a note to its program slot + create a
live-state SlotMovementOverride. Deterministic; NO LLM in this path.
NO from __future__ import annotations."""
from sqlmodel import Session as DBSession, select

from ..models.program import ProgramDay, Tier, TierExercise, SlotMovementOverride
from ..models.session import Note, Session as WorkoutSession


class SlotResolutionError(Exception):
    """No program slot matches the note's (day_role, movement_id)."""


class AmbiguousSlotError(Exception):
    """More than one slot matches — apply is rejected rather than guessing."""


def resolve_slot(note, db: DBSession) -> TierExercise:
    ws = db.get(WorkoutSession, note.session_id) if note.session_id else None
    if ws is None:
        raise SlotResolutionError("note has no session")
    days = db.exec(select(ProgramDay).where(ProgramDay.day_role == ws.day_role)).all()
    tier_ids = []
    for d in days:
        tier_ids += [t.id for t in db.exec(select(Tier).where(Tier.program_day_id == d.id)).all()]
    matches = []
    for tid in tier_ids:
        matches += db.exec(select(TierExercise).where(
            TierExercise.tier_id == tid,
            TierExercise.movement_id == note.movement_id)).all()
    if not matches:
        raise SlotResolutionError(f"no slot for movement {note.movement_id} in day {ws.day_role!r}")
    if len(matches) > 1:
        raise AmbiguousSlotError(f"{len(matches)} slots match; cannot auto-apply")
    return matches[0]
```

- [ ] **Step 6: Run resolve tests + full suite**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_note_apply_resolve.py tests/test_migrations.py'` → PASS (resolve + migration parity incl. 021). Then full suite → green.

- [ ] **Step 7: Commit**

```bash
cd ~/projects/IronLog-V2 && git add ironlog/models/program.py deploy/migrations/021_slot_movement_override.sql ironlog/notes/apply.py tests/test_note_apply_resolve.py
git commit -m "feat(apply): SlotMovementOverride model + migration 021 + resolve_slot"
```

---

### Task 2: `_effective_movement_id` in lay_skeleton + dismiss sets applied

**Files:**
- Modify: `ironlog/generation/skeleton.py`
- Modify: `ironlog/api/app.py` (dismiss)
- Test: `tests/test_slot_override_skeleton.py`, extend `tests/test_notes_review_endpoints.py`

**Interfaces:**
- `_effective_movement_id(db, te, meso_number) -> int` (skeleton.py) — precedence override>meso>base.

- [ ] **Step 1: Write the failing skeleton test**

Create `tests/test_slot_override_skeleton.py`: seed a program day with a bench anchor slot + generate its skeleton; assert the emitted movement for that slot == bench. Then add an active `SlotMovementOverride(tier_exercise_id=bench_te, override_movement_id=incline)` and assert the skeleton now emits incline for that slot **and** any other slot is unchanged; set the override `active=False` and assert it reverts to bench. (Use `lay_skeleton(day_role, db)` and inspect `anchor_movement_ids` / `adaptive_slots[*].program_movement_id`; read `skeleton.py` for the exact return shape + how to pass `meso_number`.)

- [ ] **Step 2: Run to verify it fails** — `ssh myflix … pytest -q tests/test_slot_override_skeleton.py` → FAIL (override ignored).

- [ ] **Step 3: Add `_effective_movement_id` + wire both branches**

In `ironlog/generation/skeleton.py`, add the helper (import `SlotMovementOverride`) and use it where the anchor branch computes `movement_id` (currently `mr.movement_id if mr else te.movement_id`) and where the adaptive branch sets `program_movement_id=te.movement_id`:
```python
def _effective_movement_id(db, te, meso_number):
    ov = db.exec(select(SlotMovementOverride).where(
        SlotMovementOverride.tier_exercise_id == te.id,
        SlotMovementOverride.active == True)).first()  # noqa: E712
    if ov is not None:
        return ov.override_movement_id
    mr = db.exec(select(MesoRotation).where(
        MesoRotation.tier_exercise_id == te.id,
        MesoRotation.meso_number == meso_number)).first()
    return mr.movement_id if mr is not None else te.movement_id
```
Anchor branch → `movement_id = _effective_movement_id(db, te, meso_number)` (replaces the inline mr check). Adaptive branch → `program_movement_id = _effective_movement_id(db, te, meso_number)`.

- [ ] **Step 4: Extend `dismiss` to set `applied=True`**

In `ironlog/api/app.py` `dismiss_note`, add `n.applied = True` alongside the `classification = JOURNAL` set. Add a test to `tests/test_notes_review_endpoints.py`: after dismiss, the note has `applied == True` (and `classification == JOURNAL`).

- [ ] **Step 5: Run tests + full suite** → green.

- [ ] **Step 6: Commit**
```bash
git add ironlog/generation/skeleton.py ironlog/api/app.py tests/test_slot_override_skeleton.py tests/test_notes_review_endpoints.py
git commit -m "feat(apply): lay_skeleton honors SlotMovementOverride; dismiss sets applied"
```

---

### Task 3: `/notes/{id}/apply` + `/overrides` list + revert

**Files:**
- Modify: `ironlog/api/app.py`; `ironlog/notes/apply.py` (add `apply_override`)
- Test: `tests/test_note_apply_endpoints.py`

**Interfaces:**
- `apply_override(note, target_movement_id, db) -> SlotMovementOverride` (apply.py) — resolve_slot + validate movement + create override + set note.confirmed/applied.
- `POST /notes/{id}/apply {target_movement_id}`; `GET /overrides`; `POST /overrides/{id}/revert`.

- [ ] **Step 1: Write the failing endpoint test**

Create `tests/test_note_apply_endpoints.py` (TestClient + StaticPool, mirroring `test_notes_review_endpoints.py`): seed a program day + bench slot + a CONFIG_CHANGE note on bench; `POST /notes/{id}/apply {"target_movement_id": <incline>}` → 200, override row created (active), note `confirmed & applied` True; `GET /overrides` lists it (with `from`/`to` names); `POST /overrides/{id}/revert` → 200 + `active=False` + gone from list; apply on a bad note → 404; ambiguous slot → 409; unknown target movement → 404.

- [ ] **Step 2: Run to verify it fails** — 404s on missing routes.

- [ ] **Step 3: `apply_override` + endpoints**

Add to `ironlog/notes/apply.py`:
```python
def apply_override(note, target_movement_id, db):
    from ..models.library import Movement
    if db.get(Movement, target_movement_id) is None:
        raise SlotResolutionError(f"target movement {target_movement_id} not found")
    te = resolve_slot(note, db)  # raises SlotResolutionError / AmbiguousSlotError
    ov = SlotMovementOverride(
        tier_exercise_id=te.id, override_movement_id=target_movement_id,
        source_note_id=note.id, active=True)
    db.add(ov)
    note.confirmed = True
    note.applied = True
    db.add(note); db.commit(); db.refresh(ov)
    return ov
```
In `ironlog/api/app.py`, add a request model + endpoints:
```python
class ApplyNoteRequest(BaseModel):
    target_movement_id: int

@app.post("/notes/{note_id}/apply")
def apply_note(note_id: int, req: ApplyNoteRequest, db: Session = Depends(get_session)):
    from ..models.session import Note
    from ..notes.apply import apply_override, SlotResolutionError, AmbiguousSlotError
    n = db.get(Note, note_id)
    if n is None:
        raise HTTPException(404, "note not found")
    try:
        ov = apply_override(n, req.target_movement_id, db)
    except AmbiguousSlotError as e:
        raise HTTPException(409, str(e))
    except SlotResolutionError as e:
        raise HTTPException(404, str(e))
    return {"id": ov.id, "tier_exercise_id": ov.tier_exercise_id,
            "override_movement_id": ov.override_movement_id, "note_id": note_id}

@app.get("/overrides")
def list_overrides(db: Session = Depends(get_session)):
    from ..models.program import SlotMovementOverride, TierExercise, Tier, ProgramDay
    rows = db.exec(select(SlotMovementOverride).where(
        SlotMovementOverride.active == True).order_by(SlotMovementOverride.id.desc())).all()  # noqa: E712
    out = []
    for ov in rows:
        te = db.get(TierExercise, ov.tier_exercise_id)
        tier = db.get(Tier, te.tier_id) if te else None
        day = db.get(ProgramDay, tier.program_day_id) if tier else None
        frm = db.get(Movement, te.movement_id) if te else None
        to = db.get(Movement, ov.override_movement_id)
        out.append({"id": ov.id, "day_role": (day.day_role if day else None),
                    "tier_label": (tier.tier_label if tier else None),
                    "slot_id": (te.slot_id if te else None),
                    "from_movement_name": (frm.name if frm else None),
                    "to_movement_name": (to.name if to else None),
                    "source_note_id": ov.source_note_id})
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
```

- [ ] **Step 4: Run tests + full suite** → green.

- [ ] **Step 5: Commit**
```bash
git add ironlog/api/app.py ironlog/notes/apply.py tests/test_note_apply_endpoints.py
git commit -m "feat(api): /notes/{id}/apply + /overrides list + revert"
```

---

### Task 4: Client — Apply → movement picker + active-swaps list

**Files:** `data/api/dto/NotesModels.kt` (+ `OverrideOut`, `ApplyNoteRequest`), `data/repo/NotesRepo.kt` (`apply`, `overrides`, `revert`), `ui/screens/review/ReviewScreen.kt` + `ReviewViewModel.kt` (Apply picker + active-swaps), reuse the existing movements endpoint/DTO + `LibraryRepo`/`MovementsList` for the picker. Test: `NotesDtoTest` extension + build.

- [ ] **Step 1** Add DTOs: `OverrideOut(id, day_role, tier_label, slot_id, from_movement_name, to_movement_name, source_note_id)`; `ApplyNoteRequest(target_movement_id)`. Failing DTO decode test.
- [ ] **Step 2** Verify fail (compile).
- [ ] **Step 3** `NotesRepo.apply(id, targetMovementId)`, `overrides()`, `revert(id)` (Ktor, mirror existing). Repo/DTO test passes.
- [ ] **Step 4** `ReviewViewModel`: `apply(noteId, targetMovementId)` / `revert(id)` (call repo → reload); load `/overrides` alongside `/notes/review`. `ReviewScreen`: a `CONFIG_CHANGE` proposal shows **Apply** → a movement-picker dialog (reuse the library list, pre-filter by `proposed_change.movement` text) → pick → `apply`. Add an **Active swaps** section listing overrides with **Revert**. Match existing screen/nav patterns.
- [ ] **Step 5** `./gradlew :app:assembleDebug` BUILD SUCCESSFUL; `./gradlew :app:testDebugUnitTest` green.
- [ ] **Step 6** Commit `feat(review): apply swap via movement picker + active-swaps revert`.

## On-device smoke (deferred — meaningful post-go-live)
On a real (post-go-live) program: a CONFIG_CHANGE swap note → Review → Apply → pick the concrete movement → next generated session shows the swapped movement in that slot only → Revert restores it.

## Routing Plan
| Task | Repo | Route |
|---|---|---|
| 1 model+migration+resolve | server | Claude Code Agent subagent (ssh myflix) |
| 2 skeleton override + dismiss | server | Claude Code Agent subagent |
| 3 apply/overrides endpoints | server | Claude Code Agent subagent |
| 4 client apply picker | client | Claude Code Agent subagent |

**Delegation ratio: 4/4 (100%).** Fresh implementer per task + two-verdict review gate + final whole-branch review. Consensus workers unused.

## Self-Review
**Spec coverage:** live-state override table → T1; lay_skeleton honors it (override>meso>base), only that slot → T2; deterministic apply, human-picked target → T3/T4; per-slot resolution → T1 `resolve_slot`; applied on apply+dismiss (closes context.py flag) → T2/T3; audit list + revert → T3/T4; migration additive+parity → T1; base program never mutated (override table only) ✓; no LLM in apply ✓.

**Placeholder scan:** the migration DDL is derived from the model at build-time (Step 4 shows the exact command) rather than hand-frozen — that's a grounded instruction, not a placeholder. Task 4 client is guided-prose (UI, build-gated) like prior client tasks; contract-bearing DTOs are concrete. No TBD.

**Type consistency:** `SlotMovementOverride(tier_exercise_id, override_movement_id, source_note_id, active)`, `resolve_slot(note, db)→TierExercise`, `apply_override(note, target_movement_id, db)`, `_effective_movement_id(db, te, meso_number)`, `/notes/{id}/apply {target_movement_id}` — names consistent across tasks and server↔client (`OverrideOut` fields match the `/overrides` dict keys).
