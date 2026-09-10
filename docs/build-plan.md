# IronLog-V2 Build Plan (living punch-list)

**Last updated 2026-09-10.** Source of truth for the in-flight feature/bug work. The
2026-07-09 version of this file stopped tracking reality for two months — everything
below "Shipped since 2026-07-09" was refreshed by re-reading `docs/STATE.md` in full plus
every `.specs/*.md` file. Test count as of this refresh: **851 passing**.

## ✅ Shipped + live (through 2026-07-09)

See git history for full detail; summary only, unchanged from the prior version of this
file:

- Progression engine (K + K2): `progression_rule` wired from YAML, advance→load bridge.
- Knee-raise retype (bodyweight/incline, INCLINE_REDUCTION).
- Client capture fixes: logged-actuals+edit, weight carry-forward, idempotent logging,
  band-color off-by-one, resume-cursor.
- Load ratchet (`performed_floor_delta`), Hip Thrust note-apply LOAD override fix,
  `unit_hint` on `ExerciseOut` (server half of the lb-vs-degrees display bug).
- Ramp sets (auto-derived 40/60/80% ramp ahead of heavy T1 anchors).
- EMOM finishers (`DayFinisher` + duration→rope-weight progression).
- Fallback-replay slot-identity fix, D4 exercise reorder deploy, two athlete notes
  resolved (Hip Thrust band-composite search, Meadows Row LOAD override).
- Giant-set cap raise (3→4) + knee-modality grouping fix.
- `assist_ladder` seed-source gap closed for the Nordic Curl family.
- G+D+E: autoregulated + background-capable rest timer (client repo), confirmed working
  on-device.

## ✅ Shipped since 2026-07-09

### Periodization / advancement engine (the biggest epic this window)
- **Long-range periodization** (specs 01–06, periodization series) — replaced the old
  `Phase` enum with a real Macrocycle/Mesocycle/Microcycle hierarchy across 4 orthogonal
  axes (Mesocycle/BodyCompState/RecoveryStatus/DeloadState), a deterministic policy
  resolver, generation wiring + `prescription_snapshot`, cutover tooling, 2 read-only
  endpoints. Merged to `main` 2026-09-04, production cutover run same day
  (`scripts/migrate_phase_to_periodization.py --apply`); caught and fixed 2 live bugs
  (spurious POOR recovery status from a window mismatch; a stuck NOT_STARTED
  Microcycle). **Live.**
- **Microcycle/Mesocycle advancement engine** (specs adv-01–adv-07) — the state machine
  that actually advances weeks/mesocycles over time: schema+enums, hash utilities
  (`program_hash.py`), reconciler core, generation/API wiring, `plan_next_mesocycle.py`,
  `acknowledge_program_drift.py`, `bootstrap_microcycle_one.py`. Merged 2026-09-05,
  migration 068 applied 2026-09-07. **Live**, with one real gap — see Open Items #1.
- **Mesocycle #1 hash-bootstrap live-data repair** (2026-09-08) — mesocycle #1 was set up
  via the one-off bootstrap path (not `plan_next_mesocycle.py`) and was left with zero
  `MicrocycleSlot` rows and null prescription/topology hashes, unconditionally blocking
  every `/generate`+`/approve` cycle with 409 PROGRAM_DRIFT. Fixed live via the repo's own
  recovery scripts, no code change. Confirmed one-time, not architectural. **Resolved.**

### Feature epics
- **Weak-point assessment** (specs 35–38) — growth-rate/lagging-detection functions,
  `MovementWeaknessSignal`, `GET /weak-points`. **Live.**
- **Withings body-scan integration** (specs 24–29 + follow-ups) — credentials model,
  `DailyReadiness.body_fat_pct`, OAuth2 flow, sync, nightly reconciliation, webhook +
  manual trigger. **Live** (CSRF-validation/typed-exception follow-up fix and a
  reconciliation-timer move 03:00→09:00 both shipped after initial deploy).
- **Goal-driven CUT→STAB gate** (specs 30–33) — `GoalSettings`, `compute_goal_stable`,
  `GET`/`POST /goals`. **Live.**
- **Missed-workout handling** (specs 39–42) — `MissedDayRecord`, detection/
  auto-resolution, nightly timer, review/acknowledge/reschedule endpoints. **Live.**
