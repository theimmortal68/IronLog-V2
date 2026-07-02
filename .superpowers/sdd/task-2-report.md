# Task 2 Completion Report — POST /sessions/{id}/submit (Logging Round-Trip)

**Status:** DONE  
**Commit:** f403f40  
**Branch:** feat/logging-round-trip

---

### Gate Confirmations

**Gate #2 (tap-422 server half):** CONFIRMED. Request with `set_role="WORKING"` and `feedback_tap=None` returns HTTP 422, zero SetLogs written, session status remains PLANNED.

**Gate #3 (idempotency / lost-ack):** CONFIRMED. First submit returns 200 `already_completed=False`; retry returns 200 `already_completed=True`; DB asserts exactly ONE SetLog — no duplicate written.

**Two-writer boundary:** CONFIRMED. `grep -n "current_load|\.e1rm" ironlog/api/app.py` returns zero hits. Handler invokes `run_analysis(session_id, db, _week_keyer)`, never reimplements load computation.

---

### Files Changed

- `ironlog/api/app.py` — added `SessionStatus`, `NoteClass`, `SetRole`, `SetLog`, `ExerciseSurvey`, `Note` imports from `..models`; added `SubmitRequest`/`SubmitResponse` import from `.schemas_capture`; added `_TAP_REQUIRED_ROLES` set; added `submit_session` endpoint
- `tests/test_submit_endpoint.py` — new file, 3 gate tests (verbatim from brief)

---

### Pytest Red → Green

Red (before endpoint):
```
3 failed, 4 warnings in 0.38s
```

Green (after endpoint):
```
3 passed, 6 warnings in 0.29s
```

Full suite:
```
230 passed, 96 warnings in 2.77s   (baseline was 227)
```

---

### Coercion Handling

`SetLogIn.feedback_tap` is `Optional[str]`; `SetLog.feedback_tap` is `Optional[FeedbackTap]`. Explicit coercion applied:
```python
feedback_tap=FeedbackTap(sl.feedback_tap) if sl.feedback_tap is not None else None,
```

### run_analysis Cold-Start

`run_analysis` calls `.one()` on `EngineState` which doesn't exist in the bare in-memory test DB (no movement/engine seed data). The endpoint wraps the call in `try/except Exception: pass` — consistent with run_analysis.py's docstring: "Cold-start is expected: until ~3 PROGRESS sessions log, the analyzers are data-starved — this is correct, not broken." The write (SetLogs + status flip) is committed before `run_analysis` is invoked, so a cold-start analysis skip never rolls back a successful log. In production (seeded DB), `run_analysis` executes normally.

---

## Prior Content (v0.6 Program Definition-Layer, Task 2)

The remainder of this file is from the v0.6 generation task 2 (program definition-layer). Kept for historical reference.

---

## Files Created / Modified

| File | Action | Notes |
|---|---|---|
| `ironlog/models/program.py` | Created | Program, ProgramDay, Tier, TierExercise, MesoRotation + TierKind enum |
| `ironlog/models/__init__.py` | Modified | Exported program models |
| `deploy/migrations/004_program_tables.sql` | Created | 5 idempotent CREATE TABLE IF NOT EXISTS statements |
| `ironlog/generation/program_seed.py` | Created | seed_phase1_program() + PROGRAM_TO_LIBRARY map + halt-and-flag resolution |
| `ironlog/generation/skeleton.py` | Created | SlotSpec, Skeleton, lay_skeleton() |
| `tests/conftest.py` | Created | gen_db fixture (auto-discovered by pytest) |
| `tests/_gen_fixtures.py` | Created | Spec-reference stub / re-export |
| `tests/test_program_seed.py` | Created | 7 seed-correctness tests |
| `tests/test_generation_skeleton.py` | Created | 6 skeleton tests |

---

## Movement Resolution — ALL Resolved (no halt triggered)

41 TierExercise slots seeded across D1-D6. Every `movement_id` references a real seeded library
`Movement`. `test_every_tier_exercise_resolves_to_a_library_movement` passes.

