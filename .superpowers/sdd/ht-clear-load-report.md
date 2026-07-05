# HT scalar target_load clear — report

## Objective
Fix HT (band-composite) sets showing a confusing scalar "Target: Nlb" alongside the
real plates+bands prescription. `assembler.py`'s HT block set `target_plates`,
`band_config`, and `target_felt_peak` but left the earlier-resolved scalar
`target_load` populated (from `_resolve_load`/`current_load`).

## Fix
`ironlog/generation/assembler.py`, in `_build_exercise`'s HT block
(`for ps in sets: ...`), added `ps.target_load = None` alongside the existing
`target_plates` / `band_config` / `target_felt_peak` assignments. HT sets now
carry plates+bands+felt-peak and NO scalar target_load.

## TDD
1. Extended `tests/test_ht_composite_wiring.py::test_assembled_ht_carries_plates_and_config`
   with `assert ht_set.target_load is None`. Confirmed it failed pre-fix
   (`assert 100.0 is None`).
2. Applied the one-line fix. Test file passes (8/8).

## Downstream check (no regression)
- `ironlog/engine/validator.py` floor/cap checks guard on
  `ps.target_load is not None` before comparing — HT sets with `target_load=None`
  are already null-safe and cannot trigger a false LOAD_BELOW_FLOOR/LOAD_OVER_CAP.
- `ironlog/generation/repair.py` only writes `ps.target_load = corrected_value` in
  response to those same guarded violations — won't fire for HT sets.
- Capture/submit path (`ironlog/api/app.py`, `schemas_capture.py`) reads
  `felt_peak`/`target_felt_peak` for HT, not `target_load` — unaffected.
- No test in the suite asserted a *non-null* `target_load` for an HT/HIP_THRUST/
  COMPOSITE movement except the one found and fixed below.

## Existing test updated (encoded the buggy behavior)
`tests/test_generation_fallback.py::test_gate_c_all_five_day_roles` looped over
every set of every "loaded" movement (`load_field_for_mode(mode) is not None`,
true for LADDER/COMPOSITE/ASSISTED) and asserted `target_load is not None`. Since
HIP_THRUST/COMPOSITE resolve `load_field_for_mode -> "current_load"`, this
unconditionally required a non-null scalar `target_load` even for HT sets —
i.e. it encoded the bug. Failed for `D2 Lower A`, `D5 Lower B`, `D6 Weak Points`
(the day roles containing the Hip Thrust movement) once the fix cleared
`target_load`.

Updated (not weakened): added an explicit HT branch (mirrors
`assembler._is_ht_movement`: `lift_category == HIP_THRUST or progression_mode ==
COMPOSITE`) that asserts `target_load is None` AND `target_plates is not None`
for HT sets — i.e. HT sets must still carry a real load, just via plates/bands
instead of the scalar. Non-HT loaded movements still require `target_load is
not None` as before.

## Test results
- Targeted: `tests/test_ht_composite_wiring.py` — 8 passed.
- Full suite: **433 passed** (same count as baseline — one existing test was
  extended with an additional assertion, not added as a new test function).

## Commit
`fix(assembler): clear scalar target_load on HT sets (plates+bands only)`
on branch `fix/ht-clear-scalar-load`.

## Concerns
None outstanding. The only ripple was the one test identified and fixed above;
no other test or downstream code path assumed a non-null `target_load` on HT
sets.
