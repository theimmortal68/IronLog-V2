# Engine Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix two HT-engine correctness gaps — never prescribe a retired band, and only flip a band to MEASURED after 3 distinct, agreeing sessions.

**Architecture:** Two independent, server-only, pure changes. (1) `Band` namedtuple gains a defaulted `usable` field and `ht_next_setup` filters candidates by it, with the assembler passing the real flag through. (2) `ht_refine`'s MODELED→MEASURED gate counts distinct sessions and requires a 15% consistency check instead of counting sets. No schema/migration, no client, no D6.

**Tech Stack:** Python/FastAPI/SQLModel, pytest. Tests run REMOTE: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q ...'` (repo is NFS-mounted; venv lives on myflix). Do NOT run pytest locally.

**Spec:** `~/projects/IronLog-V2/docs/superpowers/specs/2026-07-04-engine-hardening-design.md` (commit 65c496e).

## Global Constraints

- Server only; **NO `from __future__ import annotations`**; **no schema/migration**; `engine/band_composite.py` stays **pure** (no DB/IO imports); **Option-C** boundary preserved — neither change writes `current_load`/`ht_plates`/`ht_band_config`.
- The full existing pytest suite (baseline **359**) must stay green. Where a pre-existing test encoded the OLD behavior being fixed, update it to the new semantics (a coupled test) — do NOT weaken it to pass.
- Tests run via `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q'`. BUILD-AND-TEST-ONLY: never touch the live DB or restart the service.

---

## File Structure

- `ironlog/engine/band_composite.py` — MODIFY: `Band` gains `usable` (defaulted); `ht_next_setup` + `_all_configs` become usable-aware.
- `ironlog/generation/assembler.py:225` — MODIFY: pass `bp.usable` into the `Band(...)` inventory (one line).
- `ironlog/persistence/ht_refine.py` — MODIFY: replace the set-count flip gate with a distinct-session + consistency gate.
- `tests/test_ht_next_setup.py` — MODIFY: add usable-aware cases (existing 3-arg `Band` cases unaffected).
- `tests/test_ht_composite_wiring.py` — MODIFY: add an assembler integration case (retired band not prescribed).
- `tests/test_felt_peak_refine.py` — MODIFY: update the old flip test to session semantics + add the new gate cases.

---

### Task 1: Band wear-gate (`ht_next_setup` + assembler)

**Files:**
- Modify: `ironlog/engine/band_composite.py`
- Modify: `ironlog/generation/assembler.py` (line ~225)
- Test: `tests/test_ht_next_setup.py`, `tests/test_ht_composite_wiring.py`

**Interfaces:**
- `Band = namedtuple("Band", "id rest peak usable", defaults=(True,))` — 4th field, defaulted; existing `Band(id, rest, peak)` callers get `usable=True`.
- `ht_next_setup(plates, config, inventory, plate_step=5, clamp=220)` signature unchanged; behavior now skips non-usable bands as candidates and won't keep loading a retired current config.

- [ ] **Step 1: Write the failing pure tests**

Append to `tests/test_ht_next_setup.py`:

```python
def test_band_defaults_usable_true():
    assert Band(0, 18, 45).usable is True


def test_search_skips_retired_band():
    # Red (id 1) retired. From a capped Orange the reconfigure must not pick Red.
    inv = [Band(0, 18, 45), Band(1, 36, 90, False), Band(2, 60, 150),
           Band(3, 80, 200), Band(4, 130, 325), Band(5, 190, 475)]
    plates, config = ht_next_setup(202, [0], inv, 5, 220)
    assert 1 not in config


def test_current_config_with_retired_band_reconfigures_off_it():
    # Orange (id 0) is the current config but is now retired -> skip the
    # raise-plates shortcut, reconfigure to a usable band (drop Orange).
    inv = [Band(0, 18, 45, False), Band(1, 36, 90), Band(2, 60, 150),
           Band(3, 80, 200), Band(4, 130, 325), Band(5, 190, 475)]
    plates, config = ht_next_setup(180, [0], inv, 5, 220)
    assert 0 not in config


def test_all_bands_retired_falls_back_to_plates_only():
    # No usable band -> the only usable config is the empty (plates-only) one.
    inv = [Band(0, 18, 45, False), Band(1, 36, 90, False)]
    plates, config = ht_next_setup(100, [0], inv, 5, 220)
    assert config == []          # no retired band prescribed
    assert plates >= 100
```