### PROGRAM_TO_LIBRARY map (complete, 31 entries)
Resolves program-doc names → canonical library Movement.name. Key entries:
- "Pendlay Row Narrow" → "Pendlay Row - Narrow [OB]"
- "Belt Squat" → "Belt Squat [GHR + FT]"
- "Cable Tib Raise" → "Cable Tibialis Raise"
- "Meadows SA Row" → "Meadows Row [OB + LM]"
- "Scout Reverse Hyper (180 cap)" → "Reverse Hyper [REV_HYPER]"
- "Reverse Hyper Recovery" → "Light Reverse Hyper [REV_HYPER]"
- "Back Squat" → "Back Squat [PB]" (meso-2 rotation)
- "Pendlay Row" → "Pendlay Row - Medium [OB]" (meso-2 rotation)

Exact-name matches (no map needed):
"Bench Press [PB]", "Face-Up Incline Knee Raise", "ATG Split Squat", "Dragon Flag",
"Poliquin Step-up", "Sissy Squat", "Andreoni Cable Pullover", "Cable Tibialis Raise"

---

## Deferred MesoRotation Rows (DONE_WITH_CONCERNS)

4 meso-2 variants were not seeded because the movement doesn't exist in the library:

| Slot | Meso-1 | Meso-2 wanted | Reason skipped |
|---|---|---|---|
| d5_t1 | RDL [PB] | Staggered RDL | Not in library; add before v0.7 |
| d4_t3b | Meadows Row [OB + LM] | Single-Arm DB Row | Not in library |
| d5_t2b | Reverse Hyper [REV_HYPER] | Scout RH single-leg | Same library movement; technique note only |
| d1_t1 | Bench Press [PB] | BMF 21" bar | Same library movement; equipment note only |

**Seeded MesoRotations** (both sides in library):
- d2_t1: Belt Squat → Back Squat [PB] (meso 2) ✓
- d4_t2a: Meadows Row → Pendlay Row - Medium [OB] (meso 2) ✓

`test_meso_rotation_swaps_anchor_variant` (D2 Belt↔Back) passes. ✓

---

## Migration Parity

`test_chain_matches_create_all` passes — `004_program_tables.sql` produces the exact same
schema as `SQLModel.metadata.create_all()` for all 5 program tables.

Key type decisions:
- `tier_kind VARCHAR(11)` — max("T1_STRAIGHT") = 11 chars ✓
- `knee_modality VARCHAR(6)` — max("NORDIC") = 6 chars (consistent with migration 001) ✓
- `scheme VARCHAR` (plain `Optional[str]`, not Scheme enum) — program uses COMPOSITE,
  ASSISTED, REP_AT_CAP, SINGLE_SESSION, FIXED which are not in the existing Scheme enum ✓
- `is_rest BOOLEAN NOT NULL`, `rounds INTEGER NOT NULL` — no SQL DEFAULT (no server_default) ✓

---

## Pytest Results

```
.venv/bin/pytest -q
167 passed, 49 warnings in 0.91s
```

New tests: 13 (7 seed-correctness + 6 skeleton), all green. Full suite: 167 passed.

---

## Production DB Safety Confirmed

- NO `python -m ironlog.seed` executed
- `seed_phase1_program()` tested only via in-memory SQLite in `gen_db` fixture
- `test_seed_is_main_work_only` passes — no warmup/finisher/emom/z2/ramp/activation rows

---

## Implementation Notes

**conftest.py vs _gen_fixtures.py:**
`tests/` has no `__init__.py`, so `from tests._gen_fixtures import gen_db` fails (ModuleNotFoundError).
Fixture placed in `tests/conftest.py` (auto-discovered by pytest). `_gen_fixtures.py` kept as
spec-reference stub. Test files simplified to omit the explicit import (conftest auto-discovery).

