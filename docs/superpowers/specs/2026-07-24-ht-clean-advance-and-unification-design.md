# HT Clean-Advance Gating + D2/D5 Unification — Design

## Problem

Two related gaps in Hip Thrust (HT) band-composite progression, both found live tonight (2026-07-24) while investigating why a clean 3x8 session didn't produce the expected next-session plate bump.

### 1. HT's plates advance is blind, not performance-gated

Every other progression rule in this engine only advances after a clean session (`_clean(perf)`: hit-target AND max_rpe<=8 AND all-sides-cleared). HT is structurally different: `advance.py`'s `_rule_driven` explicitly no-ops for HT/COMPOSITE movements (`_is_ht_composite` check) — by design, because the "next setup" computation was moved to **generation time** (`assembler.py`'s `_build_exercise`, calling `ht_next_setup` unconditionally) instead of analysis time. But nothing replaced the missing performance gate: `ht_next_setup` gets called and its result gets written to `MovementState.ht_plates`/`ht_band_config` (via `commit_session`, Option-C's sole writer) **every single time a session is approved**, regardless of whether the athlete ever trained the prior prescription, let alone whether they trained it cleanly.

Confirmed live tonight: D2's HT state advanced from a prior value to 175 the moment session 13 was *approved* — before the athlete had touched a barbell. They then trained session 13 cleanly at 175 (8/8/8, felt_peak exactly matching the prescribed setup, no self-selected deviation). Under the current design, the resulting advance to 180 wouldn't land until the session *after* the one being generated today — an unearned one-session lag, and one that would occur identically even if session 13 had been a total failure.

**User's stated rule**: HT should advance by the smallest available step (mirroring `ht_next_setup`'s own smallest-peak-increase search, which in practice reads as "+5lb equivalent") every time the athlete hits a clean 3x8 (all 3 working sets, 8 reps, `ON_TARGET`/RPE<=8) — the exact same clean-session gate every other progression rule already uses, applied to HT for the first time.

### 2. D2 and D5 track Hip Thrust as two independent progressions

`MovementState` is composite-keyed on `(movement_id, day_id)` everywhere in this engine — a deliberate pattern, added specifically to fix a real day-blind last-write-wins bug in an earlier feature (weak-point hints, spec 12: a movement like Hip Thrust genuinely has separate rows per D2/D5/D6 track, and a day-blind read silently picked an arbitrary one). Applied to HT, this means D2 and D5 — both T1b anchor slots, both 8-rep straight sets, structurally identical prescriptions — have been progressing as two unrelated numbers. Tonight the two happened to diverge (D2 at 175→180 after a clean session; D5 independently at 170→175), and the athlete confirmed, when told they track separately, that they want D2 and D5 unified into one shared HT progression.

