# HT (band-composite) performed-floor reconciliation — Design

## Problem

`ht_next_setup` (`ironlog/engine/band_composite.py`) — the peak-search that decides Hip Thrust's next plates/band setup — always advances from `MovementState.ht_plates`/`ht_band_config`, the system's own last *committed* setup, never from what was actually logged. Confirmed live tonight: the athlete self-selected heavier plates than prescribed (170 vs. the system's stored 165) and hit clean reps, logging `felt_peak=260`. The system had no way to know this — `run_analysis.py`'s `_rule_driven()` treats HT as a permanent no-op ("this rule always no-ops for COMPOSITE/HIP_THRUST movements... one progression path, not two"), and the real advance lives entirely inside `assembler.py`'s generation-time `ht_next_setup` call, which only ever reads the stored setup. The earned advance never lands — the system just keeps ratcheting up by one small step from its own stale number, permanently lagging behind what the athlete has already proven.

This is the third occurrence of the same underlying pattern in one evening (Hip Thrust twice, Nordic Curl once via a separate, larger, deferred fix). Manual DB corrections got tonight's sessions right, but a real code fix closes the gap going forward.

## Scope decisions (from brainstorming)

- **HT (band-composite) only.** The assist-ladder case (Nordic Curl, Face-Up Incline Knee Raise) hit the same failure mode tonight but needs a genuinely new client capture field (there's no existing "actual assist level used" signal, unlike HT's `felt_peak`) — a larger lift, explicitly deferred to a separate design.
- **Reconcile using data already captured.** HT sets already log `felt_peak` (a subjective "what did the total resistance feel like" number) on every working set. No new client capture, no new schema.
- **Same-config only.** The fix only reconciles a self-selected *plates* change within the *same* band config as what's currently stored — not a self-selected band swap. Inferring which band was actually used purely from a total `felt_peak` number isn't reliably possible (multiple band+plate combinations can produce the same total), so a config mismatch is treated as "can't reconcile, fall through to today's behavior" rather than guessed at.
- **Respect the existing Option-C boundary.** `commit_session` is documented as "THE ONLY PLACE generation writes `ht_plates`/`ht_band_config`" — this fix does not add a second writer. It only changes what *input* feeds the existing `ht_next_setup` call at generation time; no new write path, no analysis-time mutation.

## Components

### 1. Extract `resolved_band_config` into the shared engine module
`ironlog/persistence/ht_refine.py` currently has a private `_resolved_band_config(sl: SetLog, ps: Optional[PlannedSet]) -> Optional[List[int]]` (PlannedSet.band_config first, falling back to band_pair_id fields). Move this to `ironlog/engine/band_composite.py` as a public `resolved_band_config`, with `ht_refine.py` importing and using the shared version instead of its own copy. Behavior is unchanged — this is a pure extraction so the new reconciliation logic (below) and the existing calibration logic share one implementation instead of two that could drift.

### 2. New engine helper: `ht_performed_floor`
In `ironlog/engine/band_composite.py`, alongside `config_peak`/`ht_next_setup`:
```python
def ht_performed_floor(plates: float, config: list, felt_peak: float, by_id: dict) -> float:
    """Floor `plates` up to what's needed to explain a logged `felt_peak` for
    the SAME `config` — mirrors performed_floor_delta's shape (never regress,
    only floor up). Returns `plates` unchanged if `felt_peak` doesn't imply a
    higher value."""
    implied_plates = felt_peak - sum(by_id[b].peak for b in config)
    return max(plates, implied_plates)
```

### 3. Reconciliation point in `assembler.py`
In `_build_exercise`, right where `_resolve_ht_current_setup(state, load)` currently produces `cur_plates, cur_config`: look up the most recent *completed* session for this `(movement_id, day_role)` (mirroring `context.py`'s existing `_recent_same_role_sessions` query shape, scoped to this one movement instead of a whole day). Find its last working `SetLog` for this movement with a non-null `felt_peak`. Resolve that set's actual band config via the now-shared `resolved_band_config`. If it matches `cur_config` exactly (same set of band ids, order-independent), replace `cur_plates` with `ht_performed_floor(cur_plates, cur_config, felt_peak, by_id)` **before** it feeds into `ht_next_setup`. If no matching prior session/config/felt_peak exists, `cur_plates` is unchanged — today's behavior, byte-for-byte.

## Data flow

Generation time only. `commit_session` remains the sole writer of `ht_plates`/`ht_band_config`, unchanged. The reconciliation only changes what value `ht_next_setup` receives as its starting point — a pure input adjustment, no new persistence, no new invariant to protect beyond the ones already in place.

## Edge cases

- **No prior completed session for this movement/day**: `cur_plates` unchanged (falls through cleanly — this is the existing cold-start behavior, untouched).
- **Prior session logged, but no `felt_peak`** (e.g. a warmup-only or otherwise incomplete log): unchanged, falls through.
- **Prior session's resolved config differs from the currently stored config** (a genuine band swap happened since): unchanged, falls through — correctly does NOT guess a plates floor against a config that no longer applies.
- **`felt_peak` implies FEWER plates than currently stored** (the athlete under-performed relative to the system's own record, or logged a lighter session): `ht_performed_floor`'s `max(plates, implied_plates)` correctly leaves `cur_plates` unchanged — this is a floor, never a ceiling, matching `performed_floor_delta`'s "never regress" contract.
- **Multiple working sets in the prior session with different `felt_peak` values**: use the LAST working set's felt_peak (mirrors the existing pattern elsewhere in this codebase for "the set that best represents the session's final effort," e.g. `_build_session_perf`'s `last_set_hit_target`) — not a max or average across sets, since a single coherent number representing "what I actually did at the end" is more meaningful than aggregating potentially-inconsistent self-reports across a giant-set round.

## Deploy classification

Code-only change (`ironlog/engine/band_composite.py`, `ironlog/generation/assembler.py`, `ironlog/persistence/ht_refine.py`'s import). No schema, no migration, no auth/secrets, no public API surface change. Class 1 restart per the Deploy Gate.

## Out of scope

- Assist-ladder movements (Nordic Curl, Face-Up Incline Knee Raise, Reverse Nordic Curl, Pull-up) — separate, larger design needing new client capture, deferred.
- Band-swap reconciliation (a self-selected DIFFERENT band, not just heavier plates in the same band) — not reliably inferable from `felt_peak` alone without new capture; out of scope.
- Any change to `ht_refine.py`'s own `BandPair.peak_lb` calibration logic beyond the shared-helper extraction — that's a different, already-working concern (band-level inventory calibration, not movement-state-level setup advancement).