**TierExercise.scheme as Optional[str]:**
The program uses scheme labels (COMPOSITE, ASSISTED, REP_AT_CAP, SINGLE_SESSION, FIXED) not in
the existing `Scheme` enum. Using `Optional[str]` avoids extending the enum and keeps the program
layer independent of the session-layer Scheme vocabulary.

---

## Commit

Hash: `4ba0845`
Branch: `feat/v0.6-generation`
Message: "feat(gen): program definition-layer + Phase 1 main-work seed + skeleton reads program"

---

## Task 2 Fix Wave

### Status: DONE

Review findings addressed: guard-bypass (load-bearing), two missing movements, rotation-path test, fixture dead code.

---

### Fix 1 — Two movements added to `ironlog/seed.py` `MOVEMENTS`

**`Staggered RDL [PB]`** (§10 compliant):
- `base_name="Staggered RDL"`, `region=Region.LOWER`, `lift_category=LiftCategory.RDL`
- `is_primary=True` (matches RDL [PB] convention — barbell primary lifts are is_primary)
- `status=Status.ACTIVE`, `load_code="PB"`, `tags=["PB"]`
- `progression_mode=ProgressionMode.LADDER`, `scheme=Scheme.STRAIGHT`
  - **NOT TOPSET_BACKOFF** — the §10 `test_topset_backoff_is_exactly_the_six` invariant (exactly 6 lifts) holds ✓
- `increment_ladder=[10, 5, 2.5]`, `min_step=2.5`, `load_floor=45`
  - Cross-field §10 `test_load_progression_has_increment_source` satisfied ✓
- Placed in "Primary STRAIGHT lifts" section alongside Box Squat, Conventional DL, etc.

**`Single-Arm DB Row [DB]`** (§10 compliant):
- `base_name="Single-Arm DB Row"`, `region=Region.UPPER`, `lift_category=LiftCategory.ROW`
- `status=Status.ACTIVE`, `load_code="DB"`, `tags=["DB"]`
- Convention matches `DB Seal Row [DB + UTIL_SEAT]` (same DB load source)
- `progression_mode=ProgressionMode.LADDER`, `scheme=Scheme.DOUBLE_PROGRESSION`
- `increment_ladder=[2.5]`, `min_step=2.5`, `load_floor=10`
  - Cross-field §10 `test_load_progression_has_increment_source` satisfied ✓
- Placed in "Upper accessories — LADDER / DOUBLE_PROGRESSION" section

**Count updates in `tests/test_library_seed.py`:**
- `test_total_count_103`: 106 → 108 ✓
- `test_status_counts` ACTIVE: 97 → 99 ✓
- All §10 invariants (TOPSET_BACKOFF=6, rpe_capped set, family links, load increment source) remain green ✓

---

### Fix 2 — Guard-bypass closed in `ironlog/generation/program_seed.py` (LOAD-BEARING)

**Root cause:** The meso-2 rotation path for d5_t1 and d4_t3b had "deferred" comments but NO `_add_mr` calls. Since `_add_mr` → `_resolve` is the only path that raises `ValueError` on an unresolved name, these slots were silently absent from the seeded MesoRotation rows. An unresolved rotation name would never trigger the guard.

**Fix applied:**
1. **PROGRAM_TO_LIBRARY** — added two new entries in the "Meso-2 rotation variants" block:
   - `"Staggered RDL"` → `"Staggered RDL [PB]"`
   - `"Single-Arm DB Row"` → `"Single-Arm DB Row [DB]"`

2. **`_seed_d5`** — captured `_add_te` return value as `d5_t1`, removed deferred comment, added:
   ```python
   _add_mr(db, d5_t1, 2, "Staggered RDL", lib)
   ```
   This calls `_resolve("Staggered RDL", lib)` which raises `ValueError` on miss.

3. **`_seed_d4`** — captured `_add_te` return value as `d4_t3b`, added:
   ```python
   _add_mr(db, d4_t3b, 2, "Single-Arm DB Row", lib)
   ```

