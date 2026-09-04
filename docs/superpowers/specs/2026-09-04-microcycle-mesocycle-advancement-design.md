# Microcycle/Mesocycle Advancement — Design

## Background

The long-range periodization system (`docs/superpowers/specs/2026-09-03-long-range-periodization-design.md`, live in production since 2026-09-04) resolves a session's effective envelope from whatever the *current* Macrocycle/Mesocycle/Microcycle/BodyCompState/RecoveryStatus/DeloadState happen to be — but nothing advances that state over time. The cutover seeded exactly one Microcycle (#1, ordinal 1, planned 2026-09-04 to 2026-09-10) and manually activated it as a one-time bootstrap. Without this design, that state is permanent: periodization is live and correctly *wired*, but temporally *static*.

This document covers the state machine that makes it move: Microcycle lifecycle transitions, Mesocycle rollover, and the scheduling/scaffolding (`MicrocycleSlot`, `Session.microcycle_id`, the reconciler service, the audit log) those transitions need to be evaluated correctly. It does not cover DeloadState's own trigger/evidence logic (explicitly out of scope, see §6) or exercise-rotation/constraint-type classification (a separate, later topic — see the CORE memory note from this same session for that content).

This design was produced through the same brainstorming-skill architectural process as the original periodization design, in a single continuous session immediately following that design's production cutover — informed directly by two real bugs caught live during that cutover (see §7's regression-test requirements).

## Architecture invariants this design must not violate

Same six from repo-root `CLAUDE.md` as the original design doc; the two most load-bearing for this one:
1. **Rules dispose; the model proposes.** The reconciler (§3) is deterministic. It never asks an LLM anything.
3. **Planned vs Logged**, extended again: `MicrocycleSlot.resolution` (§2) is itself a planned-vs-actual pair — `planned_date`/`day_role` (what was expected) vs. `resolution`/`session_id`/`resolved_at` (what happened). Never collapse these into a single mutable field.

## 1. `Session.microcycle_id`

