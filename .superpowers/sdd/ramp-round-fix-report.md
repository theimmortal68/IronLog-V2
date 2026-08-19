# Ramp Set Round-Up-to-5 Fix — Completion Report

## Objective
Athlete directive: warmup ramp sets on heavy-anchor lifts (Bench Press, Belt
Squat, Back Squat, RDL, Staggered RDL, Seated BTN OHP) must always round UP
to the nearest 5lb, regardless of the movement's own load increment (e.g.
Bench Press's own working-set step is 2.5lb, but its ramp sets must snap to
a clean 5lb number, never down and never to a 2.5lb fraction).

## What I implemented

1. **`ironlog/engine/loading.py`**: added `round_up_to_step(target, floor,
   step)` — ceiling-rounds `target` up to the nearest multiple of `step`
   (via `math.ceil`), still clamped to `floor` as a lower bound (same
   floor-clamp behavior as the existing `round_to_achievable`, just ceiling
   instead of nearest for the rounding itself). `math` imported.
   Also exported it from `ironlog/engine/__init__.py` alongside the other
   loading helpers, for consistency with `round_to_achievable`.

2. **`ironlog/generation/assembler.py`** (`_build_exercise`'s ramp-set
   construction block, ~line 459-478):
   - Imported `round_up_to_step` alongside the existing
     `clamp_to_cap, round_to_achievable` import from `..engine.loading`.
   - Changed `target_load=round_to_achievable(load * pct, floor, step)` to
     `target_load=round_up_to_step(load * pct, floor, 5)` — note the
     HARDCODED `5`, not the movement's own `step` variable. `floor` is left
     as-is (the movement's own floor still applies as a lower bound; only
     the step/rounding-direction changed).
   - Working sets (`_sets_for_scheme`, called just above, using
     `round_to_achievable(base, floor, step)` at line 447) are completely
     untouched — the new function is used ONLY inside the
     `is_anchor and movement.ramp_eligible` ramp-set list comprehension.

## Scope check (item 3 of the request)

Grepped the whole codebase for `SetRole.RAMP`, `ramp_eligible`,
`RAMP_ELIGIBLE`:
- `ironlog/generation/program_seed.py`, `live_seed_ramp_and_finishers.py`,
  `ironlog/models/library.py` — all just mark/seed the `ramp_eligible` flag
  or the movement-name allowlist. No rounding logic there.
- `tests/test_capture_skip_swap.py` — one PlannedSet fixture with
  `set_role=SetRole.RAMP, target_load=60.0` (a hardcoded literal, not
  derived from any rounding function) used to test a swap-endpoint 409
  rejection path. Unaffected by this change — not touched.
- No client-facing code or other test asserts a specific ramp-set rounding
  formula outside `assembler.py` / `tests/test_ramp_sets.py` /
  `tests/test_loading.py`. No coordinated fix needed elsewhere.

## What I tested

### 1. `tests/test_loading.py` — new unit tests for `round_up_to_step`
- `test_round_up_to_step_exact_multiple_stays_same`: `round_up_to_step(50.0,
  None, 5) == 50.0` — an exact multiple must NOT round up further.
- `test_round_up_to_step_between_multiples_rounds_up_never_down`:
  `round_up_to_step(92.0, None, 5) == 95.0` (never 90) and
  `round_up_to_step(90.1, None, 5) == 95.0` (even a hair over a multiple
  jumps to the NEXT multiple, not back down to it) — rules out
  nearest-rounding disguised as round-up.
- `test_round_up_to_step_respects_floor`: `round_up_to_step(3.0, 10, 5) ==
  10` — floor still wins over a ceiling result below it.

### 2. `tests/test_ramp_sets.py` — integration proof
- Updated `test_d2_belt_squat_anchor_gets_three_ramp_sets_before_working_sets`
  to compute its `expected_loads` via `round_up_to_step(working_load * pct,
  belt.load_floor, 5)` (hardcoded step 5) instead of the old
  `round_to_achievable(..., belt.min_step)` — matching what the code now
  actually does.
