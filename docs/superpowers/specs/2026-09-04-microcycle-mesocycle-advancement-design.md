# Microcycle/Mesocycle Advancement — Design

**Revision 8** — incorporates 6 corrections from a seventh review pass on revision 7, 2 flagged as blockers, after which the reviewer considers the architecture implementation-ready. (1) The fixed-point reconciler never actually specified the branch that *notices* an instantiated-but-not-yet-active Microcycle and retries its activation — revision 7's prose said "the reconciler retries activation on a later run," but the pseudocode across §4a, §5's rollover step 3, and §5's resume branch each only handled the moment of *creating* that pending state, not the moment of *resuming* it. This mattered acutely at a Mesocycle boundary: after revision 7, `Macrocycle.planning_state` is `ACTIVE` as soon as a successor exists, so the old `AWAITING_NEXT_MESOCYCLE` resume branch's trigger condition no longer fires to pick the pending Microcycle back up. Fixed by introducing a single, generalized **pending-activation branch** (new §3b) that every fixed-point iteration checks first: it finds the one Microcycle sitting `NOT_STARTED` with zero slots (instantiated but not yet activated, from any of the three call sites that can produce that state, or left there by an earlier `PROGRAM_DRIFT` block) and either activates it or re-blocks it. §4a and §5 now describe *instantiation* only; all activation logic lives in §3b, described once. (2) Revision 7 solved drift acknowledgment invalidating slots *before* a Microcycle activates, but not *after*: an acknowledged `Program` edit that changes prescription only (exercises, sets, RPE) is fine to accept against an already-`ACTIVE` Microcycle, but one that changes slot topology (a day flips `TRAINING`↔`REST`, a day is added/removed, day ordering changes) would leave the already-snapshotted `MicrocycleSlot`s describing a `Program` state the newly-accepted hash no longer matches. Fixed with a second, narrower hash — `Microcycle.slot_topology_hash`, covering only what determines the slot snapshot — checked by `acknowledge_program_drift.py` before it will accept a `Program` revision against an `ACTIVE` Microcycle; a topology change is refused (routed to the explicit interruption/replan path, §4) while a prescription-only change is accepted normally. Also folded in, cheap and worth doing now: (3) Microcycle activation (§3b) is stated as one explicit transaction covering hash check, slot snapshot, slot-count validation, and both status transitions. (4) Zero-`TRAINING`-slot Microcycles are now rejected *at activation*, not just prevented from reaching `COMPLETE` — an invalid configuration fails loudly instead of producing a Microcycle that can activate but never finish. (5) The bootstrap (§2b) now hard-fails for operator resolution on any post-cutover `Session` that can't be deterministically mapped to a Microcycle/slot, rather than silently backfilling it as `plan_status=PLANNED`/`microcycle_id=NULL` — a handful of cutover-era edge cases deserve deterministic reconciliation, not a permanent historical exception to what `PLANNED` means. (6) `plan_next_mesocycle.py` itself now sets `Macrocycle.planning_state=ACTIVE` transactionally at the moment it creates a successor Mesocycle, closing the last sliver of "successor exists, but `planning_state` hasn't caught up yet" that could otherwise exist between the script's commit and the reconciler's next run; logged as a distinct `SUCCESSOR_PLANNED` audit event, kept separate from `MESOCYCLE_ADVANCED` (plan continuation becoming available and training actually entering that Mesocycle are different events).

**Revision 7** — incorporates 6 corrections from a sixth review pass on revision 6, 2 flagged as blockers. (1) `Macrocycle.planning_state` and `blocked_reason` could contradict each other: once an operator planned a successor Mesocycle via `plan_next_mesocycle.py`, the plan genuinely had continuation, but revision 6 left `planning_state=AWAITING_NEXT_MESOCYCLE` untouched until the successor actually *activated* — so a state read during the `WAITING_FOR_MICROCYCLE_START` window could show "awaiting a successor" and "the successor is materialized and just waiting to start" at the same time. Fixed by redefining `AWAITING_NEXT_MESOCYCLE` to mean strictly *no successor plan exists* — `planning_state` moves to `ACTIVE` the moment a successor is found/materialized, independent of whether it has actually started training yet. (2) Revision 6's "materialize slots early, activate later" design for the no-early-start fix created a new correctness gap: if a `Program` drift is acknowledged (accepting revision B) *after* a future Microcycle's slots were already snapshotted from revision A, the accepted hash and the actual snapshotted slots could describe two different `Program` revisions. Fixed by splitting what revision 6 called "materialization" into three distinct steps — **instantiate** (create the `Microcycle` row, `NOT_STARTED`, no slots yet), **activate** (hash check, then snapshot `MicrocycleSlot`s from the *now-verified* `Program`, then `NOT_STARTED → ACTIVE`) — so slots are only ever snapshotted from a `Program` state that was hash-verified at that exact moment, never from a state that might later be superseded by a drift acknowledgment. Also folded in, not blockers but cheap and worth doing now: (3) `Session.status → COMPLETED`/`SKIPPED` and the corresponding `MicrocycleSlot.resolution` transition are now specified as one atomic transaction, closing a crash-window where a `Session` could commit as completed while its slot stayed `PENDING` forever. (4) The reconciler's stopping-condition prose (§3) now lists all four `blocked_reason` values instead of the two from revision 4. (5) `Macrocycle.planning_state=COMPLETE` is explicitly documented as operator-declared/future — nothing in this design transitions to it, and that's intentional, not an oversight. (6) Added an explicit statement of the residual concurrency assumption this design still rests on: `Program`-mutating writes are assumed administrative/low-frequency and not yet required to serialize against planned generation; a future `Program.revision` counter is named as the eventual hardening path, not built here.

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