- **Notes/reorder pipeline** (specs 13–17) — `REORDER` action type, `OverrideType.REORDER`,
  `lay_skeleton` reorder-override support, the deterministic note resolver, resolved
  proposals on `GET /notes/review`. **Believed live** — no retraction found, but wasn't
  confirmed via an explicit "MERGED+LIVE" marker the way most other batches were; spot
  check before treating as gospel.
- **HT / assist-ladder progression-integrity fixes** (specs 46–52) — additive
  load-floor+earned-advance stacking, HT performed-floor reconciliation, assist-ladder
  performed-floor reconciliation, HT clean-advance gating, HT D2/D5 unification, HT D6
  derived-from-unified. **Live.**
- **HGC condensed-week** (specs 51, 53, 54, 55) — one-off script materializing sessions
  for a condensed multi-mini-session week, plus finisher/warmup dedup fixes (once per
  real training day, not per mini-session). **Live.**
- **Alternating-pair tiers + duration-based TierExercise** (specs 58, 59) — real
  alternating-pair interleaving (D1 Bench/Pendlay, D4 OHP/Lat-Pulldown) and a
  duration-based prescription type (Suitcase Dreadmill Carry). Merged + live
  2026-09-02, client finisher-timer rewrite shipped alongside. Follow-up gap: see Open
  Items #2.