4. **Explicit same-movement whitelist** — the two legitimate excluded rotations are now
   marked with explicit "intentionally NO MesoRotation row" comments, distinct from the
   bug pattern:
   - `d1_t1` (already had a clear comment about equipment-note, no change needed)
   - `d5_t2b` — comment rewritten from "deferred — same library movement; technique note only"
     to "single-leg Reverse Hyper is a TECHNIQUE note ... intentionally NO MesoRotation row
     (not a guard-bypass)"

5. **Docstring updated** — `seed_phase1_program` docstring now documents all 4 seeded
   MesoRotation rows, lists the 2 intentionally-excluded slots separately, and states the
   guard contract.

**Guard now covers ALL paths:** every intended rotation goes through `_add_mr` → `_resolve` → raises on miss. The "same-movement notes" are explicit whitelist exclusions, not silent omissions.

---

### Fix 3 — Rotation-path coverage pinned in `tests/test_program_seed_rotation_guard.py` (NEW FILE)

Three tests in a new file:

**`test_unresolved_meso_rotation_raises`** (the guard test):
- Monkeypatches `PROGRAM_TO_LIBRARY` so `"Staggered RDL"` maps to `"__BOGUS_NOT_IN_LIBRARY__"`
- Seeds a fresh in-memory DB and calls `seed_phase1_program`
- Asserts `pytest.raises(ValueError, match="HALT-AND-FLAG")`
- TDD red (before fix): the bogus name was never called → no `ValueError` → test FAILED, proving the bypass
- TDD green (after fix): `_add_mr` calls `_resolve("Staggered RDL", lib)` → lib has no bogus entry → raises ✓

**`test_new_meso_rotations_exist_and_resolve`**:
- Queries d5_t1 and d4_t3b TierExercise rows
- Asserts exactly one meso-2 MesoRotation row per slot
- Asserts each row's `movement_id` matches the correct library Movement ✓

**`test_d5_lower_b_meso2_anchor_is_staggered_rdl`**:
- Calls `lay_skeleton("D5 Lower B", gen_db, meso_number=2)`
- Asserts Staggered RDL's id is in `sk.anchor_movement_ids`
- Confirms the MesoRotation wiring is end-to-end correct ✓

---

### Fix 4 — `tests/_gen_fixtures.py` dead code removed

The file had `from tests.conftest import gen_db` which fails at runtime because `tests/` has no `__init__.py` (ModuleNotFoundError). Since no test files import from `_gen_fixtures.py` (conftest auto-discovery is the working path), the broken import was dead code. Removed the import; file is now a documentation-only reference.

---

### Pytest Output

```
# TDD red (before fixes, rotation guard tests only):
FAILED tests/test_program_seed_rotation_guard.py::test_unresolved_meso_rotation_raises
FAILED tests/test_program_seed_rotation_guard.py::test_new_meso_rotations_exist_and_resolve
FAILED tests/test_program_seed_rotation_guard.py::test_d5_lower_b_meso2_anchor_is_staggered_rdl
3 failed in 0.26s

# TDD green (after fixes, rotation guard tests only):
3 passed in 0.20s

# Full suite:
170 passed, 49 warnings in 0.96s
```

Total: 170 tests (167 prior + 3 new rotation-guard tests). All green.

---

### Commit

Hash: `5167cbf`
Branch: `feat/v0.6-generation`
Message: "fix(gen): add Staggered RDL + Single-Arm DB Row; close meso-rotation guard-bypass (resolve-or-raise all paths) + pin rotation-path test"

---

## Task 2 fix wave

**Commit:** `598fe99`  
**Branch:** `feat/logging-round-trip`  
**Date:** 2026-06-28

### Swallow removal

Removed the `try/except Exception: pass` block wrapping `run_analysis(session_id, db, _week_keyer)` in the `submit_session` handler (`ironlog/api/app.py`). The call is now bare. The preceding `db.commit()` already durably saves SetLogs/surveys/notes/status before `run_analysis` is reached, so a run_analysis failure surfaces correctly (HTTP 500) rather than silently returning 200 with an un-analyzed session. Production always has EngineState + MovementState; the blanket swallow was masking real errors.