**D6 is excluded from this unification, deliberately, by design (not an oversight):** D6's Hip Thrust slot (`hip_thrust_d6`) is a structurally different variant — 12 reps (not 8), seeded at ~80% of D5's working load (155/Orange vs D5's 205/Orange at the time it was seeded — a "Weak Points" / recovery-context day, not a straight compound-lift day). Unifying D6 into the same absolute-weight progression as D2/D5 would silently prescribe D6 at full working intensity, defeating its own design intent. The user's own request was specifically "D2 and D5" — this matches.

## Fix 1: Clean-3x8-gated HT advance (mirrors the existing K2 pattern)

The engine already has a template for exactly this shape: scalar-load movements stage an earned advance at analysis time (`run_analysis.py`, K2's `pending_load_delta`), which `commit_session` applies once and clears at the next approval (Option-C's write boundary preserved — analysis only *stages*, `commit_session` remains the sole writer of `ht_plates`/`ht_band_config`). HT gets the identical treatment instead of a new mechanism:

1. **`advance.py`**: replace `_rule_driven`'s HT no-op with real logic. When `_is_ht_composite(movement)`, if `_clean(perf)` (the session that was JUST logged): compute `ht_next_setup(state.ht_plates, state.ht_band_config, band_inventory)` and return it via two new `AdvanceResult` fields, `earned_ht_plates`/`earned_ht_band_config`. If not clean (or `session_performed` is False), no-op exactly as today (preserves the existing degraded/RPE-exempt/no-signal behavior).
2. **`run_analysis.py`**: load `band_inventory` (currently only built in `assembler.py`; mirror its exact construction from `BandPair` rows) once per `run_analysis` call, pass it into the `advance()` dispatch for HT movements. Stage `d.pending_ht_plates`/`d.pending_ht_band_config` on the delta when `earned_ht_plates` is present — same shape as `pending_load_delta`.
3. **`apply.py`**: `apply_analysis` writes `state.pending_ht_plates`/`state.pending_ht_band_config` from the delta (staging only — never `ht_plates` itself, preserving Option-C).
4. **`assembler.py`**: `_build_exercise`'s HT block stops calling `ht_next_setup` unconditionally. Instead: if `state.pending_ht_plates` is set (a clean session earned it), that staged value becomes `prospective_ht`'s "next" entry — no new search needed, the search already ran at analysis time. If not set, "next" = "current" (hold — no advance, matching every other rule's default-hold behavior). `commit_session` applies `prospective_ht` exactly as today (no change there) and additionally clears `state.pending_ht_plates`/`pending_ht_band_config` after applying (apply-once, mirroring `pending_load_delta`'s clear).

This changes WHEN and WHETHER the advance happens; it does not change `ht_next_setup`'s own search logic (already correct — verified tonight it computes 175→180 correctly) or the floor-reconciliation mechanism from spec 47 (self-selected-deviation flooring stays generation-time, unaffected — it's a different concern: "what's the athlete's real current setup" vs. "did they earn the next one").

## Fix 2: Unify D2 + D5 into one shared HT progression

New table, mirroring the existing singleton pattern (`EngineState`, `GoalSettings`: `id: Optional[int] = Field(default=1, primary_key=True)`) — but keyed by `movement_id` rather than a fixed `id=1`, since `_is_ht_movement` matches on `lift_category == HIP_THRUST`, not a single hardcoded movement id, and a future second HT-category movement (unlikely but not precluded by the current schema) should not be forced to share state with this one:

```python
class HtProgressionState(SQLModel, table=True):
    """Day-independent Hip Thrust progression, decoupled from the (movement_id,
    day_id) composite key every other MovementState field uses. One row per
    HT movement_id, shared across every day that references it -- NOT every
    day: D6's Hip Thrust slot is a deliberately-scaled, different-rep-scheme
    variant (see design doc) and keeps its own day-scoped MovementState.ht_plates
    row, untouched by this table."""
    movement_id: int = Field(primary_key=True)
    ht_plates: float
    ht_band_config: list = Field(sa_column=Column(JSON))
    pending_ht_plates: Optional[float] = None
    pending_ht_band_config: Optional[list] = Field(default=None, sa_column=Column(JSON))
    calibration_status: CalibrationStatus = CalibrationStatus.MEASURED
```

**Which days read/write this table vs. the existing day-scoped `MovementState.ht_plates`**: a new `movement.ht_unified: bool` column (default `False`) marks Hip Thrust (`movement_id=12`) as `True`. `_build_exercise`'s HT block, `apply_analysis`, and `run_analysis`'s HT staging all branch on this flag: `True` → read/write `HtProgressionState` (day-blind); `False` → today's exact behavior (day-scoped `MovementState.ht_plates`/`ht_band_config`/`pending_ht_plates`/`pending_ht_band_config` — Fix 1's staging fields land on `MovementState` too, for movements that never unify). D6's HT session generation stays entirely on the `MovementState` path regardless of the flag, because D6's `TierExercise` slot doesn't reference the SAME progression concept even though it shares `movement_id=12` — **this is the one genuinely awkward part of the design**, addressed below.

**Handling D6's exception cleanly**: rather than a per-slot flag (which `Movement.ht_unified` can't express, since it's a property of the *slot*, not the movement), D2 and D5's `TierExercise` rows for Hip Thrust get a new field `unified_ht_group: Optional[str]` (e.g., `"main"`), while D6's stays `NULL`. `_build_exercise` receives the resolved `TierExercise` (it already does, via `tier_exercise_id`) and branches on *that* row's `unified_ht_group`, not a movement-level flag: non-null → `HtProgressionState` keyed by `(movement_id, unified_ht_group)`; null → today's day-scoped `MovementState` path unchanged. This is more precise than a movement-level flag (correctly scopes to "this slot participates in the unified group," not "this movement always does") and generalizes if a future day ever wants a second, differently-scoped shared group.