### Program reconciliation batches
- **Outside-review reconciliation** (2026-08-31 → 09-03) — program renamed "APEX Bridge";
  migrations 044–057, 064–066, 069–072 covering day-role relabeling, rest-time bumps, D6
  tier restructuring, D1/D4/D6 T2/T3 swaps/reorders. Recurring seed-code/yaml drift debt
  substantially closed 2026-09-03 (still a standing risk — Open Items #4).
- **D4/D6 rear-delt-extension split** (migration 072, 2026-09-10) — the duplicate
  "Better Fly Rear Delt Extension" slot on both D4 and D6 split into two distinct angled
  movements (Cross-Body Fly / Rear Delt Raise).
- **Incline-reduction terminal-handoff fix** (2026-09-10) — `_incline_reduction` now
  hands off to `RPE_8_STANDARD` at the top of its ladder instead of holding forever
  (mirrors `_assistance_reduction`). Affects Ab Trainer Decline Sit-up, Hanging Leg
  Raise, Russian Twist. Found abandoned on a 3-week-stale branch, merged today.

### Standalone fixes/incidents
- 2026-08-24: D4 finisher sandbag→slam-ball swap; finisher payload exposes
  `movement_id`; `_confirmation_window()` flattened to always-1.
- 2026-08-28/29/09-07: recurring `increment_ladder=[5, 2.5]`→`[2.5]` fixes, 4 separate
  occurrences across different movements (see Open Items #5 — never swept proactively).
- 2026-08-29: Kickstand RDL DB→barbell correction surfaced a real ramp-eligibility bug;
  Reverse Nordic Curl missing `increment_ladder` entirely, fixed. **Same night, a
  production incident**: `rm -f ironlog.db && seed` run against the live NFS-mounted DB
  mid-workout wiped session/setlog tables; recovered from a 3-day-old nightly backup,
  permanently lost anything logged after it. (This is the origin of the "never reseed
  this checkout" rule now in `CLAUDE.md`.)
- 2026-08-31: Dips band-model misclassification fixed (ASSISTED/CABLE_LB model was
  walking load the wrong direction on an added-resistance movement).
- 2026-09-02: stale in-memory enum caused a false 500 on `/generate` (service restart,
  not a code fix); ALT_PAIR set-ordering bug root-caused to the Android client, fixed
  there.

## Open items (carried forward, not yet done)

1. **Duration-based movements (spec 59) are invisible to e1RM/stall/CEILING analysis** —
   only the load-advance path was generalized. No real impact yet (single-rung ladder),
   but a real gap for any future multi-rung timed movement.
2. **Advancement engine's regression-test gap** — 4 fixed paths (crash-window,
   concurrent-approval race, no-matching-slot, missing-Microcycle/Program guards) were
   independently verified correct but have no dedicated regression tests yet.
3. **Advancement design's own stated non-goals, still unscoped**: exact drift-tolerance
   day-counts, an explicit `INCOMPLETE`-abandonment API, an explicit
   `UNPLANNED_WORKOUT` path, stale-`IN_PROGRESS`-session cleanup, a `Program.revision`
   counter for real write concurrency.
4. **Seed-code/live-DB reconciliation is a standing, recurring debt** — every live-only
   migration risks drifting from `program_seed.py`/yaml until someone notices (has
   happened repeatedly). Partially closed 2026-09-03; will recur without a systematic
   check (e.g. a CI-style diff between live DB wiring and seed source).
5. **Proactive `increment_ladder=[5, 2.5]` sweep never done** — fixed reactively 4 times
   so far. A single `grep -n "increment_ladder=\[5, 2.5\]" ironlog/seed.py` + review pass
   would close this permanently; still not done.
6. **Matrix Machine Bulgarian Split Squat scheme conflict, unresolved** — a fix
   (DOUBLE_PROGRESSION 8-12 → STRAIGHT 8-8) is committed but deliberately withheld from
   deployment because it conflicts with the athlete's own prior approval of the old
   state. Needs an explicit athlete decision, not a code call.
7. **Small carried-forward review nits**: a migration WHERE-clause guard could be
   tighter; `tests/test_program_seed_yaml_parity.py`'s own `YAML_M_TO_LIBRARY` mirror has
   drifted one entry from the production map (see `ironlog/generation/rule_wiring.py`'s
   unrelated pre-existing `Stryker Pad Seated OHP [DB]` vs `[PB]` mismatch, found
   2026-09-10 — same class of drift, not yet fixed); `RestTimer.kt`'s rest-suppression is
   order-naive for ALT_PAIR's interleaved final set.
8. **Wide-Grip Pull-up streak anomaly** (`consecutive_advance_count` desync after a
   corrected session) — never root-caused, only made moot in practice by the window=1
   change. The underlying "a correction can silently desync progression state"
   mechanism could recur elsewhere.
9. **Bicep Curl's logged `actual_load`/`feedback_tap` for 2026-08-23 doesn't reflect
   real band progression** — same class of gap as the (now-fixed) Dips issue; would
   require editing real `SetLog` rows, not done.
10. **Rope-ladder progression activation side-effect** — the window=1 change also
    activated a previously-unreachable `FINISHER_DURATION_THEN_ROPE` progression.
    Unrequested but not incorrect; never confirmed as intended with the athlete.
11. **`docs/program/source/2026-08-10-maintenance-block-seed-data-FINAL.md`** still
    specifies per-movement `confirmation_window` values the code no longer parses —
    needs reconciling so a future session doesn't "restore" a stale value from that doc.
12. **`CLAUDE.md`'s "Current state" table is stale and self-flagged as such** — says
    "744 passing" (real count: 851) and references a nonexistent
    `ironlog/engine/generation.py` (real code is in `ironlog/generation/loop.py`
    /`assembler.py`/`context.py`). Needs the table-wide audit its own header calls for.
13. **Ad-hoc program-export script never promoted to a committed file** — hand-rebuilt
    each time it's needed, which already caused one bug (missing ALT_PAIR grouping
    display) from being non-versioned.
14. **UX hardening, explicitly deferred by the athlete**: a confirmation step before
    final Finish & Submit; a visible "Submitting..." loading state ("we will come back
    to that later").

## Queued (design needed before spec-ready)

| Item | What | Size |
|---|---|---|
| **H** | AI acts on programming notes (reorder / assist+reps / cross-day requests) — own design | large |

Scoping doc written (`.specs/10-ai-acts-on-notes-scoping.md`) — needs a brainstorming
session (central open question: should anything ever apply without per-instance human
confirmation) before an implementation spec. Unchanged status since 2026-07-09.

## Deferred / tiny

- Clean the single stray Bench duplicate setlog from Day 1 (negligible).
- Side-aware unilateral **edit** (currently unilateral logged cards are view-only): key
  `loggedSetActuals`/`editingSetId`/`existingLog` by `(plannedSetId, sideIndex)` +
  per-side cards.
- `build_weak_point_hints` still reads `MovementState` day-blind (stall-detection only,
  not loads).
