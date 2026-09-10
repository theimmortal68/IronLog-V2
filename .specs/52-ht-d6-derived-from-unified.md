# Spec 52: D6 Hip Thrust — live-derived 80% of the unified D2/D5 value

## Objective

D6's Hip Thrust currently tracks its own independent day-scoped progression (separate `MovementState` row, `day_id="D6 Weak Points"`), advancing on its own clean 3x12 sessions exactly like any other HT slot. The athlete wants this changed: **D6's HT setup should always be a fixed 80% (by peak, i.e. `config_peak`) of whatever the unified D2/D5 group (`HtProgressionState`, `unified_ht_group="main"`) currently sits at — live-linked, not a one-time reseed.** D6 stops earning its own independent advance; its plates/band config become a pure derived value, recomputed and pushed every time the unified group advances.

Depends on specs 49/50 (clean-advance gating + D2/D5 unification), already merged and live.

**Schema change — 2 new nullable `TierExercise` columns. HUMAN GATE required at dispatch and merge**, per the Forbidden list.

## Current state (verified live, 2026-07-26)

- Unified D2/D5: `HtProgressionState(movement_id=12, unified_ht_group="main")`, `ht_plates=180.0`, `ht_band_config=[2]` (Red), peak = 180+90 = **270**.
- D6: `MovementState(movement_id=12, day_id="D6 Weak Points")`, `ht_plates=165.0`, `ht_band_config=[1]` (Orange), peak = 165+45 = **210** (≈77.8% of 270 — close to the intended 80% but not exact; this spec's backfill will correct it to the true nearest-80% setup).
- 80% target peak: 270 × 0.8 = **216**.

## New pure function: `ht_scaled_setup` (`ironlog/engine/band_composite.py`)

Mirrors `ht_next_setup`'s search structure (`_all_configs`, per-config plate sweep, clamp/plate_step), but a DIFFERENT objective: find the `(plates, config)` combo whose peak is **closest** to a target peak (not "smallest peak strictly above current"). Tiebreak: fewest bands (same convention as `ht_next_setup` and the D2/D5 unification backfill script).

```python
def ht_scaled_setup(target_peak: float, inventory: List[Band], plate_step=5, clamp=225) -> Tuple[float, list]:
    """Find the (plates, config) combo whose peak is closest to target_peak,
    among all usable-band subsets respecting the bottom-position clamp.
    Tiebreak: fewest bands. Pure -- no DB, no HTTP. Mirrors ht_next_setup's
    search structure with a different objective (closest-to-target instead
    of smallest-strictly-above)."""
    by_id = {b.id: b for b in inventory}
    best = None
    for cfg in _all_configs(inventory):
        srest = sum(by_id[b].rest for b in cfg)
        if srest > clamp:
            continue
        max_plates = int((clamp - srest) // plate_step) * plate_step
        p = 0.0
        while p <= max_plates:
            pk = p + sum(by_id[b].peak for b in cfg)
            dist = abs(pk - target_peak)
            key = (dist, len(cfg))   # closest peak, then fewest bands
            if best is None or key < best[0]:
                best = (key, (p, list(cfg)))
            p += plate_step
    return best[1] if best else (0.0, [])
```

Add real unit tests in `tests/test_band_composite.py` (find this file first — if it doesn't exist, check wherever `ht_next_setup`'s own tests live and add alongside): verify `ht_scaled_setup(216, <the 6 real seeded bands>)` returns `(170.0, [1])` (Orange, peak 215 — closest to 216 among 5-lb-step feasible combos; verify this by exhaustive check in the test, don't hardcode the expected answer without deriving it), verify the clamp is respected, verify the fewest-bands tiebreak with a constructed case where two configs tie on distance.

## New `TierExercise` fields

```python
derived_from_unified_group: Optional[str] = None
derive_ratio: Optional[float] = None
```

Both nullable, additive. Set on D6's Hip Thrust `TierExercise` row (`d6_g1c`) only: `derived_from_unified_group="main"`, `derive_ratio=0.8`. Every other `TierExercise` row keeps both `None` (including D2/D5's own HT rows, which use the *existing* `unified_ht_group` field for a completely different relationship — a slot can have `unified_ht_group` set OR `derived_from_unified_group` set, never both; add an assertion/comment noting this, don't enforce it at the DB level, YAGNI).

## Migration

Additive-only: `ALTER TABLE tierexercise ADD COLUMN derived_from_unified_group VARCHAR; ALTER TABLE tierexercise ADD COLUMN derive_ratio FLOAT;` — confirm the next-available migration number (038 was D2/D5 unification; check `deploy/migrations/` for what's actually next, specs 49-51 may have claimed numbers already — verify, don't assume).

## D6 stops earning its own HT advance (`ironlog/persistence/run_analysis.py`)

Add a helper mirroring `_resolve_unified_ht_progression_state`'s shape:

```python
def _is_derived_ht_slot(db, workout, movement) -> bool:
    if not _is_ht_movement(movement):
        return False
    tier_exercise = _tier_exercise_for_session_movement(db, workout, movement.id)
    return tier_exercise is not None and tier_exercise.derived_from_unified_group is not None
```

In the per-movement loop (around the existing `unified_group, ht_state = _resolve_unified_ht_progression_state(...)` call), add:

```python
if _is_derived_ht_slot(db, workout, movement):
    # D6's HT is purely derived from the unified D2/D5 group (spec 52) --
    # it never earns its own advance. advance() still runs for bookkeeping
    # (active_rule, consecutive_advance_count) but band_inventory is withheld
    # so _rule_driven's HT branch takes its existing "no band_inventory ->
    # safe hold" path (spec 49) rather than computing an independent step.
    band_inventory_for_this_movement = None
else:
    band_inventory_for_this_movement = band_inventory
```

...then pass `band_inventory=band_inventory_for_this_movement` to the existing `advance(...)` call instead of the bare `band_inventory` variable. This reuses spec 49's ALREADY-TESTED "no band inventory → safe hold, no advance" path verbatim — no changes to `advance.py`/`_rule_driven` needed at all. Confirm this by reading spec 49's `_rule_driven` HT branch (`if not perf.session_performed or not _clean(perf) or band_inventory is None: return AdvanceResult(False, rule, state.consecutive_advance_count)`) — passing `None` here hits exactly that existing guard.

## D6's HT gets pushed a new derived value whenever the unified group advances (`ironlog/generation/loop.py`, `commit_session`)

After the existing `for (mid, group), (plates, config) in assembled.prospective_ht_unified.items():` loop (which writes the unified `HtProgressionState` row), add a new step: for each unified group just written, find every `TierExercise` whose `derived_from_unified_group` matches that group and movement, and push the scaled value to ITS day's `MovementState` row.

```python
from ..engine.band_composite import ht_scaled_setup, config_peak

for (mid, group), (plates, config) in assembled.prospective_ht_unified.items():
    ht_row = db.exec(
        select(HtProgressionState).where(
            HtProgressionState.movement_id == mid,
            HtProgressionState.unified_ht_group == group,
        )
    ).one()
    ht_row.ht_plates = plates
    ht_row.ht_band_config = list(config)
    ht_row.pending_ht_plates = None
    ht_row.pending_ht_band_config = None
    db.add(ht_row)

    # Spec 52: push the scaled value to every slot derived from this group.
    by_id = {bp.id: bp for bp in db.exec(select(BandPair)).all()}  # confirm exact BandPair->Band conversion pattern used elsewhere in this file/module before writing
    new_peak = config_peak(plates, config, <Band-namedtuple-keyed-by-id, mirror the exact construction used in run_analysis.py/assembler.py -- do not re-derive it differently here>)
    derived_tes = db.exec(
        select(TierExercise).where(
            TierExercise.derived_from_unified_group == group,
            TierExercise.movement_id == mid,
        )
    ).all()
    for te in derived_tes:
        day_role = <resolve te's ProgramDay.day_role via te.tier_id -> Tier.program_day_id -> ProgramDay.day_role>
        derived_plates, derived_config = ht_scaled_setup(new_peak * te.derive_ratio, band_inventory)
        derived_state = _resolve_movement_state(db, mid, day_role)
        derived_state.ht_plates = derived_plates
        derived_state.ht_band_config = list(derived_config)
        derived_state.pending_ht_plates = None
        derived_state.pending_ht_band_config = None
        db.add(derived_state)
```

Fill in the exact `Band` construction and `TierExercise -> Tier -> ProgramDay.day_role` join precisely by reading how `assembler.py`/`run_analysis.py` already do both (they do this multiple times already this session for HT-related code) — do not invent a new pattern. `band_inventory` here is the SAME `[Band(bp.id, bp.bottom_lb, bp.peak_lb, bp.usable) for bp in db.exec(select(BandPair)).all()]` list already constructed at the top of `commit_session` if it exists there, or constructed fresh the same way — check first, don't duplicate a differently-shaped list.

**This only fires when the unified group ACTUALLY advances this commit** (i.e., only for entries actually present in `assembled.prospective_ht_unified` — which per spec 49/50's existing gating only contains an entry when a clean session staged a real advance). A commit that doesn't touch the unified group must NOT touch D6's derived state either — leave it exactly as last pushed.

## Edge Cases

- **D6's own logged HT sessions still get analyzed for non-advance bookkeeping** (active_rule, consecutive_advance_count) via the existing code path — only the actual plates/band write is suppressed (via the `band_inventory=None` trick above). Confirm this doesn't produce a confusing `active_rule` value on D6's row — read what `_rule_driven` sets `active_rule` to on its no-advance path (should still be `ProgressionRule.RULE_DRIVEN.value`, matching every other HT slot) and confirm this is fine to display, not a red flag.
- **First deploy / backfill**: this spec must ALSO immediately correct D6's live `MovementState.ht_plates`/`ht_band_config` to the true 80%-of-270 value (170.0, [1] per the worked example above — Tier A verifies this exact value empirically, doesn't trust the spec's own arithmetic blindly) as part of deployment, since the push-on-advance mechanism only fires on a FUTURE unified advance, not retroactively. This is a one-time manual data correction Tier A applies directly after this spec merges and deploys (mirrors the Face Pull `current_load` correction pattern from earlier tonight) — not something the script/migration needs to handle itself.
- **What if D2/D5's unified group NEVER advances again**: D6's derived value simply stays at whatever it was last pushed (or the one-time backfill correction above, if no unified advance has happened since). This is correct — "derived" doesn't mean "recomputed every session," it means "recomputed whenever the source of truth changes."
- **Two unified-group advances in quick succession** (e.g. D2 and D5 both logged clean before either regenerates — spec 50's existing documented edge case): the SECOND commit's push simply overwrites D6's derived value with the newer 80%-of-the-latest-unified-peak computation. Fine, matches spec 50's existing "redundant, not a conflict" reasoning for the analogous unified-group case.
- **`_confirmation_window`/tier-advance side effects on D6's own `MovementState` row**: confirm `d.new_consecutive_advance_count`/other non-HT-specific delta fields still get written normally by `apply_analysis` for D6's row (only `pending_ht_plates`/`ht_plates` itself are suppressed/redirected) — this spec must not accidentally break D6's OTHER bookkeeping fields.

## File Targets

- `ironlog/engine/band_composite.py` — new `ht_scaled_setup` function.
- `ironlog/models/program.py` — `TierExercise.derived_from_unified_group`, `TierExercise.derive_ratio`.
- `deploy/migrations/0XX_ht_derived_from_unified.sql` — additive, confirm next number.
- `ironlog/persistence/run_analysis.py` — `_is_derived_ht_slot` helper + guard on the `advance()` call's `band_inventory` argument.
- `ironlog/generation/loop.py` — `commit_session`'s new derived-push step after the unified-group write loop.
- `ironlog/generation/program_seed.py` / `docs/program/phase1-seed-source.yaml` — set D6's HT `TierExercise` fields (`derived_from_unified_group="main"`, `derive_ratio=0.8`) so a future reseed doesn't regress it.
- `tests/test_band_composite.py` (or wherever `ht_next_setup`'s tests live) — new tests for `ht_scaled_setup`.
- New test file, e.g. `tests/test_ht_d6_derived.py` — integration tests: (1) a clean D2 or D5 session that advances the unified group correctly pushes a new 80%-scaled value to D6's `MovementState`; (2) a clean D6 session does NOT change D6's own `ht_plates` (no independent advance — the `band_inventory=None` suppression path); (3) a commit that doesn't touch the unified group leaves D6's derived state untouched.

## Verification

- New + full test suite green, zero regressions (baseline 687 passing as of this spec's writing).
- `tests/test_migrations.py::test_chain_matches_create_all` stays green.
- Manual, against a scratch copy of production (mirroring tonight's established practice for every HT-adjacent change): confirm a clean D2 session's commit correctly pushes D6's `MovementState.ht_plates`/`ht_band_config` to the new 80%-scaled value; confirm a clean D6 session's own commit does NOT change D6's `ht_plates` independently.
- After merge, Tier A applies the one-time backfill correction to D6's live `ht_plates`/`ht_band_config` (170.0/[1] per the worked example, re-verified empirically at deploy time, not copied blindly from this spec) and smoke-checks via `/generate` for D6 to confirm the corrected value is prescribed.