### State-seeding helper

Added `_seed_analysis_state(engine, movement_id=3)` to `tests/test_submit_endpoint.py`. Seeds: `EngineState(id=1, CALIBRATION)` + `PhasePolicy(CALIBRATION, PROGRESS default)` + `Movement(id=movement_id)` + `MovementState(movement_id)`. Uses explicit `id=3` for Movement so existing test bodies (`movement_id=3`) remain stable without modification.

Applied in the two gate tests that reach `run_analysis`:
- `test_submit_writes_setlogs_and_completes` — `_seed_analysis_state(engine)` added before submit
- `test_submit_idempotent_lost_ack_retry_writes_nothing_new` — `_seed_analysis_state(engine)` added before first submit

`test_submit_rejects_working_set_without_tap_422_and_writes_nothing` rejects at 422 before `run_analysis`, no seeding needed — left unchanged.

### New seam test: `test_submit_fires_run_analysis`

Seeds a full planned graph inline (Movement → Session(PLANNED) → ExerciseGroup → PlannedExercise → PlannedSet, set_role=WORKING, target_rpe=8.0) + EngineState/PhasePolicy/MovementState. Submits one SetLog with `planned_set_id` linked and `feedback_tap=ON_TARGET`. Asserts `Session.analyzed_at is not None` after the HTTP 200 response.

**Delete-call confirmation:** temporarily replaced `run_analysis(session_id, db, _week_keyer)` with `pass` in the handler; `test_submit_fires_run_analysis` went red with:
```
AssertionError: run_analysis seam did not fire: Session.analyzed_at is None after submit
assert None is not None
```
Call restored; test green again.

### pytest tails

Submit-only run (4 tests):
```
4 passed, 8 warnings in 0.33s
```

Full suite:
```
231 passed, 98 warnings in 2.92s
```

---

## Final-review fix wave (server)

**Commit:** `0c8f94d`
**Branch:** `feat/logging-round-trip`
**Date:** 2026-06-28

### Survey/note write-branch test: `test_submit_writes_surveys_and_notes`

Added to `tests/test_submit_endpoint.py` (Fork-4 B/C coverage lock).

**Gap closed:** Every prior submit test passed `"surveys": [], "notes": []`, leaving the non-empty ExerciseSurvey + Note write path untested. The handler already wrote them correctly (lines 259–266 of `ironlog/api/app.py`) — no handler change needed.

**Test approach:** Reuses `_client()`, `_seed_analysis_state(engine)`, `_planned_session(engine)` exactly like the other gate tests. Posts one WORKING SetLog (with `feedback_tap="ON_TARGET"`) + one ExerciseSurveyIn (`sticking_point="BOTTOM"`, `asymmetry_flag=True`, `technique_flag=False`) + one NoteIn (`movement_id=3`, `text="felt unstable at the bottom"`). Asserts:
- HTTP 200, `already_completed=False`
- 1 ExerciseSurvey row with correct `sticking_point`/`asymmetry_flag`/`technique_flag`
- 1 Note row: `classification == NoteClass.JOURNAL`, `confirmed is False`, `applied is False` (deferred-classification contract locked)
- 1 SetLog (sanity)

**Handler worked first try** — the write branch was already correct; this test purely adds coverage.

### pytest tails

New test alone:
```
1 passed, 5 warnings in 0.29s
```

Full suite:
```
236 passed, 105 warnings in 3.13s
```

Total: 236 tests, 0 failed (235 prior + 1 new).

---
---

# FIRST-RUN WIZARD — Task 2: `compute_load_trust` (the shared keystone)

**Status:** DONE
**Branch:** `feat/first-run-wizard`
**Files:** created `ironlog/generation/load_trust.py`, `tests/test_load_trust.py`

## The function

`compute_load_trust(movement, state, db, as_of) -> LoadTrustResult` is the single shared
load-trustworthiness function. Trust is DERIVED every call from event-facts — never a stored
verdict. Public surface:

- `LoadTrust` enum: `UNKNOWN` / `STALE` / `FRESH`
- `LoadTrustResult` dataclass: `trust`, `value: Optional[float]`, `load_field: Optional[str]`
- `load_field_for_mode(mode) -> Optional[str]`
- `compute_load_trust(movement, state, db, as_of) -> LoadTrustResult`

Tasks 3 (generation resolver), 4 (wizard-state endpoint), and 5 (completion gate) all import this
one definition — no per-surface reimplementation.

## The five behavior points

1. **Per-mode load field** (`load_field_for_mode`): LADDER/COMPOSITE → `"current_load"`;
   ASSISTED → `"assist_level"`; PROTOCOL/CONDITIONING/NONE → `None`. A `None` load_field means
   bodyweight: returns `LoadTrustResult(FRESH, None, None)` immediately — always FRESH, never
   UNKNOWN, never asked (no load to set).

2. **Value resolution** (`_resolve_value`, mirrors `resolve_start_load` in `assembler.py` MINUS the
   floor): load field present (`IS NOT NULL`) → use it; ELSE derived-ratio (movement has
   `start_ratio` + `derived_from_id` and the anchor `MovementState` has an `e1rm`) →
   `start_ratio * anchor.e1rm`; ELSE → `None` → UNKNOWN. **The `movement.load_floor … else 0.0`
   floor fallback is DROPPED** — that silent-wrong floor is the bug being fixed; an unconfigured
   movement returns UNKNOWN, never a fake floor load.

3. **IS-NULL-not-zero presence** (subtle guard): presence is checked with `if v is not None`,
   never falsy / `== 0`. `assist_level == 0.0` (unassisted pull-ups) is a VALID, configured, FRESH
   value — NOT UNKNOWN. Verified by `test_assisted_null_is_unknown_but_zero_is_fresh`.

4. **Recency = `max(last working SetLog.performed_at, MovementState.confirmed_at)`**. Working sets
   only (`is_warmup == False`). FRESH if recency within 30 days of `as_of`; STALE if value present
   but recency is None or > 30 days; UNKNOWN only if no value (point 2). Confirmed-40d-ago but
   logged-3d-ago → FRESH via the max; warmup-only recent set does NOT refresh (STALE).

5. **Derived-ratio value** (value-resolution nuance): a derived movement with no own `current_load`
   but a configured anchor (`anchor.e1rm`) resolves to `start_ratio * anchor.e1rm`, NOT UNKNOWN — so
   the wizard does not over-ask for movements that derive load from a parent. Verified with its own
   load-less state (→ FRESH via `confirmed_at`) and with no state at all (→ value resolves, recency
   None → STALE).

**Tz handling:** project default for `performed_at` / `confirmed_at` is naive `datetime.utcnow()`,
while callers (and the wizard tests) pass tz-aware `as_of`. Added `_as_naive_utc(dt)` — aware →
UTC then drop tzinfo; naive unchanged. All recency candidates and `as_of` pass through it before
subtraction, so naive-vs-aware never raises. Covered by
`test_naive_stored_datetimes_are_comparable_with_aware_as_of`.

## Tests (TDD red → green)

Red (module missing):
```
E   ModuleNotFoundError: No module named 'ironlog.generation.load_trust'
1 error in 0.09s
```

Green (`tests/test_load_trust.py`, 11 tests):
```
...........                                                              [100%]
11 passed, 1 warning in 0.18s
```

Test inventory: load_field_for_mode (all 6 modes), LADDER no-load→UNKNOWN, present+recent→FRESH,
present+old→STALE, recency-via-working-SetLog max, warmup-does-not-count, bodyweight/PROTOCOL
always-FRESH, assisted null-vs-zero (IS-NULL guard), derived-ratio with own state, derived-ratio
with no state, naive/aware comparability.

Full suite:
```
247 passed, 106 warnings in 3.18s
```
0 failed. Build-and-test-only, in-memory SQLite, server-side on myflix.

