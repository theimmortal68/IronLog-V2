# Microcycle/Mesocycle Advancement — Design

**Revision 2** — incorporates 12 corrections from a review pass on revision 1 (a written contradiction between §2 and §4, a production-critical missing-bootstrap bug, a regression against the original periodization design's own lifecycle/drift split, an ambient-mutable-state dependency, a single-event reconciler result that can't represent catching up across multiple boundaries, unspecified transaction/concurrency behavior, an off-by-one posture index, unpersisted plan-exhaustion state, an under-specified audit log, a fragile display-label match, undefined duplicate-session handling, and three smaller schema cleanups). Six of the twelve were assessed as blockers (would produce incorrect advancement, not just untidy architecture); all twelve are incorporated here since none required new design questions — every fix was already fully specified.

## Background

The long-range periodization system (`docs/superpowers/specs/2026-09-03-long-range-periodization-design.md`, live in production since 2026-09-04) resolves a session's effective envelope from whatever the *current* Macrocycle/Mesocycle/Microcycle/BodyCompState/RecoveryStatus/DeloadState happen to be — but nothing advances that state over time. The cutover seeded exactly one Microcycle (#1, ordinal 1, planned 2026-09-04 to 2026-09-10) and manually activated it as a one-time bootstrap. Without this design, that state is permanent: periodization is live and correctly *wired*, but temporally *static*.

This document covers the state machine that makes it move: Microcycle lifecycle transitions, Mesocycle rollover, and the scheduling/scaffolding (`MicrocycleSlot`, `Session.microcycle_id`, the reconciler service, the audit log) those transitions need to be evaluated correctly. It does not cover DeloadState's own trigger/evidence logic (explicitly out of scope, see §6) or exercise-rotation/constraint-type classification (a separate, later topic — captured in CORE memory from this same session, not part of this design).

## Architecture invariants this design must not violate

Same six from repo-root `CLAUDE.md` as the original design doc; the two most load-bearing for this one:
1. **Rules dispose; the model proposes.** The reconciler (§3) is deterministic. It never asks an LLM anything.
3. **Planned vs Logged**, extended again: `MicrocycleSlot.resolution` (§2) is itself a planned-vs-actual pair — `planned_date`/`day_code` (what was expected) vs. `resolution`/`session_id`/`resolved_at` (what happened). Never collapse these into a single mutable field.

## 1. `Session.microcycle_id`

