# Microcycle/Mesocycle Advancement — Design

**Revision 6** — incorporates 3 corrections from a fifth review pass on revision 5, 2 flagged as blockers. (1) Revision 5's no-early-start fix (§4a) correctly stopped implicit early activation, but left the resulting gap unrepresented: from the moment a Microcycle completes early until its successor's `planned_start_date` arrives, there is no `ACTIVE` Microcycle at all, and nothing in revision 5 said what the reconciler, session generation, or `GET /training/plan/current` should do during that window. Fixed with a new `blocked_reason=WAITING_FOR_MICROCYCLE_START`, applied uniformly at both Microcycle-to-Microcycle and Mesocycle-boundary timing (a Mesocycle no longer activates early either — it stays `PLANNED` with its first Microcycle materialized-but-`NOT_STARTED` until the planned start date actually arrives). (2) The `program_structure_hash` check (now renamed `program_prescription_hash` — see below) only guarded *materialization*, but Session generation still reads prescription detail (sets/reps/RPE/etc.) straight from the live, mutable `Program` at generation time, days after its Microcycle's slots were already snapshotted — so a `Program` edit between materialization and generation was still invisible. Fixed by adding the same hash check as a precondition of planned Session generation, with its own `blocked_reason=PROGRAM_DRIFT`. Also broadened what the hash actually covers (sets, rep range, RPE target/cap, rest, progression scheme, equipment/configuration — not just day/tier/exercise identity and order) and renamed it accordingly, since "structure" undersold what it was protecting. (3) Removed the contradiction between §2a (duplicate generation → `UNPLANNED`) and §5a (duplicate generation → return existing `Session`) — planned generation is now unambiguously idempotent (a bound slot always returns its existing `Session`); `plan_status=UNPLANNED` stays in the schema, reserved for a future explicit "create a second, deliberately unplanned session" operation that this design does not build.

**Revision 5** — incorporates 3 corrections from a fourth review pass on revision 4, 2 flagged as blockers. (1) The `program_structure_hash` check (§5c) only ran at Mesocycle activation, but `MicrocycleSlot`s materialize one week at a time across the Mesocycle's life — a `Program` edited in place mid-Mesocycle (e.g. during week 1) would silently be picked up, unchecked, when week 2 materializes. Fixed by making the hash check a precondition of *every* slot-materializing operation, not just Mesocycle activation. (2) Nothing previously stopped the reconciler from activating next week's Microcycle the moment the current one's slots all resolve, even if that happens days before the new week's `planned_start_date` — fixed by splitting materialization (which may run early) from activation (which waits for the planned date, unless the reconciler run itself is already past it). (3) A lost slot-verification race (§5a) previously fell through to creating an `UNPLANNED` session, but a concurrency failure means the requested planned workout is no longer valid — it does not mean the athlete intended an unplanned one; fixed to return a typed conflict instead, consistent with revision 4 already refusing to build a generic unplanned-workout escape hatch.

**Revision 4** — incorporates 9 corrections from a third review pass on revision 3, 4 flagged as blockers. Headline fixes: (1) `plan_status` was specified as both a required column and nullable-for-legacy-rows — contradictory at the schema level; resolved with a third `LEGACY` value instead of `NULL`. (2) The Mesocycle rollover algorithm is only triggered by "final Microcycle reaches `COMPLETE`" — but that event has already happened by the time `Macrocycle.planning_state` sits at `AWAITING_NEXT_MESOCYCLE`, so planning a successor Mesocycle via `plan_next_mesocycle.py` produced a row the reconciler had no rule left to notice; fixed with an explicit resume branch. (3) `allow_unplanned` previously fell back to legacy `PhasePolicy`-driven resolution when the plan was blocked — reopening exactly the back door the clean cutover closed; the escape hatch is removed from this design pass entirely rather than resurrecting legacy authority. (4) Revision 3 never said what happens when a `Session` bound to an already-`SKIPPED` slot is completed days later — fixed by making slot resolution monotonic (`PENDING → {COMPLETED, SKIPPED}`, both terminal).

**Revision 3** — incorporates 7 corrections from a second review pass on revision 2, 3 flagged as blockers. The biggest: **revision 2 still conflated Session *generation* with Session *completion*.** IronLog-V2 generates a `Session` row on-demand when the athlete opens the app for a day (`status=PLANNED`) — that is not the same event as actually training. Revision 2's "generating a Session resolves the slot to `COMPLETED`" would have let opening the app and never training still count as a completed workout toward advancement. Fixed by separating *binding* (generation) from *resolution* (actual completion, keyed off the existing `Session.status` transition to `COMPLETED` — confirmed to already exist at `ironlog/api/app.py:553`, not invented here). The other two blockers: `EXTENDED` was defined as a `schedule_state` value in revision 2 but the drift-band transition rules never actually produced it (unreachable state), and revision 2 never specified what session generation/policy resolution should do when reconciliation leaves the plan in a blocked state.

## Background

The long-range periodization system (`docs/superpowers/specs/2026-09-03-long-range-periodization-design.md`, live in production since 2026-09-04) resolves a session's effective envelope from whatever the *current* Macrocycle/Mesocycle/Microcycle/BodyCompState/RecoveryStatus/DeloadState happen to be — but nothing advances that state over time. The cutover seeded exactly one Microcycle (#1, ordinal 1, planned 2026-09-04 to 2026-09-10) and manually activated it as a one-time bootstrap. Without this design, that state is permanent: periodization is live and correctly *wired*, but temporally *static*.

This document covers the state machine that makes it move. It does not cover DeloadState's own trigger/evidence logic (§6) or exercise-rotation/constraint-type classification (captured in CORE memory, not part of this design).