- **New: `test_bench_ramp_sets_round_up_to_5_not_movement_own_step`** — the
  test that actually proves round-UP, not round-to-nearest, with a value
  that can't pass by coincidence:
  - Overrides Bench Press [PB]'s `MovementState.current_load` to 185.0
    (bench's own `min_step` is 2.5, `load_floor` is 45).
  - `185 * 0.6 = 111` → OLD nearest-2.5 behavior: 110. NEW round-up-to-5: 115.
  - `185 * 0.8 = 148` → OLD nearest-2.5 behavior: 147.5. NEW round-up-to-5: 150.
  - The test explicitly computes and asserts the OLD-behavior values first
    (`old_behavior_loads == [75.0, 110.0, 147.5]`) as a sanity check, then
    asserts the actual ramp `target_load`s equal the NEW values
    (`[75.0, 115.0, 150.0]`), and asserts `expected_loads != old_behavior_loads`
    — so this test cannot pass on a value that happens to agree with both
    the old and new rounding rules.

### Test run (RED → GREEN)
Before writing the new bench test, I ran it mentally against the OLD
formula's numbers (110/147.5) to confirm they diverge from the NEW formula
(115/150) — chose 185.0 specifically because the default `gen_db_calibrated`
current_load of 100.0 produces `100*0.4/0.6/0.8 = 40/60/80`, all already
exact multiples of 5, which would make old vs. new rounding numerically
indistinguishable (a "lucky value" trap the task called out explicitly to
avoid).

Full suite, from the worktree using the main checkout's venv:
```
cd /home/jstout/projects/IronLog-V2-wt-rampround
~/projects/IronLog-V2/.venv/bin/python -m pytest -q
```
Result: **730 passed** (baseline was 726; +4 new tests: 3 in
`test_loading.py`, 1 in `test_ramp_sets.py`). Zero failures, zero skips.

Targeted run before the full suite:
```
~/projects/IronLog-V2-wt-rampround/.venv... (n/a, worktree has no venv)
~/projects/IronLog-V2/.venv/bin/python -m pytest -q tests/test_loading.py tests/test_ramp_sets.py
```
Result: 12 passed.

## Files changed
- `ironlog/engine/loading.py` — added `round_up_to_step`, `import math`
- `ironlog/engine/__init__.py` — exported `round_up_to_step`
- `ironlog/generation/assembler.py` — import + ramp-set-block change
- `tests/test_loading.py` — 3 new unit tests
- `tests/test_ramp_sets.py` — updated belt-squat test's expected-value
  formula to match the new implementation; added the bench-press
  round-up-vs-nearest proof test

## Self-review

- **Completeness**: `round_up_to_step` is used ONLY in the ramp-set list
  comprehension in `assembler.py`; the working-set load resolution at line
  447 (`clamp_to_cap(round_to_achievable(base, floor, step), movement.cap)`)
  is untouched, still uses the movement's own `step`/`floor` and nearest
  rounding. Verified by re-reading the diff.
- **Quality**: floor-clamping behavior mirrors the existing
  `round_to_achievable` pattern exactly (same shape, same semantics, just
  `math.ceil` instead of `round`) — no divergent style introduced.
- **Discipline**: touched only `ironlog/engine/loading.py`,
  `ironlog/engine/__init__.py`, `ironlog/generation/assembler.py`, and
  their two test files. No client code, no other movement/scheme logic
  touched.
- **Testing rigor**: the bench test deliberately picks a working load
  (185) chosen specifically so the old and new formulas diverge on two of
  the three ramp percentages, and asserts both the old-formula value and
  the new-formula value explicitly, with an inequality assertion between
  them — this is not a test that could pass under the old nearest-rounding
  code path.

## Concerns
None. No files outside the described scope needed changes; no
client-facing or other-test rounding assumptions were found.
