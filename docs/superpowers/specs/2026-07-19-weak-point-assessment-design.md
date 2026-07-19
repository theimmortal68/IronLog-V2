# Real Weak-Point Assessment — Design

## Problem

The existing `build_weak_point_hints()` (`ironlog/generation/context.py`) is an internal, per-movement stall signal that silently nudges set generation — it's never shown to the athlete. This is a genuinely different feature: a real, athlete-visible assessment surfacing which movements and muscle groups are lagging, aggregated across the athlete's whole training, not just fed silently into generation. Third of the remaining Phase-1 roadmap items (recovery/readiness and its extensions already shipped; missed-workout handling and a standalone cardio/interval day-type are the other two, designed separately).

## Scope decisions (from brainstorming)

- **Both granularities in one view**: a muscle-group summary (headline) with the specific weak movements shown underneath each group — not movement-only or muscle-only.
- **Precomputed after each session, stored** — not computed fresh on every read. Wired into `run_analysis.py` alongside the batch's other analysis outputs.
- **Two combined signals, not stall alone**: `is_weak = stalled OR lagging`.
  - `stalled` reuses the existing `detect_stall()` output directly — no new stall computation.
  - `lagging` (new) compares each movement's e1RM growth rate against the athlete's own median growth rate across their other tracked movements — catches a genuinely underperforming lift even before it technically stalls by the strict trend-plateau definition.
- **Muscle-group aggregation is a read-time rollup, not a second persisted table.** Only the movement-level signal is precomputed/stored (the one thing worth caching — real analysis math); the muscle-group view is cheap arithmetic (weak-movement-count ÷ total-movement-count per muscle) computed live over the already-stored movement rows. Avoids a second write path that has to stay in sync.
- **Primary muscle only for aggregation** — a movement's weak/lagging signal counts fully toward its `Movement.primary_muscle`, not its `secondary_muscles`. Avoids diluting the signal (e.g. Bench Press stalling shouldn't make triceps look weak just because it's one of many tricep-involved lifts).
- **Lagging threshold**: a movement's growth rate must be at least 5 percentage points below the athlete's median growth rate across their other movements (e.g. median +8%, this movement +2% or worse → lagging). Requires at least 3 OTHER movements with sufficient data to compute a meaningful median — with fewer, the comparison is too noisy, so a movement falls back to stall-only classification.

## Components

### 1. Growth-rate computation (pure function)

New function in `ironlog/engine/stall.py` (same module as `detect_stall`, same pure/DB-free style) or a new small module if `stall.py` would grow too large — decided at spec-writing time:

```python
def compute_growth_rate(progress_anchor_e1rms: List[float]) -> Optional[float]:
    """(latest - oldest) / oldest over the given e1RM window (same 3-anchor
    PROGRESS-window the caller already selects for detect_stall). Returns
    None if fewer than 2 data points (can't compute a rate)."""
```

```python
LAG_THRESHOLD_PCT = 0.05
LAG_MIN_COMPARISON_MOVEMENTS = 3

def compute_lagging(
    this_movement_rate: Optional[float],
    other_movement_rates: List[float],
) -> bool:
    """True iff this_movement_rate is not None, at least
    LAG_MIN_COMPARISON_MOVEMENTS other rates are available, and
    median(other_movement_rates) - this_movement_rate >= LAG_THRESHOLD_PCT."""
```

### 2. `MovementWeaknessSignal` model + migration

New table, one row per movement per computation (append-only history, not a singleton — the athlete's weak points change over time and a trend view is plausible future value):

```python
class MovementWeaknessSignal(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    movement_id: int = Field(foreign_key="movement.id", index=True)
    session_id: int = Field(foreign_key="session.id")
    computed_at: datetime = Field(default_factory=datetime.utcnow)
    stalled: bool
    growth_rate: Optional[float] = None
    lagging: bool
    is_weak: bool  # stalled or lagging
```

Migration: additive `CREATE TABLE`, single statement.

### 3. Wiring in `run_analysis.py`

After the existing per-movement analysis loop (which already computes stall-relevant data for the generation-hint path), for each analyzed movement with an anchor this session:
- Compute `growth_rate` via `compute_growth_rate()` using the same PROGRESS-anchor window already resolved for stall detection.
- Collect growth rates across ALL the athlete's movements with sufficient data (not just this session's logged movements) to compute the comparison population.
- Compute `lagging` via `compute_lagging()`.
- Write one `MovementWeaknessSignal` row per movement.

### 4. `GET /weak-points` endpoint

Reads the MOST RECENT `MovementWeaknessSignal` row per movement (latest `computed_at` per `movement_id`), returns:
```json
{
  "muscle_groups": [
    {"muscle": "HAMSTRINGS", "weak_count": 2, "total_count": 3, "weak_movements": [...]},
    ...
  ],
  "movements": [
    {"movement_id": 1, "name": "Romanian Deadlift", "stalled": true, "lagging": false, "growth_rate": -0.01},
    ...
  ]
}
```
Muscle-group rollup groups by `Movement.primary_muscle`, computed live from the latest-per-movement rows — no separate storage.

## Data flow

```
run_analysis.py (per movement with an anchor this session)
        │
        ├──▶ detect_stall() [existing]  ──▶ stalled
        ├──▶ compute_growth_rate() [new] ──▶ growth_rate
        └──▶ compute_lagging() [new, vs all-movements median] ──▶ lagging
                        │
                        ▼
              MovementWeaknessSignal row (stored)

GET /weak-points ──▶ latest row per movement ──▶ group by primary_muscle (read-time rollup) ──▶ response
```

## Testing approach

- Pure unit tests for `compute_growth_rate`/`compute_lagging`: correct rate math, `None` on insufficient anchors, the exact 5-point-below-median threshold at its boundary, the minimum-3-comparison-movements fallback (fewer → `lagging=False` regardless of the numbers, falls back to stall-only via the `is_weak = stalled or lagging` combination — stall still applies independently).
- `run_analysis.py` integration test: seed movements with known e1RM histories producing a known median, confirm the right movements get flagged.
- Endpoint test: seed `MovementWeaknessSignal` rows across multiple computation dates, confirm only the LATEST row per movement is used; confirm muscle-group rollup counts only `primary_muscle`, not `secondary_muscles`.

## Explicitly out of scope

- Any UI/trend visualization — server-first, same pattern as every other feature this session.
- Extending or modifying the existing `build_weak_point_hints()` generation-hint mechanism — untouched, stays exactly as it is, serving its existing silent-nudge purpose.
- A trend/history view over `MovementWeaknessSignal` rows (even though the schema supports it via append-only rows) — `GET /weak-points` only surfaces the latest state in this pass.
- Secondary-muscle-weighted aggregation — primary-only for this pass, per the brainstorming decision.
