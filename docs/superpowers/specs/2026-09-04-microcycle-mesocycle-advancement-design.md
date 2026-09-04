# Microcycle/Mesocycle Advancement — Design

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

**A slot with `session_id != NULL` is already bound, regardless of its `resolution` value.** A second `Session` generated for the same `day_code` must not attach to an already-bound slot — it becomes `plan_status=UNPLANNED` (§1), full stop, never a fallback count.

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

Unchanged from revision 2 — still required, still blocking, still must run before the reconciler is ever invoked against live data:
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
  blocked_reason: Optional[str]   -- "AWAITING_NEXT_MESOCYCLE" | "INCOMPLETE_MICROCYCLE"
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
- **`NOT_STARTED → ACTIVE`**: on materialization/activation. Sets `actual_start_date`.
- **`ACTIVE → COMPLETE`**: whenever every `TRAINING` slot's `resolution != PENDING`, checked after every relevant slot resolution and after the `>4 days` inferred-skip pass. Reason logged: `ALL_SESSIONS_RESOLVED` or `DRIFT_INFERRED_SKIP`. Sets `actual_completion_date` (the field already on the `Microcycle` model from spec 01 — see §7 for why this design does not add a second, redundant timestamp field alongside it).
- **`ACTIVE → INCOMPLETE`**: only on an explicit, operator-declared interruption/abandonment/replan action — never automatically from drift. Out of scope for a write endpoint in this pass (direct operator action only, same precedent as the cutover script). **Terminal and fully blocking**: does not trigger next-Microcycle advancement or Mesocycle rollover; the reconciler's fixed-point loop stops here.

`planned_posture` is never touched by any transition.

## 5. Mesocycle lifecycle + rollover

```
PLANNED → ACTIVE → COMPLETE
```

