# 60 — Bootstrap current_load from logged performance on needs-calibration movements

**REVISED v2** (2026-09-16) after a Fable review REJECT on v1's implementation
(`task/60`, commit on `/tmp/wt-60` — abandoned, not merged). v1 correctly modified
`performed_floor_delta` to stage the heaviest performed load into
`pending_load_delta` for a `current_load=None` movement, but that marker had
**nowhere to land**: `commit_session` only applies `pending_load_delta` to
movements the assembler already computed a prospective load for
(`assembled.prospective_current_loads`), and the assembler *never* computes a
prospective load for a needs-calibration movement — that's the exact case
being bootstrapped. Net effect of v1: the fix was inert for the bug it
targeted, AND it opened a real corruption path — a later `/wizard-resolve`
call sets `current_load` directly without clearing the now-stale marker, so
the NEXT generation folds the stale marker on top of the manually-confirmed
value (Fable reproduced this empirically: log 265, stage marker, wizard-set
100, next prescription = 365). This revision keeps v1's `performed_floor_delta`
change (that part was correct and unanimously clean in review) and adds the
two missing pieces: a real consumption path in the assembler, and clearing
the marker in `/wizard-resolve`.

## Objective

A movement stuck in needs-calibration (`MovementState.current_load is None`)
must acquire a real `current_load` from the athlete's own logged performance
by the NEXT time a session is generated for it — restoring prescribed load,
ramp/warmup sets (when `ramp_eligible`), and prefill — instead of staying
needs-calibration forever regardless of how many real sessions get logged.

## Background (root cause, confirmed live 2026-09-15/16, re-confirmed by Fable review 2026-09-16)

See the v1 background (still accurate) for how three real IronLog-V2 production
movements (Kickstand RDL [PB], Lying Leg Curl [GHR + Ares], Matrix Machine
Bulgarian Split Squat) were found stuck at `current_load=None` despite 9-18 real
logged `SetLog` rows each. All three were hand-patched directly in the DB as an
interim stopgap (2026-09-15/16) — not a substitute for this fix.

**What v1 got right (keep, do not re-derive):** `performed_floor_delta()` in
`ironlog/engine/advance.py` now returns the heaviest performed load when
`current_load is None` (bootstrap) instead of always `0.0`. This is a real,
correct, reviewed change — the diff already exists in git history
(commit message: "fix(progression): bootstrap current_load from logged
performance on needs-calibration movements", superseded by this revision, not
yet merged). A worker picking this spec up should re-derive the SAME
`performed_floor_delta`/`run_analysis.py` changes if starting from a clean
`main` (they are not yet merged there), but must NOT stop there — that part
alone is the inert-and-corrupting v1 diff.

**What was missing (the actual gap this revision closes):**

1. **No consumption path.** `ironlog/generation/assembler.py`'s `_build_exercise`
   (~lines 589-614) does:
   ```python
   base = resolve_start_load(movement, state, db)
   if base is None:
       load = None   # needs-calibration -- assemble structurally, no target_load
   else:
       if (state is not None and state.pending_load_delta is not None
               and _progression_carry_forward_allowed(ctx)):
           base = base + state.pending_load_delta   # K2 bridge: ADD to an existing base
       load = clamp_to_cap(round_to_achievable(base, floor, step), movement.cap)
       prospective[movement.id] = load   # commit_session's ONLY source of current_load writes
   ```
   When `base is None` (needs-calibration), the `else` branch — the only place
   that populates `prospective[movement.id]`, which is the only thing
   `commit_session` ever writes to `current_load` from — never runs. A staged
   bootstrap `pending_load_delta` is invisible to this function entirely.