New nullable, indexed FK on `Session`. Populated at generation time (`ironlog/generation/assembler.py`'s `WorkoutSession(...)` construction, alongside the existing `prescription_snapshot`) going forward. **One-time backfill**, not a live migration: for the handful of sessions already generated since the 2026-09-04 cutover, extract `microcycle_id` from their existing `prescription_snapshot.microcycle_id` JSON key where present and resolvable to a real row. Genuinely pre-periodization sessions stay `NULL` — no inference attempted. Deleting a `Microcycle` that has resolved `Session` rows pointing to it must be restricted (FK `ON DELETE RESTRICT` or equivalent), never cascaded — historical training records are never invalidated by periodization-entity cleanup.

**Unplanned/duplicate sessions (fix #11):** only a `Session` that binds to a `PENDING` `MicrocycleSlot` at generation time resolves planned work. If a slot for that `day_code` is already resolved (a second session generated for a day already completed) or no matching slot exists in the current Microcycle at all, the new `Session` is classified `UNPLANNED` (a new boolean/flag on `Session`, or a sentinel in `prescription_snapshot` — implementer's call) and **never** resolves a slot by falling back to a raw count. This is the entire reason slot identity was introduced instead of `expected_sessions`; a count-based fallback anywhere defeats it.

## 2. `MicrocycleSlot` — the real source of truth for "what was this week supposed to look like"

`expected_sessions` (an int on `Microcycle`) cannot answer "which specific day got skipped" or distinguish "5 arbitrary sessions happened" from "the actual planned rotation happened." `MicrocycleSlot` replaces it as the authoritative model:

```
MicrocycleSlot
  id
  microcycle_id (FK)
  ordinal                      -- position within the week
  day_code                     -- stable identity: "D1".."D7" (fix #10)
  day_label                    -- display snapshot at materialization time: "Upper A" etc,
                                   never matched against -- purely informational
  planned_date
  slot_type                    -- TRAINING | REST
  resolution                   -- PENDING | COMPLETED | SKIPPED | NOT_APPLICABLE (fix #12;
                                   REST slots are NOT_APPLICABLE, never PENDING/COMPLETED --
                                   "pending" implies an obligation a rest day doesn't carry)
  resolution_source            -- SESSION | INFERRED_BOUNDARY | USER_EXPLICIT
  session_id (FK, nullable, unique when non-null — fix #6)
  resolved_at (nullable)
```

**Snapshotted once, at Microcycle materialization** (bootstrap, or the advancement engine creating the next one — §4/§5) from the `Program` the owning `Mesocycle` is bound to (§4a) — not whatever `Program` happens to be active at that instant. If a `Program`'s day rotation changes later, already-materialized Microcycles are unaffected. Same principle as `prescription_snapshot`: preserve what was true at the time.

**Resolution flow:** when a `Session` is generated for a given `day_code`, it resolves the matching `PENDING` slot in the current Microcycle (`resolution=COMPLETED`, `resolution_source=SESSION`, `session_id` set, `resolved_at` set), in the **same transaction** as the `Session` insert (§6) — a shifted session (planned Sunday, trained Monday) still resolves its own originally-planned slot, not a slot from whatever microcycle Monday nominally falls in. A `TRAINING` slot only becomes `SKIPPED` (`resolution_source=INFERRED_BOUNDARY`) at drift-window expiry (§4) — **never inferred mid-week, and never by synthesizing a fake `Session` row.**

**Invariant:** at most one `TRAINING` slot per `(microcycle_id, day_code)`, and `(microcycle_id, ordinal)` unique (fix #6) — unless a future program model explicitly supports repeated day-roles in one week (it doesn't today). **A Microcycle with zero `TRAINING` slots may never automatically reach `COMPLETE`** (fix #2) — the "all resolved" check is vacuously true over an empty set, which is exactly the bug the bootstrap in §2a exists to prevent.

### 2a. Bootstrap: `MicrocycleSlot` for the already-live Microcycle #1 (production-critical, fix #2)

Microcycle #1 (id 1, ordinal 1, `ACTIVE`, planned 2026-09-04 to 2026-09-10) already exists in production with **zero** `MicrocycleSlot` rows — it was materialized by the cutover script before this subsystem existed. If the reconciler ships without a bootstrap, its first invocation would find zero `PENDING` training slots (there are zero slots, period) and immediately, incorrectly complete Microcycle #1. This is not a hypothetical edge case; it is the exact next thing that would happen in production. **This design cannot ship without this bootstrap, and the bootstrap must run before the reconciler is ever invoked against live data.**

One-time bootstrap script (following the cutover script's own precedent):
1. Snapshot Microcycle #1's expected `TRAINING`/`REST` slots from the `Program` its owning Mesocycle is bound to (§4a — for the bootstrap specifically, this is "whichever `Program` row is currently the seeded one," since no `program_id` existed on Mesocycle #1 until this same bootstrap sets it).
2. Backfill `Session.microcycle_id` (§1) for any sessions already generated since 2026-09-04.
3. For each backfilled `Session`, resolve its matching `day_code` slot: `resolution=COMPLETED`, `resolution_source=SESSION`, `session_id` set.
4. **Verify** the resulting slot count matches the expected `TRAINING` slot count for the program's day rotation before considering the bootstrap successful — a silent zero-slot result must hard-fail the bootstrap, not proceed.
5. Only after this verified bootstrap runs does the reconciler (§3) become safe to invoke against production.

## 3. The reconciler: `reconcile_current_training_state()`

A single idempotent entry point, invoked lazily — **no scheduler, no background job, no new infrastructure** in this pass. Called at the top of:
- session generation (`ironlog/generation/context.py`'s `resolve_context`, before `resolve_current_microcycle` is used)
- `GET /training/plan/current`
- any future write path that depends on current periodization state

Ordered steps per invocation:
1. Refresh/evaluate RecoveryStatus *(already exists, unchanged)*
2. Evaluate DeloadState — **no-op placeholder in this pass** (§6)
3. **Reconcile lifecycle to a fixed point** (fix #5): loop applying due Microcycle/Mesocycle transitions (§4/§5) until either no further transition is due, or a blocking state is reached (`INCOMPLETE`, `AWAITING_NEXT_MESOCYCLE` — §5). A capped iteration bound (e.g. 100) guards against a logic bug turning this into an infinite loop; hitting the bound is itself logged as an error, not silently truncated. This single loop replaces what revision 1 described as separate "step 3 (Microcycle)" / "step 4 (Mesocycle)" steps — advancing a Microcycle can immediately make a Mesocycle transition due (final Microcycle completing), which can immediately make the next Mesocycle's first Microcycle due to activate, all in one lazy call after (for example) two weeks of the app not being opened.
4. Resolve effective policy *(already exists — `resolve_envelope`, unchanged)*

**Transaction/concurrency (fix #6):** the entire reconciliation (steps 1-3) runs inside one DB transaction, with the current active Mesocycle/Microcycle row(s) locked for the duration (`SELECT ... FOR UPDATE` or SQLite's equivalent serialization) — two concurrent calls must not both observe "due" and both attempt to advance. DB-level uniqueness backs this up structurally, not just at the application level:
- `UNIQUE(macrocycle_id, ordinal)` on `Mesocycle`
- `UNIQUE(mesocycle_id, ordinal)` on `Microcycle`
- `UNIQUE(microcycle_id, ordinal)` and `UNIQUE(microcycle_id, day_code)` on `MicrocycleSlot`
- `UNIQUE(session_id)` on `MicrocycleSlot` where `session_id IS NOT NULL`
- At most one `ACTIVE` Mesocycle per Macrocycle, at most one `ACTIVE`/`EXTENDED`-schedule-state Microcycle per Mesocycle — enforced by a partial unique index where the target DB supports it (SQLite does, via `CREATE UNIQUE INDEX ... WHERE status = 'ACTIVE'`), application-level check as a fallback otherwise.

**Idempotency:** repeated calls with nothing due are pure reads. Return shape is a **structured list, not a single enum** (fix #5):
```
ReconcileResult
  transitions: List[Transition]   -- e.g. [MICROCYCLE_COMPLETED, MESOCYCLE_COMPLETED,
                                            MESOCYCLE_ACTIVATED, MICROCYCLE_ACTIVATED]
  final_microcycle_id
  final_mesocycle_id
  blocked_reason: Optional[str]   -- e.g. "INCOMPLETE_MICROCYCLE" | "AWAITING_NEXT_MESOCYCLE"
```

## 4. Microcycle lifecycle

**Lifecycle status and schedule drift are separate axes** (fix #3 — this design's revision 1 accidentally recombined them; the *original* periodization design doc already established this split correctly and it must not regress here):

```
lifecycle_status:  NOT_STARTED | ACTIVE | COMPLETE | INCOMPLETE
schedule_state:    ON_TIME | EXTENDED | DRIFT_FLAGGED       (independent of lifecycle_status)
```

A late-but-still-open week is `lifecycle_status=ACTIVE, schedule_state=EXTENDED, drift_days=2` — never `lifecycle_status=EXTENDED`.

**Transitions:**
- **`NOT_STARTED → ACTIVE`**: on materialization/activation. Sets `actual_start_date`.
- **Drift progression while `ACTIVE`** (fix #1 — resolves a direct contradiction in revision 1 between "slots become SKIPPED at boundary expiry" and "INCOMPLETE at outer-bound drift," which were competing outcomes for the same event):
  ```
  planned_end_date passes
    0–2 days late:  schedule_state stays ON_TIME (tolerated silently)
    3–4 days late:  schedule_state = DRIFT_FLAGGED
    >4 days late:   remaining PENDING TRAINING slots -> SKIPPED / INFERRED_BOUNDARY,
                     then re-check completion (below) -- ordinary missed-workout drift
                     does NOT produce INCOMPLETE. One forgotten session must never freeze
                     the whole periodization system until someone touches the database.
  ```
- **`ACTIVE → COMPLETE`**: whenever every `TRAINING` slot's `resolution != PENDING` (`COMPLETED` or `SKIPPED`), checked after every relevant slot resolution *and* after the `>4 days late` inferred-skip pass above. Reason logged: `ALL_SESSIONS_RESOLVED` (or `DRIFT_INFERRED_SKIP` when the completion was triggered by the inferred-skip pass specifically — captured in `AdvancementLog.details_json`, fix #9).
- **`ACTIVE → INCOMPLETE`**: **only** on an explicit, operator-declared interruption/abandonment/replan action — never automatically from drift alone. Out of scope for a write endpoint in this pass, so reachable only via direct operator action for now (same precedent as the cutover script). **Terminal and fully blocking**: an `INCOMPLETE` Microcycle does not trigger advancement to the next ordinal or Mesocycle rollover — the reconciler's fixed-point loop (§3) stops here, same as it stops at `AWAITING_NEXT_MESOCYCLE` (§5). "Terminal" describes this Microcycle's own state machine (it can never become `COMPLETE` after `INCOMPLETE`); it does not mean the *plan* is unrecoverable — a future write path can materialize a replacement, which is a distinct, not-yet-designed operation, not an automatic "un-terminal-ing" of the same row.

`planned_posture` is never touched by any of these transitions — set once at materialization, immutable for the Microcycle's lifetime. `effective_posture` is what a future deload override touches, not `planned_posture`.

Set `completed_at` (fix #12, alongside the existing `actual_start_date`/`actual_completion_date` already on the model) whenever `lifecycle_status` reaches `COMPLETE` or `INCOMPLETE`.

## 5. Mesocycle lifecycle + rollover

```
PLANNED → ACTIVE → COMPLETE
```
(+ `CANCELLED`/`ABORTED` as escape hatches for a future manual-intervention path — no transition into them defined here.)

**Rollover, evaluated inside the reconciler's fixed-point loop (§3), when the current Mesocycle's final Microcycle reaches `COMPLETE`** (not `INCOMPLETE` — an `INCOMPLETE` Microcycle blocks the loop before rollover is ever considered, §4):
1. Close the current Mesocycle (`ACTIVE → COMPLETE`), set `completed_at`.
2. Query the owning Macrocycle for the next ordered `Mesocycle` with `status=PLANNED` (`ordinal` = current + 1).
3. **If found**: activate it (`PLANNED → ACTIVE`, `actual_start_date` set), materialize its first `Microcycle` (ordinal 1, slots per §2, `planned_posture = MesocycleTemplate.postures[microcycle.ordinal - 1]` — **explicitly 0-indexing off a 1-based ordinal, fix #7**: Microcycle ordinal 1 → `postures[0]`, ordinal 2 → `postures[1]`, etc; a dedicated test asserts all four index mappings for a 4-week template), activate that Microcycle. Set `Macrocycle.planning_state = ACTIVE` if it wasn't already. Reason logged: `MESOCYCLE_ADVANCED`. The loop continues (a freshly-activated Microcycle doesn't itself need another pass, but the loop re-checks for due-ness rather than assuming this is the end).
4. **If not found**: set `Macrocycle.planning_state = AWAITING_NEXT_MESOCYCLE` (fix #8 — see §5a) if it isn't already in that state; log `PLAN_EXHAUSTED` **only on the transition into that state**, not on every subsequent call that finds it already there (idempotent logging, matching the reconciler's general idempotency contract). The loop stops here (`blocked_reason="AWAITING_NEXT_MESOCYCLE"`).

**Within an active Mesocycle, Microcycle-to-Microcycle advancement** (the normal weekly case, not a rollover): when the current Microcycle reaches `COMPLETE` and it is *not* the Mesocycle's last, materialize and activate the next ordinal's Microcycle the same way. **Planned dates are never slid**: the next Microcycle's `planned_start_date`/`planned_end_date` are computed from the Mesocycle's own schedule (start + N weeks), not from whenever the previous one actually finished — if Microcycle 1 ran two days late, Microcycle 2 still gets its original planned window; only its `actual_start_date` reflects when it really began.

### 5a. Plan-exhaustion state (fix #8)

`PLAN_EXHAUSTED` in revision 1 was a transient reconciler-call result with no persisted representation — leaving "is it logged every GET, or once?" and "how does the next call know it's already been surfaced?" both undefined, and `AdvancementLog.entity_type` (microcycle/mesocycle only) had no way to represent an exhausted *plan* at all. Fixed with a new field:

```
Macrocycle.planning_state: ACTIVE | AWAITING_NEXT_MESOCYCLE | COMPLETE
```
Lifecycle/planning metadata, not engine-prescription behavior — does not violate the original design's "Macrocycle has no engine behavior" non-goal. `AWAITING_NEXT_MESOCYCLE → ACTIVE` transitions cleanly and idempotently once `scripts/plan_next_mesocycle.py` (§5b) adds the next block and rollover finds it on a subsequent reconcile call.

### 5b. Mesocycle materialization mechanism (minimal, non-UI)

**Rollover consumes a plan, it does not author one** — building a full write API/authoring UI stays explicitly out of scope. A script, following the cutover script's precedent: `scripts/plan_next_mesocycle.py`, taking a `MesocycleTemplate` (existing or newly named, with its posture list), the target Macrocycle, **an explicit `program_id`** (fix #4, see §5c), and planned dates; inserts one `PLANNED`-status `Mesocycle` row. No `Microcycle`/`MicrocycleSlot` materialization happens at this stage — that's rollover's job when the mesocycle actually activates.

### 5c. `Mesocycle.program_id` — binding to a specific Program, not ambient state (fix #4)

Revision 1 had each Microcycle snapshot "the then-active `Program`" with no mechanism pinning *which* `Program` a *planned* future Mesocycle was meant to use — meaning rollover's slot materialization would depend on whatever `Program` happened to be active at the moment rollover actually fired, not what was intended when the Mesocycle was planned. This matters specifically because deliberate exercise/stimulus rotation across mesocycles (different Belle Mere grip widths, different row variants, etc. — see the CORE-memory-captured rotation-strategy discussion from this session) is the entire reason Mesocycle rollover refuses to auto-clone.

Fix: add `Mesocycle.program_id` (FK to `Program`, required), set by `plan_next_mesocycle.py` at planning time. Rollover's slot materialization (§2) reads the day-role rotation from **that specific `program_id`**, not "whatever's active."

**Caveat, explicitly not solved by this design:** `ironlog/models/program.py`'s `Program` is a single mutable row today (confirmed directly against the live model) — nothing prevents `program_seed.py`/direct edits from changing a `Program`'s `ProgramDay`/`Tier`/`TierExercise` structure in place after a `Mesocycle` has already been bound to its `id`. `Mesocycle.program_id` is strictly better than the current ambient-mutable-state default (it at least records *intent*), but it does not achieve true immutability. Making `Program` genuinely versioned/immutable (e.g. a new `Program` row per meaningful revision, an append-only edit model) is a real, separate architectural change and is an explicit non-goal of this pass.

## 6. Deload: explicitly out of scope, orchestration seam only

Unchanged from revision 1. Advancement answers "where am I in the plan?" Deload evaluation answers "how should I train given current fatigue?" — different state machine, not built here. This design:
- Treats current `DeloadState` as **read-only input** to policy resolution (already true today).
- **Never** triggers, resolves, or clears a `DeloadState` as a side effect of any transition.
- **Never** lets an active deload rewrite `planned_posture`.
- Reserves reconciler step 2 (§3) as a defined no-op seam for a future deload evidence-evaluator.

## 7. Audit trail: `AdvancementLog`

```
AdvancementLog
  id
  reconcile_run_id        -- shared across every row produced by one reconciler invocation (fix #9)
  entity_type              -- "microcycle" | "mesocycle" | "macrocycle"  (macrocycle added for
                               PLAN_EXHAUSTED/AWAITING_NEXT_MESOCYCLE, fix #8)
  entity_id
  event_type                -- from_state/to_state pair, or a named event
  from_state
  to_state
  reason                    -- ALL_SESSIONS_RESOLVED | DRIFT_INFERRED_SKIP | EXPLICIT_ABANDON |
                               MICROCYCLE_ADVANCED | MESOCYCLE_ADVANCED | PLAN_EXHAUSTED
  details_json               -- e.g. {"skipped_day_codes": ["D5"], "drift_days": 5} (fix #9)
  occurred_at
```

One `reconcile_run_id` groups every row a single fixed-point loop (§3) produces — e.g. a two-week-absence catch-up producing `MICROCYCLE_COMPLETED` → `MESOCYCLE_COMPLETED` → `MESOCYCLE_ADVANCED` → `MICROCYCLE_ADVANCED` all share one `reconcile_run_id`, reconstructable as one atomic operation.

## 8. Timezone (fix #12)

All date/drift comparisons (`planned_end_date` vs. "today," slot resolution timing) are computed in the athlete/program's local timezone, not server UTC — a server-UTC comparison would shift day boundaries by hours depending on time of day/season, causing exactly the kind of "works in tests, breaks at midnight in production" bug this session's two live-caught cutover bugs already exemplify. Implementer's call whether this is a fixed configured timezone or read from existing athlete-profile data if any exists; must not default to naive `date.today()` server-local behavior without an explicit decision either way.

## 9. Regression tests for the two live-caught cutover bugs

- A test asserting `_compute_recovery_status`'s data-sufficiency pre-check window uses the exact same cutoff formula as `readiness.py`'s own `_trailing_rows` (already fixed in commit `6af5440`; re-assert at the advancement-engine level if any new code path re-derives a similar window).
- A test asserting that **any** Microcycle produced by this design's own materialization paths (bootstrap, Microcycle-to-Microcycle advancement, Mesocycle rollover's first-Microcycle materialization) is never left in `NOT_STARTED` after the operation that was supposed to activate it completes.
- **New, from this revision's own fixes:** a test asserting a Microcycle with zero `MicrocycleSlot` rows can never reach `COMPLETE` (fix #2's core invariant) — the exact bug this revision caught before it could ship.
- A test asserting posture indexing for all four ordinals of a 4-week template (fix #7).

## Non-goals (this design pass)

- DeloadState trigger/evidence logic (§6).
- A scheduled/background job (the reconciler's lazy-invocation design makes one unnecessary for correctness; a future one would call the same service, not add new logic).
- An explicit user-facing "skip this session" endpoint or client UI change — skip stays boundary-inferred in this pass; `resolution_source=USER_EXPLICIT` is reserved in the schema for when that's built.
- A full Mesocycle-authoring write API/UI — §5b's script is a deliberately minimal bridge.
- Auto-replanning an `INCOMPLETE` Microcycle, or any mechanism that un-terminal-izes one automatically.
- True `Program` immutability/versioning (§5c's caveat) — `Mesocycle.program_id` records intent but doesn't yet enforce it.
- Movement constraint-type classification / exercise-rotation strategy (captured in CORE memory, orthogonal to advancement).

## Open questions carried forward

- Exact drift-tolerance day-counts (0–2/3–4/>4) are reused from the original design doc's placeholders, still pending real-data tuning.
- Whether `INCOMPLETE`'s explicit-abandonment path needs a real API before it's usable day-to-day, or whether direct operator action remains acceptable long-term, is deferred to whenever it's first actually needed.
- Athlete/program timezone source (§8) — fixed config vs. profile data — left to implementation.
