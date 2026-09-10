## Objective
Implement `reconcile_current_training_state()` — the deterministic state machine that advances Microcycles/Mesocycles over time — including the unified pending-activation branch (§3b), Microcycle lifecycle/drift transitions (§4), Mesocycle rollover with idempotent first-Microcycle instantiation (§5), and the `AdvancementLog` writes for every transition.

## Context
Source: design doc §3, §3a, §3b, §4, §4a, §5, §5a (only the hash-verification-before-generation piece belongs to `adv-04`, not here), §5b, §5d, §7, §8. This is the largest and most invariant-dense spec in the batch — read the full design doc sections listed above in full before writing code, not just this summary.

Depends on `adv-01` (schema: `MicrocycleSlot`, `AdvancementLog`, `Macrocycle.planning_state`, `Mesocycle.program_id`/`program_prescription_hash`, `Microcycle.slot_topology_hash`, `MicrocycleLifecycleStatus.INCOMPLETE`) and `adv-02` (`compute_program_prescription_hash`, `compute_slot_topology_hash`).

## File targets
- `ironlog/engine/advancement.py` (new file) — the reconciler and all its helpers.
- `ironlog/config.py` (or wherever app-wide config constants live — confirm exact location) — add `TRAINING_TIMEZONE` constant and a `local_today()` helper (design §8). If no central config module exists, create `ironlog/config.py`.
- `tests/test_advancement.py` (new file).

## Changes

### `local_today()` (design §8)
One function, one config value (`TRAINING_TIMEZONE`), used by every date/drift comparison in this module — never a scattered `date.today()`.

### `reconcile_current_training_state(db) -> ReconcileResult` (design §3)
```python
@dataclass
class ReconcileResult:
    transitions: list[Transition]
    final_microcycle_id: Optional[int]
    final_mesocycle_id: Optional[int]
    blocked_reason: Optional[str]  # None | "AWAITING_NEXT_MESOCYCLE" | "INCOMPLETE_MICROCYCLE"
                                    # | "WAITING_FOR_MICROCYCLE_START" | "PROGRAM_DRIFT"
```
Steps (design §3):
1. Refresh/evaluate RecoveryStatus (existing logic — locate and call, do not reimplement).
2. Evaluate DeloadState — no-op placeholder (design §6 — literally a pass-through, do not build deload trigger logic).
3. Fixed-point loop: **each iteration checks §3b's pending-activation branch first**, then whichever other transition is due (drift/completion check, rollover discovery). Loop until no transition is due or a blocking state is reached (all four `blocked_reason` values are legitimate stops). Cap iterations (e.g. 50) and log an error if hit — this should never happen in correct operation, it's a safety valve against an infinite-loop bug.
4. Resolve effective policy — conditionally, per §3a (call existing `resolve_envelope()` only when `blocked_reason is None`; this spec does NOT change `resolve_envelope()` itself, just gates the call).

