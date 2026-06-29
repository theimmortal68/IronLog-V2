# Task 5 Report — wizard-resolve (write) + start (gate) + the spine test

**Status:** DONE
**Branch:** `feat/first-run-wizard`
**Commit:** `4c11e53` — `feat(wizard): POST wizard-resolve (confirmed_at only-on-touched) + start (gate + activate); spine can't-disagree test`
**Full suite:** 261 passed, 0 failed (was ~255 + 6 new).

This is the THIRD and final consumer of the `compute_load_trust` keystone — the wizard's WRITE + completion-gate surfaces. The server phase is now built-and-tested-stable.

## What was built

### DTOs (`ironlog/api/schemas_wizard.py`)
Mirror spec §5: `WizardResolution(movement_id:int, value:float)`, `WizardResolveRequest(resolutions:List[WizardResolution])`, `WizardResolveResponse(resolved:int, needs_attention_count:int, ready_to_start:bool)`, `StartProgramResponse(program_id:int, started:bool, active:bool)`.

### Shared enumeration/gate helpers (`ironlog/api/app.py`)
Extracted `_program_movement_ids(program_id, db)` (TierExercises + MesoRotations across the program's days/tiers) and `_needs_attention_count(program_id, db, now)` (UNKNOWN+STALE over load-bearing movements via the SHARED `compute_load_trust`, bodyweight excluded). `get_wizard_state` was refactored to use the same enumeration helper, so the read surface, the write surface, and the gate all derive from one function — they cannot diverge.

### `POST /programs/{program_id}/wizard-resolve`
Per-mode write: `load_field_for_mode(movement.progression_mode)` decides `current_load` (LADDER/COMPOSITE) vs `assist_level` (ASSISTED); `setattr(state, field, value)` + `state.confirmed_at = now`. Get-or-create the MovementState row per resolved movement (preserve existing fields if present). **§7.3 honesty pin:** iterates `req.resolutions` ONLY — never loops over all program movements — so confirmed_at is stamped strictly on touched movements. Bodyweight resolutions (load_field None) are skipped. Recomputes `needs_attention_count` after commit. 404 if program or a resolution's movement is missing.

### `POST /programs/{program_id}/start`
Gate: if `_needs_attention_count > 0` → `started=false, active=false` (refuse, no writes). If ready → singleton get-or-create `EngineState(id=1)`, set `active_program_id = program_id`, stamp `Program.started_at = now`, return `started=true, active=true`. 404 if program missing.

### Two-writer adherence
The resolve endpoint writes ONLY the canonical load field + `confirmed_at`. It never touches `e1rm`, `calibration_status`, `current_increment_tier`, or the ceiling/failure counters — proven by `test_resolve_preserves_other_movementstate_fields` (pre-seeds those fields, asserts all unchanged after a resolve that sets current_load).

## Tests (`tests/test_wizard_resolve_and_start.py`, 6 tests, all green)

- **`test_spine_wizard_finish_guarantees_clean_generation` (THE SPINE, §7.2+§7.6):** seeds a LADDER + ASSISTED + bodyweight(PROTOCOL) program. Before resolve: wizard-state `ready_to_start=false`, needs=2 (bodyweight excluded), `/start` refuses. Resolves both load-bearing movements. After: (a) wizard-state reports `ready_to_start=true`/needs=0; (b) `/start` returns `{started:true, active:true}`; (c) for each load-bearing movement it asserts `wizard_state.trust == compute_load_trust(...).trust == FRESH` AND `resolve_start_load(...) is not None` (generation prescribes a real number, not needs-calibration). It proves the wizard surface verdict EQUALS the generation resolver verdict because they call the same function — they can't disagree. Bodyweight asserted FRESH (legit no-load), never blocking.
- **`test_resolve_stamps_confirmed_at_only_on_touched` (§7.3):** resolve A (UNKNOWN→185) stamps A.confirmed_at + writes A.current_load; untouched FRESH B's confirmed_at stays at its sentinel (now−5d) and its load unchanged. Catches stamp-everything.
- **`test_resolve_assisted_writes_assist_level_not_current_load`:** ASSISTED resolve writes `assist_level=20`, leaves `current_load=None`, stamps confirmed_at.
- **`test_resolve_preserves_other_movementstate_fields`:** two-writer boundary (above).
- **`test_start_gate_refuses_then_activates`:** UNKNOWN program → `/start` refuses, no active pointer / started_at; resolve → `/start` activates (EngineState.active_program_id==pid, started_at set).
- **`test_start_404_when_program_missing`:** both endpoints 404 on a missing program.

## pytest tails
```
# new file (red → green):
5 failed, 1 passed   (red, before implementation)
6 passed, 23 warnings in 0.41s   (green)
# full suite:
261 passed, 333 warnings in 3.63s
```

## Notes / concerns
- `import compute_load_trust` / `load_field_for_mode` from `ironlog/generation/load_trust.py` — trust is NOT reimplemented anywhere; the spine test enforces this by comparing surfaces.
- Added a module-level `from datetime import datetime` to app.py (the new `_needs_attention_count` annotation evaluates at module load; project has no `from __future__ import annotations`).
- Build-and-test-only, in-memory; prod DB NOT reseeded/touched. Schema columns (confirmed_at, active_program_id, started_at) already existed on the models from prior tasks.
- Deprecation warnings are pre-existing `datetime.utcnow()` usage repo-wide; not in scope.

## Task 5 spine-test tighten

**Status:** DONE — spine test is now RED-against-reimplementation (was the weak "agreement-on-trivially-FRESH-inputs" form).
**Branch:** `feat/first-run-wizard`
**Scope:** test-only tightening; production code unchanged (the red-demo flip was applied then restored — `git diff ironlog/` is empty).

### The problem
The old `test_spine_wizard_finish_guarantees_clean_generation` compared the three surfaces (wizard-state endpoint, `compute_load_trust`, generation's `resolve_start_load`) only on just-resolved, value-present, trivially-FRESH movements (LADDER current_load=145, ASSISTED assist_level=10). Any naive reimplementation (falsy `if value:` presence check, missing derived-ratio path, old floor fallback) would ALSO agree on those simple cases → green-but-can't-go-red.

### Edges added (the divergence edges)
1. **ASSISTED `assist_level = 0` (IS-NULL-not-falsy):** new movement resolved via wizard to `0.0`. `compute_load_trust` returns FRESH (0.0 is a real value via the IS-NOT-NULL check in `_resolve_value`). A falsy `(value is not None and value)` presence check mis-handles it as UNKNOWN.
2. **Derived-ratio movement:** new LADDER movement with `start_ratio=0.8` + `derived_from_id=anchor`, own `current_load=None`, anchor `MovementState.e1rm=200.0`, recent `confirmed_at` → `compute_load_trust` resolves `0.8*200 = 160.0` → FRESH (not UNKNOWN). A reimpl missing the derived-ratio path calls it UNKNOWN.

### Cross-surface assertions (now span the edges)
For every load-bearing movement `[ladder, assisted, assisted_zero, derived]`:
- `wiz_trust[mid] == compute_load_trust(...).trust.value == LoadTrust.FRESH.value` (wizard-state endpoint verdict == generation keystone verdict).
- `resolve_start_load(mv, st, db) is not None` (load-bearing: assisted_zero resolves to `0.0` — falsy but valid; `is not None`, never a bare truthy assert).
- Derived specifically: `resolve_start_load(derived, ...) == 160.0` (the derived value, not None).
- Pre-resolve `needs_attention_count == 3` (ladder/assisted/assisted_zero UNKNOWN; derived already FRESH via derived path; bodyweight excluded).

### RED-against-naive-reimpl confirmation
**Surface flipped:** the wizard-state endpoint `get_wizard_state` in `ironlog/api/app.py` — replaced the shared `compute_load_trust(...)` call with a naive inline reimpl that resolves the value (incl. the derived path) but applies the **falsy** presence rule `LoadTrust.FRESH if (val is not None and val) else LoadTrust.UNKNOWN` (the `and val` mis-handles `assist_level == 0.0`).

Running ONLY the spine test against the flip → **RED**:
```
        state1 = client.get(f"/programs/{pid}/wizard-state").json()
>       assert state1["needs_attention_count"] == 0
E       assert 1 == 0
tests/test_wizard_resolve_and_start.py:297: AssertionError
1 failed, 7 warnings in 0.36s
```
The naive wizard-state surface reported `assist_level=0.0` as UNKNOWN (needs=1) while the real `compute_load_trust` (used by the resolve response + generation) says FRESH (needs=0) → the surfaces diverged → test caught it. The derived-ratio edge is independently protected by the cross-surface equality + `== 160.0` assertions (a no-derived-path reimpl diverges there).

**Production restored** to the shared `compute_load_trust` call; `git diff ironlog/` empty.

### pytest tails (real, shared code)
```
# spine test alone:
1 passed, 18 warnings in 0.27s
# full suite:
261 passed, 339 warnings in 3.64s
```
Count: **261 passed, 0 failed** (no new test functions; the spine test was tightened in place, +62/-13 lines). NO `from __future__ import annotations`; build-and-test-only, in-memory; prod DB untouched.