**Rollover** (evaluated inside the fixed-point loop, when the current Mesocycle's final Microcycle reaches `COMPLETE`, not `INCOMPLETE`):
1. Close the current Mesocycle (`ACTIVE → COMPLETE`, sets `actual_end_date` — the field already on `Mesocycle`).
2. Query the Macrocycle for the next ordered `Mesocycle` with `status=PLANNED`.
3. **If found**: validate template cardinality (§5d) before activating. Activate it, materialize its first `Microcycle` (`planned_posture = MesocycleTemplate.postures[microcycle.ordinal - 1]`, 0-indexed off a 1-based ordinal — a dedicated test asserts all four index mappings for a 4-week template), activate that Microcycle. Set `Macrocycle.planning_state = ACTIVE`. Reason logged: `MESOCYCLE_ADVANCED`. **The entire step (close old Mesocycle, activate new Mesocycle, materialize+activate its first Microcycle) is one transaction** (fix #6) — a validation/index failure partway through must roll back everything, never leaving the old Mesocycle `COMPLETE` with the new one half-activated.
4. **If not found**: `Macrocycle.planning_state = AWAITING_NEXT_MESOCYCLE` if not already; log `PLAN_EXHAUSTED` only on the transition into that state. Loop stops (`blocked_reason="AWAITING_NEXT_MESOCYCLE"`).

**Resume branch (fix #2, blocker).** The rollover algorithm above is triggered by "the current Mesocycle's final Microcycle reaches `COMPLETE`" — but that event has already happened by the time `Macrocycle.planning_state == AWAITING_NEXT_MESOCYCLE`. `plan_next_mesocycle.py` running later creates the successor `Mesocycle` row successfully, but nothing in the fixed-point loop as written above would ever notice it: there is no `ACTIVE` Mesocycle left to complete, so the rollover trigger never fires again, and the system stays stuck in `AWAITING_NEXT_MESOCYCLE` forever even though a plan now exists. The fixed-point loop (§3, step 3) therefore gets a second, independent entry condition, checked every iteration alongside "final Microcycle just completed":
```
if Macrocycle.planning_state == AWAITING_NEXT_MESOCYCLE:
    previous = latest Mesocycle with status=COMPLETE (ordinal-max, this Macrocycle)
    successor = Mesocycle with macrocycle_id=this, ordinal=previous.ordinal + 1, status=PLANNED

    if successor exists:
        # same steps 3's body: validate cardinality, activate, materialize+activate
        # Microcycle 1, all in one transaction
        activate successor; Macrocycle.planning_state = ACTIVE
        log MESOCYCLE_ADVANCED
        continue the fixed-point loop (a freshly activated Mesocycle/Microcycle
          may itself already be due for further transitions, e.g. if planning
          happened well after the gap opened)
    else:
        # unchanged -- still AWAITING_NEXT_MESOCYCLE, still blocked
        stop
```
This is the same activation logic as step 3 above, reached from a second trigger — not a new activation path to keep in sync separately.

**Within an active Mesocycle, Microcycle-to-Microcycle advancement**: unchanged from revision 2 — next Microcycle materializes with dates computed from the Mesocycle's own schedule, never slid from when the previous one actually finished.

### 5a. Session generation transaction race (fix #4, tighten)

A gap exists between "the reconciler determined slot X is the target" and "the Session insert actually happens" — another concurrent request could advance state in between. Session generation must not trust a reconciled `microcycle_id`/slot identity from a moment ago; it re-verifies inside its own insert transaction:

```
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

If verification fails (lost the race), the generation falls through to `plan_status=UNPLANNED` (§1) rather than force-attaching to a different slot.

### 5b. Plan-exhaustion state

Unchanged from revision 2: `Macrocycle.planning_state: ACTIVE | AWAITING_NEXT_MESOCYCLE | COMPLETE`. Lifecycle/planning metadata, not engine-prescription behavior.

### 5c. `Mesocycle.program_id` + corruption detection (fix #4 from rev-2-review, elevated with a new addition)

`Mesocycle.program_id` (FK, required) — set by `plan_next_mesocycle.py` at planning time, read by rollover's slot materialization instead of "whatever `Program` is active." Unchanged from revision 2.

**New: `Mesocycle.program_structure_hash`** — a hash computed at planning time over the bound `Program`'s relevant `ProgramDay`/`Tier`/`TierExercise` structure, **canonicalized before hashing (fix #7)**: the collections are sorted deterministically (e.g. by `ProgramDay.day_code`, then `Tier.ordinal`, then `TierExercise.ordinal`) before serializing, so a harmless row-order difference (e.g. a re-save that reorders JSON keys or rows without changing content) can never trigger a false-positive drift. At activation (rollover step 3), recompute the hash against the live `Program` state and compare:
```
current hash == planned hash  → proceed
current hash != planned hash  → fail loudly, require explicit operator
                                  acknowledgment before proceeding (see
                                  the recovery mechanism below)
```
This does not achieve `Program` immutability (still an explicit non-goal — see below), but it turns a silent, dangerous failure mode into a loud one: without it, planning "Mesocycle 3 = 28\" Belle Mere + T-bar emphasis" and then editing that `Program` in place four weeks later would let rollover silently materialize something the athlete never actually planned. The hash is cheap now and becomes redundant-but-harmless audit metadata once real `Program` versioning exists.

**Recovery mechanism (fix #7, blocker-adjacent — revision 3 defined the detection but not the way out).** A hash mismatch blocks rollover with no defined resolution otherwise leaves the system correctly suspicious but permanently stuck. A small administrative script, not a UI:
```
scripts/acknowledge_program_drift.py --mesocycle <id> [--accept-current-program-revision]
```
which:
1. shows the planned hash and the current live hash side by side (and, best-effort, a summary of what changed if cheaply derivable — not required);
2. requires the explicit `--accept-current-program-revision` flag to actually act (a bare status check by default, never a silent accept);
3. on acceptance, updates `Mesocycle.program_structure_hash` to the current live value;
4. writes an `AdvancementLog` row (`PROGRAM_DRIFT_ACKNOWLEDGED`) recording old hash, new hash, and operator action — this is a deliberate, audited override, not a code path anyone should be able to trigger accidentally.
Rollover re-attempts (or is manually re-triggered) after acknowledgment and proceeds normally against the now-matching hash.

**Caveat, still explicitly not solved by this design:** `Program` remains a single mutable row (confirmed against the live model). `program_id` + `program_structure_hash` together record and *detect drift from* intent; they do not *prevent* the underlying row from being edited. True `Program` versioning/immutability is a real, separate architectural change and stays a non-goal here.

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
- Any Microcycle produced by this design's materialization paths is never left `NOT_STARTED` after the operation meant to activate it.
- A Microcycle with zero `MicrocycleSlot` rows can never reach `COMPLETE`.
- Posture indexing for all four ordinals of a 4-week template.
- **New (from this revision):** generating a `Session` alone (status still `PLANNED`) must leave its bound slot at `resolution=PENDING`, not `COMPLETED` — the core regression guard for this revision's headline fix. A companion test: that same slot resolves to `COMPLETED` only after `Session.status` actually transitions to `COMPLETED`.
- **New:** a second `Session` generated for a `day_code` whose slot is already bound (`session_id != NULL`) produces `plan_status=UNPLANNED` and does not touch the already-bound slot.
- **New:** `program_structure_hash` mismatch at rollover activation blocks and requires acknowledgment rather than silently materializing against the changed `Program`.
- **New (this revision):** a Microcycle stuck at `Macrocycle.planning_state == AWAITING_NEXT_MESOCYCLE` resumes correctly once `plan_next_mesocycle.py` creates a successor — the resume-branch regression guard for fix #2.
- **New:** a slot already resolved `SKIPPED` is left unchanged when its bound `Session` later completes; the `Session` itself still transitions to `COMPLETED` normally. A companion test: a slot still `PENDING` resolves `COMPLETED` normally (the non-regressed case).
- **New:** an `IN_PROGRESS` `Session` is never touched by the drift-expiry pass, regardless of how stale its owning Microcycle is.
- **New:** two `Program` structures that differ only in row/JSON-key order hash identically (canonicalization regression guard for fix #7).
- **New:** planning a Mesocycle whose template posture count doesn't exactly match its microcycle count fails validation at planning time, not at rollover.

## Non-goals (this design pass)

- DeloadState trigger/evidence logic (§6).
- A scheduled/background job.
- An explicit user-facing "skip this session" endpoint/client UI change — the `Session.status=SKIPPED` → slot resolution path is defined (§2a) but nothing produces it yet.
- A full Mesocycle-authoring write API/UI.
- Auto-replanning an `INCOMPLETE` Microcycle.
- True `Program` immutability/versioning (§5c) — `program_id` + `program_structure_hash` detect drift, they don't prevent it.
- Movement constraint-type classification / exercise-rotation strategy.
- **A blocked-plan escape hatch** (revision 3's `allow_unplanned`) — removed in this revision (§3a, fix #3); a real `UNPLANNED_WORKOUT` policy path that doesn't require Mesocycle posture and doesn't resurrect legacy `PhasePolicy` is a separate future design.
- A stale-`IN_PROGRESS`-session cleanup policy (§2a notes the drift-expiry pass never touches one, but doesn't define what, if anything, eventually should).

## Open questions carried forward

- Exact drift-tolerance day-counts (now 0/1–2/3–4/>4) are still reused placeholders pending real-data tuning.
- Whether `INCOMPLETE`'s explicit-abandonment path needs a real API before it's usable day-to-day is deferred.
- Whether `UNPLANNED_WORKOUT` (the deferred blocked-plan path, see Non-goals) is ever actually needed in practice, or whether a blocked plan should just stay blocked until resolved.
- Whether a genuinely stale `IN_PROGRESS` `Session` (abandoned mid-workout weeks ago) ever needs its own cleanup policy.