2. **Stale marker never cleared on manual calibration.** `/programs/{id}/wizard-resolve`
   (`ironlog/api/app.py`, `resolve_wizard`, ~line 1405) writes `current_load`
   (or `assist_level`) and `confirmed_at` directly via `setattr(state, field,
   res.value)`, by design bypassing generation entirely (its docstring: "Two-writer
   boundary: writes ONLY the load field + confirmed_at; never e1rm/calibration_status/
   counters"). It does not touch `pending_load_delta`. If a bootstrap marker was
   staged by `run_analysis` before the athlete calibrates through the wizard, the
   marker survives the wizard write and gets folded onto the NEXT prescription by
   the K2 bridge above — a real, deterministic corruption (not a rare race).

## File targets

- `ironlog/engine/advance.py` — `performed_floor_delta()` (re-derive v1's change:
  bootstrap branch when `current_load is None`)
- `ironlog/persistence/run_analysis.py` — the advance/floor-delta step (~lines
  590-660): re-derive v1's staging change, but **tighten the `earned_load_step`
  suppression** (Fable Low finding) to be gated by the SAME condition that
  gates `floor_delta` itself — `load_field_for_mode(movement.progression_mode)
  == "current_load"` — not a bare `state.current_load is not None` check, so it
  cannot suppress anything for ASSISTED/bodyweight movements (which always have
  `current_load is None` but are not this bug's target).
- `ironlog/generation/assembler.py` — `_build_exercise` (~lines 589-614): the
  new consumption path (see Changes below)
- `ironlog/api/app.py` — `resolve_wizard` (~line 1405): clear
  `pending_load_delta` alongside the existing load-field write
- `tests/test_advance_load_bridge.py` — re-derive v1's `performed_floor_delta`
  test additions (they were clean in review)
- `tests/test_run_analysis_progression.py` — re-derive v1's staging test, but
  **replace the hand-simulated "commit_session consumption" block** (Fable
  High finding: it invented an apply path — `current_load = (current_load or
  0.0) + pending_load_delta` — that does not exist anywhere in the real code)
  with a real end-to-end drive through `assemble()` + `commit_session` (see
  Verification below)
- `tests/test_rule_wiring.py` — re-derive v1's fixture adjustment (was
  confirmed a legitimate adaptation, not scope creep, by Fable review)
- `tests/test_wizard_resolve_and_start.py` — add coverage for the marker-clear
  in `resolve_wizard`

## Changes

1. **`performed_floor_delta` / `run_analysis.py`**: as v1, plus the tightened
   `earned_load_step` suppression scoping described above.
2. **`assembler.py`'s `_build_exercise`**: give a needs-calibration movement a
   narrow, explicit path to consume a staged bootstrap marker:
   ```python
   base = resolve_start_load(movement, state, db)
   bootstrapped = False
   if (base is None and state is not None and state.pending_load_delta is not None
           and _progression_carry_forward_allowed(ctx)):
       # Bootstrap: no calibrated current_load yet, but a prior completed
       # session logged real performance and run_analysis staged the heaviest
       # weight as pending_load_delta (performed_floor_delta's bootstrap
       # branch). There's no existing current_load to add to (unlike the K2
       # bridge below) -- the staged delta IS the base.
       base = state.pending_load_delta
       bootstrapped = True
   if base is None:
       load = None
   else:
       if (not bootstrapped and state is not None and state.pending_load_delta is not None
               and _progression_carry_forward_allowed(ctx)):
           base = base + state.pending_load_delta   # K2 bridge, unchanged
       load = clamp_to_cap(round_to_achievable(base, floor, step), movement.cap)
       prospective[movement.id] = load
   ```
   The `bootstrapped` flag is required to prevent the K2 bridge from adding
   the SAME `pending_load_delta` a second time on top of itself. Once
   `prospective[movement.id]` is populated, `commit_session`'s existing,
   unmodified apply-once path (`loop.py`) writes it to `current_load` and
   clears `pending_load_delta` exactly as it already does for every other
   movement — no change needed there.
   - Bodyweight movements (`load_field_for_mode` returns `None`) also have
     `base is None` from `resolve_start_load`, but `run_analysis.py`'s guard
     already prevents `pending_load_delta` from ever being set for them (the
     `load_field_for_mode(...) == "current_load"` gate on the floor_delta
     block) — so `state.pending_load_delta` is always `None` for them and this
     new branch is a no-op. Confirm this stays true; do not add a redundant
     bodyweight-specific guard here, but a test covering it is worthwhile.
3. **`resolve_wizard` (`app.py`)**: alongside `setattr(state, field, res.value)`
   and `state.confirmed_at = now`, also set `state.pending_load_delta = None`.
   Update the function's docstring — it currently states "writes ONLY the load
   field + confirmed_at" — to note this one additional, deliberate exception:
   a manually-confirmed load supersedes any staged-but-unapplied delta,
   whether that delta came from the bootstrap path or an ordinary earned-step
   advance that hadn't cycled through `commit_session` yet. (Edge case,
   accepted: if an athlete had a legitimate pending earned-advance staged for
   an already-calibrated STALE movement and reconfirms it through the wizard
   before generating a new session, that earned step is discarded rather than
   applied — acceptable because the athlete's manual value is the more
   authoritative, more recent input.)

