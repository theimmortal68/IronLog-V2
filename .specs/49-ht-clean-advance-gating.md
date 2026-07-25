# Spec 49: HT Clean-Advance Gating

## Objective

HT's plates advance is currently computed and applied unconditionally at generation/approval time, with zero performance gating (unlike every other progression rule). Gate it on a clean session (all 3 working sets hit 8 reps, RPE<=8) the same way scalar loads already work via `pending_load_delta` — staged at analysis time, consumed once at the next generation, cleared after.

Design doc (approved, source of truth): `docs/superpowers/specs/2026-07-24-ht-clean-advance-and-unification-design.md`, "Fix 1".

**Correction to the design doc**: it states Fix 1 needs "No schema change." That's wrong — staging `pending_ht_plates`/`pending_ht_band_config` on `MovementState` (mirroring the real, persisted `pending_load_delta` column) requires two new columns. **This spec DOES touch schema — HUMAN GATE required at dispatch and merge**, same as Fix 2.

## File Targets

- `ironlog/models/library.py` — add `MovementState.pending_ht_plates: Optional[float]`, `MovementState.pending_ht_band_config: Optional[list]`.
- `deploy/migrations/037_ht_pending_advance.sql` (confirmed next-available number; match this repo's established migration-file header/comment format — check `deploy/migrations/036_cardio_log.sql` for the exact template) — additive `ADD COLUMN` for both new fields (both additive schema, may share one file per the README's carve-out).
- `ironlog/engine/advance.py` — new `AdvanceResult.earned_ht_plates`/`earned_ht_band_config` fields; `_rule_driven`'s HT branch replaced with real clean-gated logic; `advance()`'s own signature gains a `band_inventory` param, threaded ONLY into `_rule_driven`'s call.
- `ironlog/engine/analysis.py` — `MovementDelta` gains `pending_ht_plates: Optional[float] = None`, `pending_ht_band_config: Optional[list] = None`.
- `ironlog/persistence/run_analysis.py` — load `band_inventory` once per call (mirroring `assembler.py`'s construction); pass it into the `advance()` call; stage `d.pending_ht_plates`/`d.pending_ht_band_config` from the result.
- `ironlog/persistence/apply.py` — `apply_analysis` writes `state.pending_ht_plates`/`state.pending_ht_band_config` from the delta (staging only — never `ht_plates` itself, Option-C stays intact).
- `ironlog/generation/assembler.py` — `_build_exercise`'s HT block stops calling `ht_next_setup` unconditionally; uses `state.pending_ht_plates`/`state.pending_ht_band_config` as the staged "next" if present, else holds at current (no advance).
- `ironlog/generation/loop.py` — `commit_session` additionally clears `state.pending_ht_plates`/`state.pending_ht_band_config` after applying `prospective_ht` (apply-once, mirroring `pending_load_delta`'s clear at line ~106-110).
- `tests/test_advance_ht_gating.py` — new file, unit tests for the new `_rule_driven` HT logic.
- `tests/test_run_analysis_ht_gating.py` — new file, integration tests through `run_analysis`/`apply_analysis`.
- `tests/test_ht_commit_gating.py` — new file (or extend `tests/test_ht_assembler_reconciliation.py` if that's a better fit — check its current scope first), integration tests through `assemble()`/`commit_session`.

## Changes

### `ironlog/models/library.py` — `MovementState`

Add two new columns, placed near the existing HT fields (`ht_plates`, `ht_band_config`, `ht_felt_peak`):

```python
pending_ht_plates: Optional[float] = None
pending_ht_band_config: Optional[list] = Field(default=None, sa_column=Column(JSON))
```

Match the exact `Column(JSON)` pattern already used for `ht_band_config`/`stall_signal` on the same model — read the existing field declarations directly, don't guess the SQLModel/SQLAlchemy field syntax.

### Migration

Additive-only (`ALTER TABLE movementstate ADD COLUMN pending_ht_plates FLOAT; ALTER TABLE movementstate ADD COLUMN pending_ht_band_config JSON;`), following this repo's established migration-file convention exactly (check the most recent migration file under `deploy/migrations/` for the header/comment format and the parity-keystone requirement mentioned in `deploy/migrations/README.md` — `tests/test_migrations.py::test_chain_matches_create_all` must stay green).

### `ironlog/engine/advance.py`

Add two new `AdvanceResult` fields, next to `earned_load_step`:

```python
earned_ht_plates: Optional[float] = None
earned_ht_band_config: Optional[list] = None
```

Replace `_rule_driven`'s HT branch:

```python
def _rule_driven(state, perf, movement, window, band_inventory=None) -> AdvanceResult:
    rule = ProgressionRule.RULE_DRIVEN.value
    if _is_ht_composite(movement):
        if not perf.session_performed or not _clean(perf) or band_inventory is None:
            return AdvanceResult(False, rule, state.consecutive_advance_count)
        from .band_composite import ht_next_setup
        next_plates, next_config = ht_next_setup(
            state.ht_plates, state.ht_band_config or [], band_inventory,
        )
        return AdvanceResult(
            True, rule, 0,
            earned_ht_plates=next_plates,
            earned_ht_band_config=list(next_config),
        )
    if _at_cap(state, movement):
        return _rep_ladder(state, perf, movement, window)
    if not perf.session_performed:
        return AdvanceResult(False, rule, 0)
    streak = state.consecutive_advance_count + 1
    if streak >= 1:
        return AdvanceResult(True, rule, 0, earned_load_step=_earned_step(state, movement))
    return AdvanceResult(False, rule, streak)
```

`_clean(perf)` is the same session-wide clean check every other rule already uses (`hit_target and max_rpe<=8.0 and all_sides_cleared`) — HT's `session_performed`-only check (no RPE-exemption carve-out) matches how the pre-cap tier-advance branch right below it already treats HT-adjacent RULE_DRIVEN sessions, so don't add a new exemption path here. `band_inventory is None` (the caller genuinely has no band data, e.g. a test fixture) is a safe no-op hold, not a crash.

`advance()`'s own signature: add a trailing keyword-only `band_inventory=None` parameter, threaded ONLY to `_rule_driven` (every other handler's call site and signature stays completely untouched — this is a deliberate minimal-diff choice, not an oversight):

```python
def advance(rule, state, perf, movement, confirmation_window, band_inventory=None) -> AdvanceResult:
    fn = _DISPATCH.get(rule)
    if fn is None:
        return AdvanceResult(False, None, state.consecutive_advance_count)
    if rule == ProgressionRule.RULE_DRIVEN:
        return fn(state, perf, movement, confirmation_window, band_inventory)
    return fn(state, perf, movement, confirmation_window)
```

### `ironlog/engine/analysis.py` — `MovementDelta`

Add, next to `pending_load_delta`:

```python
pending_ht_plates: Optional[float] = None
pending_ht_band_config: Optional[list] = None
```

### `ironlog/persistence/run_analysis.py`

Near the top of `run_analysis` (once per call, not per movement — mirror `assembler.py`'s exact construction, confirm the exact import paths for `Band`/`BandPair` before writing):

```python
from ..engine.band_composite import Band
from ..models.library import BandPair
# ...
band_inventory = [Band(bp.id, bp.bottom_lb, bp.peak_lb, bp.usable)
                  for bp in db.exec(select(BandPair)).all()]
```

In the per-movement loop, change the existing `advance()` call:

```python
adv = advance(movement.progression_rule, state, perf, movement, window, band_inventory=band_inventory)
```

After the existing `d.active_rule = adv.active_rule` / `earned_load_step` staging block, add:

```python
if adv.earned_ht_plates is not None:
    d.pending_ht_plates = adv.earned_ht_plates
    d.pending_ht_band_config = adv.earned_ht_band_config
```

### `ironlog/persistence/apply.py`

In `apply_analysis`'s per-delta loop, add (near the existing `pending_load_delta` staging block, same pattern):

```python
if d.pending_ht_plates is not None:
    state.pending_ht_plates = d.pending_ht_plates
    state.pending_ht_band_config = d.pending_ht_band_config
```

### `ironlog/generation/assembler.py` — `_build_exercise`'s HT block

Current code (unconditional):
```python
next_plates, next_config = ht_next_setup(cur_plates, cur_config, band_inventory)
prospective_ht[movement.id] = (next_plates, list(next_config))
```

New code — use the staged, gated value if present, else hold (no advance):
```python
if state is not None and state.pending_ht_plates is not None:
    next_plates = state.pending_ht_plates
    next_config = state.pending_ht_band_config or cur_config
else:
    next_plates, next_config = cur_plates, cur_config
prospective_ht[movement.id] = (next_plates, list(next_config))
```

This REPLACES the unconditional `ht_next_setup(...)` call at this site entirely — the search itself now runs at analysis time (inside `_rule_driven`), not here. Do not remove the `ht_next_setup` import if `band_composite.py`'s other functions (`ht_performed_floor`, `resolved_band_config`) are still imported/used elsewhere in this file — check before deleting any import.

### `ironlog/generation/loop.py` — `commit_session`

In the existing `for mid in touched_mids:` loop, after the existing `if mid in assembled.prospective_ht_setups:` block (which writes `st.ht_plates`/`st.ht_band_config` — unchanged), add:

```python
st.pending_ht_plates = None
st.pending_ht_band_config = None
```

Only clear these for movements actually touched by `prospective_ht_setups` (inside the existing `if mid in assembled.prospective_ht_setups:` block, not unconditionally for every movement) — mirrors exactly how `pending_load_delta` is only cleared inside the `if mid in assembled.prospective_current_loads:` block right above it.

## Edge Cases

- **A dirty (non-clean) HT session**: `_rule_driven` returns `advanced=False`, no `earned_ht_plates` — nothing staged, next generation holds at current setup (no advance). This is the core fix — previously this case still advanced blindly.
- **`band_inventory` unavailable at analysis time** (e.g. a test calling `advance()` directly without threading it through): treated as a safe hold, not a crash — matches the `band_inventory is None` guard above.
- **A clean HT session, but the athlete self-selected a HARDER setup than prescribed** (spec 47's floor-reconciliation territory): spec 47's `ht_performed_floor` mechanism stays entirely at generation time (assembler.py, unaffected by this spec) — reconciling "what's the athlete's real current setup" is a different concern from "did they earn the next one," and the two must not be conflated. `_rule_driven`'s new logic computes `ht_next_setup` from `state.ht_plates` (the CURRENT, already-floor-reconciled-by-a-prior-generation value), not from anything logged this session directly.
- **Two HT sessions logged before either's next generation** (unlikely given HT's real training cadence, but structurally possible): the second `run_analysis` call overwrites `pending_ht_plates` with its own freshly-computed `ht_next_setup(state.ht_plates, ...)` — since `state.ht_plates` hasn't changed between the two analysis calls (only `pending_ht_plates` has), both computations use the same input and land on the same result — harmless, not a real conflict.
- **A movement mid-`try`-block exception in `run_analysis`'s per-movement loop** (the existing degraded-path guarantee: "a broken engine step reduces to your program yesterday"): `pending_ht_plates`/`pending_ht_band_config` staging must participate in the same all-or-nothing discipline the rest of this loop already has — confirm this staging happens inside the existing `try` block, not before it, so an exception anywhere in the same movement's step leaves nothing partially staged (mirrors the exact atomicity concern spec 48's Opus review caught and fixed for `state.assist_level` earlier tonight — do not repeat that mistake here for `state.ht_plates`... wait, this spec never mutates `state.ht_plates` directly, only `d.pending_ht_plates` on the delta object, which is NOT applied to `state` until `apply_analysis` runs in a separate transaction after `run_analysis`'s per-movement loop completes — confirm this ordering holds and no direct `state.*` mutation happens inside this spec's new code, unlike spec 48's `state.assist_level` case which needed the snapshot-restore fix specifically because it mutated `state` in-place mid-loop).

## Dependencies

None — standalone within this repo, but Spec 50 (D2/D5 unification) depends on this spec being merged first (its `HtProgressionState` table needs `pending_ht_plates`/`pending_ht_band_config`-equivalent fields that mirror this spec's `MovementState` additions).

## Verification

- `~/projects/IronLog-V2/.venv/bin/pytest -q tests/test_advance_ht_gating.py tests/test_run_analysis_ht_gating.py tests/test_ht_commit_gating.py -v` — new tests pass.
- `~/projects/IronLog-V2/.venv/bin/pytest -q` — full suite green, zero regressions (baseline 672 passing on `main` as of this spec's writing).
- `tests/test_migrations.py::test_chain_matches_create_all` — the parity keystone test — must stay green after the new migration.
- Manual: replay tonight's real scenario against a DB copy (not production) — D2's Hip Thrust MovementState (movement_id=12, day_id="D2 Lower A") at `ht_plates=180, ht_band_config=[2]`, a clean 3x8 logged session, confirm `pending_ht_plates` gets staged to the correct `ht_next_setup(180, [2], ...)` result and that a subsequent generation-then-approval correctly advances `ht_plates` to that value, clearing the pending marker.
