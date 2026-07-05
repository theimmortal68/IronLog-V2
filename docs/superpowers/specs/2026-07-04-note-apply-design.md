# Note-Apply (confirmed → applied) — Design

**Date:** 2026-07-04
**Repos:** server `~/projects/IronLog-V2` (FastAPI/SQLModel) + client `~/projects/IronLog-V2-Client` (Kotlin/Compose).
**Status:** Approved design → spec for implementation planning.

## Goal

Close the confirmed→applied loop for note-confirm. Today a confirmed `CONFIG_CHANGE` note only sets `confirmed=True`; `Note.applied` is never set `True` and nothing enacts the change. This task makes a confirmed **movement-swap** actually take effect, as a deterministic **live-state override** the generator honors — the base program is never mutated.

## Scope — the ripe slice

| IN | OUT (deferred) |
|---|---|
| `CONFIG_CHANGE` notes whose action is a **movement swap** ("switch Bench → Incline") | Other actions (drop / bump-load / change-reps) — different mechanisms |
| **Live-state override** (`SlotMovementOverride`) honored by `lay_skeleton`; base `TierExercise` never mutated | Any static-program edit; any LLM-in-the-apply-loop (apply is deterministic code) |
| Human picks the concrete target movement at confirm-time | AI auto-resolving the fuzzy target |
| Per-**slot** swap (the exact slot the note came from) | Program-wide swaps |
| `applied=True` on apply AND on dismiss (closes the context.py deviation-flag loop) | Auto-apply without human confirm |
| Minimal audit: list active overrides + revert | Rich override-history UI |

**Timing:** the go-live reconcile wipes Notes and recreates TierExercises (new ids), so any override created pre-launch is throwaway. This feature is **built now, durable post-go-live** — pre-launch it's testable but wiped Saturday. That's acceptable and expected (like the other on-hold pieces).

## Data model — `SlotMovementOverride` (live-state, mirrors MesoRotation)

New table (additive):
```
SlotMovementOverride
  id: int PK
  tier_exercise_id: int  FK -> tierexercise.id   (the slot being overridden)
  override_movement_id: int FK -> movement.id     (the swap target)
  source_note_id: int    FK -> note.id            (provenance)
  created_at: datetime
  active: bool = True                             (revert = set False)
```
The base program (`TierExercise.movement_id`) is **never changed**. Migration `021_slot_movement_override.sql` — `CREATE TABLE` (additive; parity keystone `tests/test_migrations.py` stays green).

## Slot resolution (deterministic, note → slot)

