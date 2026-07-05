# Note→Apply Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make Apply explicit + safe (confirmable source slot, action-routed) and add load + rep adjustments — all as deterministic, reversible, Option-C-safe live-state overrides.

**Architecture:** Generalize `SlotMovementOverride` into a slot override carrying movement/load/reps (migration 022, additive). Movement overrides apply at `lay_skeleton` (already); load/rep apply in the assembler at prescription (never writing `current_load`). The classifier emits a structured `action_type`. Apply becomes explicit: the client confirms the source slot (defaulted from Gemini's subject) + the adjustment, and POSTs `{tier_exercise_id, override_type, payload}` — no silent note-based slot resolution.

**Tech Stack:** Python/FastAPI/SQLModel, pytest (`ssh myflix`). Client Kotlin/Compose/Ktor. Gemini `gemini-3.1-flash-lite`.

**Spec:** `docs/superpowers/specs/2026-07-05-note-apply-redesign-design.md` (commit 33d425e, main).

## Global Constraints
- Server: NO `from __future__ import annotations`; migration 022 additive (`ADD COLUMN`) + `tests/test_migrations.py` parity keystone green (derive DDL to match SQLModel); apply is DETERMINISTIC (no LLM in the apply path); base program (`TierExercise.movement_id`) NEVER mutated; **Option-C: load/rep overrides applied at prescription (assembler), NEVER write `current_load`/MovementState** (commit_session stays sole writer); full pytest suite green (baseline 404). Tests remote via `ssh myflix`; BUILD-AND-TEST-ONLY.
- Client: no new Gradle dependency; `app/build.gradle.kts` not committed.

---

### Task 1: Generalize `SlotOverride` + migration 022 + assembler LOAD/REPS

**Files:**
- Modify: `ironlog/models/program.py` (`SlotMovementOverride` → general columns), `ironlog/models/enums.py` (`OverrideType`)
- Create: `deploy/migrations/022_slot_override_generalize.sql`
- Modify: `ironlog/generation/assembler.py` (apply LOAD/REPS at prescription)
- Test: `tests/test_slot_override_apply.py`, `tests/test_migrations.py` (parity)

**Interfaces:**
- `OverrideType(str, Enum)`: `MOVEMENT`, `LOAD`, `REPS`.
- `SlotMovementOverride` gains: `override_type: OverrideType = OverrideType.MOVEMENT`, `load_delta: Optional[float]=None`, `load_absolute: Optional[float]=None`, `rep_low: Optional[int]=None`, `rep_high: Optional[int]=None`. (Keep the class + table name `slotmovementoverride` — it's now a general slot override; docstring updated. `override_movement_id` becomes nullable-in-practice for non-MOVEMENT types but keep its column as-is.)
- Assembler applies an active `LOAD`/`REPS` override for a slot's `TierExercise` at prescription.

- [ ] **Step 1: Write the failing assembler test**

Create `tests/test_slot_override_apply.py`: seed a program day with a slot whose movement has a calibrated `current_load` (via MovementState); assemble → assert the slot's `target_load` == the engine load. Then add an active LOAD override `load_delta=10` on that TierExercise → assemble → `target_load == engine_load + 10` (only that slot). Then `load_absolute=225` → `target_load == 225`. Then a REPS override `rep_low=5, rep_high=8` → the slot's PlannedSets carry `target_reps_low=5, target_reps_high=8`. Then `active=False` → reverts. Read `assembler.py` (`_resolve_load`, `_sets_for_scheme`, the per-slot loop) + the existing generation test fixtures (`gen_db_calibrated`) to build this against the real assemble entrypoint.

Also add an **Option-C guardrail** assertion: after assemble with a LOAD override, the movement's `MovementState.current_load` is UNCHANGED (the override didn't write it).

- [ ] **Step 2: Run to verify it fails** — `ssh myflix '… pytest -q tests/test_slot_override_apply.py'` → FAIL (columns/behavior missing).

- [ ] **Step 3: Add `OverrideType` + generalize the model**

In `ironlog/models/enums.py`:
```python
class OverrideType(str, Enum):
    MOVEMENT = "MOVEMENT"
    LOAD = "LOAD"
    REPS = "REPS"
```
In `ironlog/models/program.py`, extend `SlotMovementOverride` (update the docstring to "general per-slot override — movement swap / load adjust / rep-target change"; import `OverrideType`):
```python
    override_type: OverrideType = Field(default=OverrideType.MOVEMENT)
    load_delta: Optional[float] = None
    load_absolute: Optional[float] = None
    rep_low: Optional[int] = None
    rep_high: Optional[int] = None
```
(Make `override_movement_id` `Optional[int]` if not already, so a LOAD/REPS override needn't set it — but keep the FK. Verify existing MOVEMENT-override code still sets it.)

- [ ] **Step 4: Derive + write migration 022**

Print the exact new-column DDL SQLModel expects (compare create_all output before/after) and write `deploy/migrations/022_slot_override_generalize.sql` with additive `ALTER TABLE slotmovementoverride ADD COLUMN ...` for each new column, matching SQLModel's SQLite types (VARCHAR for the enum with length = longest member "MOVEMENT" = 8; FLOAT for load_*; INTEGER for rep_*; `override_type` NOT NULL DEFAULT 'MOVEMENT'). Verify against the parity test — run:
```bash
ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/python -c "from sqlalchemy.schema import CreateTable; from ironlog.models.program import SlotMovementOverride; from ironlog.db import engine; print(CreateTable(SlotMovementOverride.__table__).compile(engine))"'
```
and make the migration's cumulative result match.

- [ ] **Step 5: Apply LOAD/REPS in the assembler**

In `ironlog/generation/assembler.py`, at the per-slot prescription (where `_resolve_load` result + `rep_low`/`rep_high` feed `_sets_for_scheme`), look up the active `SlotOverride` for that slot's `TierExercise` and apply:
```python
def _apply_slot_override(db, tier_exercise_id, load, rep_low, rep_high):
    from ..models.program import SlotMovementOverride
    from ..models.enums import OverrideType
    ov = db.exec(select(SlotMovementOverride).where(
        SlotMovementOverride.tier_exercise_id == tier_exercise_id,
        SlotMovementOverride.active == True)).first()  # noqa: E712
    if ov is None:
        return load, rep_low, rep_high
    if ov.override_type == OverrideType.LOAD:
        if ov.load_absolute is not None:
            load = ov.load_absolute
        elif ov.load_delta is not None and load is not None:
            load = load + ov.load_delta
    elif ov.override_type == OverrideType.REPS:
        if ov.rep_low is not None: rep_low = ov.rep_low
        if ov.rep_high is not None: rep_high = ov.rep_high
    return load, rep_low, rep_high
```
Call it at the slot's prescription point (resolve the slot's `TierExercise.id` via the existing `slot_id → TierExercise` mapping used for rep-scheme resolution). MOVEMENT overrides are handled by `lay_skeleton` — this helper ignores them. It NEVER writes `current_load`/MovementState.

- [ ] **Step 6: Run tests + full suite** → green (baseline 404 + new). Migration parity green. Commit:
```bash
git add ironlog/models/program.py ironlog/models/enums.py deploy/migrations/022_slot_override_generalize.sql ironlog/generation/assembler.py tests/test_slot_override_apply.py
git commit -m "feat(apply): generalize SlotOverride (movement/load/reps) + assembler applies load/rep at prescription (migration 022)"
```

---

### Task 2: Classifier `action_type`

**Files:** `ironlog/notes/classify.py`; test `tests/test_note_classifier.py` (extend).

- [ ] **Step 1** Extend the failing test: assert `classify(...)` returns `action_type` for each of SWAP / LOAD_INCREASE / LOAD_DECREASE / REP_CHANGE / OTHER given canned Gemini JSON (injected `http`). Assert an unknown action_type defaults to OTHER.
- [ ] **Step 2** Run → fail.
- [ ] **Step 3** In `NOTE_CLASSIFICATION_SCHEMA` add `action_type` (enum of the 5 values, add to `required`). Update `NOTE_CLASSIFICATION_INSTRUCTION` to classify the action into that enum (SWAP=replace movement; LOAD_INCREASE/DECREASE="too light/heavy"; REP_CHANGE=different reps; OTHER=else). Add `action_type` to `NoteClassification` (map unknown→OTHER, like classification). `classify_session_notes` persists it inside `classification_meta` (add an `"action_type"` key alongside proposed_change/confidence/rationale). Keep `GeminiProposer` tests green.
- [ ] **Step 4** Run test + proposer regression + full suite → green. Commit `feat(notes): classifier emits structured action_type for apply routing`.

---

### Task 3: Explicit `/notes/{id}/apply` + `/programs/{id}/slots` + generalized `/overrides`

**Files:** `ironlog/api/app.py`; `ironlog/notes/apply.py` (`apply_override` takes explicit slot+override); test `tests/test_note_apply_redesign_endpoints.py`.

**Interfaces:**
- `POST /notes/{id}/apply` body `{tier_exercise_id, override_type, override_movement_id?, load_delta?, load_absolute?, rep_low?, rep_high?}`.
- `GET /programs/{id}/slots` → `[{tier_exercise_id, slot_id, day_role, tier_label, movement_id, movement_name, current_rep_low, current_rep_high}]`.
- `GET /overrides` generalized (type + summary fields + source_note_text).

- [ ] **Step 1: Write the failing endpoint tests**

Create `tests/test_note_apply_redesign_endpoints.py` (TestClient+StaticPool): seed program day + a bench slot + a Hip Thrust slot + a CONFIG_CHANGE note (LOAD_INCREASE, subject "hip thrust"). Assert:
- `GET /programs/1/slots` lists the slots with day/tier/movement.
- `POST /notes/{id}/apply {tier_exercise_id: <hip-thrust-te>, override_type:"LOAD", load_delta:10}` → 200; a LOAD override row on the HIP THRUST slot (not the note's attachment); note confirmed+applied.
- `POST .../apply` with `override_type:"MOVEMENT", override_movement_id:<x>` → MOVEMENT override.
- Validation: LOAD with both delta+absolute → 400; LOAD with neither → 400; unknown tier_exercise_id → 404; unknown target movement → 404; unknown note → 404.
- `GET /overrides` returns the override with type + rendered fields + source_note_text; revert works.

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Rewrite `apply_override` + endpoints**

`ironlog/notes/apply.py` — replace the note-based `apply_override` with an explicit one:
```python
def apply_override(note, tier_exercise_id, override_type, db, *,
                   override_movement_id=None, load_delta=None, load_absolute=None,
                   rep_low=None, rep_high=None):
    from ..models.program import TierExercise, SlotMovementOverride
    from ..models.enums import OverrideType
    from ..models.library import Movement
    te = db.get(TierExercise, tier_exercise_id)
    if te is None:
        raise SlotResolutionError(f"slot {tier_exercise_id} not found")
    ot = OverrideType(override_type)  # raises ValueError→caller maps 400
    kw = dict(tier_exercise_id=tier_exercise_id, source_note_id=note.id,
              active=True, override_type=ot)
    if ot == OverrideType.MOVEMENT:
        if override_movement_id is None or db.get(Movement, override_movement_id) is None:
            raise SlotResolutionError("target movement not found")
        kw["override_movement_id"] = override_movement_id
    elif ot == OverrideType.LOAD:
        if (load_delta is None) == (load_absolute is None):
            raise ValueError("exactly one of load_delta / load_absolute required")
        kw["load_delta"], kw["load_absolute"] = load_delta, load_absolute
    elif ot == OverrideType.REPS:
        if rep_low is None and rep_high is None:
            raise ValueError("rep_low and/or rep_high required")
        kw["rep_low"], kw["rep_high"] = rep_low, rep_high
    ov = SlotMovementOverride(**kw)
    db.add(ov); note.confirmed = True; note.applied = True
    db.add(note); db.commit(); db.refresh(ov)
    return ov
```
`ironlog/api/app.py`:
- Replace the `/notes/{id}/apply` body model + handler to take the explicit fields; map `ValueError`→400, `SlotResolutionError`→404, missing note→404.
- Add `GET /programs/{id}/slots` (iterate the program's ProgramDays→Tiers→TierExercises; join movement name + rep_low/high).
- Generalize `GET /overrides`: include `override_type` + type-specific fields + `to_movement_name` (for MOVEMENT) + `source_note_text` (join Note); keep active-only + revert.

- [ ] **Step 4: Run tests + full suite** → green. Commit `feat(api): explicit /notes/{id}/apply (movement/load/reps) + /programs/{id}/slots + generalized /overrides`.

---

### Task 4: Client — apply confirm-wizard + Active-adjustments rewrite

**Files:** `data/api/dto/NotesModels.kt` (+ slot/override/apply DTOs), `data/repo/NotesRepo.kt` (apply-explicit, programSlots, overrides), `ui/screens/review/ReviewScreen.kt`+`ReviewViewModel.kt`+`ReviewLogic.kt`. Reuse the movements list for the swap picker.

**Interfaces (match server verbatim):** `ProgramSlotOut{tier_exercise_id, slot_id, day_role, tier_label, movement_id, movement_name, current_rep_low, current_rep_high}`; `ApplyOverrideRequest{tier_exercise_id, override_type, override_movement_id?, load_delta?, load_absolute?, rep_low?, rep_high?}`; generalized `OverrideOut`.

- [ ] **Step 1** DTOs + a pure helper `defaultSourceSlot(subject: String?, slots: List<ProgramSlotOut>): ProgramSlotOut?` (case-insensitive best substring match of the subject against movement_name) + `adjustmentKind(actionType: String?): AdjustmentKind` (SWAP/LOAD/REPS/NONE, with a keyword fallback on proposed_change.action when action_type absent). Failing unit tests for both (e.g. subject "hip thrust" → the Hip Thrust slot; "LOAD_INCREASE" → LOAD; null+"switch to incline" → SWAP).
- [ ] **Step 2** Verify fail (compile/logic).
- [ ] **Step 3** Implement the DTOs + helpers; `NotesRepo.applyOverride(noteId, ApplyOverrideRequest)`, `programSlots(programId)`, `overrides()`. Logic tests pass.
- [ ] **Step 4** `ReviewViewModel`: on Apply, load `/programs/1/slots`, pre-select `defaultSourceSlot(subject, slots)`, expose the confirm-wizard state (source slot editable + the adjustment inputs by `adjustmentKind`); `applyOverride(...)` → reload. `ReviewScreen`: the Apply flow shows **"Change <movement> · <day> · <tier>"** (editable slot picker) + the action-routed adjustment (swap→movement picker; load→[+5][+10][-5] or set field; reps→low/high fields); gate Apply to SWAP/LOAD/REPS action kinds; Active-adjustments section renders the generalized `/overrides` as legible sentences with Revert. Build.
- [ ] **Step 5** `./gradlew :app:assembleDebug` SUCCESSFUL + `:app:testDebugUnitTest` green. Commit `feat(review): explicit apply confirm-wizard (slot + movement/load/reps) + Active adjustments`.

## On-device smoke (post-go-live meaningful)
"hip thrust too light" → Review → Apply → confirm slot shows **Hip Thrust** (not Dips) → pick +10 → Active adjustments shows "D#·tier·Hip Thrust +10 lb"; next generated session prescribes +10 on that slot only; Revert restores.

## Routing Plan
| Task | Repo | Route |
|---|---|---|
| 1 model+migration+assembler | server | Claude Code Agent subagent (ssh myflix) |
| 2 classifier action_type | server | Claude Code Agent subagent |
| 3 endpoints | server | Claude Code Agent subagent |
| 4 client wizard | client | Claude Code Agent subagent |

**Delegation ratio: 4/4 (100%).** Fresh implementer per task + two-verdict review + final cross-repo whole-branch review.

## Self-Review
**Spec coverage:** generalized override (movement/load/reps) → T1; assembler applies load/rep at prescription, Option-C guardrail → T1; classifier action_type → T2; explicit apply (slot+override, validated) + slots endpoint + generalized overrides → T3; client confirm-wizard (source-slot default from subject, action-routed adjustment) + Active-adjustments rewrite → T4. Migration additive+parity → T1. No LLM in apply, base program untouched, Option-C preserved ✓.

**Placeholder scan:** migration DDL derived from the model (Step 4 command shown); client screen is guided-prose (build-gated) with concrete DTOs/helpers. No TBD.

**Type consistency:** `override_type`/`load_delta`/`load_absolute`/`rep_low`/`rep_high`/`tier_exercise_id` names identical across model (T1), `apply_override` (T3), the apply endpoint body, and client `ApplyOverrideRequest` (T4). `OverrideType` values (MOVEMENT/LOAD/REPS) + `action_type` values (SWAP/LOAD_INCREASE/LOAD_DECREASE/REP_CHANGE/OTHER) used consistently. `ProgramSlotOut`/`OverrideOut` fields match the server dicts.
