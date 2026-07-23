# Assist-Ladder Performed-Floor Reconciliation — Design

## Problem

Assist-ladder movements (Nordic Curl `[GHR]`, Face-Up Incline Knee Raise, Reverse Nordic Curl `[GHR]`) advance via `_ladder_step` in `ironlog/engine/advance.py`, which decides whether to hold or step based on `state.assist_level` — the system's *belief* about the currently-prescribed rung. That belief is never reconciled against what the athlete actually trained.

Concretely: the athlete self-selects a harder rung than prescribed (e.g. Nordic Curl prescribed at 20°, athlete actually trains at 15°, a harder setting) and hits clean reps there. The system's "clean" judgment is computed correctly against the rep target (rep targets don't change with assist level in this program), so `hit_target` is true — but `_ladder_step`'s streak/advance math still starts from the stale `state.assist_level` (20°), not the demonstrated 15°. The earned advance never reconciles against reality. This is the identical shape to the HT band-composite bug fixed the same night (spec 47) — a self-selected deviation the system has no mechanism to recognize — but for `assist_level` instead of HT plates+band peak.

## Why this wasn't a "needs new client capture" problem

Initial framing assumed this needed a new `SetLog` column and new client capture UI (mirroring HT's `felt_peak`), because `SetLog` has no explicit "actual assist level used" field. Investigation of `CaptureScreen.kt` found this assumption wrong: **`SetLog.actual_load` already captures this signal.** The client's existing "Load" input field (labeled with degree units via an earlier `unit_hint` fix) is the SAME generic field used for both scalar-load and assist-mode movements — confirmed via live data (Face-Up Incline Knee Raise showing `actual_load=15.0` against a `target_load=10.0` prescription). The gap is entirely server-side: `run_analysis.py`'s existing performed-floor reconciliation (the scalar-load fix, spec 46) is explicitly scoped to `load_field_for_mode(movement.progression_mode) == "current_load"`, which excludes `ASSISTED` mode. No new schema, no new client work — this is a pure server-side wiring gap.

## Design

### The existing scalar-load floor (already shipped, spec 46) — the template

`run_analysis.py` (~line 480-487) already floors `current_load` against the heaviest weight actually logged this session, regardless of whether reps were hit:

```python
floor_delta = 0.0
if (load_field_for_mode(movement.progression_mode) == "current_load"
        and movement.lift_category != LiftCategory.HIP_THRUST):
    performed_loads = [sl.actual_load for sl in set_logs
                       if sl.movement_id == mid and not sl.is_warmup and sl.actual_load is not None]
    floor_delta = performed_floor_delta(state.current_load, performed_loads)
```

`performed_floor_delta(current_load, performed_loads)` returns `max(max(performed_loads) - current_load, 0.0)` — any attempt counts, clean or not, because moving more weight for even a partial set is still real evidence of capacity.

### Why assist-ladder needs a different rule, not the same one reused

Two properties don't transfer from scalar load to assist-ladder, both already confirmed with the user:

1. **Clean sets only, not any attempt.** For assist-ladder, an unclean attempt at a harder rung is a normal, expected exploratory probe — not evidence of trained capability. The user's own clarifying scenario: "I did all 8 reps at 15 degrees and it was easy, so I wanted to try 10 degrees, but couldn't make the minimum reps and marked it hard, then went back to 15 degrees. Obviously, that means not ready to make the jump to 10 degrees." Flooring on any attempt (mirroring scalar load) would incorrectly floor to 10° here. The fix floors ONLY on individually clean (rep-target-hit) set-groups.
2. **Ladder-index comparison, not magnitude.** Assist ladders are not uniformly ascending or descending in raw value — Nordic Curl's ladder descends in degrees as difficulty increases (`[20,15,10,5,0]`); other ladders may ascend. "More advanced" always means a higher index into `movement.assist_ladder` (that ordering already encodes "advancing walks toward index+1 = harder" — the same convention `_ladder_step` itself relies on), never a raw-value comparison.

### New pure function: `performed_assist_floor`

`ironlog/engine/advance.py`, beside `performed_floor_delta`:

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

### Where it's wired in: `run_analysis.py`, before `advance()` is dispatched

Scoped to `movement.progression_rule in {ProgressionRule.INCLINE_REDUCTION, ProgressionRule.ASSISTANCE_REDUCTION}` — narrower than "mode == ASSISTED". This naturally excludes Pull-up `[TOWER + TUBES]` (confirmed live: `progression_mode=ASSISTED`, but `progression_rule=PULL_UP_ROLLING_MAX`, `assist_ladder=null`) without a special-case exclusion, and naturally excludes `BODY_POSITION`-ladder movements (Dragon Flag) which use a different state field (`current_body_position`), not `assist_level`.

A new small helper (NOT threaded through `_build_session_perf`'s existing return shape, to avoid touching its other call sites) computes per-set-index clean status, duplicating `_group_hits`'s grouping logic:

```python
def _clean_performed_assist_values(mid: int, set_logs: List[SetLog], planned_sets: dict) -> List[float]:
    """actual_load (falling back to the PlannedSet's target_load when
    actual_load is None -- no signal logged means assume the prescribed
    value was used, the same default as today) for every set-group that
    individually hit its rep target this session."""
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

Wiring (right after `perf = _build_session_perf(...)`, before `adv = advance(...)`):

```python
if (movement.progression_rule in (ProgressionRule.INCLINE_REDUCTION.value, ProgressionRule.ASSISTANCE_REDUCTION.value)
        and movement.assist_ladder):
    clean_values = _clean_performed_assist_values(mid, set_logs, planned_sets)
    reconciled = performed_assist_floor(state.assist_level, movement.assist_ladder, clean_values)
    if reconciled != state.assist_level:
        state.assist_level = reconciled
```

This mutates the in-memory `state.assist_level` before `advance()` is called. `_ladder_step` always echoes `current` back through `result_field` on every path — advance, hold-at-streak, and dirty-session-hold — so the floored value flows through to `adv.new_assist_level` automatically and gets staged onto `d.new_assist_level` by the existing line `if adv.new_assist_level is not None: d.new_assist_level = adv.new_assist_level`. No changes needed to `_ladder_step`, `_incline_reduction`, `_assistance_reduction`, or the staging code.

### Why the mixed-session scenario resolves correctly without extra logic

Tracing the user's exact scenario (prior `state.assist_level` prescribed at 20°, this session: clean @ 15°, failed probe @ 10°, back to clean @ 15°):

1. `_clean_performed_assist_values` returns `[15.0, 15.0]` (only the two clean 15° set-groups; the failed 10° group is excluded — it didn't hit its rep target).
2. `performed_assist_floor(20.0, [20,15,10,5,0], [15.0, 15.0])` → index of 20 is 0, index of 15 is 1, more advanced → returns `15.0`.
3. `state.assist_level` mutates to `15.0` before `advance()` is called.
4. `_ladder_step`'s `_clean(perf)` gate uses `perf.hit_target`, which is **session-wide** (unchanged, still computed by the existing `_build_session_perf`) — and this session has a failed set-group (the 10° probe), so `perf.hit_target` is `False` session-wide. `_ladder_step` takes the dirty-session branch: `return AdvanceResult(False, rule, 0, **{result_field: current})` — echoing back `current`, which is now the reconciled `15.0`.
5. Net effect: `state.assist_level` floors to 15° (the demonstrated capability is recognized), but does NOT advance further toward 10° (the session had a genuine miss). Exactly matches the user's own conclusion: "Obviously, that means not ready to make the jump to 10 degrees."

No new logic is needed to prevent over-advancing past the floor — the existing session-wide dirty-check in `_ladder_step` already provides that guard for free, because it operates on the (now-reconciled) `current` value rather than needing its own floor-awareness.

## Scope

- **In scope:** Nordic Curl `[GHR]` (`INCLINE_REDUCTION`), Face-Up Incline Knee Raise (`INCLINE_REDUCTION`), Reverse Nordic Curl `[GHR]` (`ASSISTANCE_REDUCTION`) — every movement whose `progression_rule` is one of these two.
- **Out of scope (explicitly confirmed):** Pull-up `[TOWER + TUBES]` — ASSISTED mode, but a structurally different rule (`PULL_UP_ROLLING_MAX`) with its own rolling-max mechanism and no `assist_ladder`; naturally excluded by the rule-based scoping above, no special case needed.
- **Out of scope:** any change to `_ladder_step`, `_build_session_perf`, or `SessionPerf`'s shape. The new helper duplicates ~10 lines of grouping logic rather than threading a new return value through `_build_session_perf` and touching its other call sites.
- **Out of scope:** client changes. `actual_load` capture already exists and already flows through correctly for assist-mode movements.

## Edge Cases

- **No clean sets this session** (movement not trained, or every set missed reps): `_clean_performed_assist_values` returns `[]`, `performed_assist_floor` returns `current` unchanged (the `if not candidate_indices: return current` branch) — no floor, no regression.
- **Clean set logged with `actual_load=None`** (athlete didn't touch the Load field): falls back to the `PlannedSet.target_load` — contributes the prescribed value itself, a no-op floor (matches today's implicit behavior when no signal exists).
- **Logged value not on the ladder** (fat-fingered entry, or a value from before a ladder was edited): excluded from `candidate_indices` entirely — never raises, never crashes on `ladder.index()` of a missing value.
- **`state.assist_level` itself off-ladder or `None`** (needs-calibration, or a stale/corrupted value): `performed_assist_floor` returns `current` unchanged immediately (the guard clause) — the reconciliation is a no-op, falling back to today's existing behavior (the seed/calibration path is unaffected).
- **Movement newly transitioned onto the ladder** (`state.assist_level is None`): `performed_assist_floor`'s guard returns `None` unchanged; `_ladder_step`'s own `current is None` branch (unaffected by this change) seeds `ladder[0]` as it does today.
- **Multiple distinct clean assist values in one session** (e.g. accidental cross-rung logging): `performed_assist_floor` takes the single most advanced (highest-index) among all clean values, not the first or the average.

## Testing

- **Unit — `performed_assist_floor`:** descending ladder (Nordic Curl `[20,15,10,5,0]`) floors correctly on a more-advanced clean value; ascending ladder floors correctly (direction-agnostic); no floor when no clean value is more advanced than `current`; no floor when `current` is `None` or off-ladder; off-ladder clean values are excluded from consideration; multiple clean values pick the most advanced.
- **Unit — `_clean_performed_assist_values`:** the exact mixed-session scenario (clean @ 15°, failed @ 10°, clean @ 15° again) returns only the clean 15° values, excluding the failed 10° set-group; `actual_load=None` falls back to `PlannedSet.target_load`; a session with zero logged sets for the movement returns `[]`.
- **Integration — `run_analysis.py`:** a full session for Nordic Curl `[GHR]` with `state.assist_level=20`, this session's sets clean at 15°, asserts `d.new_assist_level == 15.0` even though the session-wide `perf.hit_target` may be `False` (mixed session) and `_ladder_step` itself did not "advance" (`adv.advanced == False`). A companion test asserts Pull-up `[TOWER + TUBES]` is untouched by this reconciliation path (its `d.new_assist_level`/rolling-max behavior is unaffected, confirmed by not entering the new scoped branch at all since `assist_ladder` is `null`).