## Architecture invariants this design must not violate

Same six from repo-root `CLAUDE.md` as the original design doc; the two most load-bearing for this one:
1. **Rules dispose; the model proposes.** The reconciler (§3) is deterministic. It never asks an LLM anything.
3. **Planned vs Logged**, extended again: `MicrocycleSlot.resolution` (§2) is a planned-vs-actual pair. Never collapse it into a single mutable field.

## 1. `Session.microcycle_id` and `Session.plan_status`

New nullable, indexed FK `microcycle_id` on `Session`, plus a new **required, real column** `plan_status: PLANNED | UNPLANNED | LEGACY` (fix #1, blocker — revision 3 specified `plan_status` as both required and "pre-periodization sessions stay `NULL`", which cannot both be true at the schema level; a `NULL` carrying business meaning is exactly the ambiguity a required column is supposed to remove). **One-time backfill**: sessions generated since the 2026-09-04 cutover get `microcycle_id` extracted from existing `prescription_snapshot.microcycle_id` JSON where resolvable and `plan_status=PLANNED`, since periodization wasn't yet distinguishing unplanned sessions when they were generated; every session that predates the cutover backfills to `plan_status=LEGACY`, `microcycle_id=NULL`. `LEGACY` means: *this Session predates periodization accounting; no statement is made about whether it corresponded to a `MicrocycleSlot`.* Deleting a `Microcycle` with resolved `Session` rows must be restricted, never cascaded.

**Terminology clarification (fix #5):** `plan_status=PLANNED` means *this Session originated from / was bound to a planned `MicrocycleSlot`* — it does **not** mean the session counted as completed planned work. Whether the underlying training actually happened is the slot's `resolution` (§2), not `plan_status`. A query like `WHERE plan_status = 'PLANNED'` answers "which sessions were bound to the plan," not "which sessions represent successfully completed programmed work" — see §2a's monotonic-resolution case for a concrete example of a `PLANNED` session whose slot resolved `SKIPPED`.

**`UNPLANNED` is reserved, not produced (revision 6).** Nothing built in this design pass ever sets `plan_status=UNPLANNED` — §2a's duplicate-generation case now returns the existing `Session` idempotently instead. The value stays in the enum for a distinct future explicit operation (an athlete deliberately requesting a second, non-programmed workout), which this design does not build (see Non-goals).

## 2. `MicrocycleSlot` — the real source of truth for "what was this week supposed to look like"

```
MicrocycleSlot
  id
  microcycle_id (FK)
  ordinal
  day_code                     -- stable identity: "D1".."D7"
  day_label                    -- display snapshot, never matched against
  planned_date
  slot_type                    -- TRAINING | REST
  resolution                   -- PENDING | COMPLETED | SKIPPED | NOT_APPLICABLE (REST slots)
  resolution_source            -- nullable: SESSION | INFERRED_BOUNDARY | USER_EXPLICIT
  session_id (FK, nullable, unique when non-null)
  resolved_at (nullable)
```

**`resolution_source` is nullable (fix #6):** `TRAINING` slots sitting at `PENDING` and `REST` slots at `NOT_APPLICABLE` have no source to report. Invariant:
```
PENDING        -> resolution_source NULL
NOT_APPLICABLE -> resolution_source NULL
COMPLETED      -> resolution_source = SESSION
SKIPPED        -> resolution_source = INFERRED_BOUNDARY | USER_EXPLICIT
```

**Snapshotted once, at Microcycle materialization**, from the specific `Program` the owning `Mesocycle` is bound to (§5c) — never "whatever's active." **Invariant, unchanged from revision 2:** at most one `TRAINING` slot per `(microcycle_id, day_code)`; `(microcycle_id, ordinal)` unique. A Microcycle with zero `TRAINING` slots may never automatically reach `COMPLETE` — see the bootstrap in §2b, which exists specifically because Microcycle #1 is already live in production with zero slots and this exact vacuous-truth bug was caught in review before shipping.

### 2a. Binding vs. resolution (fix #1, blocker)

**Generating a `Session` binds a slot; it does not resolve it.** Two distinct events, keyed off `Session.status` (already exists: `PLANNED | IN_PROGRESS | COMPLETED | SKIPPED`; the transition to `COMPLETED` already happens at `ironlog/api/app.py:553`, the workout-submit endpoint — this design reuses that signal, it does not invent a second "completed" concept):

```
Session generated (status=PLANNED):
  slot.session_id = session.id
  slot.resolution stays PENDING

Session.status → COMPLETED (existing submit-workout flow, app.py:553):
  slot.resolution = COMPLETED
  slot.resolution_source = SESSION
  slot.resolved_at = the completion timestamp already recorded on Session
```

If `Session.status` is ever set to `SKIPPED` (the enum value already exists) instead, the bound slot resolves `SKIPPED` / `resolution_source=USER_EXPLICIT` — not `INFERRED_BOUNDARY`, since this was an explicit signal, not a boundary-time inference. Nothing in this design currently produces `Session.status=SKIPPED` (no explicit-skip endpoint is built here — same non-goal as revision 1/2), but the resolution path is defined now so it composes cleanly whenever that's built.

**A slot with `session_id != NULL` is already bound, regardless of its `resolution` value.** Planned Session generation is unambiguously idempotent (fix #3, revision 6 — this replaces revision 5's "a second `Session` generated for the same `day_code` becomes `plan_status=UNPLANNED`", which directly contradicted §5a's later, correct idempotency rule): a generation request for a `day_code` whose slot is already bound **returns the existing `Session`**, always. The system cannot distinguish "the app retried the same request" from "the athlete deliberately wants a second workout" from the request shape alone, and since a generic unplanned-workout pathway is explicitly out of scope (§3a), guessing intent by manufacturing `UNPLANNED` here is exactly the ambiguity to avoid. `plan_status=UNPLANNED` (§1) stays in the schema, reserved for a distinct, explicit future operation (e.g. `create_unplanned_session(...)`) that this design does not build — nothing in this pass ever sets it.

**Drift expiry and a bound-but-incomplete slot:** if a slot is bound to a `Session` that never reached `COMPLETED` (the athlete opened the app but never trained, or never submitted) and the Microcycle's drift window expires (§4), that slot still resolves `SKIPPED` / `INFERRED_BOUNDARY` — the `session_id` FK is **not** cleared; it remains as historical evidence the day was opened but not completed. This is exactly the gap revision 2 missed. **Exception: a `Session` sitting at `IN_PROGRESS` when drift expiry runs is never touched** — the drift-expiry pass only resolves slots bound to `PLANNED` (never opened for real) or absent sessions; a genuinely active workout is left alone regardless of how stale its Microcycle is. (A policy for a *stale* `IN_PROGRESS` session — e.g. one abandoned mid-workout weeks ago — is deferred; this rule only guarantees the expiry pass itself never overwrites one.)

**Slot resolution is monotonic (fix #4, blocker) — `COMPLETED` and `SKIPPED` are both terminal, never re-resolved:**
```
PENDING   -> COMPLETED
PENDING   -> SKIPPED
COMPLETED -> (no further transition)
SKIPPED   -> (no further transition)
```
This matters because a bound `Session` can be completed *after* its slot already resolved `SKIPPED` at drift expiry — e.g. a D5 `Session` generated and left `PLANNED`, drift passes `>4` days and the slot resolves `SKIPPED`/`INFERRED_BOUNDARY`, the Microcycle completes and advancement moves on, and only then does the athlete open that old `Session` and submit it. The completion hook (§2a's `Session.status → COMPLETED` handler) therefore checks the slot's current `resolution` first:
```
if slot.resolution == PENDING:
    resolve COMPLETED (as already specified above)
elif slot.resolution == SKIPPED:
    leave the slot unchanged
    the Session may still complete historically,
    it does not retroactively alter periodization state
```
The resulting historical record is legitimate and informative, not a bug: `Session.status=COMPLETED`, `Session.plan_status=PLANNED`, `MicrocycleSlot.resolution=SKIPPED` — the workout originated as planned work but wasn't completed within the Microcycle's accepted execution window, and was trained later outside that window.

### 2b. Bootstrap: `MicrocycleSlot` for the already-live Microcycle #1 (production-critical)

Unchanged from revision 2 — still required, still blocking, still must run before the reconciler is ever invoked against live data. The §5c hash re-check (fix #1) applies here too, trivially: since the bootstrap runs once, immediately, against whatever `Program` state exists at the moment it's executed, there is nothing to have drifted from yet — the hash is simply computed and stored on the bound Mesocycle at this point, not compared against an earlier value.
1. Snapshot Microcycle #1's expected `TRAINING`/`REST` slots from the `Program` its (now-bound, §5c) Mesocycle uses.
2. Backfill `Session.microcycle_id`/`plan_status` for sessions already generated since 2026-09-04.
3. For each, resolve its matching `day_code` slot per the binding/resolution split in §2a (i.e., check the real `Session.status`, not just "a Session exists" — a session generated but never completed binds the slot without resolving it, exactly as live behavior should be).
4. **Verify** the resulting slot count matches the expected `TRAINING` count before considering the bootstrap successful — a silent zero-slot result hard-fails the bootstrap.

## 3. The reconciler: `reconcile_current_training_state()`

Invoked lazily, no scheduler, at the top of session generation, `GET /training/plan/current`, and any future write path depending on periodization state.

1. Refresh/evaluate RecoveryStatus *(unchanged)*
2. Evaluate DeloadState — no-op placeholder (§6)
3. **Reconcile lifecycle to a fixed point**: loop applying due Microcycle/Mesocycle transitions until no further transition is due or a blocking state is reached (`INCOMPLETE`, `AWAITING_NEXT_MESOCYCLE`). Capped iteration bound, logged as an error if hit.
4. Resolve effective policy — **conditionally, see §3a**.

**Transaction/concurrency, unchanged from revision 2:** steps 1-3 run in one DB transaction with the current active Mesocycle/Microcycle locked. DB-level uniqueness: `UNIQUE(macrocycle_id, ordinal)` on Mesocycle, `UNIQUE(mesocycle_id, ordinal)` on Microcycle, `UNIQUE(microcycle_id, ordinal)` + `UNIQUE(microcycle_id, day_code)` on `MicrocycleSlot`, `UNIQUE(session_id)` on `MicrocycleSlot` where non-null. **Simplified uniqueness invariant (fix #7b — revision 2's phrasing conflated the now-separate axes):** at most one `lifecycle_status=ACTIVE` Microcycle per Mesocycle; `schedule_state` is irrelevant to this constraint since it's orthogonal to lifecycle.

Return shape unchanged from revision 2 — structured, not a single enum:
```
ReconcileResult
  transitions: List[Transition]
  final_microcycle_id
  final_mesocycle_id
  blocked_reason: Optional[str]
    -- "AWAITING_NEXT_MESOCYCLE" | "INCOMPLETE_MICROCYCLE"
    -- | "WAITING_FOR_MICROCYCLE_START"   (new, revision 6, fix #1 -- see §4a/§5)
    -- | "PROGRAM_DRIFT"                  (new, revision 6, fix #2 -- see §5c)
```

### 3a. What "blocked" actually means for callers (fix #3, blocker)

Revision 2 persisted `blocked_reason` but never said what happens next. Explicit now:

```
if reconcile_result.blocked_reason is not None:
    # session generation:
    do NOT call resolve_envelope() against a stale/absent microcycle
    do NOT generate a normally-planned Session
    do NOT fall back to legacy PhasePolicy-driven generation silently
    return a typed BlockedPlanError (or equivalent) explaining which
      blocked_reason applies -- the caller (API layer) surfaces this,
      it does not swallow it into a degraded-but-successful response

    # GET /training/plan/current:
    still returns 200 -- reports the blocked state explicitly
      (blocked_reason, last valid microcycle/mesocycle) rather than
      erroring or silently showing stale "current" state
```

**`WAITING_FOR_MICROCYCLE_START` (fix #1, blocker, revision 6) is a `blocked_reason` like the others, not a special case** — it falls through the same branch above: no `resolve_envelope()` call, no normally-planned `Session`, a typed error from generation, a 200 with the blocked state disclosed from `GET /training/plan/current`. Two things make it worth calling out explicitly rather than leaving it implicit in "some blocked state":
- **Why it exists:** §4a's no-early-start fix stops the reconciler from activating next week's Microcycle before its `planned_start_date`, but that leaves a real gap — from the moment the previous Microcycle completes until the next one's planned start, there is no `ACTIVE` Microcycle at all. Without a named reason, that gap would either silently fall through as "no blocked_reason, but also no active microcycle" (an inconsistent state) or get lumped into an existing reason that doesn't actually describe it.
- **What callers see:**
```
GET /training/plan/current during this window:
  current_active_microcycle: null
  next_microcycle: <the materialized NOT_STARTED one>
  starts_on: <its planned_start_date>
  blocked_reason: "WAITING_FOR_MICROCYCLE_START"

session generation during this window: blocked (typed error, same as any
  other blocked_reason) -- resolve_envelope() is never called against the
  future NOT_STARTED Microcycle, because RecoveryStatus/DeloadState can
  change between now and its actual start; presenting today's envelope as
  that Microcycle's prescription would be wrong the moment either changes
```
See §4a and §5 for exactly when this state is entered and cleared.

**`PROGRAM_DRIFT` (fix #2, blocker, revision 6)** is likewise a normal `blocked_reason` — see §5c for what triggers it (now checked at Session generation, not just materialization) and `scripts/acknowledge_program_drift.py` for how it clears.

**No escape hatch in this design pass (fix #3, blocker).** Revision 3's `allow_unplanned=True` fell back to legacy `PhasePolicy`-driven resolution when the plan was blocked — that reopens exactly the back door the clean cutover closed: legacy `Phase` stopped being authoritative, and a blocked-plan escape hatch would make it authoritative again, silently, whenever advancement gets stuck. This design does not resolve that tension by restoring legacy semantics under a different name.

A blocked plan (`blocked_reason` set) simply **cannot generate a normally-planned `Session`** — the caller gets the typed `BlockedPlanError` above, full stop. There is no `allow_unplanned` parameter in this pass. If the athlete needs to train through a blocked-plan gap, that requires a real `UNPLANNED_WORKOUT` policy path that doesn't require a Mesocycle posture but also doesn't resurrect `PhasePolicy` — and that path does not exist yet on either side of this design (it wasn't built by the original periodization work either, since a blocked-plan gap is new to this design). Building it is explicitly deferred; see Non-goals.

## 4. Microcycle lifecycle

```
lifecycle_status:  NOT_STARTED | ACTIVE | COMPLETE | INCOMPLETE
schedule_state:    ON_TIME | EXTENDED | DRIFT_FLAGGED       (independent axis)
```

**Drift bands, corrected (fix #2, blocker — revision 2 defined `EXTENDED` but its transition rules never produced it, an unreachable state):**

```
drift_days = max(0, local_today - planned_end_date)   -- explicit formula, fix #2

drift_days == 0  (on or before planned_end_date):  schedule_state = ON_TIME
drift_days 1-2:                                     schedule_state = EXTENDED
drift_days 3-4:                                     schedule_state = DRIFT_FLAGGED
drift_days > 4:   remaining PENDING TRAINING slots -> SKIPPED / INFERRED_BOUNDARY,
                   then re-check completion (below). Ordinary missed-workout drift
                   does NOT produce INCOMPLETE -- one forgotten session must never
                   freeze the whole periodization system.
```

`local_today` uses the timezone from §8, not server UTC.

**Transitions:**
- **`NOT_STARTED → ACTIVE`**: see §4a — materialization and activation are now split, not a single step.
- **`ACTIVE → COMPLETE`**: whenever every `TRAINING` slot's `resolution != PENDING`, checked after every relevant slot resolution and after the `>4 days` inferred-skip pass. Reason logged: `ALL_SESSIONS_RESOLVED` or `DRIFT_INFERRED_SKIP`. Sets `actual_completion_date` (the field already on the `Microcycle` model from spec 01 — see §7 for why this design does not add a second, redundant timestamp field alongside it).
- **`ACTIVE → INCOMPLETE`**: only on an explicit, operator-declared interruption/abandonment/replan action — never automatically from drift. Out of scope for a write endpoint in this pass (direct operator action only, same precedent as the cutover script). **Terminal and fully blocking**: does not trigger next-Microcycle advancement or Mesocycle rollover; the reconciler's fixed-point loop stops here.

`planned_posture` is never touched by any transition.

### 4a. Materialization vs. activation — no early start, and naming the gap (fix #2 rev-5 / fix #1 rev-6, both blockers)

Revision 4's fixed-point loop would activate the next Microcycle the instant the current one completes, even if that happens days before the new week's `planned_start_date` (e.g. the athlete finishes a Thursday–Wednesday week's D6 on Tuesday) — silently offering next week's D1 two days early, which contradicts this design's calendar-anchored-but-tolerant premise. Materialization (creating the `Microcycle` row + its `MicrocycleSlot`s, per §4's hash-check precondition) and activation (`NOT_STARTED → ACTIVE`, opening it for session generation) are split into two steps:
```
previous Microcycle -> COMPLETE
next Microcycle materializes immediately as NOT_STARTED
  (this is when the program_prescription_hash re-check runs, per §5c)

if local_today >= next.planned_start_date:
    activate it now (NOT_STARTED -> ACTIVE), continue the fixed-point loop
else:
    leave it NOT_STARTED
    this reconciler run's blocked_reason = "WAITING_FOR_MICROCYCLE_START"
      (fix #1, revision 6 -- see §3a for what this means to callers)
    the reconciler activates it on a later run, the first time it's
      invoked on or after planned_start_date
```
Materializing early (but not activating early) is deliberate, not a half-measure: it's what lets the hash check and slot snapshot happen deterministically at completion time rather than being deferred to whatever moment activation happens to occur, while still guaranteeing no session generates against next week's plan before its planned start. **Revision 5 left the resulting gap — no `ACTIVE` Microcycle between completion and the next `planned_start_date` — unrepresented in `blocked_reason`; revision 6 names it.** The same materialize-now/activate-if-due split, and the same `WAITING_FOR_MICROCYCLE_START` reason, applies at Mesocycle rollover (§5) — see that section for why the *Mesocycle* itself also stays `PLANNED`, not just its first Microcycle staying `NOT_STARTED`. A later, explicit `EARLY_START_ALLOWED` policy (opt-in, athlete-facing) is a plausible future addition but is not built in this pass.

## 5. Mesocycle lifecycle + rollover

```
PLANNED → ACTIVE → COMPLETE
```

**Rollover** (evaluated inside the fixed-point loop, when the current Mesocycle's final Microcycle reaches `COMPLETE`, not `INCOMPLETE`):
1. Close the current Mesocycle (`ACTIVE → COMPLETE`, sets `actual_end_date` — the field already on `Mesocycle`).
2. Query the Macrocycle for the next ordered `Mesocycle` with `status=PLANNED`.
3. **If found**: validate template cardinality (§5d), then re-verify `program_prescription_hash` (§5c, fix #2 rev-5 — required before this or any materialization). Materialize its first `Microcycle` as `NOT_STARTED` (`planned_posture = MesocycleTemplate.postures[microcycle.ordinal - 1]`, 0-indexed off a 1-based ordinal — a dedicated test asserts all four index mappings for a 4-week template). **The Mesocycle itself stays `PLANNED` at this point (fix #1, revision 6) — it does not activate just because its predecessor closed.** Then, in the same step:
   ```
   if local_today >= microcycle.planned_start_date:
       Mesocycle: PLANNED -> ACTIVE, actual_start_date = today
       Microcycle: NOT_STARTED -> ACTIVE
       Macrocycle.planning_state = ACTIVE
       log MESOCYCLE_ADVANCED
   else:
       Mesocycle stays PLANNED, Microcycle stays NOT_STARTED
       this reconciler run's blocked_reason = "WAITING_FOR_MICROCYCLE_START"
       Macrocycle.planning_state is NOT touched here -- it only moves to
         ACTIVE when the Mesocycle actually activates, above
   ```
   Revision 5 activated the Mesocycle unconditionally at this point, which meant `Mesocycle.status=ACTIVE` and `actual_start_date` could both claim the block had started when no training in it was actually allowed to happen yet — the same problem §4a already fixed for Microcycles, just one level up. **The entire step (close old Mesocycle, materialize the new Mesocycle's first Microcycle, activate both if due) is one transaction** (fix #6, revision 4) — a validation/index failure partway through must roll back everything, never leaving the old Mesocycle `COMPLETE` with the new one half-materialized.
4. **If not found**: `Macrocycle.planning_state = AWAITING_NEXT_MESOCYCLE` if not already; log `PLAN_EXHAUSTED` only on the transition into that state. Loop stops (`blocked_reason="AWAITING_NEXT_MESOCYCLE"`).

**Resume branch (fix #2, blocker).** The rollover algorithm above is triggered by "the current Mesocycle's final Microcycle reaches `COMPLETE`" — but that event has already happened by the time `Macrocycle.planning_state == AWAITING_NEXT_MESOCYCLE`. `plan_next_mesocycle.py` running later creates the successor `Mesocycle` row successfully, but nothing in the fixed-point loop as written above would ever notice it: there is no `ACTIVE` Mesocycle left to complete, so the rollover trigger never fires again, and the system stays stuck in `AWAITING_NEXT_MESOCYCLE` forever even though a plan now exists. The fixed-point loop (§3, step 3) therefore gets a second, independent entry condition, checked every iteration alongside "final Microcycle just completed":
```
if Macrocycle.planning_state == AWAITING_NEXT_MESOCYCLE:
    previous = latest Mesocycle with status=COMPLETE (ordinal-max, this Macrocycle)
    successor = Mesocycle with macrocycle_id=this, ordinal=previous.ordinal + 1, status=PLANNED

    if successor exists:
        # identical to step 3's body: validate cardinality, re-verify hash,
        # materialize Microcycle 1 as NOT_STARTED, Mesocycle stays PLANNED,
        # activate both together only if local_today >= planned_start_date
        # (otherwise blocked_reason = WAITING_FOR_MICROCYCLE_START instead),
        # all in one transaction
        if local_today >= microcycle.planned_start_date:
            activate successor; Macrocycle.planning_state = ACTIVE
            log MESOCYCLE_ADVANCED
            continue the fixed-point loop (a freshly activated Mesocycle/
              Microcycle may itself already be due for further transitions,
              e.g. if planning happened well after the gap opened)
        else:
            leave successor PLANNED, its Microcycle 1 NOT_STARTED
            blocked_reason = "WAITING_FOR_MICROCYCLE_START"
            (this is progress, not a stall: planning_state was
              AWAITING_NEXT_MESOCYCLE, a plan now exists, it's just not
              due to start yet -- distinct from "no plan exists at all")
    else:
        # unchanged -- still AWAITING_NEXT_MESOCYCLE, still blocked
        stop
```
This is the same materialize/activate logic as step 3 above, reached from a second trigger — not a new path to keep in sync separately.

**Within an active Mesocycle, Microcycle-to-Microcycle advancement**: next Microcycle materializes with dates computed from the Mesocycle's own schedule, never slid from when the previous one actually finished — unchanged from revision 2. **Materialization and activation timing now follow §4a** (no early activation; the hash re-check runs at materialization, per §5c).

### 5a. Session generation transaction race (fix #4, tighten)

A gap exists between "the reconciler determined slot X is the target" and "the Session insert actually happens" — another concurrent request could advance state in between. Session generation must not trust a reconciled `microcycle_id`/slot identity from a moment ago; it re-verifies inside its own insert transaction:

```
verify owning Mesocycle's program_prescription_hash still matches
  the live Program (fix #2, blocker, revision 6 -- see §5c for why
  materialization-time checking alone isn't enough)
    mismatch -> block with blocked_reason="PROGRAM_DRIFT", same
      typed-error shape as any other blocked generation (§3a);
      do NOT generate, do NOT fall through to any other path

lock target MicrocycleSlot
verify:
    owning Microcycle still lifecycle_status=ACTIVE
    slot.slot_type == TRAINING
    slot.resolution == PENDING
    slot.session_id IS NULL
create Session
bind slot.session_id = session.id  (resolution stays PENDING per §2a)
commit
```

**If verification fails, do not fall through to `plan_status=UNPLANNED` (fix #3, blocker).** Revision 4 treated a lost race as grounds to silently generate an unplanned session — but a concurrency failure means *the requested planned workout is no longer valid*, not *the athlete intended an unplanned one*. Manufacturing `UNPLANNED` here would also be inconsistent with §3a's fix #3, which already refuses to build a generic unplanned-workout fallback — this would have been a second, unintentional way to reach the same place. Instead, branch on why verification failed:
```
slot.session_id already set (someone else's generation won the race,
  or this is a retried request for a session that now exists):
    return the existing Session (idempotent success, not an error)

owning Microcycle no longer ACTIVE, or slot.resolution != PENDING
  (advancement moved the plan out from under this request):
    return a typed Conflict -- the caller re-fetches
    GET /training/plan/current and retries against the now-current plan
```
Neither branch creates an `UNPLANNED` session — consistent with §2a and §1, nothing in this design pass ever produces `plan_status=UNPLANNED` (revision 6, fix #3): a bound slot always returns its existing `Session`, whether the caller lost a race (this section) or is a plain retried/duplicate generation request (§2a). `UNPLANNED` stays reserved in the schema for a distinct future explicit operation, not for either of these cases.

### 5b. Plan-exhaustion state

Unchanged from revision 2: `Macrocycle.planning_state: ACTIVE | AWAITING_NEXT_MESOCYCLE | COMPLETE`. Lifecycle/planning metadata, not engine-prescription behavior.

### 5c. `Mesocycle.program_id` + corruption detection (fix #4 from rev-2-review, elevated twice since)

`Mesocycle.program_id` (FK, required) — set by `plan_next_mesocycle.py` at planning time, read by rollover's slot materialization instead of "whatever `Program` is active." Unchanged from revision 2.

**`Mesocycle.program_prescription_hash`** (renamed from `program_structure_hash`, revision 6, fix #2 — see below for why) — a hash computed at planning time over every part of the bound `Program` that can materially alter generated training: `ProgramDay`/`Tier`/`TierExercise` identity and order, **plus exercise configuration, sets, rep range, RPE target/cap, rest, progression scheme, and equipment/configuration** (the broadened scope; revision 5 only hashed identity/order, which would have missed e.g. a Belle Mere grip-width change on an otherwise-unchanged `TierExercise` row). **Canonicalized before hashing (fix #7, revision 4):** the collections are sorted deterministically (e.g. by `ProgramDay.day_code`, then `Tier.ordinal`, then `TierExercise.ordinal`) before serializing, so a harmless row-order difference can never trigger a false-positive drift. Checked by comparing current vs. planned:
```
current hash == planned hash  → proceed
current hash != planned hash  → fail loudly, require explicit operator
                                  acknowledgment before proceeding (see
                                  the recovery mechanism below)
```
This does not achieve `Program` immutability (still an explicit non-goal — see below), but it turns a silent, dangerous failure mode into a loud one: without it, planning "Mesocycle 3 = 28\" Belle Mere + T-bar emphasis" and then editing that `Program` in place four weeks later would let rollover — or, per the fix below, session generation — silently produce training the athlete never actually planned. The hash is cheap now and becomes redundant-but-harmless audit metadata once real `Program` versioning exists.

**Why the rename:** `program_structure_hash` undersold what it protects — "structure" suggests day/tier/exercise shape, not the actual prescription values inside that shape. `program_prescription_hash` names what's actually being guarded against drifting: the prescription the athlete planned this Mesocycle around.

**Checked at every materialization (fix #1, revision 5) *and* at Session generation (fix #2, blocker, revision 6).** Revision 4 only checked the hash when a *Mesocycle* activated; revision 5 broadened that to every `MicrocycleSlot`-materializing operation, since `MicrocycleSlot`s materialize one `Microcycle` at a time and a `Program` edit mid-Mesocycle would otherwise be picked up unchecked at the next week's materialization. **That still wasn't the full picture: `MicrocycleSlot` only snapshots day/slot identity, not the actual prescription detail — Session generation reads sets/reps/RPE/etc. straight from the live `Program` at generation time, which can be days after materialization.** A `Program` edited between a Microcycle's materialization and a specific day's Session generation was still invisible. The invariant is broadened one more time: **any operation that consumes the mutable `Program` to produce planned training first re-verifies the owning Mesocycle's `program_prescription_hash`.** That now covers three kinds of call sites:
- Mesocycle activation + Microcycle 1 materialization (§5, rollover step 3, and the resume branch)
- every subsequent Microcycle-to-Microcycle materialization within an active Mesocycle (§4/§5, "Within an active Mesocycle...")
- the Microcycle #1 bootstrap (§2b) — checked once, against whatever `Program` state existed at the 2026-09-04 cutover, satisfied trivially since nothing could have drifted yet
- **planned Session generation itself (§5a, new this revision)** — the gap this fix closes
A mismatch at any of these points blocks: materialization/rollover sites fail loudly with `blocked_reason` set as before; Session generation returns `blocked_reason="PROGRAM_DRIFT"` (§3a) instead of generating against a drifted `Program`. All are resolved the same way, via `scripts/acknowledge_program_drift.py`. This is one check reused at every call site, not a separate mechanism per site.

**Recovery mechanism (fix #7, revision 4 blocker-adjacent — revision 3 defined the detection but not the way out).** A hash mismatch blocks with no defined resolution otherwise leaves the system correctly suspicious but permanently stuck. A small administrative script, not a UI:
```
scripts/acknowledge_program_drift.py --mesocycle <id> [--accept-current-program-revision]
```
which:
1. shows the planned hash and the current live hash side by side (and, best-effort, a summary of what changed if cheaply derivable — not required);
2. requires the explicit `--accept-current-program-revision` flag to actually act (a bare status check by default, never a silent accept);
3. on acceptance, updates `Mesocycle.program_prescription_hash` to the current live value;
4. writes an `AdvancementLog` row (`PROGRAM_DRIFT_ACKNOWLEDGED`) recording old hash, new hash, and operator action — this is a deliberate, audited override, not a code path anyone should be able to trigger accidentally.
Whatever operation was blocked (rollover, materialization, or a Session-generation request) re-attempts after acknowledgment and proceeds normally against the now-matching hash.

**Caveat, still explicitly not solved by this design:** `Program` remains a single mutable row (confirmed against the live model). `program_id` + `program_prescription_hash` together record and *detect drift from* intent; they do not *prevent* the underlying row from being edited. True `Program` versioning/immutability is a real, separate architectural change and stays a non-goal here.

### 5d. Template cardinality validation (fix #6, tightened by fix #8)

`plan_next_mesocycle.py` validates `len(template.postures) == microcycle_count` **exactly** (fix #8 — revision 3 left exact-vs-at-least to the implementer; this revision decides it: a template with six postures applied to a four-week Mesocycle is ambiguous configuration even though indexing would technically still work, so it fails validation rather than silently ignoring the extra two entries) at planning time. **Re-validated defensively at rollover/materialization time too** (§5, step 3), inside the same all-or-nothing transaction — an index error during rollover must never leave the old Mesocycle `COMPLETE` and the new one partially activated.

## 6. Deload: explicitly out of scope, orchestration seam only

Unchanged from revision 1/2. Advancement never triggers, resolves, or clears `DeloadState`, and never lets it rewrite `planned_posture`. Reconciler step 2 stays a defined no-op seam.

## 7. Audit trail: `AdvancementLog`

Unchanged from revision 2: `reconcile_run_id` groups every row one fixed-point loop produces; `entity_type` includes `"macrocycle"` for plan-exhaustion events; `details_json` carries free-form context (`skipped_day_codes`, `drift_days`, etc).

**Timestamp field cleanup (fix #7a):** revision 2 introduced a new `completed_at` field "alongside the existing `actual_completion_date`/`actual_end_date`" — two independently-writable representations of the same event is exactly the kind of thing that drifts apart in practice. **This revision drops the new field entirely** and uses only the timestamps already on the `Microcycle`/`Mesocycle` models from spec 01 (`actual_start_date`, `actual_completion_date`, `actual_end_date` — all `date`, not `datetime`). If sub-day precision is ever needed, add it deliberately later; don't carry two clocks for the same event now.

## 8. Timezone

**One authoritative source, not left to the implementer (fix #9).** A single config value, `TRAINING_TIMEZONE`, and a single helper (e.g. `local_today()`) that every drift/date comparison in this design calls — never a scattered `date.today()`. No athlete-profile timezone subsystem is being built for this; the app already operates a single training calendar in one local timezone, so a fixed config value is sufficient. Athlete-profile timezone support, if ever needed, is a separate future change that would update the one helper's source, not something this design needs to anticipate.

## 9. Regression tests

- `_compute_recovery_status` window-formula match (already fixed in `6af5440`; re-assert if any new code re-derives a similar window).
- A Microcycle materialized when its `planned_start_date` is already on or before `local_today` is activated in the same operation, not left `NOT_STARTED` (revised this pass — see the early-start test below for the complementary not-yet-due case).
- A Microcycle with zero `MicrocycleSlot` rows can never reach `COMPLETE`.
- Posture indexing for all four ordinals of a 4-week template.
- **New (from this revision):** generating a `Session` alone (status still `PLANNED`) must leave its bound slot at `resolution=PENDING`, not `COMPLETED` — the core regression guard for this revision's headline fix. A companion test: that same slot resolves to `COMPLETED` only after `Session.status` actually transitions to `COMPLETED`.
- **Revised (revision 6, fix #3):** a second generation request for a `day_code` whose slot is already bound (`session_id != NULL`) returns the existing `Session` unchanged — the old version of this test (asserting `plan_status=UNPLANNED`) is removed, since that behavior directly contradicted §5a's idempotency rule and no longer exists.
- **New:** `program_prescription_hash` mismatch at rollover activation blocks and requires acknowledgment rather than silently materializing against the changed `Program`.
- **New (this revision):** a Microcycle stuck at `Macrocycle.planning_state == AWAITING_NEXT_MESOCYCLE` resumes correctly once `plan_next_mesocycle.py` creates a successor — the resume-branch regression guard for fix #2.
- **New:** a slot already resolved `SKIPPED` is left unchanged when its bound `Session` later completes; the `Session` itself still transitions to `COMPLETED` normally. A companion test: a slot still `PENDING` resolves `COMPLETED` normally (the non-regressed case).
- **New:** an `IN_PROGRESS` `Session` is never touched by the drift-expiry pass, regardless of how stale its owning Microcycle is.
- **New:** two `Program` structures that differ only in row/JSON-key order hash identically (canonicalization regression guard for fix #7).
- **New:** planning a Mesocycle whose template posture count doesn't exactly match its microcycle count fails validation at planning time, not at rollover.
- **New (this revision, fix #1):** a `Program` edited in place during an active Mesocycle's week 1 is caught at week 2's materialization, not silently picked up — the multi-materialization hash-check regression guard.
- **New (fix #2):** a Microcycle whose previous Microcycle completed early (before `planned_start_date`) materializes as `NOT_STARTED` and is not activated until a reconciler run occurs on or after `planned_start_date`; a companion test confirms it *does* activate immediately when the completing run is already on/after that date.
- **New (fix #3):** a session-generation request that loses the slot-verification race against a concurrent advancement returns a typed `Conflict`, not an `UNPLANNED` session; a companion test confirms a retried request against an already-created `Session` returns that same `Session` idempotently rather than erroring or duplicating.
- **New (revision 6, fix #1):** a Microcycle that completes before its successor's `planned_start_date` leaves `blocked_reason="WAITING_FOR_MICROCYCLE_START"` and no `ACTIVE` Microcycle until a reconciler run occurs on/after that date; a companion test asserts the same at a Mesocycle boundary — the successor Mesocycle stays `PLANNED` (not `ACTIVE`) and its first Microcycle stays `NOT_STARTED` until due, and `actual_start_date` is not set early on either.
- **New (revision 6, fix #1):** `GET /training/plan/current` during a `WAITING_FOR_MICROCYCLE_START` window reports `current_active_microcycle=null`, the correct `next_microcycle`/`starts_on`, and the blocked reason — never a stale prior-Microcycle envelope presented as current.
- **New (revision 6, fix #2):** planned Session generation is blocked with `blocked_reason="PROGRAM_DRIFT"` when the `Program` was edited after the target Microcycle's materialization but before generation — the gap materialization-only checking couldn't catch.
- **New (revision 6, fix #2):** a `Program` edit that changes only prescription detail on an existing `TierExercise` (e.g. a grip-width or rep-range change, no day/tier/exercise added or removed) still changes `program_prescription_hash` — the broadened-scope regression guard, since revision 5's identity/order-only hash would have missed this.

## Non-goals (this design pass)

- DeloadState trigger/evidence logic (§6).
- A scheduled/background job.
- An explicit user-facing "skip this session" endpoint/client UI change — the `Session.status=SKIPPED` → slot resolution path is defined (§2a) but nothing produces it yet.
- A full Mesocycle-authoring write API/UI.
- Auto-replanning an `INCOMPLETE` Microcycle.
- True `Program` immutability/versioning (§5c) — `program_id` + `program_prescription_hash` detect drift, they don't prevent it.
- Movement constraint-type classification / exercise-rotation strategy.
- **A blocked-plan escape hatch** (revision 3's `allow_unplanned`) — removed in this revision (§3a, fix #3); a real `UNPLANNED_WORKOUT` policy path that doesn't require Mesocycle posture and doesn't resurrect legacy `PhasePolicy` is a separate future design.
- A stale-`IN_PROGRESS`-session cleanup policy (§2a notes the drift-expiry pass never touches one, but doesn't define what, if anything, eventually should; a future `STALE_IN_PROGRESS` explicit-recovery flow is the likely shape, not automatic database surgery).
- **An explicit, athlete-facing `EARLY_START_ALLOWED` policy** (§4a) — this pass only guarantees no *implicit* early start; an opt-in "let me start next week early" feature is a separate future design.

## Open questions carried forward

- Exact drift-tolerance day-counts (now 0/1–2/3–4/>4) are still reused placeholders pending real-data tuning.
- Whether `INCOMPLETE`'s explicit-abandonment path needs a real API before it's usable day-to-day is deferred.
- Whether `UNPLANNED_WORKOUT` (the deferred blocked-plan path, see Non-goals) is ever actually needed in practice, or whether a blocked plan should just stay blocked until resolved.
- Whether a genuinely stale `IN_PROGRESS` `Session` (abandoned mid-workout weeks ago) ever needs its own cleanup policy.