Steps 1-3 run in one DB transaction with the current active Mesocycle/Microcycle locked (`SELECT ... FOR UPDATE` or SQLAlchemy's `with_for_update()` if the DB backend supports it — confirm SQLite's actual locking behavior and document the real mechanism used, since SQLite doesn't support row-level locking the same way; a whole-transaction serialization may be the practical equivalent).

### §3b: the pending-activation branch
```python
def _find_pending_microcycle(db) -> Optional[Microcycle]:
    # NOT_STARTED, zero MicrocycleSlot rows, AND activation-eligible:
    #   same Mesocycle: predecessor Microcycle (ordinal-1) is COMPLETE
    #   first of a Mesocycle: owning Mesocycle's predecessor Mesocycle
    #     (ordinal-1) is status == COMPLETE
    ...

def _activate_pending(db, pending: Microcycle) -> ActivationOutcome:
    # local_today < pending.planned_start_date -> WAITING_FOR_MICROCYCLE_START
    # else: BEGIN TRANSACTION (hash check INSIDE it -- design revision 9 fix #3)
    #   lock pending + owning Mesocycle
    #   verify program_prescription_hash vs live Program
    #     mismatch -> ROLLBACK, PROGRAM_DRIFT
    #   snapshot MicrocycleSlots from the Program
    #   compute + store slot_topology_hash from that exact snapshot
    #   if training_slot_count == 0:
    #     ROLLBACK, raise InvalidPlanConfigurationError (NOT a blocked_reason --
    #       distinct exception type, log a configuration-failure AdvancementLog
    #       row, this is a real defect in the Program/template, not a normal
    #       blocked state)
    #   else:
    #     if owning Mesocycle.status == PLANNED: activate it, actual_start_date=today,
    #       log MESOCYCLE_ADVANCED
    #     activate Microcycle, actual_start_date=today
    #   COMMIT
    ...
```
This is the ONLY place `NOT_STARTED -> ACTIVE` happens for a Microcycle, and the only place a Mesocycle's `PLANNED -> ACTIVE` happens as a *consequence* of its Microcycle 1 activating.

### Microcycle lifecycle (design §4)
- Drift bands: `drift_days = max(0, local_today - planned_end_date)`. `0` → `ON_TIME`, `1-2` → `EXTENDED`, `3-4` → `DRIFT_FLAGGED`, `>4` → remaining `PENDING` `TRAINING` slots resolve `SKIPPED`/`INFERRED_BOUNDARY`, then re-check completion. **Never produces `INCOMPLETE`** — that's operator-only (see below).
- `ACTIVE -> COMPLETE`: whenever every `TRAINING` slot's `resolution != PENDING`. Log `ALL_SESSIONS_RESOLVED` or `DRIFT_INFERRED_SKIP` (whichever applies) to `AdvancementLog`. Sets `actual_completion_date`.
- `ACTIVE -> INCOMPLETE`: **not built as an automatic transition in this spec** — expose a plain function (e.g. `mark_microcycle_incomplete(db, microcycle_id, reason)`) callable by a future operator-facing tool, but nothing in the reconciler calls it automatically. Terminal: stops the fixed-point loop, no next-Microcycle advancement, no rollover.
- `planned_posture` is never touched by any transition in this module.

### Mesocycle rollover (design §5)
Triggered when the current Mesocycle's final Microcycle reaches `COMPLETE` (not `INCOMPLETE`):
1. Close current Mesocycle (`ACTIVE -> COMPLETE`, `actual_end_date`).
2. Query next ordered `Mesocycle` with `status=PLANNED`.
3. If found: validate template cardinality (§5d — `len(template.postures) == microcycle_count` exactly). `Macrocycle.planning_state = ACTIVE` immediately. Call `ensure_first_microcycle_instantiated(successor)` (see below) — this only actually creates a row if it doesn't already exist (it may already exist if `plan_next_mesocycle.py`, built in `adv-05`, instantiated it eagerly because the predecessor was already `COMPLETE` when that script ran). The Mesocycle itself stays `PLANNED` — activation is entirely §3b's job on this or a later iteration.
4. If not found: `Macrocycle.planning_state = AWAITING_NEXT_MESOCYCLE` (only if not already), log `PLAN_EXHAUSTED` only on the transition into that state, `blocked_reason="AWAITING_NEXT_MESOCYCLE"`.

### `ensure_first_microcycle_instantiated(mesocycle) -> Microcycle` (design §5, revision 9 fix #2)
Idempotent, shared by rollover (here) and `plan_next_mesocycle.py` (`adv-05`):
```python
def ensure_first_microcycle_instantiated(db, mesocycle: Mesocycle) -> Microcycle:
    existing = query Microcycle where mesocycle_id=mesocycle.id, ordinal=1
    if existing is None:
        create it: NOT_STARTED, no slots,
          planned_posture = template.postures[0],
          dates from mesocycle's own schedule
        return new row
    else:
        verify existing.lifecycle_status == NOT_STARTED, zero slots,
          and planned dates/posture match what the template/schedule
          would produce
        if consistent: return existing
        else: raise (a genuine inconsistency, not a race -- do not
          silently reuse a mismatched row)
```
Export this from `ironlog/engine/advancement.py` so `adv-05`'s script can import it directly.

## Edge cases (map directly to design §9's regression test list — implement all of these as tests)
- A Microcycle with zero `MicrocycleSlot` rows can never reach `COMPLETE` (vacuous-truth guard, independent of the zero-training-slot activation guard above — this one matters for the already-bootstrapped Microcycle #1 before `adv-07`'s bootstrap runs, if tested in isolation).
- Planning a successor while the current Microcycle is still `ACTIVE` must NOT produce `blocked_reason="WAITING_FOR_MICROCYCLE_START"` for the *current* Microcycle — the eligibility check must correctly ignore a far-future pending Microcycle whose predecessor hasn't completed (design revision 9's headline regression guard).
- A pending Microcycle blocked on `WAITING_FOR_MICROCYCLE_START` activates automatically on a later reconciler run once due — no separate trigger needed.
- A pending Microcycle blocked on `PROGRAM_DRIFT` activates automatically on the run immediately following an `acknowledge_program_drift.py` acceptance (that script is `adv-06`; this spec's test can simulate the acceptance by directly updating `program_prescription_hash` and re-running the reconciler).
- `ensure_first_microcycle_instantiated` called twice for the same Mesocycle does not raise (idempotent no-op on the second call); it DOES raise if the existing row's stored posture/dates don't match what the template/schedule would produce.
- Posture indexing correct for all four ordinals of a 4-week template (0-indexed off 1-based ordinal).
- A `Macrocycle` stuck at `AWAITING_NEXT_MESOCYCLE` correctly moves to `ACTIVE` once a successor is planned (this spec tests the reconciler side; `adv-05` tests the script side that actually sets it).
- The hash-mismatch branch of the activation transaction rolls back cleanly with no partial writes (no slots persisted, no status transitions applied).
- A zero-`TRAINING`-slot activation attempt raises `InvalidPlanConfigurationError`, never sets a `blocked_reason`.
- `two Program structures that differ only in row/JSON-key order` — this is `adv-02`'s test, not this spec's; do not duplicate, just ensure this module calls `adv-02`'s functions rather than reimplementing hashing.

## Dependencies
`adv-01`, `adv-02`.

## Verification
- `pytest tests/test_advancement.py -q` covering every edge case above.
- `pytest -q` overall stays green.
- Manual trace: construct a small in-memory scenario (2 Mesocycles, 2 Microcycles each) and step the reconciler through a full lifecycle by hand, confirming `AdvancementLog` rows match expectations at each step.