**Migration for the three existing live rows**: a data migration (not just schema) sets the initial `HtProgressionState` row for `(movement_id=12, unified_ht_group="main")` to the **more advanced** of D2's and D5's current `ht_plates`/`ht_band_config` (by ladder-index-style peak comparison, mirroring tonight's assist-ladder floor logic — never regress either day's athlete-earned progress). D2's and D5's own `MovementState.ht_plates`/`ht_band_config` rows are left in place untouched (harmless, orphaned once the slots stop reading them — same precedent as tonight's other orphaned-row cases, e.g. the D2 Nordic Curl swap). D6's row is untouched and continues exactly as today.

## Edge Cases

- **A clean D2 session and a clean D5 session both logged before either's next generation**: both stage `pending_ht_plates` via Fix 1's mechanism — but since Fix 2 makes them share ONE `HtProgressionState` row, the second analysis to run overwrites the first's staged value (both computed via the same `ht_next_setup(current, ...)`, so both stage the SAME next value — not a real conflict, just a redundant write, harmless).
- **D2 and D5 generated back-to-back before either is logged** (e.g., the athlete regenerates D2 same-day as D5): both read the SAME current `HtProgressionState`, both prescribe the same setup — correct, matches "one shared progression."
- **The migration's "more advanced of the two" pick**: if D2 and D5 are on genuinely different bands (not just different plates) at migration time, comparison needs the same `ht_performed_floor`-style peak comparison (`config_peak`), not a raw plates comparison — a higher-peak, lower-plates+bigger-band setup could be more advanced than a lower-peak, higher-plates+smaller-band one.
- **`pending_ht_plates` staged but the athlete never returns to train HT again on either day**: no different from any other staged-and-never-consumed pending delta elsewhere in this engine (matches `pending_load_delta`'s existing behavior) — not a new failure mode.

## Testing

- Unit tests for `advance.py`'s new HT gating logic: clean session stages `earned_ht_plates`/`earned_ht_band_config`; unclean session no-ops exactly as today; RPE-exempt/no-signal session no-ops.
- Integration test through `run_analysis` → `apply_analysis`: a logged clean HT session results in `pending_ht_plates` set on the correct state row (day-scoped `MovementState` for D6-style non-unified slots; `HtProgressionState` for D2/D5-style unified slots).
- Integration test through `assemble()`/`commit_session`: a session generated with a pending advance prescribes the OLD setup (matching every other movement's "advancement is timed at commit" prescription-time behavior — no change there) and, once approved, moves state to the pending value and clears the marker.
- A same-day-regenerate test for the unified case: generating both D2 and D5 without an intervening commit reads the same `HtProgressionState` row and prescribes identically.
- A migration test asserting the initial `HtProgressionState` row is seeded from the more-advanced (by peak, not raw plates) of D2's/D5's pre-migration values.
- D6 regression coverage: confirm D6's HT generation/analysis path is completely unaffected (still reads/writes its own day-scoped `MovementState` row, never touches `HtProgressionState`).

## Scope

Two specs, sequential (Fix 2 depends on Fix 1's staging fields existing on both `MovementState` and the new `HtProgressionState` table — building the unification table before the gating mechanism that populates it would leave it dead code; building gating first, unification second, is the natural order and matches how this design is described above).

1. **Fix 1 — clean-advance gating**: `advance.py`, `run_analysis.py`, `apply.py`, `assembler.py`, `loop.py` (commit_session's pending-clear). No schema change.
2. **Fix 2 — D2/D5 unification**: new `HtProgressionState` table (migration), new `TierExercise.unified_ht_group` column (migration), data migration for the three existing live rows, `_build_exercise`/`apply_analysis`/`run_analysis` branching logic. **Schema change — HUMAN GATE required at dispatch and merge, per the Forbidden list.**
