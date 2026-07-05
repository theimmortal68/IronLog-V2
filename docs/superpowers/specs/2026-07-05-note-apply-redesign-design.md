# Note→Apply Redesign — Design

**Date:** 2026-07-05
**Repos:** server `~/projects/IronLog-V2` + client `~/projects/IronLog-V2-Client`.
**Status:** Approved design → spec for implementation planning.

## Goal

On-device use exposed four flaws in the shipped note-apply: (1) capture never showed which movement a note attached to; (2) Apply resolved the slot from the note's *attachment* not its *subject*, misfiring silently; (3) a load-change note ("too light") was funneled through the swap-only Apply into a bogus swap; (4) Active Swaps was unreadable. This redesign makes Apply **explicit and safe** and adds **load + rep adjustments** — all as deterministic, reversible, Option-C-safe live-state overrides.

## Scope

| IN | OUT (deferred) |
|---|---|
| Apply routes by action: **movement swap · load adjust · rep-target change** | ② per-exercise capture notes (mitigated by explicit source-slot confirm) |
| Explicit, confirmable **source slot** at apply-time (defaulted from Gemini's subject) | Auto-apply / any LLM in the apply path |
| Generalized `SlotOverride` (movement/load/reps) honored by generation; reversible | Writing `current_load`/MovementState (Option-C — overrides applied at prescription only) |
| Structured `action_type` from the classifier for deterministic routing | Multi-slot / program-wide changes |
| Active Swaps → **Active adjustments** rewrite with provenance | |

**Timing:** like the prior note-apply, durable only post-go-live (reconcile wipes notes + rebuilds TierExercises); built + testable now.

## 1. Data model — generalize the override

Extend `SlotMovementOverride` → **`SlotOverride`** (additive migration `022`; existing rows are `MOVEMENT`):
```
SlotOverride
  id, tier_exercise_id (FK), source_note_id (FK), created_at, active (revert=false)
  override_type: OverrideType  (MOVEMENT | LOAD | REPS)   # new enum col, default MOVEMENT
  override_movement_id: FK movement, nullable             # MOVEMENT
  load_delta: float, nullable                             # LOAD (tracks engine)
  load_absolute: float, nullable                          # LOAD (freeze); exactly one of delta/absolute
  rep_low: int, nullable                                  # REPS
  rep_high: int, nullable                                 # REPS
```
Migration `022`: `ADD COLUMN` for `override_type` (default `'MOVEMENT'`), `load_delta`, `load_absolute`, `rep_low`, `rep_high` (all nullable) on `slotmovementoverride`. Keep the table name (renaming is migration-churn); the Python model class may be renamed `SlotOverride` with `table=True` mapping to the existing table name via `__tablename__` if desired, else keep the class name and treat it as general. Additive `ADD COLUMN` only → parity keystone `tests/test_migrations.py` stays green (derive the ADD COLUMN DDL to match SQLModel).

## 2. Classifier — structured `action_type`

`NoteClassifier` (`ironlog/notes/classify.py`) gains a structured action in the response schema so Apply routes deterministically instead of keyword-guessing free text:
- `NOTE_CLASSIFICATION_SCHEMA` adds `action_type: enum(SWAP, LOAD_INCREASE, LOAD_DECREASE, REP_CHANGE, OTHER)` (required for CONFIG_CHANGE; else OTHER). Persisted in `classification_meta.action_type`.
- Instruction updated: classify the change's action into that enum (SWAP = replace the movement; LOAD_INCREASE/DECREASE = "too light/heavy"; REP_CHANGE = different rep target; OTHER = anything else). `proposed_change.movement` remains the extracted subject.
- Back-compat: notes classified before this (no `action_type`) → the client falls back to a keyword heuristic on `proposed_change.action`, or simply offers the source-slot picker + all three adjustment types.

## 3. Generation honors overrides (deterministic, Option-C-safe)

Per slot, query the active `SlotOverride` for that `TierExercise` and apply by type:
- **MOVEMENT** → `lay_skeleton._effective_movement_id` (already implemented; unchanged).
- **LOAD** → in the assembler, after `compute_load_trust` yields the prescribed `load` and **before** `_sets_for_scheme`: `load_delta` → `load + delta` (tracks engine progression); `load_absolute` → the fixed value. If `load` is None (needs-calibration) a delta is a no-op (stays None); an absolute sets it. Never writes `current_load`.
- **REPS** → override the slot's `rep_low`/`rep_high` passed into `_sets_for_scheme`.
- The assembler resolves the slot's `TierExercise` (via the existing `slot_id → TierExercise` map used by the rep-scheme resolution) to key the override lookup.
- Revert (`active=false`) → slot reverts to program/engine on next generate. Precedence for MOVEMENT unchanged (override > meso > base).

## 4. Apply becomes explicit + safe

The Apply flow (client) is a small confirm step; the note's attachment is no longer authoritative:
- **Source slot — shown + confirmable.** Client fetches the program's slots, **defaults** the source slot by fuzzy-matching `proposed_change.movement` (the subject, e.g. "hip thrust") against slot movement names, shows it plainly (**"Change Hip Thrust · D6 · GS1"**), and lets the athlete change it (pick any program slot). So a note attached to Dips still resolves to *Hip Thrust*, and the athlete always sees what's changing.
- **Action-routed adjustment**, by `action_type`:
  - `SWAP` → pick target movement (the existing picker).
  - `LOAD_INCREASE`/`LOAD_DECREASE` → `[+5] [+10] [+15]` (delta) **or** `set: __` (absolute).
  - `REP_CHANGE` → set rep low–high.
  - `OTHER`/unclassifiable → no Apply (Dismiss only).
- `POST /notes/{id}/apply { tier_exercise_id, override_type, payload }` — server validates (slot + movement/values exist; exactly one of delta/absolute for LOAD), creates the `SlotOverride`, sets note `confirmed=True, applied=True`. **No silent note-based slot resolution.**

## 5. Endpoints
- `POST /notes/{id}/apply` — body `{tier_exercise_id: int, override_type: str, override_movement_id?: int, load_delta?: float, load_absolute?: float, rep_low?: int, rep_high?: int}`. Validates per type (400 on bad payload, 404 on missing note/slot/movement). Creates the override; sets note confirmed+applied.
- `GET /programs/{id}/slots` (new) → `[{tier_exercise_id, slot_id, day_role, tier_label, movement_id, movement_name, current_rep_low, current_rep_high}]` for the source-slot picker (active program).
- `GET /overrides` (generalized) → each override with type + a rendered summary (see §6). `POST /overrides/{id}/revert` (unchanged).
- Existing `/notes/review`, `/notes/{id}/confirm`, `/notes/{id}/dismiss` unchanged.

## 6. Active adjustments (rewrite)
`/overrides` returns fields for a legible sentence; the client renders provenance:
> **D6 · GS1 · Hip Thrust** — +10 lb · *from "hip thrust too light"* · **[Revert]**
> **D1 · T1 · Bench → Incline DB** · *from "switch to incline"* · **[Revert]**

`OverrideOut`: `{id, override_type, day_role, tier_label, slot_id, movement_name, to_movement_name?, load_delta?, load_absolute?, rep_low?, rep_high?, source_note_text, created_at}` (joined; null-safe).

## 7. Error handling & boundaries
- Apply is deterministic — **no LLM in the apply path**; the human confirmed slot + adjustment. Base program never mutated. Overrides reversible with provenance.
- **Option-C:** LOAD/REPS overrides are applied at *prescription* (assembler), never write `current_load`/MovementState; `commit_session` remains the sole `current_load` writer. Guardrail test asserts apply/assemble write no MovementState/current_load.
- Bad payload → 400; missing note/slot/movement → 404.

## 8. Testing
Server (pytest, `ssh myflix`, no live Gemini): SlotOverride per type; assembler applies load delta/absolute + rep override for the right slot only; needs-calibration + delta no-op; MOVEMENT unchanged; Option-C guardrail (no current_load/MovementState write); classifier emits `action_type` (injected http) + proposer tests stay green; `/notes/{id}/apply` validates each type + explicit slot; `/programs/{id}/slots`; generalized `/overrides` + revert; migration 022 parity. Client: source-slot default-match + action→adjustment routing (pure logic), DTOs, build.

## 9. Build order (SDD, server-first)
1. `SlotOverride` generalization + migration 022 + assembler LOAD/REPS application + Option-C guardrail.
2. Classifier `action_type`.
3. Endpoints: explicit `/notes/{id}/apply`, `/programs/{id}/slots`, generalized `/overrides`.
4. Client: source-slot confirm + action-routed adjustment wizard + Active-adjustments rewrite.

## Global constraints
- Server: NO `from __future__ import annotations`; migration additive (`ADD COLUMN`) + parity keystone; deterministic apply (no LLM); base program never mutated; Option-C preserved (no current_load/MovementState write); full suite green (baseline 404). Client: no new Gradle dependency; `SERVER_BASE_URL` local-uncommitted.