## Edge cases

(Carried from v1, still binding, plus new ones from the Fable review:)

- **No SetLogs this session**: `performed_loads` empty → `performed_floor_delta`
  returns `0.0`, no bootstrap staged, unchanged.
- **Heaviest-performed-value selection**: unchanged from v1 — use the max, not
  average or last.
- **HIP_THRUST / non-current_load fields**: unaffected, per the existing guard
  — verify the tightened `earned_load_step` scoping (this revision's change)
  doesn't alter this.
- **Bodyweight**: see Changes item 2 above — must stay a no-op.
- **A movement with `start_ratio`/`derived_from_id` (derived-ratio anchor)**:
  flagged by Fable as an unprobed second entry point — such a movement is
  FRESH via its anchor's e1rm while its OWN `current_load` is still `None`. If
  it logs a session before ever being committed once, does the new
  `bootstrapped` branch in `assembler.py` interact correctly, or does
  `resolve_start_load` returning non-`None` (because trust is FRESH via the
  anchor, not UNKNOWN) mean `base is None` never triggers for it in the first
  place? Trace this explicitly and add a test — do not leave it unprobed
  again.
- **Race with a concurrent `/wizard-resolve` call landing between `run_analysis`
  staging the marker and the next `assemble()` call**: accepted, unchanged
  from v1 — matches the existing apply-once model's assumption of no
  concurrent `MovementState` writers. This revision's wizard-resolve marker
  clear is a sequential-correctness fix (the deterministic corruption Fable
  found), not a new concurrency guarantee.
- **Rounding**: unchanged from v1 — `performed_floor_delta`/`run_analysis`
  stage the RAW performed weight; `round_to_achievable`/`clamp_to_cap` in the
  assembler (already called on `base` regardless of which branch set it) is
  what makes it achievable. No double-rounding risk since both branches now
  funnel through the same `load = clamp_to_cap(round_to_achievable(base, ...))`
  line.

## Dependencies

None — self-contained across the four files above.

## Verification

- `~/projects/IronLog-V2/.venv/bin/pytest -q` — must stay green.
- `performed_floor_delta` unit tests: re-derive v1's (bootstrap value, empty
  performed_loads, unchanged ratchet cases) — these were confirmed
  non-tautological by Fable review.
- **Real end-to-end integration test (replaces v1's hand-simulated one — this
  is the fix for Fable's High finding):** seed a needs-calibration movement,
  log a completed session with real `actual_load` on its working sets, run
  `run_analysis` (stages `pending_load_delta`), then drive the ACTUAL
  `generate_session`/`assemble()` + `commit_session` path for that movement's
  day (see `tests/test_advance_load_bridge.py`'s existing pattern for driving
  this real path — grep for how it calls `generate_session`/`commit_session`
  already, do not hand-roll a new simulation) — assert the resulting session's
  `PlannedSet.target_load` for that movement is now the bootstrapped value, AND
  that `MovementState.current_load` is non-`None` and equals it after commit,
  AND that `pending_load_delta` is `None` afterward (real apply-once
  consumption, not simulated).
- `resolve_wizard` marker-clear test: seed a movement with a non-`None`
  `pending_load_delta`, call `/programs/{id}/wizard-resolve` (or the handler
  directly) with a manual value for it, assert `pending_load_delta` is `None`
  afterward and `current_load` is the manually-resolved value — and, per the
  Fable-found corruption scenario, assert that a SUBSEQUENT `assemble()` call
  for that movement does NOT fold any stale delta on top of the manually-set
  value.
- Derived-ratio-anchor edge case: add a test tracing exactly what `base is
  None` evaluates to for such a movement, per the Edge cases entry above.
- Manual/live check (optional, not required for merge): a genuinely fresh
  needs-calibration movement, logged once in the app, shows a prescribed load
  (and ramp sets, if `ramp_eligible`) on its NEXT generated session.