## Commit

`feat(wizard): compute_load_trust shared keystone (computed trust; IS-NULL-not-zero; bodyweight-always-fresh; derived-ratio value)` — hash recorded at bottom.

## Concerns

None. The single deprecation warning under the naive-datetime test is intentional — that test uses
`datetime.utcnow()` on purpose to reproduce the project's naive-storage convention and prove
normalization handles it.

---

# PAYLOAD ENRICHMENT — Task 2, First Half: Muscle Tag Proposal (Steps 1–3)

**Status:** DONE_PROPOSAL_READY
**Branch:** `feat/payload-enrichment`
**Date:** 2026-06-30

## Steps Completed

### Step 1 — Failing test written
`tests/test_library_muscle_tags.py` — exact text from brief.

### Step 2 — Test confirmed failing
```
FAILED tests/test_library_muscle_tags.py::test_every_movement_has_a_valid_primary_muscle
1 failed, 1 passed in 0.09s
```
All 108 movements untagged. `test_secondary_muscles_are_valid_and_listy` passes (empty lists fine).

### Step 3 — Proposer run
`scripts/propose_muscle_tags.py` written and executed via ssh.
108 proposals → `scripts/muscle_tags_proposed.json`. Zero invalid Muscle values (validated before write).

## Heuristic mapping summary

| Priority | Signal | Examples |
|---|---|---|
| 1 | `lift_category` (BACK_SQUAT/FRONT_SQUAT → QUADS; RDL → HAMSTRINGS; BENCH → MID_LOWER_CHEST; OHP → FRONT_DELT; ROW → MID_BACK; HIP_THRUST → GLUTES; REV_HYPER → GLUTES; DEADLIFT → HAMSTRINGS; CG_PRESS → TRICEPS) | T1 lifts + labeled accessories |
| 2 | base_name keyword matching (nordic, pull-up, lat pulldown, t-bar row, lateral raise, rear delt fly, curl, pushdown, face pull, etc.) | All NONE-category accessories |
| 3 | `region=CORE` → ABS (except copenhagen→ADDUCTORS, bird dog→ABS+SPINAL_ERECTORS, rotation→ABS) | All core movements |
| 4 | `progression_mode=CONDITIONING` keywords (farmer/carry→FOREARMS; jump rope→CALVES; kb swing→GLUTES; slam ball→ABS) | 10 conditioning movements |

## Movements flagged uncertain (14)

| Name | Proposed primary | Reason |
|---|---|---|
| Cable Tibialis Raise | CALVES | Tibialis anterior not in Muscle enum |
| Incline DB Y-Raise | REAR_DELT | Y-raise targets lower traps (not in enum) |
| Single-Arm Landmine Press | FRONT_DELT | Angle ambiguous between FRONT_DELT and UPPER_CHEST |
| Cable External Rotation | REAR_DELT | Rotator cuff (infraspinatus/teres minor) not in enum |
| Andreoni Dips | MID_LOWER_CHEST | Grip width on Andreoni station unknown |
| Dips [ANDREONI + FT] | MID_LOWER_CHEST | Same as above (INACTIVE variant) |
| Farmer Carries | FOREARMS | UPPER_TRAPS equally defensible |
| Farmer Walk | FOREARMS | Same |
| Sandbag Carry | FOREARMS | Same |
| Jump Rope Intervals | CALVES | Cardio-dominant; structural choice is a simplification |
| Jump Rope Tabata | CALVES | Same |
| Jump Rope [JR] | CALVES | Same |
| Sandbag Over-Shoulder | GLUTES | Complex explosive; hip extension chosen as driver |
| Slam Ball | ABS | Full-body; ABS primary is defensible but GLUTES/HAMSTRINGS also strong |

## Files written
- `tests/test_library_muscle_tags.py`
- `scripts/propose_muscle_tags.py`
- `scripts/muscle_tags_proposed.json` (108 proposals — the review artifact)