- [ ] **Step 2: Run pure tests to verify they fail**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_ht_next_setup.py'`
Expected: `test_band_defaults_usable_true` FAILS (Band has 3 fields → no `.usable`); the retired-band tests FAIL (4-arg `Band(...)` is a TypeError until the field is added, or the filter isn't applied).

- [ ] **Step 3: Make `Band` and `ht_next_setup` usable-aware**

In `ironlog/engine/band_composite.py`:

Change the `Band` definition:
```python
Band = namedtuple("Band", "id rest peak usable", defaults=(True,))
```

Change `_all_configs` to enumerate only usable bands:
```python
def _all_configs(inventory):
    ids = [b.id for b in inventory if b.usable]
    for k in range(len(ids) + 1):
        for combo in combinations(ids, k):     # each usable band at most once
            yield list(combo)
```

Change the "raise plates within current config" shortcut in `ht_next_setup` to require the current config be all-usable (leave the rest of the function as-is):
```python
def ht_next_setup(plates, config, inventory: List[Band], plate_step=5, clamp=220) -> Tuple[float, list]:
    by_id = {b.id: b for b in inventory}     # ALL bands: prices the current config even if retired
    cur_peak = config_peak(plates, config, by_id)
    # 1) prefer raising plates within the current config — only if it uses no
    #    retired band (don't keep loading a band the athlete retired).
    if all(by_id[b].usable for b in config) and config_bottom(plates + plate_step, config, by_id) <= clamp:
        return (plates + plate_step, list(config))
    # 2) search USABLE subsets for the smallest peak strictly above current
    best = None
    for cfg in _all_configs(inventory):
        srest = sum(by_id[b].rest for b in cfg)
        if srest > clamp:
            continue
        max_plates = int((clamp - srest) // plate_step) * plate_step
        p = 0.0
        while p <= max_plates:
            pk = p + sum(by_id[b].peak for b in cfg)
            if pk > cur_peak:
                key = (pk, len(cfg))
                if best is None or key < best[0]:
                    best = (key, (p, list(cfg)))
            p += plate_step
    return best[1] if best else (plates, list(config))
```

- [ ] **Step 4: Run pure tests to verify they pass**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_ht_next_setup.py'`
Expected: PASS (all, incl. the pre-existing cases which use `Band(id, rest, peak)` → `usable=True`).

- [ ] **Step 5: Wire the assembler inventory (pass the real flag) + write the failing integration test**

In `ironlog/generation/assembler.py`, at the inventory construction (~line 225), pass `bp.usable`:
```python
    band_inventory = [Band(bp.id, bp.bottom_lb, bp.peak_lb, bp.usable)
                      for bp in db.exec(select(BandPair)).all()]
```

Append an integration test to `tests/test_ht_composite_wiring.py` (reuses the existing `gen_db_calibrated` fixture + `_first_ht_working_set`; the calibrated D2 HT is 180+Orange, band id 0):
```python
def test_assembler_does_not_prescribe_a_retired_band(gen_db_calibrated):
    gen_db = gen_db_calibrated
    # Retire Orange (band id 0) — the band the calibrated D2 HT currently uses.
    orange = gen_db.get(BandPair, 0)
    orange.usable = False
    gen_db.add(orange)
    gen_db.commit()

    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    sk = lay_skeleton("D2 Lower A", gen_db)
    ctx = resolve_context("D2 Lower A", sk, gen_db, wk)
    sel = program_selections(sk)
    assembled = assemble(sel, sk, ctx, gen_db)

    ht_set = _first_ht_working_set(assembled)
    assert ht_set.band_config is not None
    assert 0 not in ht_set.band_config          # retired Orange is never prescribed
```
(If band id 0 is not the Orange/HT band in this fixture, adapt to whichever band the calibrated D2 HT uses — read the fixture's `BandPair` seed and the `_first_ht_working_set` result before retiring, and assert that specific id is excluded.)

- [ ] **Step 6: Run the integration test + the full suite**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_ht_composite_wiring.py'`
Expected: PASS (new test + the 4 existing wiring tests). Then the full suite:
`ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q'`
Expected: all green (baseline 359 + the new tests). If any OTHER test breaks, your `Band` change wasn't back-compatible — investigate (existing `Band(id,rest,peak)` calls must still work via the default).

- [ ] **Step 7: Commit**

```bash
cd ~/projects/IronLog-V2 && git add ironlog/engine/band_composite.py ironlog/generation/assembler.py \
    tests/test_ht_next_setup.py tests/test_ht_composite_wiring.py
git commit -m "fix(engine): band wear-gate — ht_next_setup + assembler honor BandPair.usable"
```

---

### Task 2: Felt-peak calibration — sessions + consistency gate

**Files:**
- Modify: `ironlog/persistence/ht_refine.py`
- Test: `tests/test_felt_peak_refine.py`

**Interfaces:**
- Constant rename: `CONSISTENT_READINGS_TO_MEASURE = 3` → `CONSISTENT_SESSIONS_TO_MEASURE = 3`; add `CONSISTENCY_TOLERANCE = 0.15`.
- New helper `_single_band_session_observations(db, band_id) -> List[float]` (one mean observed per distinct session, ordered by session_id) replaces `_count_single_band_readings`. New helper `_is_consistent(obs) -> bool`.
- `refine_from_logged_ht(session_id, db)` unchanged signature; only the flip gate changes; the per-set EMA nudge is untouched.

- [ ] **Step 1: Write the failing tests**

First, inspect `tests/test_felt_peak_refine.py` and find the existing test that asserts a MODELED→MEASURED flip (it plants qualifying readings then asserts `calibration_status == MEASURED`). If it flips using 3 sets within ONE session (one `_log_ht_session` call, or 3 sets on one session), UPDATE it so its flip now comes from **3 distinct `_log_ht_session` calls** (3 sessions) with consistent readings — keep the assertion that it reaches MEASURED, just via 3 sessions. Do NOT weaken any assertion.

Then append these cases (they use the file's existing `_make_engine` + `_log_ht_session` helpers; `_log_ht_session` creates one session per call). Use a single band and consistent plates so `observed = felt_peak - actual_plates`:

```python
def test_three_sets_one_session_does_not_flip():
    engine = _make_engine()
    with DBSession(engine) as db:
        band = BandPair(id=0, label="#0 Orange", bottom_lb=18.0, peak_lb=45.0,
                        calibration_status=BandCalStatus.MODELED)
        mv = Movement(name="Hip Thrust [HIP_THRUST]", base_name="Hip Thrust")
        db.add(band); db.add(mv); db.commit(); db.refresh(band); db.refresh(mv)
        # ONE session; three qualifying single-band sets on that session.
        sid = _log_ht_session(db, movement_id=mv.id, band_config=[0],
                              target_plates=180.0, actual_plates=180.0,
                              felt_peak=225.0, session_date=date(2026, 7, 1))
        # add two more sets to the SAME session
        grp = ExerciseGroup(session_id=sid, order_index=1, group_type=GroupType.STRAIGHT)
        db.add(grp); db.commit(); db.refresh(grp)
        pex = PlannedExercise(group_id=grp.id, movement_id=mv.id, order_index=0,
                              scheme=Scheme.STRAIGHT, objective=Objective.MAINTAIN)
        db.add(pex); db.commit(); db.refresh(pex)
        for _ in range(2):
            ps = PlannedSet(planned_exercise_id=pex.id, set_index=0, set_role=SetRole.WORKING,
                            target_plates=180.0, band_config=[0])
            db.add(ps); db.commit(); db.refresh(ps)
            db.add(SetLog(session_id=sid, movement_id=mv.id, planned_set_id=ps.id,
                          set_index=0, set_role=SetRole.WORKING, is_warmup=False,
                          actual_plates=180.0, felt_peak=225.0,
                          feedback_tap=FeedbackTap.ON_TARGET))
        db.commit()

        refine_from_logged_ht(sid, db)
        assert db.get(BandPair, 0).calibration_status == BandCalStatus.MODELED


def _plant_and_refine_sessions(db, mv_id, peaks):
    """One qualifying single-band (Orange) session per felt_peak in `peaks`,
    plates 180, then refine each. Returns the final band status."""
    for i, fp in enumerate(peaks):
        sid = _log_ht_session(db, movement_id=mv_id, band_config=[0],
                              target_plates=180.0, actual_plates=180.0,
                              felt_peak=fp, session_date=date(2026, 7, 1 + i))
        refine_from_logged_ht(sid, db)
    return db.get(BandPair, 0).calibration_status


def test_three_consistent_sessions_flip_to_measured():
    engine = _make_engine()
    with DBSession(engine) as db:
        db.add(BandPair(id=0, label="#0 Orange", bottom_lb=18.0, peak_lb=45.0,
                        calibration_status=BandCalStatus.MODELED))
        mv = Movement(name="Hip Thrust [HIP_THRUST]", base_name="Hip Thrust")
        db.add(mv); db.commit(); db.refresh(mv)
        # observed = felt_peak-180 -> 45,46,47 : spread 2, mean 46 -> ~4% <= 15%
        status = _plant_and_refine_sessions(db, mv.id, [225.0, 226.0, 227.0])
        assert status == BandCalStatus.MEASURED


def test_three_sessions_with_outlier_stay_modeled():
    engine = _make_engine()
    with DBSession(engine) as db:
        db.add(BandPair(id=0, label="#0 Orange", bottom_lb=18.0, peak_lb=45.0,
                        calibration_status=BandCalStatus.MODELED))
        mv = Movement(name="Hip Thrust [HIP_THRUST]", base_name="Hip Thrust")
        db.add(mv); db.commit(); db.refresh(mv)
        # observed 45, 46, 70 -> spread 25 over mean ~53.7 = 47% > 15% -> not consistent
        status = _plant_and_refine_sessions(db, mv.id, [225.0, 226.0, 250.0])
        assert status == BandCalStatus.MODELED
```

(Ensure the imports at the top of the test file cover `ExerciseGroup`, `PlannedExercise`, `PlannedSet`, `GroupType`, `Scheme`, `Objective`, `SetRole`, `FeedbackTap`, `BandCalStatus` — most are already imported; add any missing.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_felt_peak_refine.py'`
Expected: `test_three_sets_one_session_does_not_flip` FAILS (current code counts the 3 sets → flips to MEASURED); the consistency tests may fail too.

- [ ] **Step 3: Replace the flip gate**

In `ironlog/persistence/ht_refine.py`:

Replace the constant near the top:
```python
CONSISTENT_SESSIONS_TO_MEASURE = 3
CONSISTENCY_TOLERANCE = 0.15
```

Replace `_count_single_band_readings` with these two helpers:
```python
def _single_band_session_observations(db: DBSession, band_id: int) -> List[float]:
    """One observed peak per DISTINCT session (mean of that session's qualifying
    single-band readings for `band_id`), ordered by session_id ascending. A set
    qualifies iff it has a non-null felt_peak, resolves to the single band
    `band_id`, and has a plates reference (actual_plates or the PlannedSet's
    target_plates)."""
    all_logs = db.exec(select(SetLog).where(col(SetLog.felt_peak).is_not(None))).all()
    planned_sets = _load_planned_sets(db, all_logs)
    by_session: dict = {}
    for sl in all_logs:
        ps = planned_sets.get(sl.planned_set_id) if sl.planned_set_id else None
        config = _resolved_band_config(sl, ps)
        if config is None or len(config) != 1 or config[0] != band_id:
            continue
        plates = sl.actual_plates
        if plates is None and ps is not None:
            plates = ps.target_plates
        if plates is None:
            continue
        by_session.setdefault(sl.session_id, []).append(sl.felt_peak - plates)
    return [sum(by_session[sid]) / len(by_session[sid]) for sid in sorted(by_session)]


def _is_consistent(observations: List[float]) -> bool:
    """True iff there are >= N distinct-session observations and the most recent
    N agree within CONSISTENCY_TOLERANCE of their mean."""
    if len(observations) < CONSISTENT_SESSIONS_TO_MEASURE:
        return False
    window = observations[-CONSISTENT_SESSIONS_TO_MEASURE:]
    mean = sum(window) / len(window)
    if mean <= 0:
        return False
    return (max(window) - min(window)) <= CONSISTENCY_TOLERANCE * mean
```

In `refine_from_logged_ht`, replace the flip condition:
```python
        if _count_single_band_readings(db, band_id) >= CONSISTENT_READINGS_TO_MEASURE:
```
with:
```python
        if _is_consistent(_single_band_session_observations(db, band_id)):
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_felt_peak_refine.py'`
Expected: PASS (updated flip test + 3 new cases + the untouched EMA-nudge tests).

- [ ] **Step 5: Full suite (no regression)**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q'`
Expected: all green (baseline 359 + Task 1 tests + Task 2 tests).

- [ ] **Step 6: Commit**

```bash
cd ~/projects/IronLog-V2 && git add ironlog/persistence/ht_refine.py tests/test_felt_peak_refine.py
git commit -m "fix(engine): felt-peak MEASURED gate counts distinct sessions + 15% agreement"
```

---

## Routing Plan

| Task | Deliverable | Route |
|---|---|---|
| Task 1 | band wear-gate (band_composite + assembler + tests) | Claude Code Agent subagent (codex read-only → subagent applies+tests via `ssh myflix`) |
| Task 2 | felt-peak session/consistency gate + tests | Claude Code Agent subagent |

**Delegation ratio: 2/2 tasks delegated (100%).** Tier A writes no implementation code — dispatches a fresh implementer per task, runs the two-verdict review gate between tasks, and the final whole-branch review. Consensus workers unused (Python via subagent because codex can't apply/test).

## Self-Review

**Spec coverage:** GAP 1 (Band.usable default + usable-aware search + assembler wiring) → Task 1 ✓; degenerate all-retired → plates-only (empty config is the only usable config) → Task 1 `test_all_bands_retired_falls_back_to_plates_only` ✓ (refines the spec's "hold" note: plates-only is itself a usable config, so all-retired progresses plates-only rather than hard-holding — captured in the test). GAP 2 (distinct-session count + 15% consistency, rolling last-3, EMA unchanged) → Task 2 ✓. Option-C untouched by both; no schema; engine pure; existing suite green (both tasks' full-suite step).

**Placeholder scan:** no TBD/TODO; every code step carries complete code. The one adaptation directive (assembler integration test: confirm which band id the calibrated D2 HT uses before retiring it) is a grounded instruction, not a placeholder — the default (id 0 = Orange) is stated with a fallback to read the fixture.

**Type consistency:** `Band` 4-field with default is used consistently (3-arg existing calls, 4-arg new/assembler). `CONSISTENT_SESSIONS_TO_MEASURE`/`CONSISTENCY_TOLERANCE`, `_single_band_session_observations`, `_is_consistent` names are consistent between the Task 2 definitions and their single call site in `refine_from_logged_ht`. The removed `_count_single_band_readings`/`CONSISTENT_READINGS_TO_MEASURE` have no other references (verify with grep during implementation).