New nullable, indexed FK on `Session`. Populated at generation time (`ironlog/generation/assembler.py`'s `WorkoutSession(...)` construction, alongside the existing `prescription_snapshot`) going forward. **One-time backfill**, not a live migration: for the handful of sessions already generated since the 2026-09-04 cutover, extract `microcycle_id` from their existing `prescription_snapshot.microcycle_id` JSON key where present and resolvable to a real row. Genuinely pre-periodization sessions stay `NULL` — no inference attempted. Deleting a `Microcycle` that has resolved `Session` rows pointing to it must be restricted (FK `ON DELETE RESTRICT` or equivalent), never cascaded — historical training records are never invalidated by periodization-entity cleanup.

## 2. `MicrocycleSlot` — the real source of truth for "what was this week supposed to look like"

`expected_sessions` (an int on `Microcycle`) cannot answer "which specific day got skipped" or distinguish "5 arbitrary sessions happened" from "the actual planned rotation happened." `MicrocycleSlot` replaces it as the authoritative model:

```
MicrocycleSlot
  id
  microcycle_id (FK)
  ordinal                      -- position within the week
  day_role                     -- "D1 Upper A", "D2 Lower A", ... matches Session.day_role
  planned_date
  slot_type                    -- TRAINING | REST
  resolution                   -- PENDING | COMPLETED | SKIPPED
  resolution_source            -- SESSION | INFERRED_BOUNDARY | USER_EXPLICIT
  session_id (FK, nullable)
  resolved_at (nullable)
```

**Snapshotted once, at Microcycle materialization** (cutover, or the advancement engine creating the next one — §4) from the active `Program`'s day-role rotation at that moment. Not live-recomputed. If the `Program`'s day rotation changes mid-week, this Microcycle's slots are unaffected — the next Microcycle snapshots whatever is active when *it* materializes. Same principle as `prescription_snapshot`: preserve what was true at the time, not what's true now.

**Resolution flow:** when a `Session` is generated for a given `day_role`, it resolves the matching `PENDING` slot in the current Microcycle (`resolution=COMPLETED`, `resolution_source=SESSION`, `session_id` set, `resolved_at` set) — a shifted session (planned Sunday, trained Monday) still resolves its own originally-planned slot, not a slot from whatever microcycle Monday nominally falls in. A `TRAINING` slot only becomes `SKIPPED` (`resolution_source=INFERRED_BOUNDARY`) when the Microcycle's boundary evaluation (§3/§4) runs with that slot still `PENDING` past the tolerated drift window — **never inferred mid-week, and never by synthesizing a fake `Session` row.**

**Invariant:** at most one `TRAINING` slot per `(microcycle_id, day_role)` unless a future program model explicitly supports repeated day-roles in one week (it doesn't today).

## 3. The reconciler: `reconcile_current_training_state()`

A single idempotent entry point, invoked lazily — **no scheduler, no background job, no new infrastructure** in this pass (this codebase has none today; a future scheduled job, if ever built, calls this exact same service rather than containing separate transition logic). Called at the top of:
- session generation (`ironlog/generation/context.py`'s `resolve_context`, before `resolve_current_microcycle` is used)
- `GET /training/plan/current`
- any future write path that depends on current periodization state

Ordered steps (a fixed pipeline — this shape is the design's real deliverable, even though only steps 3-5 have real logic in this pass):
1. Refresh/evaluate RecoveryStatus *(already exists, unchanged by this design)*
2. Evaluate DeloadState — **no-op placeholder in this pass** (§6)
3. Reconcile Microcycle lifecycle (§4)
4. Reconcile Mesocycle lifecycle (§5)
5. Resolve effective policy *(already exists — `resolve_envelope`, unchanged)*

**Idempotency:** repeated calls with no new eligible transition are pure reads — `NO_CHANGE`. Only the first call that finds a genuinely due transition mutates anything; concurrent/repeated calls after that observe the already-reconciled state. Returns one of:
```
NO_CHANGE | MICROCYCLE_COMPLETED | MICROCYCLE_EXTENDED | MICROCYCLE_ADVANCED |
MESOCYCLE_COMPLETED | MESOCYCLE_ADVANCED | PLAN_EXHAUSTED
```

## 4. Microcycle lifecycle

```
NOT_STARTED → ACTIVE → COMPLETE
                 ↓
              EXTENDED → COMPLETE
                 ↓
              INCOMPLETE  (terminal)
```

- **`NOT_STARTED → ACTIVE`**: on materialization/activation (cutover bootstrap today; on Mesocycle rollover going forward, §5). Sets `actual_start_date`.
- **`ACTIVE → COMPLETE`**: when every `TRAINING` `MicrocycleSlot` for this Microcycle has `resolution != PENDING` (all `COMPLETED` or `SKIPPED`). Reason logged: `ALL_SESSIONS_RESOLVED`.
- **`ACTIVE → EXTENDED`**: `planned_end_date` has passed, unresolved `PENDING` `TRAINING` slots remain, and drift is still within the original design's tolerance policy (0–2 days tolerate silently, 3–4 flag — reused exactly, not redefined here). `drift_status`/`drift_days` (already on the `Microcycle` model) update accordingly.
- **`EXTENDED → COMPLETE`**: same all-resolved condition as above, checked again — an extended week still completes normally once its remaining slots resolve.
- **`ACTIVE`/`EXTENDED → INCOMPLETE`**: only on (a) explicit abandonment — out of scope for a write endpoint in this pass, so this path is only reachable via direct operator action for now, same as the cutover script's own precedent — or (b) drift exceeding the configured policy's outer bound (major interruption). **Terminal, and fully blocking**: unlike `COMPLETE`, an `INCOMPLETE` Microcycle does **not** trigger advancement to the next ordinal — the reconciler treats it the same way it treats `PLAN_EXHAUSTED` (§5), leaving the plan frozen at the incomplete Microcycle until an operator manually resolves it (a future write path). This is deliberate: auto-advancing past an abandoned/blown-out week would silently paper over exactly the situation `INCOMPLETE` exists to flag. No auto-replan is attempted by this design either — "replanning" an incomplete week is future scope, not something this pass invents a mechanism for.

`planned_posture` is never touched by any of these transitions — it was set once at materialization and stays immutable for the Microcycle's lifetime, exactly as the original design doc required. `effective_posture` (also already on the model) is what a future deload override would touch, not `planned_posture`.

## 5. Mesocycle lifecycle + rollover

```
PLANNED → ACTIVE → COMPLETE
```
(+ `CANCELLED`/`ABORTED` as escape hatches, not elaborated further here — no transition into them is defined by this design; they exist for a future manual-intervention path.)

**Rollover, triggered by the reconciler's step 4, when the current Mesocycle's final Microcycle reaches `COMPLETE`** (not `INCOMPLETE` — per §4, an `INCOMPLETE` Microcycle fully blocks further automatic advancement, mesocycle rollover included, until manually resolved):
1. Close the current Mesocycle (`ACTIVE → COMPLETE`).
2. Query the owning Macrocycle for the next ordered `Mesocycle` with `status=PLANNED` (`ordinal` = current + 1).
3. If found: activate it (`PLANNED → ACTIVE`, `actual_start_date` set), materialize its first `Microcycle` (ordinal 1, `planned_posture` from its `MesocycleTemplate.postures[0]`, `MicrocycleSlot`s snapshotted from the then-active `Program`), activate that Microcycle. Reason logged: `MESOCYCLE_ADVANCED`.
4. If not found: **`PLAN_EXHAUSTED`.** No new Mesocycle is invented, cloned, or auto-repeated — per explicit athlete directive, since mesocycles are meant to carry deliberately different exercise/stimulus selections, not a repeating default. The last valid Mesocycle/Microcycle state is preserved untouched; `GET /training/plan/current` reports `next_mesocycle: null`. Automatic rollover stays blocked until an operator materializes the next planned Mesocycle (§5a).

**Within an active Mesocycle, Microcycle-to-Microcycle advancement** (not a rollover, just the normal weekly case): when the current Microcycle reaches `COMPLETE` and it is *not* the Mesocycle's last, materialize and activate the next ordinal's Microcycle the same way (posture from `MesocycleTemplate.postures[ordinal]`, fresh `MicrocycleSlot` snapshot). Reason logged: `MICROCYCLE_ADVANCED`. **Planned dates are never slid**: the next Microcycle's `planned_start_date`/`planned_end_date` are computed from the Mesocycle's own schedule (start + N weeks), not from whenever the previous one actually finished — if Microcycle 1 ran two days late, Microcycle 2 still gets its original planned window; only its `actual_start_date` reflects when it really began.

### 5a. Mesocycle materialization mechanism (minimal, non-UI)

Since spec 05 built read-only endpoints only, this design needs *some* way to get a `PLANNED` next-Mesocycle into existence for rollover to consume — but **rollover consumes a plan, it does not author one**, so building a full write API/authoring UI is explicitly out of scope. A script, following the cutover script's own precedent (`scripts/migrate_phase_to_periodization.py`), is sufficient: `scripts/plan_next_mesocycle.py`, taking a `MesocycleTemplate` (existing or newly named, with its posture list), the target Macrocycle, and planned dates, and inserting one `PLANNED`-status `Mesocycle` row. No `Microcycle`/`MicrocycleSlot` materialization happens at this stage — that's rollover's job when the mesocycle actually activates.

## 6. Deload: explicitly out of scope, orchestration seam only

Advancement answers "where am I in the plan?" Deload evaluation answers "how should I train given current fatigue?" — different state machine, different inputs, not built here. This design:
- Treats current `DeloadState` as **read-only input** to policy resolution (already true today, unchanged).
- **Never** triggers, resolves, or clears a `DeloadState` as a side effect of any Microcycle/Mesocycle transition.
- **Never** lets an active deload rewrite `planned_posture` — a Microcycle planned as PUSH and executed under an active deload is still historically "planned PUSH, executed under deload," exactly as `prescription_snapshot` already records.
- Reserves step 2 of the reconciler pipeline (§3) as a defined no-op seam for a future deload evidence-evaluator to occupy, without restructuring the reconciler when that's built.

## 7. Audit trail: `AdvancementLog`

```
AdvancementLog
  id
  entity_type          -- "microcycle" | "mesocycle"
  entity_id
  from_state
  to_state
  reason                -- ALL_SESSIONS_RESOLVED | DRIFT_EXTENDED | DRIFT_EXCEEDED |
                            EXPLICIT_ABANDON | MICROCYCLE_ADVANCED | MESOCYCLE_ADVANCED |
                            PLAN_EXHAUSTED
  occurred_at
```
One row per transition the reconciler actually applies (not per no-op call). This is what lets `GET /training/plan/current` (or a future history view) truthfully answer "why did this advance" rather than just "what is it now."

## 8. Regression tests for the two live-caught cutover bugs

Both bugs were caught *after* the original 6-spec build's own review passes, during dry-run/apply against real data — a reminder that this class of bug (window-mismatch, activation-gap) isn't hypothetical. This design explicitly requires:
- A test asserting `_compute_recovery_status`'s data-sufficiency pre-check window is computed via the *exact same* cutoff formula as `readiness.py`'s own `_trailing_rows` (not just "close enough") — regression guard for the window-mismatch bug, already fixed in `scripts/migrate_phase_to_periodization.py` (commit `6af5440`) but worth re-asserting at the advancement-engine level if any new code path re-derives a similar window.
- A test asserting that **any** Microcycle produced by this design's own materialization paths (cutover bootstrap, Microcycle-to-Microcycle advancement, Mesocycle rollover's first-Microcycle materialization) is never left in `NOT_STARTED` after the operation that was supposed to activate it completes — regression guard for the seeded-but-inert bug caught live during the cutover.

## Non-goals (this design pass)

- DeloadState trigger/evidence logic (§6).
- A scheduled/background job (the reconciler's lazy-invocation design makes one unnecessary for correctness; a future one would be a trigger-mechanism addition only, not new logic).
- An explicit user-facing "skip this session" endpoint or client UI change — skip stays boundary-inferred in this pass (§2); `resolution_source=USER_EXPLICIT` is reserved in the schema for when that's built, but nothing produces it yet.
- A full Mesocycle-authoring write API/UI — §5a's script is a deliberately minimal bridge, not a preview of the eventual authoring surface.
- Auto-replanning an `INCOMPLETE` Microcycle or auto-cloning/inventing a Mesocycle when the plan is exhausted — both are explicit terminal/blocked states requiring manual intervention in this pass.
- Movement constraint-type classification / exercise-rotation strategy — a real, separate idea raised in this same session (captured in CORE memory), but orthogonal to advancement and not part of this design.

## Open questions carried forward

- Exact drift-tolerance day-counts (0–2/3–4/major) are reused from the original design doc's placeholders, still pending real-data tuning — not re-litigated here.
- Whether `INCOMPLETE`'s "explicit abandonment" path needs a real API before it's usable day-to-day, or whether direct operator action (matching the cutover script's own precedent) is acceptable long-term, is deferred to whenever it's first actually needed.
