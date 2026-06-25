"""
ledger.py — pure-logic aggregator that turns logged sets into a WeeklyTallies.

Implements docs/06 §5 for the two items the v0.2 validator consumes: knee
modality counts and horizontal pull/push volume. Items 3 (semi-anchor
frequency) and 4 (broader per-pattern volume) are deferred to v0.5 along
with the pattern taxonomy that's their prerequisite.

The ledger is a producer counterpart to the validator (which is a consumer
of WeeklyTallies). It re-uses validator.WeeklyTallies as its output type:
one shape, one contract. The validator's cross-session checks pair with
the ledger's projection without any glue.

Pinned units (canonical, in docstrings + tests):
  * Pull/push volume metric = load * reps per set, summed across qualifying
    sets in the window. The validator's PULL_PUSH_RATIO rule expects this
    "volume-load / tonnage" semantic. NOT sets-only, NOT reps-only.
  * Knee counts unit = distinct sessions ("frequency"), NOT sets. A session
    with three working Nordic sets contributes 1 to knee_counts["NORDIC"],
    not 3. This is the standard "Nx/wk" reading from spec §4.

Classification (the v0.3 choices):
  * Knee modality: read directly from Movement.knee_modality (the new
    nullable enum column added in v0.3).
  * Pull/push: derived from Movement.lift_category via two module-level
    frozensets. Coarse stand-in; v0.5's pattern taxonomy may supersede
    this without changing the public function signature.

Silent-skip directions (graceful degradation):
  * Warmups (is_warmup=True): never count.
  * Incomplete log (actual_load=None or actual_reps=None): no volume can
    be computed; skip.
  * Movement absent from movements dict: skip the set log entirely.
    UNDERCOUNT DIRECTION: for frequency rules (KNEE_FREQUENCY), missing
    movement -> undercount -> over-prescription, which is the safer error
    direction (more knee work than required is safe; less than required is
    not). For ratio rules (PULL_PUSH_RATIO), the direction is symmetric
    and depends on which side is missing — callers should ensure the
    movements dict is complete for the set_logs they pass.
  * Zero load (actual_load == 0): the set qualifies as working but
    contributes 0 to volume (0 * reps == 0). For bodyweight or banded
    pull/push movements (not in current seed; possible future libraries),
    this silently under-counts pull/push volume — acceptable in v0.3
    per spec §5.1; v0.5 may revisit with a bodyweight-load convention.

Not in scope (see docs/superpowers/specs/2026-06-24-weekly-ledger-design.md §10):
  * HTTP endpoint, persistent ledger table, semi-anchor frequency, broader
    pattern volume, targets sourcing, date math, apply-clamps helper.
"""

from typing import Dict, Iterable

from ..models.enums import LiftCategory
from ..models.library import Movement
from ..models.session import SetLog
from .validator import WeeklyTallies


# Coarse pull/push stand-in derived from existing lift_category.
# v0.5's pattern taxonomy may supersede this — when it does, swap the
# derivation here without changing compute_tallies's signature.
_HORIZONTAL_PULL: frozenset[LiftCategory] = frozenset({LiftCategory.ROW})
_HORIZONTAL_PUSH: frozenset[LiftCategory] = frozenset({
    LiftCategory.BENCH, LiftCategory.CG_PRESS,
})


def compute_tallies(
    set_logs: Iterable[SetLog],
    movements: Dict[int, Movement],
) -> WeeklyTallies:
    """Aggregate logged sets into a WeeklyTallies projection.

    Inputs:
      set_logs:  the logged sets to consider. Caller pre-filters by date
                 and by session status (typically COMPLETED only).
      movements: dict of movement_id -> Movement, for classification lookup.
                 Movements absent from the dict are silently skipped.

    Output: a WeeklyTallies with `knee_counts`, `pull_volume`, `push_volume`
    populated. `knee_targets` is `{}` and `pull_push_target` is `2.0` (the
    dataclass defaults) — the ledger does NOT supply targets; the caller
    merges them in from PhasePolicy / EngineState / constants when calling
    validate().
    """
    pull_volume: float = 0.0
    push_volume: float = 0.0
    knee_sessions: Dict[str, set] = {}  # modality name -> set of session_ids

    for log in set_logs:
        # Working-set gate: warmups excluded; both load and reps must be present.
        if log.is_warmup:
            continue
        if log.actual_load is None or log.actual_reps is None:
            continue
        movement = movements.get(log.movement_id)
        if movement is None:
            continue  # under-count direction documented in module docstring

        # Volume aggregation: load * reps, classified by lift_category.
        # (load == 0 contributes 0; the set still qualifies for knee-count purposes.)
        volume_contribution = log.actual_load * log.actual_reps
        if movement.lift_category in _HORIZONTAL_PULL:
            pull_volume += volume_contribution
        elif movement.lift_category in _HORIZONTAL_PUSH:
            push_volume += volume_contribution
        # else: contributes to neither (squat, hip thrust, etc.)

        # Knee-count aggregation: distinct sessions per modality.
        if movement.knee_modality is not None:
            modality_key = movement.knee_modality.value
            knee_sessions.setdefault(modality_key, set()).add(log.session_id)

    knee_counts: Dict[str, int] = {k: len(v) for k, v in knee_sessions.items()}

    return WeeklyTallies(
        knee_counts=knee_counts,
        pull_volume=pull_volume,
        push_volume=push_volume,
        # knee_targets and pull_push_target left at WeeklyTallies dataclass defaults.
    )