**Snapshotted once, at Microcycle *activation* — not instantiation (revision 7, fix #2, blocker; see §4a)** — from the specific `Program` the owning `Mesocycle` is bound to (§5c), verified via `program_prescription_hash` at that exact moment, never "whatever's active" and never from an earlier, possibly-superseded verification. **Invariant, unchanged from revision 2:** at most one `TRAINING` slot per `(microcycle_id, day_code)`; `(microcycle_id, ordinal)` unique. A Microcycle with zero `TRAINING` slots may never automatically reach `COMPLETE` — see the bootstrap in §2b, which exists specifically because Microcycle #1 is already live in production with zero slots and this exact vacuous-truth bug was caught in review before shipping.

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

**Completion is atomic (fix #3, revision 7).** `Session.status → COMPLETED` (or `→ SKIPPED`) and the corresponding `MicrocycleSlot.resolution` transition happen in **one transaction**, not two sequential writes. Without this, a process crash between committing the `Session` write and committing the slot write would leave a `Session.status=COMPLETED` with its slot still `PENDING` forever — advancement would then believe that training day never happened, even though it did. This applies to every resolution path in this section: the `COMPLETED` case above, the `SKIPPED`/`USER_EXPLICIT` case, and — symmetrically, whenever that endpoint is eventually built — any future explicit-skip endpoint's write.

### 2b. Bootstrap: `MicrocycleSlot` for the already-live Microcycle #1 (production-critical)

Unchanged from revision 2 — still required, still blocking, still must run before the reconciler is ever invoked against live data. The §5c hash re-check (fix #1) applies here too, trivially: since the bootstrap runs once, immediately, against whatever `Program` state exists at the moment it's executed, there is nothing to have drifted from yet — the hash is simply computed and stored on the bound Mesocycle at this point, not compared against an earlier value.
1. Snapshot Microcycle #1's expected `TRAINING`/`REST` slots from the `Program` its (now-bound, §5c) Mesocycle uses.
2. Backfill `Session.microcycle_id`/`plan_status` for sessions already generated since 2026-09-04.
3. For each, resolve its matching `day_code` slot per the binding/resolution split in §2a (i.e., check the real `Session.status`, not just "a Session exists" — a session generated but never completed binds the slot without resolving it, exactly as live behavior should be).
4. **Verify** the resulting slot count matches the expected `TRAINING` count before considering the bootstrap successful — a silent zero-slot result hard-fails the bootstrap.

**Unmappable sessions hard-fail for operator resolution, not a silent fallback (fix #5, revision 8).** Step 3 assumes every post-cutover `Session` maps deterministically to a `day_code` slot. If one doesn't — no resolvable `day_code`, or a `day_code` that doesn't match any expected slot — the bootstrap **must not** paper over it by backfilling `plan_status=PLANNED`/`microcycle_id=NULL` (a silent version of `LEGACY`'s semantics, smuggled onto a session that isn't actually pre-periodization). That would quietly redefine what `PLANNED` means for exactly the handful of rows where precision matters most — the ones spanning the cutover boundary. Instead: any unmappable session halts the bootstrap and reports the specific session(s) for operator resolution (manually assign a `day_code`/`microcycle_id`, or explicitly backfill it `LEGACY` if it genuinely predates periodization accounting). This is expected to be zero or a handful of rows — deterministic reconciliation is cheap here and worth doing once, rather than encoding a permanent exception into what `plan_status` means going forward.

## 3. The reconciler: `reconcile_current_training_state()`

Invoked lazily, no scheduler, at the top of session generation, `GET /training/plan/current`, and any future write path depending on periodization state.

1. Refresh/evaluate RecoveryStatus *(unchanged)*
2. Evaluate DeloadState — no-op placeholder (§6)
3. **Reconcile lifecycle to a fixed point**: each iteration first checks the pending-activation branch (§3b), then applies whatever other Microcycle/Mesocycle transition is due, looping until no further transition is due or a blocking state is reached — **all four `blocked_reason` values below are legitimate stopping conditions (revision 7, fix #4 — this prose previously only named two)**: `INCOMPLETE_MICROCYCLE`, `AWAITING_NEXT_MESOCYCLE`, `WAITING_FOR_MICROCYCLE_START`, `PROGRAM_DRIFT`. Capped iteration bound, logged as an error if hit.
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
  next_microcycle: <the instantiated NOT_STARTED one -- no slots yet,
                     per revision 7's instantiate/activate split, §4a>
  starts_on: <its planned_start_date>
  blocked_reason: "WAITING_FOR_MICROCYCLE_START"

session generation during this window: blocked (typed error, same as any
  other blocked_reason) -- resolve_envelope() is never called against the
  future NOT_STARTED Microcycle, because RecoveryStatus/DeloadState can
  change between now and its actual start; presenting today's envelope as
  that Microcycle's prescription would be wrong the moment either changes
```
See §4a and §5 for exactly when this state is entered and cleared.

**Distinct from `AWAITING_NEXT_MESOCYCLE` (revision 7, fix #1, blocker).** These two must never both describe the same moment. `AWAITING_NEXT_MESOCYCLE` means *no successor Mesocycle plan exists at all* — `Macrocycle.planning_state` reflects this and only this. The instant an operator plans a successor (`plan_next_mesocycle.py`), `planning_state` moves to `ACTIVE`, even if that successor's own Microcycle 1 isn't due to start for weeks — because the long-range plan now has continuation, independent of whether *today's* training is active. `WAITING_FOR_MICROCYCLE_START` is a purely timing-based `blocked_reason` layered on top of an otherwise-healthy plan; it says nothing about whether a plan exists. Revision 6 left `planning_state` untouched while a successor sat unstarted, which meant a state read during that window could show `AWAITING_NEXT_MESOCYCLE` and "a plan already exists" simultaneously — see §5 and §5b for the corrected transition.

**`PROGRAM_DRIFT` (fix #2, blocker, revision 6)** is likewise a normal `blocked_reason` — see §5c for what triggers it (now checked at Session generation, not just materialization) and `scripts/acknowledge_program_drift.py` for how it clears.

**No escape hatch in this design pass (fix #3, blocker).** Revision 3's `allow_unplanned=True` fell back to legacy `PhasePolicy`-driven resolution when the plan was blocked — that reopens exactly the back door the clean cutover closed: legacy `Phase` stopped being authoritative, and a blocked-plan escape hatch would make it authoritative again, silently, whenever advancement gets stuck. This design does not resolve that tension by restoring legacy semantics under a different name.

A blocked plan (`blocked_reason` set) simply **cannot generate a normally-planned `Session`** — the caller gets the typed `BlockedPlanError` above, full stop. There is no `allow_unplanned` parameter in this pass. If the athlete needs to train through a blocked-plan gap, that requires a real `UNPLANNED_WORKOUT` policy path that doesn't require a Mesocycle posture but also doesn't resurrect `PhasePolicy` — and that path does not exist yet on either side of this design (it wasn't built by the original periodization work either, since a blocked-plan gap is new to this design). Building it is explicitly deferred; see Non-goals.

### 3b. The pending-activation branch (fix #1, blocker, revision 8)

**This is the single place Microcycle activation actually happens.** §4a and §5 below describe *instantiation* (creating a `Microcycle` row, `NOT_STARTED`, no slots) at two call sites — normal week-to-week advancement, and a Mesocycle boundary (whether discovered promptly by rollover or later by `plan_next_mesocycle.py`, revision 8 — see §5) — but neither site, on its own, ever said what checks a *later* reconciler run to see whether an already-instantiated Microcycle is now due. Revision 7's prose ("the reconciler retries activation on a later run") described the intent without the mechanism. Revision 6 and 7's per-site `if local_today >= planned_start_date: activate else: block` pseudocode was that mechanism, but only at the exact moment of instantiation — it had no way to fire again on a *subsequent* run. This became a real gap once revision 7 made `Macrocycle.planning_state` go `ACTIVE` as soon as a successor exists: revisions 4–7's dedicated "resume branch" trigger (`planning_state == AWAITING_NEXT_MESOCYCLE`) stopped firing at exactly the moment a pending Microcycle needed to keep being checked — which is why revision 8 retires that trigger entirely (see §5) in favor of this generalized branch.

The fix: **one generalized branch, checked at the start of every fixed-point iteration (§3, step 3), before any other transition check:**
```
pending = the Microcycle (at most one can exist at a time, per the
  extended uniqueness invariant below) with:
    lifecycle_status == NOT_STARTED
    zero MicrocycleSlot rows

if pending exists:
    if local_today < pending.planned_start_date:
        blocked_reason = "WAITING_FOR_MICROCYCLE_START"
        stop this iteration (retried on the next reconciler invocation)
    else:
        verify pending's owning Mesocycle.program_prescription_hash
          against the LIVE Program, right now (§5c)
        if mismatch:
            blocked_reason = "PROGRAM_DRIFT"
            stop this iteration (pending stays NOT_STARTED, no slots;
              retried automatically the next time this branch runs,
              which happens as soon as an operator acknowledges the
              drift via scripts/acknowledge_program_drift.py)
        else:
            BEGIN TRANSACTION
                snapshot MicrocycleSlots from the now-verified Program
                validate: at least one TRAINING slot resulted
                  (fix #4, revision 8 -- see below; a zero-TRAINING-slot
                  snapshot fails activation outright, it does not produce
                  an ACTIVE Microcycle that can never reach COMPLETE)
                if owning Mesocycle.status == PLANNED:
                    Mesocycle: PLANNED -> ACTIVE, actual_start_date = today
                    log MESOCYCLE_ADVANCED
                Microcycle: NOT_STARTED -> ACTIVE, actual_start_date = today
            COMMIT
            continue the fixed-point loop (this Microcycle may itself
              already be due for further transitions, e.g. if it was
              stuck blocked for a long time before being resumed)
else:
    no pending Microcycle -- proceed to whichever other transition this
      iteration would otherwise check (completion, drift, rollover
      discovery, etc.)
```
This one branch is what makes activation actually resumable, and it is now the *only* place activation happens — normal advancement (§4a) and a Mesocycle-boundary successor (§5, instantiated either by rollover discovery or by `plan_next_mesocycle.py` directly) both just instantiate and then rely on this branch to pick the pending Microcycle up, whether that's on the same iteration (already due) or a later one (retried after `WAITING_FOR_MICROCYCLE_START` or an acknowledged `PROGRAM_DRIFT`). **Extended uniqueness invariant (revision 8):** at most one Microcycle across the whole Macrocycle may be `NOT_STARTED` with zero slots at a time — instantiation never runs again while a pending one already exists, which is guaranteed structurally since nothing instantiates a successor before its predecessor is `ACTIVE`.

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

### 4a. Instantiation vs. activation — no early start, no early slot snapshot (fix #2 rev-5 / fix #1 rev-6 / fix #2 rev-7, all blockers)

Revision 4's fixed-point loop would activate the next Microcycle the instant the current one completes, even if that happens days before the new week's `planned_start_date` (e.g. the athlete finishes a Thursday–Wednesday week's D6 on Tuesday) — silently offering next week's D1 two days early, which contradicts this design's calendar-anchored-but-tolerant premise. Revision 6 fixed that by splitting "materialization" (creating the `Microcycle` row + snapshotting its `MicrocycleSlot`s early, immediately on completion) from "activation" (`NOT_STARTED → ACTIVE` later, only once due) — but that introduced a subtler bug: slots snapshotted early, from a `Program` state hash-verified *at completion time*, could go stale if a `PROGRAM_DRIFT` block on some other operation got acknowledged later, accepting a *different* `Program` revision than the one the slots actually reflect. The hash would then say "this Mesocycle accepts revision B" while the already-snapshotted slots still describe revision A.

**Revision 7 fixed that by moving the slot snapshot to activation-time; revision 8 completes the picture by naming where activation actually lives.** This section now only describes **instantiation**:
```
previous Microcycle -> COMPLETE

INSTANTIATE (happens immediately, at completion):
next Microcycle row created, lifecycle_status = NOT_STARTED
NO MicrocycleSlot rows yet, and no hash check yet -- there is nothing
  to protect until slots are about to be produced
```
**Activation — the hash check, the slot snapshot, and the `NOT_STARTED → ACTIVE` transition — is entirely §3b's job now, not this section's.** Revision 7 described activation inline here (an `if local_today >= planned_start_date: ... else: ...` block); revision 8 moves that logic to a single shared branch (§3b) because the same logic needs to run repeatedly on later reconciler invocations, not just once at instantiation time — which revision 7's per-site inline version had no mechanism to do (see §3b's own rationale). Instantiating early (but never snapshotting or activating early) is still deliberate: it's what lets `WAITING_FOR_MICROCYCLE_START` have a concrete row (`starts_on`, etc.) to report from `GET /training/plan/current`, without committing to that row's actual training content before it's genuinely about to be used. The same instantiate-here/activate-in-§3b split applies at Mesocycle rollover (§5) — see that section for why the *Mesocycle* itself also stays `PLANNED`, not just its first Microcycle staying `NOT_STARTED`. A later, explicit `EARLY_START_ALLOWED` policy (opt-in, athlete-facing) is a plausible future addition but is not built in this pass.

**Zero-`TRAINING`-slot Microcycles are rejected at activation, not just prevented from completing (fix #4, revision 8).** Earlier revisions guaranteed a Microcycle with zero `TRAINING` slots can never reach `COMPLETE` (§2, §2b) — correct, but incomplete: that rule alone would let such a Microcycle activate and then sit `ACTIVE` forever, unable to finish, which is just as broken as the vacuous-completion bug it replaced. §3b's activation transaction now validates the slot snapshot before committing: if it produces zero `TRAINING` slots, activation fails outright (this is invalid `Program`/template configuration, not a runtime state to tolerate) rather than producing an unfinishable `ACTIVE` Microcycle.

## 5. Mesocycle lifecycle + rollover

```
PLANNED → ACTIVE → COMPLETE
```

**Rollover** (evaluated inside the fixed-point loop, when the current Mesocycle's final Microcycle reaches `COMPLETE`, not `INCOMPLETE`):
1. Close the current Mesocycle (`ACTIVE → COMPLETE`, sets `actual_end_date` — the field already on `Mesocycle`).
2. Query the Macrocycle for the next ordered `Mesocycle` with `status=PLANNED`.
3. **If found**: validate template cardinality (§5d). **`Macrocycle.planning_state = ACTIVE` immediately here (fix #1, revision 7) — a successor plan exists, full stop; this does not wait for the successor to actually start training.** Instantiate its first `Microcycle` as `NOT_STARTED`, no slots yet (`planned_posture = MesocycleTemplate.postures[microcycle.ordinal - 1]`, 0-indexed off a 1-based ordinal — a dedicated test asserts all four index mappings for a 4-week template). **The Mesocycle itself stays `PLANNED` at this point — it does not activate just because its predecessor closed; activation (of both the Mesocycle and its Microcycle 1 together) is entirely §3b's job (revision 8), triggered on this same iteration if already due, or on a later one otherwise.** **This step (close old Mesocycle, update `Macrocycle.planning_state`, instantiate the new Mesocycle's first Microcycle) is one transaction** (fix #6, revision 4) — a validation/index failure partway through must roll back everything, never leaving the old Mesocycle `COMPLETE` with the new Mesocycle/Microcycle half-instantiated.
4. **If not found**: `Macrocycle.planning_state = AWAITING_NEXT_MESOCYCLE` if not already; log `PLAN_EXHAUSTED` only on the transition into that state. Loop stops (`blocked_reason="AWAITING_NEXT_MESOCYCLE"`). **This is the only way `planning_state` becomes or stays `AWAITING_NEXT_MESOCYCLE` (fix #1, revision 7) — see §5b.**

**Resume branch retired as a distinct concept — folded into `plan_next_mesocycle.py` itself and §3b (fix #6, revision 8).** Revisions 4–7 handled "an operator plans a successor after the Macrocycle already went `AWAITING_NEXT_MESOCYCLE`" as a special reconciler-side trigger, checked alongside "final Microcycle just completed." That trigger depended on reading `Macrocycle.planning_state == AWAITING_NEXT_MESOCYCLE` — which revision 7 made stop being true the instant a successor exists, since `planning_state` now moves to `ACTIVE` immediately on discovery. A reconciler-side trigger keyed on a state that's true for zero time between "successor created" and "reconciler next runs" doesn't reliably fire. **Revision 8's fix: `plan_next_mesocycle.py` does the discovery-time work itself, transactionally, at creation time — not the reconciler:**
```
plan_next_mesocycle.py, when creating a successor Mesocycle:
BEGIN TRANSACTION
    create the successor Mesocycle row, status=PLANNED
    validate template cardinality (§5d)
    instantiate its first Microcycle as NOT_STARTED, no slots yet
    if Macrocycle.planning_state == AWAITING_NEXT_MESOCYCLE:
        Macrocycle.planning_state = ACTIVE
        log SUCCESSOR_PLANNED   -- distinct from MESOCYCLE_ADVANCED (fix #6):
          plan continuation becoming available and training actually
          entering that Mesocycle are different events, and conflating
          their audit reasons would make the log harder to read later
COMMIT
```
There is now never a database interval where a successor exists but `planning_state` still reads `AWAITING_NEXT_MESOCYCLE` — the transition happens in the same transaction as the row's creation, regardless of which caller creates it (this same block runs whether the successor is planned promptly or long after the gap opened). **§3b's pending-activation branch is what picks this newly-instantiated Microcycle up and actually activates it** — the reconciler no longer needs a special-cased "resume" trigger at all, because §3b already scans for exactly this shape of pending state on every iteration, independent of how or when it was created.

**Within an active Mesocycle, Microcycle-to-Microcycle advancement**: next Microcycle's dates are computed from the Mesocycle's own schedule, never slid from when the previous one actually finished — unchanged from revision 2. **Instantiation timing follows §4a; activation is entirely §3b's job (revision 8).**

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

`Macrocycle.planning_state: ACTIVE | AWAITING_NEXT_MESOCYCLE | COMPLETE`. Lifecycle/planning metadata, not engine-prescription behavior.

**`AWAITING_NEXT_MESOCYCLE` redefined precisely (fix #1, blocker, revision 7): it means strictly "no successor Mesocycle plan exists."** It says nothing about whether *today's* training is active — that's `blocked_reason` (and specifically `WAITING_FOR_MICROCYCLE_START` when a plan exists but hasn't started, per §3a/§4a/§5). Revision 6 left `planning_state` untouched while a materialized-but-unstarted successor sat waiting, which meant `planning_state=AWAITING_NEXT_MESOCYCLE` could be read at the same moment a successor plan genuinely existed — a direct contradiction. Revision 7's rule: **`planning_state` moves to `ACTIVE` the instant a successor Mesocycle is found (rollover step 3) or planned.** Revision 8 tightens *when* "planned" takes effect: `plan_next_mesocycle.py` sets `planning_state=ACTIVE` transactionally in the same commit that creates the successor (§5) — not on a subsequent reconciler run — so there is no longer even a brief database interval where a successor exists but `planning_state` hasn't caught up. Only "if not found" (rollover step 4) sets or preserves `AWAITING_NEXT_MESOCYCLE`.

**`COMPLETE` is operator-declared and future, not reached by anything in this design (fix #5, revision 7).** No transition in this pass ever sets `planning_state=COMPLETE` — reaching the end of every currently-planned Mesocycle produces `AWAITING_NEXT_MESOCYCLE` instead, because an ongoing Macrocycle may always receive another planned Mesocycle; there's no automatic signal that the *Macrocycle's own goal* (not just its current plan) is finished. `COMPLETE` stays in the enum as the eventual target of a future explicit "intentionally end this Macrocycle" operation, which this design does not build (see Non-goals). Documenting this now, rather than leaving `COMPLETE` looking reachable, avoids a future reader assuming some code path sets it that doesn't exist.

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

**Checked at every Microcycle activation (fix #1 rev-5, retimed by fix #2 rev-7) *and* at Session generation (fix #2, revision 6).** Revision 4 only checked the hash when a *Mesocycle* activated; revision 5 broadened that to every slot-producing operation, since Microcycles materialize one at a time and a `Program` edit mid-Mesocycle would otherwise be picked up unchecked at the next week's materialization. Revision 6 found that checking at materialization still wasn't the full picture — Session generation reads prescription detail straight from the live `Program` at generation time, which can be days after materialization — and added a Session-generation-time check. **Revision 7 retimes where the materialization-side check actually runs: not at the old "materialize slots immediately on completion" moment, but at *activation* (§4a) — the same moment `MicrocycleSlot`s are now snapshotted.** This closes a gap revision 6 itself introduced: if slots were snapshotted early (at completion) and a `PROGRAM_DRIFT` block elsewhere got acknowledged later accepting a different `Program` revision, the hash and the already-snapshotted slots could describe two different revisions. Checking and snapshotting at the same instant makes that impossible — there is no longer a window between "hash verified" and "slots produced from that verified state." The invariant, restated: **any operation that consumes the mutable `Program` to produce planned training first re-verifies the owning Mesocycle's `program_prescription_hash`, at the exact moment it consumes it.** That covers three kinds of call sites:
- Microcycle activation (§3b), including Microcycle 1 at a Mesocycle boundary (§5) — hash check and slot snapshot are now the same step, always
- the Microcycle #1 bootstrap (§2b) — checked once, against whatever `Program` state existed at the 2026-09-04 cutover, satisfied trivially since nothing could have drifted yet
- **planned Session generation itself (§5a)** — reads prescription detail that outlives the Microcycle's own activation-time check by days, so it needs its own check at its own moment
A mismatch at any of these points blocks: activation-time mismatches leave the Microcycle `NOT_STARTED` with no slots and set `blocked_reason` (§4a); Session generation returns `blocked_reason="PROGRAM_DRIFT"` (§3a) instead of generating against a drifted `Program`. All are resolved the same way, via `scripts/acknowledge_program_drift.py`. This is one check reused at every call site, not a separate mechanism per site.

**`Microcycle.slot_topology_hash` — a second, narrower hash guarding acknowledgment against an already-`ACTIVE` Microcycle (fix #2, blocker, revision 8).** Revision 7 closed the gap where slots could be snapshotted early from a `Program` state that a later drift acknowledgment might supersede — but that only covers the window *before* activation. A gap remains *after*: a Microcycle activates under `Program` revision A (slots snapshotted, hash stored), the athlete trains D1 under A, `Program` is edited to revision B, `PROGRAM_DRIFT` fires on D4's generation, and an operator accepts B via `acknowledge_program_drift.py`. If B changed only *prescription* (which exercises, what sets/reps/RPE) that's fine — the acceptance is exactly what the mechanism is for. But if B changed *slot topology* (a day flips `TRAINING`↔`REST`, a day is added or removed, day ordering changes), the already-`ACTIVE` Microcycle's `MicrocycleSlot`s still describe A's topology — accepting B's hash would then claim "this Mesocycle accepts B" while its live slots still reflect A's shape, the same kind of mismatch revision 7 fixed pre-activation, now reappearing post-activation.

Computed at the same moment as the slot snapshot (§3b's activation transaction), `slot_topology_hash` covers **only** the fields that determine what slots get produced — ordered `day_code` list, each day's `slot_type` (`TRAINING`/`REST`), and day ordering/ordinal — a strict subset of what `program_prescription_hash` covers. `acknowledge_program_drift.py` checks it before accepting a `Program` revision against a Microcycle that is currently `ACTIVE`:
```
if the target Microcycle is ACTIVE:
    recompute slot_topology_hash from the live Program
    if it differs from the Microcycle's stored slot_topology_hash:
        REFUSE the ordinary acknowledgment -- topology drift under a
          running Microcycle is not a prescription update, it's a
          replan; require the explicit interruption/replan path instead
          (§4's operator-declared ACTIVE -> INCOMPLETE transition, or a
          future dedicated repair workflow -- either way, not silently
          absorbed into this script)
    else:
        ordinary acknowledgment proceeds -- update
          program_prescription_hash as already specified; the Microcycle's
          slots and its slot_topology_hash are untouched, since nothing
          about what was already snapshotted has actually changed
if the target Microcycle is NOT_STARTED (pending, per §3b) or this
  Mesocycle hasn't activated any Microcycle yet:
    no slot_topology_hash exists to check -- ordinary acknowledgment
      proceeds as specified below, and §3b's activation will snapshot
      fresh slots (and a fresh slot_topology_hash) from the now-accepted
      Program the next time it runs
```
This gives the two kinds of drift genuinely different outcomes: a same-skeleton prescription change can be accepted against running training; a different-skeleton change cannot silently mutate a Microcycle whose slots are supposed to be the authoritative record of what that week actually looked like.

**Recovery mechanism (fix #7, revision 4 blocker-adjacent — revision 3 defined the detection but not the way out).** A hash mismatch blocks with no defined resolution otherwise leaves the system correctly suspicious but permanently stuck. A small administrative script, not a UI:
```
scripts/acknowledge_program_drift.py --mesocycle <id> [--accept-current-program-revision]
```
which:
1. shows the planned hash and the current live hash side by side (and, best-effort, a summary of what changed if cheaply derivable — not required);
2. requires the explicit `--accept-current-program-revision` flag to actually act (a bare status check by default, never a silent accept);
3. **checks `slot_topology_hash` first if the Mesocycle has an `ACTIVE` Microcycle (fix #2, revision 8, above)** — refuses outright on a topology mismatch, regardless of the flag;
4. on acceptance, updates `Mesocycle.program_prescription_hash` to the current live value;
5. writes an `AdvancementLog` row (`PROGRAM_DRIFT_ACKNOWLEDGED`) recording old hash, new hash, and operator action — this is a deliberate, audited override, not a code path anyone should be able to trigger accidentally.
Whatever operation was blocked (a rollover/Microcycle activation via §3b, or a Session-generation request) re-attempts after acknowledgment and proceeds normally against the now-matching hash.

**Caveat, still explicitly not solved by this design:** `Program` remains a single mutable row (confirmed against the live model). `program_id` + `program_prescription_hash` together record and *detect drift from* intent; they do not *prevent* the underlying row from being edited. True `Program` versioning/immutability is a real, separate architectural change and stays a non-goal here.

**Residual concurrency assumption, stated explicitly (fix #6, revision 7).** The hash check closes the gap between *verification* and *consumption* at each individual call site (§5a's Session-generation check, §4a's activation check) — but it does not close the gap *within* a single check-then-use sequence if `Program` writes themselves are concurrent and unserialized: hash verified → `Program` edited by a separate request → `Session` generated, all within the same instant, would still slip through. **This design assumes `Program`-mutating writes are administrative/low-frequency** (the athlete or operator editing their own program, not a high-concurrency write path) **and does not require them to serialize against planned generation.** If that assumption stops holding — e.g. `Program` editing becomes a concurrent, athlete-facing feature — the durable fix is a `Program.revision` counter incremented on every prescription-affecting write, stored alongside `program_id`/`program_prescription_hash` on `Mesocycle`, letting generation cheap-check the revision number before falling back to the full hash comparison. Not built here; the hash alone is sufficient for the current administrative-edit reality, and remains useful as an audit/direct-DB-change detector regardless.

### 5d. Template cardinality validation (fix #6, tightened by fix #8)

`plan_next_mesocycle.py` validates `len(template.postures) == microcycle_count` **exactly** (fix #8 — revision 3 left exact-vs-at-least to the implementer; this revision decides it: a template with six postures applied to a four-week Mesocycle is ambiguous configuration even though indexing would technically still work, so it fails validation rather than silently ignoring the extra two entries) at planning time. **Re-validated defensively at rollover time too** (§5, step 3), inside the same all-or-nothing transaction — an index error during rollover must never leave the old Mesocycle `COMPLETE` and the new one partially instantiated.

## 6. Deload: explicitly out of scope, orchestration seam only

Unchanged from revision 1/2. Advancement never triggers, resolves, or clears `DeloadState`, and never lets it rewrite `planned_posture`. Reconciler step 2 stays a defined no-op seam.

## 7. Audit trail: `AdvancementLog`

Unchanged from revision 2: `reconcile_run_id` groups every row one fixed-point loop produces; `entity_type` includes `"macrocycle"` for plan-exhaustion events; `details_json` carries free-form context (`skipped_day_codes`, `drift_days`, etc). **`SUCCESSOR_PLANNED` (new, revision 8, §5, fix #6)** is logged by `plan_next_mesocycle.py` itself, not the reconciler — it has no `reconcile_run_id`, since it happens outside any fixed-point loop. Deliberately distinct from `MESOCYCLE_ADVANCED`: the former records that plan continuation became available; the latter, logged later by §3b, records that training actually entered that Mesocycle. Conflating them would make it impossible to later ask "how long between planning a Mesocycle and it actually starting" from the log alone.

**Timestamp field cleanup (fix #7a):** revision 2 introduced a new `completed_at` field "alongside the existing `actual_completion_date`/`actual_end_date`" — two independently-writable representations of the same event is exactly the kind of thing that drifts apart in practice. **This revision drops the new field entirely** and uses only the timestamps already on the `Microcycle`/`Mesocycle` models from spec 01 (`actual_start_date`, `actual_completion_date`, `actual_end_date` — all `date`, not `datetime`). If sub-day precision is ever needed, add it deliberately later; don't carry two clocks for the same event now.

## 8. Timezone

**One authoritative source, not left to the implementer (fix #9).** A single config value, `TRAINING_TIMEZONE`, and a single helper (e.g. `local_today()`) that every drift/date comparison in this design calls — never a scattered `date.today()`. No athlete-profile timezone subsystem is being built for this; the app already operates a single training calendar in one local timezone, so a fixed config value is sufficient. Athlete-profile timezone support, if ever needed, is a separate future change that would update the one helper's source, not something this design needs to anticipate.

## 9. Regression tests

- `_compute_recovery_status` window-formula match (already fixed in `6af5440`; re-assert if any new code re-derives a similar window).
- A Microcycle instantiated when its `planned_start_date` is already on or before `local_today` is activated (hash-checked + slots snapshotted) in the same operation, not left `NOT_STARTED` (see the early-start test below for the complementary not-yet-due case).
- A Microcycle with zero `MicrocycleSlot` rows can never reach `COMPLETE`.
- Posture indexing for all four ordinals of a 4-week template.
- **New (from this revision):** generating a `Session` alone (status still `PLANNED`) must leave its bound slot at `resolution=PENDING`, not `COMPLETED` — the core regression guard for this revision's headline fix. A companion test: that same slot resolves to `COMPLETED` only after `Session.status` actually transitions to `COMPLETED`.
- **Revised (revision 6, fix #3):** a second generation request for a `day_code` whose slot is already bound (`session_id != NULL`) returns the existing `Session` unchanged — the old version of this test (asserting `plan_status=UNPLANNED`) is removed, since that behavior directly contradicted §5a's idempotency rule and no longer exists.
- **New:** `program_prescription_hash` mismatch at rollover activation blocks and requires acknowledgment rather than silently materializing against the changed `Program`.
- **New (revision 4, revised revision 8):** a Macrocycle stuck at `planning_state == AWAITING_NEXT_MESOCYCLE` resumes correctly once `plan_next_mesocycle.py` creates a successor — `planning_state` flips to `ACTIVE` in that same transaction (not a separate reconciler-side "resume branch," retired in revision 8 — see §5), and §3b's pending-activation branch picks up and activates the newly-instantiated Microcycle on its own schedule.
- **New:** a slot already resolved `SKIPPED` is left unchanged when its bound `Session` later completes; the `Session` itself still transitions to `COMPLETED` normally. A companion test: a slot still `PENDING` resolves `COMPLETED` normally (the non-regressed case).
- **New:** an `IN_PROGRESS` `Session` is never touched by the drift-expiry pass, regardless of how stale its owning Microcycle is.
- **New:** two `Program` structures that differ only in row/JSON-key order hash identically (canonicalization regression guard for fix #7).
- **New:** planning a Mesocycle whose template posture count doesn't exactly match its microcycle count fails validation at planning time, not at rollover.
- **New (this revision, fix #1):** a `Program` edited in place during an active Mesocycle's week 1 is caught at week 2's activation, not silently picked up — the multi-activation hash-check regression guard.
- **New (fix #2):** a Microcycle whose previous Microcycle completed early (before `planned_start_date`) instantiates as `NOT_STARTED` with no slots and is not activated (hash-checked + snapshotted) until a reconciler run occurs on or after `planned_start_date`; a companion test confirms it *does* activate immediately when the completing run is already on/after that date.
- **New (fix #3):** a session-generation request that loses the slot-verification race against a concurrent advancement returns a typed `Conflict`, not an `UNPLANNED` session; a companion test confirms a retried request against an already-created `Session` returns that same `Session` idempotently rather than erroring or duplicating.
- **New (revision 6, fix #1):** a Microcycle that completes before its successor's `planned_start_date` leaves `blocked_reason="WAITING_FOR_MICROCYCLE_START"` and no `ACTIVE` Microcycle until a reconciler run occurs on/after that date; a companion test asserts the same at a Mesocycle boundary — the successor Mesocycle stays `PLANNED` (not `ACTIVE`) and its first Microcycle stays `NOT_STARTED` until due, and `actual_start_date` is not set early on either.
- **New (revision 6, fix #1):** `GET /training/plan/current` during a `WAITING_FOR_MICROCYCLE_START` window reports `current_active_microcycle=null`, the correct `next_microcycle`/`starts_on`, and the blocked reason — never a stale prior-Microcycle envelope presented as current.
- **New (revision 6, fix #2):** planned Session generation is blocked with `blocked_reason="PROGRAM_DRIFT"` when the `Program` was edited after the target Microcycle's activation but before generation — the gap activation-only checking couldn't catch.
- **New (revision 6, fix #2):** a `Program` edit that changes only prescription detail on an existing `TierExercise` (e.g. a grip-width or rep-range change, no day/tier/exercise added or removed) still changes `program_prescription_hash` — the broadened-scope regression guard, since revision 5's identity/order-only hash would have missed this.
- **New (revision 7, fix #1):** `Macrocycle.planning_state` becomes `ACTIVE` the moment `plan_next_mesocycle.py` creates a successor Mesocycle, even though that successor's Microcycle 1 is still `NOT_STARTED` and `blocked_reason="WAITING_FOR_MICROCYCLE_START"` — asserting the two never simultaneously read as `AWAITING_NEXT_MESOCYCLE` + "a plan exists."
- **New (revision 7, fix #2):** a `PROGRAM_DRIFT` acknowledgment that changes `program_prescription_hash` between a Microcycle's instantiation and its activation is reflected correctly — the Microcycle's slots, once eventually snapshotted, always match the hash that was current *at activation*, never a stale hash from instantiation time. A companion test confirms a Microcycle is never left with slots snapshotted from a `Program` revision different from its stored `program_prescription_hash`.
- **New (revision 7, fix #3):** a simulated crash between the `Session.status → COMPLETED` write and the `MicrocycleSlot.resolution → COMPLETED` write cannot occur — both happen in one transaction; a test asserts the transaction boundary covers both writes (e.g. by forcing a rollback and confirming neither side persisted).
- **New (revision 8, fix #1):** a pending Microcycle blocked with `WAITING_FOR_MICROCYCLE_START` on one reconciler run activates automatically on a later run once `local_today >= planned_start_date`, with no additional trigger required — the general retry guarantee §3b provides, exercised independently of *how* the pending Microcycle was created (normal advancement vs. a Mesocycle boundary).
- **New (revision 8, fix #1):** a pending Microcycle blocked with `PROGRAM_DRIFT` at activation-check time activates automatically on the reconciler run immediately following an `acknowledge_program_drift.py` acceptance — no separate "resume" trigger needed.
- **New (revision 8, fix #1):** at any given time, at most one Microcycle across a Macrocycle sits `NOT_STARTED` with zero slots — asserting the extended uniqueness invariant §3b relies on.
- **New (revision 8, fix #2):** `acknowledge_program_drift.py` refuses acknowledgment against an `ACTIVE` Microcycle when `slot_topology_hash` no longer matches the live `Program`, even with `--accept-current-program-revision` passed — a companion test confirms the same script *does* accept a prescription-only change (topology hash unchanged) against that same `ACTIVE` Microcycle.
- **New (revision 8, fix #4):** a `Program`/template combination that would produce zero `TRAINING` slots fails Microcycle activation outright — the Microcycle never reaches `ACTIVE`, rather than activating and being permanently unable to reach `COMPLETE`.
- **New (revision 8, fix #5):** a post-cutover `Session` that cannot be deterministically mapped to a `day_code`/slot halts the bootstrap for operator resolution — asserting it does *not* silently backfill as `plan_status=PLANNED`/`microcycle_id=NULL`.
- **New (revision 8, fix #6):** `plan_next_mesocycle.py` sets `Macrocycle.planning_state=ACTIVE` in the same transaction that creates a successor Mesocycle — asserting there is no observable moment (even under concurrent reads) where the successor row exists but `planning_state` still reads `AWAITING_NEXT_MESOCYCLE`; a companion test confirms the audit log records `SUCCESSOR_PLANNED` at this point, distinct from `MESOCYCLE_ADVANCED` (logged later, at actual activation).

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
- **A `Program.revision` counter / serialized `Program`-write concurrency model** (§5c, fix #6) — this design assumes `Program` edits are administrative/low-frequency and relies on hash comparison alone; hardening against genuinely concurrent `Program` writes is a separate future change.
- **A generalized `Macrocycle.planning_state=COMPLETE` operation** (§5b, fix #5) — declaring a Macrocycle's own goal finished (distinct from merely running out of planned Mesocycles) is not built here.

## Open questions carried forward

- Exact drift-tolerance day-counts (now 0/1–2/3–4/>4) are still reused placeholders pending real-data tuning.
- Whether `INCOMPLETE`'s explicit-abandonment path needs a real API before it's usable day-to-day is deferred.
- Whether `UNPLANNED_WORKOUT` (the deferred blocked-plan path, see Non-goals) is ever actually needed in practice, or whether a blocked plan should just stay blocked until resolved.
- Whether a genuinely stale `IN_PROGRESS` `Session` (abandoned mid-workout weeks ago) ever needs its own cleanup policy.
