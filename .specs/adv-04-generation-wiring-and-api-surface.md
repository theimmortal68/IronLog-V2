## Objective
Wire the reconciler into the real request paths: Session generation binds a `MicrocycleSlot` and re-verifies `program_prescription_hash` before creating a `Session`; the workout-submit endpoint atomically resolves that slot; blocked states (`WAITING_FOR_MICROCYCLE_START`, `PROGRAM_DRIFT`, etc.) surface correctly from both write and read endpoints.

## Context
Source: design doc §2a (binding/resolution split, monotonic resolution, atomic completion), §3a (caller semantics for every `blocked_reason`), §5a (session-generation transaction race + hash check), §1 (`plan_status` idempotency — duplicate generation always returns the existing `Session`, never `UNPLANNED`).

**CORRECTED via a second, deeper codebase survey 2026-09-05 (this supersedes the original survey below it — the app does NOT do single-phase generation-equals-insert; read this section, not the assumptions further down):**

The real architecture is a **two-phase generate → approve flow**, not a single insert:
1. `POST /generate` (`app.py:370`, `generate()`) → calls `generate_session()` (`ironlog/generation/loop.py:190`) → resolves context, lays skeleton, assembles a candidate into an in-memory `RepairOutcome.assembled: AssembledSession` (wraps an **unpersisted** `WorkoutSession`, no `id` yet, no DB write at all — confirmed by the function's own docstring: "nothing written to DB until approve"). The candidate is cached in an in-process dict (`_candidates[candidate_id] = outcome`) and returned as a preview.
2. `POST /sessions/{candidate_id}/approve` (`app.py:405`, `approve_session()`) → pops the candidate, calls `commit_session()` (`ironlog/generation/loop.py:53`) — **this is the SOLE writer that ever calls `db.add(session)` / `db.commit()`** for a generated session (its own docstring: "The SOLE writer of current_load..."). This is where a `Session.id` first comes into existence.

This means the design's two distinct checks land in two different places, not one:
- **The `blocked_reason` gate** (design §3a: "before generation proceeds... do NOT generate a normally-planned Session") belongs at the **top of `generate_session()`** (`loop.py:190`, before `lay_skeleton()` is called) — this is genuinely "the top of session generation," and blocking here means a blocked plan never even produces a preview candidate.
- **The fresh `program_prescription_hash` re-check + the actual `MicrocycleSlot` lock/bind transaction** (design §5a) belong inside **`commit_session()`** (`loop.py:53`, around its existing `db.add(session); db.commit()`, currently at `loop.py:86-87`) — because a candidate can sit cached in `_candidates` for an arbitrary amount of time between generate and approve, `commit_session()` is the only place that closes the real race window, and it's already the codebase's designated "sole writer" seam for exactly this kind of write.

**`GenerationContext` (context.py:88) already carries `current_microcycle: Optional[Microcycle]`**, resolved by `resolve_context()` before `assemble()` runs — this is already-existing periodization wiring from the original (non-advancement) periodization build, not something this spec adds. `_prescription_snapshot()` (`assembler.py:313`) **already writes `microcycle_id` and `mesocycle_id` into `session.prescription_snapshot`** (confirmed: `assembler.py:326-327`) — so `commit_session()` can read `session.prescription_snapshot["microcycle_id"]` to know which Microcycle a candidate belongs to, without `AssembledSession` needing a new field.

To find the specific `MicrocycleSlot` to bind: `MicrocycleSlot.day_label` is set (by `adv-03`'s `_slot_specs`) to the exact `ProgramDay.day_role` string, and `WorkoutSession.day_role` is exactly the same string the caller passed to `/generate` (confirmed via live `programday` table: `day_role` values like `"D1 Upper A"`, `"D4 Upper B"`, empty string for rest days). So: match on `MicrocycleSlot.microcycle_id == <from prescription_snapshot> AND MicrocycleSlot.day_label == session.day_role`.

Unchanged from the original survey (still accurate, this part of the app is a separate, later phase untouched by the generate/approve split):
- Workout-submit endpoint: `submit_session` at `app.py:508`; idempotency check at line 519; `ws.status = SessionStatus.COMPLETED` at line 553, then `db.add(ws); db.commit()`.
- `GET /training/plan/current` at `app.py:1708` (`get_current_plan`), calls `resolve_current_microcycle` then `resolve_envelope`.
- `GET /training/macrocycles/{macrocycle_id}` at `app.py:1791` (`get_macrocycle`).

Depends on `adv-01` (schema, merged), `adv-03` (reconciler, merged — `reconcile_current_training_state`, `ReconcileResult`, `InvalidPlanConfigurationError`, all in `ironlog.engine.advancement`).

## File targets
- `ironlog/generation/loop.py` — `generate_session()` (~line 190, add the blocked-reason gate at the top) and `commit_session()` (~line 53-90, add the hash re-check + slot lock/bind transaction around the existing `db.add(session); db.commit()`).
- `ironlog/api/app.py` — `generate()` (~line 370, catch the new blocked-plan exception and turn it into a structured non-500 error response), `submit_session` (~line 508-553), `get_current_plan` (~line 1708), `get_macrocycle` (~line 1791).
- `ironlog/api/schemas_periodization.py` — extend `CurrentPlanOut` (or equivalent) with `current_active_microcycle` (nullable), `next_microcycle`, `starts_on`, `blocked_reason`.
- `tests/test_generation_loop.py` (or wherever `commit_session`/`generate_session` are currently tested — confirm actual filename before writing), `tests/test_periodization_api.py` (confirmed to exist from the original periodization build) or wherever the two `GET /training/...` endpoints are tested.

## Changes

### Phase 1 — the blocked-reason gate, in `generate_session()` (design §3a)
At the very top of `generate_session()` (`loop.py:190`), before `lay_skeleton()` is called:
```
result = reconcile_current_training_state(db)
if result.blocked_reason is not None:
    do NOT call lay_skeleton() / resolve_context() / assemble()
    raise a new BlockedPlanError(blocked_reason=result.blocked_reason)
```
`generate()` (`app.py:370`) catches `BlockedPlanError` and returns a structured non-500 error (e.g. HTTP 409 with `{"blocked_reason": ...}` in the body) — never a bare 500, never a silently-degraded 200 preview.

**Note on `InvalidPlanConfigurationError`:** this is a *different* exception (raised by the reconciler itself when a Program/template produces zero `TRAINING` slots — a configuration defect, not a normal blocked state, per design revision 8/9). If `reconcile_current_training_state()` raises it, let it propagate (don't catch and reinterpret it as a `blocked_reason`) — that's a 500-class failure (a real defect needs to surface loudly), not a 409.

### Phase 2 — hash re-check + slot lock/bind, in `commit_session()` (design §5a)
`commit_session()` (`loop.py:53`) is the actual insert point. A candidate can sit cached in `_candidates` for an arbitrary time between generate and approve, so the fresh checks belong here, wrapped around the existing `db.add(session); db.commit()` (currently `loop.py:86-87`):
```
microcycle_id = session.prescription_snapshot.get("microcycle_id") if session.prescription_snapshot else None

if microcycle_id is not None:
    microcycle = db.get(Microcycle, microcycle_id)
    mesocycle = db.get(Mesocycle, microcycle.mesocycle_id)
    program = db.get(Program, mesocycle.program_id)
    current_hash = compute_program_prescription_hash(program)
    if current_hash != mesocycle.program_prescription_hash:
        raise BlockedPlanError(blocked_reason="PROGRAM_DRIFT")

    slot = query MicrocycleSlot where microcycle_id=microcycle_id
             and day_label == session.day_role
    if slot is not None:
        if slot.session_id is not None:
            existing = db.get(Session, slot.session_id)
            return existing   # idempotent success -- a day_code already
                               # bound to a Session always returns that
                               # Session, never a new one, never UNPLANNED
                               # (design revision 6/9)
        if microcycle.lifecycle_status != ACTIVE or slot.resolution != PENDING:
            raise a typed Conflict (caller re-fetches GET /training/plan/current)
        session.plan_status = SessionPlanStatus.PLANNED   # EXPLICIT -- see below
        session.microcycle_id = microcycle_id
    # if slot is None (e.g. a genuinely unplanned/legacy day_role with no
    # matching slot), fall through to the existing unmodified commit path --
    # this spec does not change behavior for sessions with no periodization
    # binding at all (that's the pre-existing, unrelated no-periodization case)

# ... existing db.add(session); db.commit(); db.refresh(session) ...

if microcycle_id is not None and slot is not None:
    slot.session_id = session.id   # resolution stays PENDING (design §2a)
    db.add(slot)
    db.commit()
```
**Explicit `plan_status=PLANNED` is mandatory, not the model default (fix carried from adv-01's Fable review):** `adv-01`'s `Session.plan_status` model default is `LEGACY` — that default exists only to backfill pre-migration rows cleanly and must never be what a freshly-generated, periodization-bound Session ends up with. This call site must set it explicitly.

**Never produce `plan_status=UNPLANNED` from any of these paths** (design revision 6/9 — this value is schema-reserved, not built here).

**New edge case (added after adv-01's review):** a freshly-committed `Session` bound to a `MicrocycleSlot` has `plan_status=PLANNED`, never the model's `LEGACY` default — write this as an explicit regression test, not just an implicit side effect of the other tests.

### Workout-submit endpoint (design §2a)
At the existing `ws.status = SessionStatus.COMPLETED` transition (and, if it exists elsewhere, wherever `SessionStatus.SKIPPED` gets set), make the `Session` status write and the `MicrocycleSlot.resolution` transition **one transaction** (design revision 7 fix #3):
```
if ws.microcycle_id is not None:  # bound to a slot
    slot = the MicrocycleSlot with session_id == ws.id
    if slot.resolution == PENDING:
        slot.resolution = COMPLETED (or SKIPPED, matching ws.status)
        slot.resolution_source = SESSION (or USER_EXPLICIT for an explicit skip)
        slot.resolved_at = now
    elif slot.resolution == SKIPPED:
        leave slot unchanged  # monotonic -- design §2a revision 4/7
    # slot.resolution == COMPLETED should be unreachable here (idempotency
    # check at line 519 already handles re-submission of an already-COMPLETED
    # Session before this point is reached)
db.add(ws); db.add(slot) if touched; db.commit()   # ONE commit, not two
```

### Blocked-state API surfacing (design §3a)
`GET /training/plan/current`: call the reconciler (or reuse whatever the existing call already does, if it's equivalent) and, when `blocked_reason is not None`, still return 200 with:
```
current_active_microcycle: null
next_microcycle: <the pending one, if any -- may be None for AWAITING_NEXT_MESOCYCLE>
starts_on: <next_microcycle's planned_start_date, if present>
blocked_reason: "<the value>"
```
Never error, never silently show a stale "current" state as if unblocked.

`GET /training/macrocycles/{id}`: no blocking behavior needed here (it's a historical/structural view), but confirm it correctly reflects `planning_state` if that's part of its existing response shape — extend the DTO if not.

## Edge cases
- `generate_session()` raises `BlockedPlanError` immediately (before `lay_skeleton`/`assemble` ever run) when the plan is blocked — no preview candidate is cached, no partial work happens.
- `InvalidPlanConfigurationError` from the reconciler propagates uncaught through `generate_session()` (it is a config-defect signal, not a `blocked_reason`).
- Approving a candidate (`commit_session`) whose `prescription_snapshot` names a Microcycle leaves the bound slot at `resolution=PENDING`, not `COMPLETED`, immediately after commit — the core regression guard from the original binding/resolution split (design revision 3's headline fix, still the most important behavior in this spec).
- A slot resolves `COMPLETED` only after `Session.status` actually transitions to `COMPLETED` via the submit endpoint (a separate, later phase — unaffected by this spec's generate/approve changes).
- A slot already resolved `SKIPPED` (from drift expiry) is left unchanged when its bound `Session` later completes — the `Session` itself still transitions to `COMPLETED` normally (design §2a monotonic-resolution case).
- A simulated crash between the `Session.status → COMPLETED` write and the slot-resolution write cannot occur at the submit endpoint — assert both happen in one transaction (force a rollback mid-way in a test and confirm neither side persisted).
- Approving two different candidates that both target the same `day_role`/Microcycle (simulating two `/generate` calls before either is approved) — the second `commit_session()` call returns the *first* approved `Session` idempotently rather than creating a second one or raising.
- Approving a candidate against a slot whose owning Microcycle is no longer `ACTIVE` (advancement moved on between generate and approve) raises a typed `Conflict`.
- `commit_session()` is blocked with `PROGRAM_DRIFT` when the `Program` was edited after the target Microcycle's activation but before approval (this is the actual race design §5a exists to close — generate-then-wait-then-approve is exactly the window).
- A `Session` with no periodization binding at all (no `microcycle_id` resolvable from `prescription_snapshot`) commits exactly as it did before this spec — unrelated, unbroken.
- `GET /training/plan/current` during `WAITING_FOR_MICROCYCLE_START` reports `current_active_microcycle=null`, correct `next_microcycle`/`starts_on`, and the blocked reason.

## Dependencies
`adv-01`, `adv-03` (both merged to `main`).

## Verification
- `pytest -q` — extend existing generation-loop and periodization-API test files with the cases above; full suite stays green (baseline: 840 passed as of this spec's dispatch).
- Manual: hit `GET /training/plan/current` locally against a test DB seeded into each of the four blocked states and confirm the response shape.