`resolve_slot(note, db) -> TierExercise`:
1. `Session = db.get(WorkoutSession, note.session_id)` → `day_role`.
2. `ProgramDay` where `day_role == session.day_role` (the active program's day).
3. Its `Tier`s → their `TierExercise`s → the one with `movement_id == note.movement_id`.
4. Exactly one match → that slot. Zero → 404/"slot not found". Two+ (rare) → a disambiguation error (the apply is rejected rather than guessing).

The note carries `movement_id` (which movement) + `session_id` (which day); together they pin the slot.

## Generation integration (`lay_skeleton`)

Add a shared helper `_effective_movement_id(db, te, meso_number) -> int` and use it in BOTH the anchor and adaptive branches of `lay_skeleton` (skeleton.py — the anchor branch currently checks `MesoRotation`; the adaptive branch uses `te.movement_id` directly):

**Precedence: active `SlotMovementOverride` > `MesoRotation`(meso_number) > `te.movement_id`.**

```
def _effective_movement_id(db, te, meso_number):
    ov = db.exec(select(SlotMovementOverride).where(
        SlotMovementOverride.tier_exercise_id == te.id,
        SlotMovementOverride.active == True)).first()
    if ov is not None:
        return ov.override_movement_id
    mr = db.exec(select(MesoRotation).where(
        MesoRotation.tier_exercise_id == te.id,
        MesoRotation.meso_number == meso_number)).first()
    return mr.movement_id if mr is not None else te.movement_id
```
Anchor branch: `movement_id = _effective_movement_id(...)`. Adaptive branch: `program_movement_id = _effective_movement_id(...)`. So an overridden slot emits its swap target **deterministically** (no LLM), the base program stays clean, and revert (set `active=False`) restores the program movement on the next generate.

## `applied` semantics (closes the deviation-flag loop + fixes the dismiss bug)

`context.py:332` flags any movement with an open note (`movement_id` set, `applied==False`) as deviation-eligible → the proposer gets consulted. So:
- **Apply** sets `note.confirmed=True` + **`applied=True`** → the note stops flagging the movement (the change is carried by the override, not by nudging the proposer). Correct.
- **Dismiss** now also sets **`applied=True`** (+ reclassify `JOURNAL`) — fixes the review-flagged bug where a dismissed note kept flagging the movement forever.

No change to `context.py`'s query itself (it already keys on `applied==False`); we just make both terminal actions set `applied=True`.

## Endpoints (server)

- `POST /notes/{id}/apply { target_movement_id }` → `resolve_slot` → create `SlotMovementOverride(tier_exercise_id, override_movement_id=target_movement_id, source_note_id=id, active=True)` → set `note.confirmed=True, applied=True`. Returns the override. 404 (note/slot missing) + 409 (ambiguous slot) handled. Validates `target_movement_id` is a real Movement.
- `POST /notes/{id}/dismiss` → set `classification=JOURNAL, applied=True` (extends the existing endpoint).
- `GET /overrides` → active overrides: `{id, tier_exercise_id, day_role, tier_label, slot_id, from_movement_name, to_movement_name, source_note_id, created_at}` (joined for display).
- `POST /overrides/{id}/revert` → set `active=False`. Idempotent. 404 on missing.

The existing `GET /notes/review` and `POST /notes/{id}/confirm` stay (confirm remains the acknowledge-only path for non-swap actionable notes, e.g. `PROGRAMMING_REQUEST`).

## Client (Review screen)

- A `CONFIG_CHANGE` swap proposal shows an **Apply** button → opens a **movement picker** (fetch the library list — the existing movements endpoint — pre-filtered by the AI's `proposed_change.movement` guess text) → the athlete picks the concrete target → `POST /notes/{id}/apply {target_movement_id}`. On success the item leaves the inbox.
- **Dismiss** as today (now also clears the proposer flag server-side).
- A minimal **Active swaps** section (from `GET /overrides`) with a **Revert** action per row. Keep it lightweight.
- DTOs mirror the server; no new Gradle dependency.

## Error handling & boundaries

- Apply is a deterministic code path — **no LLM in the apply loop** (honors hard-rules-as-code). The LLM's role ended at classification; the human resolved the concrete target.
- Base program untouched (static-config vs live-state). Override is reversible (`active` flag) with provenance (`source_note_id`).
- Ambiguous slot → reject (409), never guess. Missing note/slot/movement → 404.
- Option-C / progression-engine writers untouched — this writes `Note.applied`/`confirmed`, `SlotMovementOverride`; it never writes `current_load`/`ht_plates`/`ht_band_config`/MovementState.

## Testing

**Server (pytest, `ssh myflix`; no live Gemini):**
- `resolve_slot`: a note on D1-Bench resolves to the D1 Bench TierExercise; missing → error; two matches → 409.
- `apply`: creates the override, sets `confirmed`+`applied`; validates target movement; provenance set.
- `lay_skeleton`: with an active override on a slot, that slot's emitted movement is the target — and **only** that slot (others unchanged); precedence override>meso>base; revert (`active=False`) restores.
- `dismiss` sets `applied=True`; a dismissed/applied note is no longer in `note_flagged_movement_ids` (movement stops being deviation-eligible).
- `/overrides` list + revert; migration `021` parity keystone.
- Full suite stays green (baseline 383).

**Client:** picker + apply/dismiss/revert wiring + DTO decode + build. On-device deferred.

## Build order (SDD, server-first)

1. `SlotMovementOverride` model + migration `021` + `resolve_slot` helper (TDD).
2. `_effective_movement_id` + wire into `lay_skeleton` (both branches) + precedence tests; `dismiss` sets `applied=True`.
3. Endpoints: `apply`, `dismiss` update, `/overrides` list + revert (+ tests).
4. Client: Apply → movement picker → override; Active-swaps list + revert; DTOs; build.

## Global constraints

- Server: NO `from __future__ import annotations`; migration additive (`CREATE TABLE`) + parity keystone green; apply is **deterministic** (no LLM); base program never mutated; Option-C/engine writers untouched; full pytest suite green.
- Client: no new Gradle dependency; `SERVER_BASE_URL` local-uncommitted.
- Scope discipline: movement-swap only; per-slot; no auto-apply.
