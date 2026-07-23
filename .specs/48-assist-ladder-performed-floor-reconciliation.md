# Spec 48: Assist-Ladder Performed-Floor Reconciliation

## Objective

Reconcile assist-ladder progression (`state.assist_level` for `INCLINE_REDUCTION`/`ASSISTANCE_REDUCTION` movements) against what the athlete actually logged this session, so a self-selected harder-than-prescribed level that hits clean reps is recognized instead of silently ignored — the same failure mode as the already-shipped HT/scalar-load floors, applied here via ladder-index comparison and clean-sets-only scoping.

Design doc (approved, source of truth): `docs/superpowers/specs/2026-07-22-assist-ladder-performed-floor-reconciliation-design.md`.

## File Targets

- `ironlog/engine/advance.py` — add `performed_assist_floor()`.
- `ironlog/persistence/run_analysis.py` — add `_clean_performed_assist_values()` helper and the scoped wiring block.
- `tests/test_advance_assist_floor.py` — new file, unit tests for `performed_assist_floor`.
- `tests/test_run_analysis_assist_floor.py` — new file, unit tests for `_clean_performed_assist_values` + integration tests through `run_analysis`.

## Changes

### `ironlog/engine/advance.py`

Add this function immediately after `performed_floor_delta` (currently ends around line 78, right before `def _rpe8(...)`):

```python
def performed_assist_floor(current: Optional[float], ladder: List[float],
                            clean_performed_values: List[float]) -> Optional[float]:
    """The most advanced (highest ladder-index) value demonstrated by a clean
    (rep-target-hit) set this session, if more advanced than `current`.
    Returns `current` unchanged if no clean value is more advanced, or if
    `current`/ladder is unusable (current is None, or off-ladder). Never
    regresses backward on the ladder. Direction-agnostic: 'more advanced'
    means a higher ladder index, regardless of whether the ladder ascends or
    descends numerically.
    """
    if current is None or not ladder or current not in ladder:
        return current
    current_idx = ladder.index(current)
    candidate_indices = [ladder.index(v) for v in clean_performed_values if v in ladder]
    if not candidate_indices:
        return current
    best_idx = max(candidate_indices)
    return ladder[best_idx] if best_idx > current_idx else current
```

No new imports needed — `Optional`/`List` are already imported at the top of the file.

### `ironlog/persistence/run_analysis.py`

**Import change** (line 22): add `performed_assist_floor` to the existing import from `..engine.advance`:

```python
from ..engine.advance import SessionPerf, advance, performed_assist_floor, performed_floor_delta, roll_unassisted_max
```

**New helper function**, placed immediately after `_build_session_perf` (which currently ends around line 210, right before `def _confirmation_window(...)`):

```python
def _clean_performed_assist_values(mid: int, set_logs: List[SetLog], planned_sets: dict) -> List[float]:
    """actual_load (falling back to the PlannedSet's target_load when
    actual_load is None -- no signal logged means assume the prescribed
    value was used, the same default as today) for every set-group that
    individually hit its rep target this session. Duplicates
    _build_session_perf's grouping/clean-check logic deliberately -- kept
    standalone rather than threaded through SessionPerf's existing shape, to
    avoid touching that function's other call sites.
    """
    groups: dict = defaultdict(list)
    for sl in set_logs:
        if sl.movement_id != mid or sl.is_warmup:
            continue
        groups[sl.set_index].append(sl)

    def _group_hits(rows) -> bool:
        for sl in rows:
            ps = planned_sets.get(sl.planned_set_id) if sl.planned_set_id else None
            if ps is None or ps.target_reps_high is None:
                return False
            if sl.actual_reps is None or sl.actual_reps < ps.target_reps_high:
                return False
        return True

    values = []
    for rows in groups.values():
        if not rows or not _group_hits(rows):
            continue
        for sl in rows:
            ps = planned_sets.get(sl.planned_set_id) if sl.planned_set_id else None
            value = sl.actual_load if sl.actual_load is not None else (ps.target_load if ps else None)
            if value is not None:
                values.append(value)
    return values
```

**Wiring**: in the per-movement loop inside `run_analysis` (the `for d in result.movement_deltas:` block), immediately after this existing line:

```python
            perf = _build_session_perf(mid, movement, set_logs, planned_sets)
```

insert:

```python
            if (movement.progression_rule in (ProgressionRule.INCLINE_REDUCTION.value, ProgressionRule.ASSISTANCE_REDUCTION.value)
                    and movement.assist_ladder):
                clean_values = _clean_performed_assist_values(mid, set_logs, planned_sets)
                reconciled = performed_assist_floor(state.assist_level, movement.assist_ladder, clean_values)
                if reconciled != state.assist_level:
                    state.assist_level = reconciled
```

before the existing `window = _confirmation_window(...)` / `adv = advance(...)` lines. `ProgressionRule` is NOT currently imported in this file — add it to the existing import at line 36: `from ..models.enums import CalibrationStatus, LiftCategory, Objective, ProgressionRule`.

No other lines in this loop change. The existing lines `d.active_rule = adv.active_rule` / `if adv.new_assist_level is not None: d.new_assist_level = adv.new_assist_level` (already present, unchanged) pick up the reconciled value automatically because `_ladder_step` always echoes `current` (now the reconciled `state.assist_level`) back through `result_field` on every path.

## Edge Cases (from the design doc — implement tests for each)

- No clean sets this session for the movement → `_clean_performed_assist_values` returns `[]` → `performed_assist_floor` returns `current` unchanged.
- A clean set with `actual_load=None` → falls back to `PlannedSet.target_load`.
- A logged value not present in `movement.assist_ladder` → excluded from consideration, never raises.
- `state.assist_level` is `None` (freshly transitioned onto the ladder) or off-ladder → `performed_assist_floor` returns it unchanged (guard clause), no crash.
- Multiple distinct clean values in one session → the floor picks the single most advanced (highest ladder index), not the first or an average.
- Mixed session (the user's real scenario): clean sets at a harder rung, plus a failed even-harder probe → floors to the harder rung, does NOT float toward the failed probe's rung. This falls out naturally from `_clean_performed_assist_values` excluding the failed set-group AND `_ladder_step`'s existing session-wide dirty check (unchanged) holding at the reconciled value rather than advancing further — verify both halves in the integration test, do not assume one implies the other.
- Pull-up `[TOWER + TUBES]`: ASSISTED mode but `progression_rule == PULL_UP_ROLLING_MAX` and `assist_ladder == None` — must not enter the new scoped branch at all (verify via an integration test that its `d.new_assist_level`/rolling-max output is byte-identical to pre-change behavior).

## Dependencies

None — single spec, single worktree.

## Verification

- `~/projects/IronLog-V2/.venv/bin/pytest -q tests/test_advance_assist_floor.py tests/test_run_analysis_assist_floor.py -v` — all new tests pass.
- `~/projects/IronLog-V2/.venv/bin/pytest -q` — full suite green, zero regressions (baseline 661 passing as of the last merge on `main`).
- Manual sanity check: `performed_assist_floor(20.0, [20,15,10,5,0], [15.0, 15.0])` returns `15.0`; `performed_assist_floor(20.0, [20,15,10,5,0], [10.0])` (no clean sets, hypothetically passed anyway) still returns `10.0` if called directly — the clean-scoping is enforced by the CALLER (`_clean_performed_assist_values` only ever returns clean values), not by `performed_assist_floor` itself, which trusts its input. Confirm the spec's test suite tests this contract at the `_clean_performed_assist_values` boundary, not by relying on `performed_assist_floor` to re-filter.
