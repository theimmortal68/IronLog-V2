# Spec 50: HT D2/D5 Unified Progression

## Objective

D2 and D5 currently track Hip Thrust as two independent progressions (separate day-scoped `MovementState` rows). Unify them into one shared progression via a new day-independent table, while D6's structurally-different, deliberately-scaled HT variant stays on its own independent day-scoped track, untouched.

Design doc (approved, source of truth): `docs/superpowers/specs/2026-07-24-ht-clean-advance-and-unification-design.md`, "Fix 2".

**Schema change — HUMAN GATE required at dispatch and merge**, per the Forbidden list.

## File Targets

- `ironlog/models/library.py` — new `HtProgressionState` table (mirrors `EngineState`/`GoalSettings`'s singleton-style pattern, but keyed by `movement_id`/`unified_ht_group`, not a fixed `id=1`).
- `ironlog/models/program.py` — `TierExercise` gains `unified_ht_group: Optional[str] = None`.
- `deploy/migrations/038_ht_progression_state.sql` (confirm the actual next-available number before writing — `037` is claimed by spec 49; check for other in-flight specs too) — additive: `CREATE TABLE htprogressionstate (...)` + `ALTER TABLE tierexercise ADD COLUMN unified_ht_group VARCHAR`.
- `scripts/backfill_ht_unification.py` — new one-off Python backfill script (NOT a raw-SQL data migration — this repo's established convention for this exact class of change: schema-only migrations, then a separate Python script for data seeding/backfill; see `deploy/migrations/README.md`'s data-vs-schema distinction and the precedent from the ramp/finisher feature, `ironlog/generation/live_seed_ramp_and_finishers.py`). Sets `TierExercise.unified_ht_group = "main"` on D2's and D5's Hip Thrust slots; seeds the initial `HtProgressionState` row from the more-advanced (by `config_peak`, not raw plates) of D2's and D5's current `MovementState.ht_plates`/`ht_band_config`. Idempotent (safe to re-run).
- `ironlog/generation/assembler.py` — `_build_exercise`'s HT block branches on the resolved `TierExercise.unified_ht_group`.
- `ironlog/persistence/apply.py` / `run_analysis.py` — the Spec-49-introduced `pending_ht_plates`/`pending_ht_band_config` staging branches the same way.
- `ironlog/generation/loop.py` — `commit_session`'s HT-write branches the same way.
- `tests/test_ht_unification.py` — new file.
- `tests/test_migrations.py` — confirm the parity keystone test covers the new table/column (should be automatic given how that test works, but verify).

## Changes

### `ironlog/models/library.py` — `HtProgressionState`

```python
class HtProgressionState(SQLModel, table=True):
    """Day-independent Hip Thrust progression, decoupled from the (movement_id,
    day_id) composite key every other MovementState field uses. One row per
    (movement_id, unified_ht_group) -- NOT one row per HT movement_id alone,
    so a future second unified group (if ever needed) has a place to live
    without colliding with this one. D6's Hip Thrust slot is a deliberately-
    scaled, different-rep-scheme variant and is NEVER represented here --
    its TierExercise.unified_ht_group stays NULL, and it keeps using its own
    day-scoped MovementState.ht_plates row exactly as today."""
    id: Optional[int] = Field(default=None, primary_key=True)
    movement_id: int = Field(foreign_key="movement.id")
    unified_ht_group: str
    ht_plates: float
    ht_band_config: list = Field(sa_column=Column(JSON))
    pending_ht_plates: Optional[float] = None
    pending_ht_band_config: Optional[list] = Field(default=None, sa_column=Column(JSON))
    calibration_status: CalibrationStatus = CalibrationStatus.MEASURED

    __table_args__ = (UniqueConstraint("movement_id", "unified_ht_group"),)
```

Match this repo's exact existing `Column(JSON)`/`UniqueConstraint` syntax conventions — read 2-3 existing models with a JSON column and a composite unique constraint before writing this (e.g. `MovementState`'s own `(movement_id, day_id)` uniqueness, if it's expressed the same way).

### `ironlog/models/program.py` — `TierExercise`

Add one field:
```python
unified_ht_group: Optional[str] = None
```

### Migration

Purely additive (one `CREATE TABLE` + one `ALTER TABLE ... ADD COLUMN` — both additive schema, may share one file per this repo's carve-out). Confirm the DDL matches what SQLModel's `create_all` would emit for the new model/column (type strings, nullability, defaults) — the parity keystone test (`test_chain_matches_create_all`) will catch a mismatch, but get it right the first time by checking 2-3 recent migration files' exact phrasing for a new table + a new nullable VARCHAR column.

### `scripts/backfill_ht_unification.py` (new, one-off, idempotent)

Pseudocode (implement fully, matching this repo's existing one-off-script conventions — check `ironlog/generation/live_seed_ramp_and_finishers.py` for the established shape):

1. Find D2's and D5's Hip Thrust `TierExercise` rows (by `slot_id` — `d2_t1b` and `d5_t1b` per the current seed, but resolve by querying `Movement.lift_category == HIP_THRUST` joined through `Tier`/`ProgramDay.day_role in ("D2 Lower A", "D5 Lower B")`, not hardcoded slot_ids, so this survives a future slot rename). Set `unified_ht_group = "main"` on both. Idempotent: skip if already set.
2. Load both days' current `MovementState.ht_plates`/`ht_band_config` (via `movement_id` + `day_id`), and the band inventory (`BandPair` rows).
3. Compare via `config_peak(plates, config, by_id)` (from `ironlog.engine.band_composite`) — the row with the HIGHER peak is more advanced. Ties: prefer the setup with fewer bands (mirrors `ht_next_setup`'s own tiebreak convention — read that function's docstring to confirm this is the right tiebreak direction before assuming).
4. Idempotent upsert: if an `HtProgressionState(movement_id=12, unified_ht_group="main")` row already exists, leave it untouched (do not silently overwrite an already-migrated row on a re-run); else create it from the more-advanced values found in step 3.
5. Leave D2's and D5's own `MovementState.ht_plates`/`ht_band_config` rows in place, untouched — they become orphaned/inert once the slots stop reading them, matching this session's established precedent for similar swaps (e.g. the D2 Nordic Curl -> Leg Curl swap).
6. Log a clear summary (which values were compared, which won, what got written) — this script runs against production data, human-gated per Class 2 of this repo's Deploy Gate, same as every other data-migration script this session.

### `ironlog/generation/assembler.py` — `_build_exercise`'s HT block

The HT block currently resolves `cur_plates`/`cur_config` from `state` (the day-scoped `MovementState`, via `_resolve_ht_current_setup`). Add a branch at the top of the HT block: fetch the resolved `TierExercise` row (via `tier_exercise_id`, mirroring `_apply_slot_override`'s existing DB-fetch pattern) and check its `unified_ht_group`:

- **`unified_ht_group` is set**: resolve `cur_plates`/`cur_config` from the matching `HtProgressionState(movement_id, unified_ht_group)` row instead of `state.ht_plates`/`state.ht_band_config`. The Spec-49 pending-advance check (`state.pending_ht_plates`) similarly reads from `HtProgressionState.pending_ht_plates` instead. `prospective_ht` staging (feeding `commit_session`) needs a parallel `prospective_ht_unified: Dict[Tuple[int, str], Tuple[float, list]]` structure (keyed by `(movement_id, unified_ht_group)`, not bare `movement_id`) — reuse the same `AssembledSession` dataclass shape as `prospective_ht_setups`, added as a sibling field.
- **`unified_ht_group` is `None`** (D6, and every other HT-category movement/slot that never opts in): entirely unchanged — today's exact day-scoped `MovementState` path.

### `ironlog/persistence/run_analysis.py` / `apply.py` (Spec-49's staging, extended)

The `pending_ht_plates`/`pending_ht_band_config` staging Spec 49 introduces on `MovementDelta`/`MovementState` needs the same branch: if the movement's slot for THIS session's day is `unified_ht_group`-tagged, stage onto (and `apply_analysis` writes to) the matching `HtProgressionState` row instead of the day-scoped `MovementState` row. Resolve the `TierExercise` the same way `assembler.py` does (via the session's day + movement, not assumed).

### `ironlog/generation/loop.py` — `commit_session`

Add a parallel write loop over `assembled.prospective_ht_unified` (alongside the existing `prospective_ht_setups` loop): get-or-create the `HtProgressionState(movement_id, unified_ht_group)` row, write `ht_plates`/`ht_band_config` from the prospective value, clear `pending_ht_plates`/`pending_ht_band_config` — mirrors the day-scoped loop exactly, just against the new table.

## Edge Cases

- **D2 and D5 generated back-to-back, same day, before either is approved/logged**: both read the SAME current `HtProgressionState` row (unaffected by the OTHER day's own generation, since neither has committed yet) — both prescribe identically. This is correct, intentional "one shared progression" behavior, not a bug.
- **A clean D2 session and a clean D5 session both logged before either's next generation**: both stage `pending_ht_plates` onto the SAME `HtProgressionState` row (via Spec 49's mechanism, extended here) — the second analysis overwrites the first's staged value with an identically-computed result (both searched from the same un-advanced current setup) — redundant, not a conflict.
- **The backfill script's "more advanced of the two" comparison**: must use `config_peak`, not raw plates — a lower-plates-but-bigger-band setup can have a higher effective peak than a higher-plates-but-smaller-band one. Get this from the design doc's own edge-case note, don't re-derive from scratch.
- **D6 regression**: after this spec, generate a real D6 session and confirm its Hip Thrust slot's `TierExercise.unified_ht_group` is `None` and its prescription still reads from the day-scoped `MovementState` row exactly as before this spec — a live, explicit regression check, not just "we didn't touch that code path" by inspection.
- **A future third day adopting the unified group**: not in scope for this spec (only D2+D5 today), but the `(movement_id, unified_ht_group)` keying (a string group name, not a boolean flag) is deliberately chosen so a future third day can join `"main"` (or a differently-named second group) without a further schema change — confirm the implementation doesn't accidentally hardcode "exactly two days" anywhere.

## Dependencies

Depends on Spec 49 (clean-advance gating) merged first — this spec's `HtProgressionState.pending_ht_plates`/`pending_ht_band_config` fields and the branching logic in `run_analysis.py`/`apply.py` extend Spec 49's staging mechanism; building this spec first would leave the unified table's pending fields dead code with nothing populating them.

## Verification

- `~/projects/IronLog-V2/.venv/bin/pytest -q tests/test_ht_unification.py -v` — new tests pass (D2/D5 same-day-regenerate-reads-same-row; a clean session on either day advances the shared row; D6 regression: untouched, still day-scoped).
- `~/projects/IronLog-V2/.venv/bin/pytest -q` — full suite green, zero regressions.
- `tests/test_migrations.py::test_chain_matches_create_all` — stays green.
- Manual, against a DB copy (not production): run `scripts/backfill_ht_unification.py`, confirm it correctly picks the more-advanced of D2's/D5's real live values (180+Red vs 180+Red as of tonight — both currently equal after tonight's manual sync, so the script's tie-handling gets exercised too, not just the "one is clearly bigger" case), confirm re-running it is a no-op (idempotent), confirm D2's/D5's original `MovementState` rows are untouched.
