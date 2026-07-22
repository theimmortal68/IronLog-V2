# Spec 47: HT performed-floor reconciliation

## Objective
`ht_next_setup` (the peak-search that decides Hip Thrust's next plates/band setup) always advances from `MovementState.ht_plates`/`ht_band_config` — the system's own last committed setup — never from what the athlete actually logged (`SetLog.felt_peak`). Reconcile the stored setup against the most recently logged `felt_peak` (same band config only) before it feeds `ht_next_setup`, so a self-selected-heavier-plates session that's already been proven doesn't get silently discarded.

## Design doc
Full design + rationale: `docs/superpowers/specs/2026-07-22-ht-performed-floor-reconciliation-design.md` (approved via brainstorming 2026-07-22). Read it for the "why" before implementing — this spec is the "what."

## File targets
- Modify: `ironlog/engine/band_composite.py` — add `resolved_band_config` (extracted, public) + new `ht_performed_floor`.
- Modify: `ironlog/persistence/ht_refine.py` — replace its private `_resolved_band_config` with an import of the shared one.
- Modify: `ironlog/generation/assembler.py` — reconciliation point in `_build_exercise`.
- New: tests added to `tests/test_ht_next_setup.py` (for `ht_performed_floor`) and a new `tests/test_ht_assembler_reconciliation.py` (for the assembler integration point).

## The fix

### 1. `ironlog/engine/band_composite.py`
Add near `config_peak`/`ht_next_setup` (matches this file's existing pure, no-DB style — `List`/`Optional` already imported from `typing`, add `Optional` if not already there):

```python
def resolved_band_config(band_config: Optional[list], band_pair_id: Optional[int]) -> Optional[List[int]]:
    """A logged set's band configuration: band_config first, falling back to
    the older singular band_pair_id field. None if unresolvable.

    Pure -- takes plain values, not ORM objects, so it works for both a
    PlannedSet-then-SetLog fallback chain (ht_refine.py's original use) and
    a plain (band_config, band_pair_id) pair (this module's own callers)."""
    if band_config:
        return list(band_config)
    if band_pair_id is not None:
        return [band_pair_id]
    return None


def ht_performed_floor(plates: float, config: list, felt_peak: float, by_id: dict) -> float:
    """Floor `plates` up to what's needed to explain a logged `felt_peak` for
    the SAME `config` -- mirrors performed_floor_delta's shape (never
    regress, only floor up). Returns `plates` unchanged if `felt_peak`
    doesn't imply a higher value (e.g. the athlete performed lighter, or
    matched what was already stored)."""
    implied_plates = felt_peak - sum(by_id[b].peak for b in config)
    return max(plates, implied_plates)
```

Note: `resolved_band_config`'s signature takes plain `(band_config, band_pair_id)` values, NOT `(sl, ps)` ORM objects like `ht_refine.py`'s original private version -- this keeps `band_composite.py` a pure, DB-free module (matching its existing docstring: "Pure — no DB, no HTTP"). `ht_refine.py`'s call sites adapt by extracting the plain values from their `SetLog`/`PlannedSet` objects before calling it (see below) -- the three-tier fallback chain (`ps.band_config` -> `ps.band_pair_id` -> `sl.band_pair_id`) becomes two calls to the shared 2-tier helper, or inline logic choosing which pair to pass. Read `ht_refine.py`'s exact current 3-tier fallback (`_resolved_band_config`) and preserve the EXACT same resolution priority when adapting call sites -- do not change ht_refine.py's behavior, only its implementation.

### 2. `ironlog/persistence/ht_refine.py`
Replace the private `_resolved_band_config(sl, ps)` function's body to delegate to the shared helper while preserving its exact 3-tier priority (`ps.band_config` -> `ps.band_pair_id` -> `sl.band_pair_id`):

```python
from ..engine.band_composite import resolved_band_config as _shared_resolved_band_config

def _resolved_band_config(sl: SetLog, ps: Optional[PlannedSet]) -> Optional[List[int]]:
    """The set's band configuration, PlannedSet.band_config first, falling
    back to the older singular band_pair_id fields. None if unresolvable."""
    if ps is not None:
        result = _shared_resolved_band_config(ps.band_config, ps.band_pair_id)
        if result is not None:
            return result
    return _shared_resolved_band_config(None, sl.band_pair_id)
```
(Keep the function name `_resolved_band_config` and its call sites in this file completely unchanged -- only its internal implementation changes to delegate. Every existing test in `tests/test_ht_refine.py` must still pass unchanged, since behavior is identical, just re-implemented.)

### 3. `ironlog/generation/assembler.py`
In `_build_exercise`, find the HT block (`if _is_ht_movement(movement) and band_inventory is not None and has_current_ht_setup:`) where `cur_plates, cur_config = _resolve_ht_current_setup(state, load)` is computed. Immediately after that line, add the reconciliation:

```python
if state is not None:
    cur_plates = _reconcile_ht_performed_floor(db, movement.id, ctx.day_role, cur_plates, cur_config, by_id)
```
(`by_id` is already computed a few lines below in the existing code as `by_id = {b.id: b for b in band_inventory}` -- move that line UP so it's available before this new reconciliation call, since `by_id` is needed by the new function. Check `ctx`'s exact attribute name for day_role -- read `GenerationContext`'s definition in `context.py` to confirm whether it's `ctx.day_role`, or if day_role needs to come from elsewhere in `_build_exercise`'s available scope; use whatever's actually correct, don't guess the name.)

Add a new private helper function in `assembler.py` (near `_resolve_ht_current_setup`):
```python
def _reconcile_ht_performed_floor(db: DBSession, movement_id: int, day_role: str,
                                   cur_plates: float, cur_config: list, by_id: dict) -> float:
    """Floor cur_plates up to what the most recent completed session's logged
    felt_peak implies, IF that session used the SAME band config -- see
    docs/superpowers/specs/2026-07-22-ht-performed-floor-reconciliation-design.md.
    Falls through to cur_plates unchanged for any cold-start/mismatch/no-data case."""
    from ..models.enums import SessionStatus
    from ..models.session import SetLog, Session as WorkoutSession

    prior = db.exec(
        select(WorkoutSession)
        .where(WorkoutSession.day_role == day_role, WorkoutSession.status == SessionStatus.COMPLETED)
        .order_by(WorkoutSession.date.desc())
    ).first()
    if prior is None:
        return cur_plates

    last_set = db.exec(
        select(SetLog)
        .where(SetLog.session_id == prior.id, SetLog.movement_id == movement_id,
               SetLog.is_warmup == False, SetLog.felt_peak.is_not(None))  # noqa: E712
        .order_by(SetLog.set_index.desc())
    ).first()
    if last_set is None:
        return cur_plates

    logged_config = resolved_band_config(None, last_set.band_pair_id)
    # Prefer the set's linked PlannedSet.band_config if present (matches ht_refine.py's own priority)
    if last_set.planned_set_id is not None:
        ps = db.get(PlannedSet, last_set.planned_set_id)
        if ps is not None:
            logged_config = resolved_band_config(ps.band_config, ps.band_pair_id)
    if logged_config is None or set(logged_config) != set(cur_config):
        return cur_plates

    return ht_performed_floor(cur_plates, cur_config, last_set.felt_peak, by_id)
```
Import `resolved_band_config`/`ht_performed_floor` from `..engine.band_composite` alongside the existing `Band, config_peak, ht_next_setup` import at the top of the file. `SetLog` needs importing from `..models.session` (not currently imported in this file -- check the existing `from .models.session import ...` line and add it there rather than a separate import statement).

## Edge cases
- No prior completed session for this movement/day: `cur_plates` unchanged (cold start, falls through).
- Prior session logged but no working set has a `felt_peak`: unchanged, falls through.
- Prior session's resolved config differs from `cur_config` (a genuine band swap happened): unchanged, falls through -- do NOT guess a floor against a config that no longer applies. `set(logged_config) != set(cur_config)` comparison is order-independent (a config is a set of band ids, not an ordered list).
- `felt_peak` implies FEWER plates than `cur_plates`: `ht_performed_floor`'s `max(...)` correctly leaves `cur_plates` unchanged -- this is a floor, never a ceiling.
- Multiple working sets in the prior session with different `felt_peak` values: use the LAST working set (`order_by(SetLog.set_index.desc())`, take `.first()`) -- not a max/average across sets.
- This reconciliation must NOT change `state.ht_plates`/`state.ht_band_config` themselves (no new write) -- it only changes the LOCAL `cur_plates` variable feeding into the very next `ht_next_setup(cur_plates, cur_config, ...)` call a few lines below in the same function. `commit_session` remains the sole writer of the persisted fields, unchanged.

## Dependencies
None.

## Verification

### New tests in `tests/test_ht_next_setup.py`
Add tests for `ht_performed_floor` (pure function, matches this file's existing `INV`/`Band` fixture style):
1. `felt_peak` implies MORE plates than stored -> floored up (e.g. stored 170 + Red[36,90], felt_peak=260 implies 260-90=170... use a case where it's genuinely higher, e.g. stored plates=170, felt_peak=265 -> implied=175 -> floored to 175).
2. `felt_peak` implies FEWER plates than stored -> unchanged (stored plates=175, felt_peak=260 implies 170 -> stays 175).
3. `felt_peak` exactly matches stored -> unchanged (idempotent).

### New test file `tests/test_ht_assembler_reconciliation.py`
Integration-level tests for `_reconcile_ht_performed_floor` (or test it via the public `assemble()`/`_build_exercise` entry point if that's more natural given the file's existing test conventions -- check `tests/test_ht_generate_banded.py`/`test_ht_composite_wiring.py` for the established pattern of testing HT generation behavior and match it):
1. **The exact real-world regression case**: seed a completed prior session logging `felt_peak` implying more plates than the current stored `ht_plates`, SAME band config -- assemble a new session for this movement/day and confirm the generated setup reflects the reconciled (higher) floor before `ht_next_setup`'s own step is applied on top (i.e., confirm the final prescribed setup is `ht_next_setup`'s result starting from the RECONCILED plates, not the stale stored ones).
2. **Config-mismatch case**: seed a completed prior session with a DIFFERENT band config than currently stored -- confirm reconciliation does NOT fire (assembled setup matches what `ht_next_setup` would produce from the UNRECONCILED stored plates).
3. **Cold-start case**: no prior completed session exists for this movement/day -- confirm assembly proceeds exactly as today (no exception, no behavior change).

### Regression
Full server suite green: `~/projects/IronLog-V2/.venv/bin/pytest -q` (current main baseline: 655 passing). Every existing test in `tests/test_ht_refine.py` must still pass UNCHANGED (behavior-preserving refactor of `_resolved_band_config`'s implementation only). Every existing test in `tests/test_ht_next_setup.py`, `tests/test_ht_composite_wiring.py`, `tests/test_ht_generate_banded.py`, `tests/test_generation_day_scoped_state.py`, `tests/test_commit_day_scoped_state.py` must still pass unchanged (the reconciliation is a no-op fall-through whenever no matching prior session/config/felt_peak exists, which is the case for every existing fixture in these files unless a test deliberately sets one up — confirm this by running the full suite, not by assuming).