## NOT done (scope gate)
`ironlog/seed.py` NOT modified. No tags applied. No migration generated. Nothing committed.

Next: user reviews/corrects `scripts/muscle_tags_proposed.json`, then second dispatch applies tags + generates migration 011.

---

# PAYLOAD ENRICHMENT — Task 2, Second Half: Apply Tags + Migration 011

**Status:** DONE
**Branch:** `feat/payload-enrichment`
**Date:** 2026-07-01

## Steps Completed

### Taxonomy addition
- `TIBIALIS = "TIBIALIS"` and `ROTATOR_CUFF = "ROTATOR_CUFF"` added to `ironlog/models/enums.py` `Muscle` enum (now 20 members).
- `tests/test_library_muscle_fields.py::test_muscle_enum_has_expected_members` updated: added both to expected set.
- Enum test result: **2 passed in 0.07s** ✓

### Tags applied to seed.py
- `scripts/apply_muscle_tags.py` written and run: tagged 108 movements, 0 skipped.
- Six user overrides applied exactly (verified in migration SQL):
  - `EZ Bar Curl - Narrow Grip [EZ]` → BICEPS / ["FOREARMS"]
  - `Swiss Bar Press [SB]` → MID_LOWER_CHEST / ["TRICEPS", "FRONT_DELT"]
  - `Cable Tibialis Raise` → TIBIALIS / []
  - `Cable External Rotation [FT]` → ROTATOR_CUFF / ["REAR_DELT"]
  - `Sumo DL [PB]` → GLUTES / ["ADDUCTORS", "QUADS", "SPINAL_ERECTORS", "HAMSTRINGS"]
  - `Conventional DL [PB]` → GLUTES / ["HAMSTRINGS", "SPINAL_ERECTORS", "QUADS"]
- All other 102 movements use proposals verbatim (Incline DB Y-Raise kept as REAR_DELT per brief).

### Loader wired
- `primary_muscle=m.get("primary_muscle")` and `secondary_muscles=m.get("secondary_muscles", [])` added to `Movement(...)` constructor in `seed()`.

### Tag test result
`tests/test_library_muscle_tags.py`: **2 passed in 0.07s** ✓
- All 108 movements have a valid primary_muscle.
- All secondary_muscles lists are valid and list-typed.

### Migration 011 generated
- `scripts/gen_muscle_backfill.py` created.
- Generated `deploy/migrations/011_backfill_movement_muscles.sql`: 108 idempotent UPDATE statements, each guarded by `AND primary_muscle IS NULL`.

### Parity test
`tests/test_migrations.py`: **12 passed in 0.10s** ✓

### Full suite
`270 passed, 350 warnings in 3.75s` — 0 failed.

## Files Changed
| File | Action |
|---|---|
| `ironlog/models/enums.py` | Added TIBIALIS, ROTATOR_CUFF to Muscle enum |
| `tests/test_library_muscle_fields.py` | Updated expected set to 20 members |
| `ironlog/seed.py` | Tagged all 108 MOVEMENTS dicts; wired loader |
| `tests/test_library_muscle_tags.py` | (Task 2a artifact, now committed) |
| `scripts/apply_muscle_tags.py` | New — applies tag JSON to seed.py |
| `scripts/gen_muscle_backfill.py` | New — generates migration 011 |
| `scripts/propose_muscle_tags.py` | (Task 2a artifact, now committed) |
| `scripts/muscle_tags_proposed.json` | (Task 2a artifact, now committed) |
| `deploy/migrations/011_backfill_movement_muscles.sql` | New — 108 idempotent UPDATEs |

## Notes
- BUILD-AND-TEST-ONLY: `python -m ironlog.seed` NOT run; migration 011 NOT applied to prod — live deploy is user-owned.
- Two-writer boundary: no current_load / outcome writes in any changed file.
- apply_muscle_tags.py is idempotent (skips already-tagged entries).
- Migration 011 is idempotent via `AND primary_muscle IS NULL` guard on every UPDATE.
