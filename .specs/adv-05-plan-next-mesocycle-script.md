## Objective
Build `scripts/plan_next_mesocycle.py` — the administrative tool that plans a successor Mesocycle, conditionally instantiating its first Microcycle and transactionally updating `Macrocycle.planning_state`, per design revision 8/9's fixes.

## Context
Source: design doc §5 (the `plan_next_mesocycle.py` block, revision 8/9), §5d (template cardinality validation), §5c (`program_id`/`program_prescription_hash` set at planning time), §7 (`SUCCESSOR_PLANNED` audit event).

Depends on `adv-01` (schema), `adv-02` (hash functions), `adv-03` (`ensure_first_microcycle_instantiated`, `AdvancementLog` writes — import and reuse, do not reimplement).

## File targets
- `scripts/plan_next_mesocycle.py` (new).
- `tests/test_plan_next_mesocycle.py` (new).

## Changes
CLI script, e.g.:
```
python -m scripts.plan_next_mesocycle --macrocycle <id> --template <mesocycle_template_id> --program <program_id> [--ordinal <n>]
```
Logic, one transaction:
```
BEGIN TRANSACTION
    determine ordinal (next after the current highest Mesocycle for this Macrocycle, or explicit --ordinal)
    create the successor Mesocycle row: status=PLANNED, program_id=<given>,
      program_prescription_hash = compute_program_prescription_hash(program)  -- adv-02
    validate template cardinality (§5d): len(template.postures) == microcycle_count EXACTLY
      -- fail loudly (non-zero exit, clear error message) if mismatched, before
         committing anything
    determine predecessor Mesocycle (ordinal - 1, same Macrocycle)
    if predecessor.status == COMPLETE:
        # Macrocycle is genuinely AWAITING_NEXT_MESOCYCLE right now --
        # nothing currently active for this Microcycle to collide with
        ensure_first_microcycle_instantiated(db, successor)   -- adv-03's helper
    else:
        pass  -- do NOT instantiate yet (design revision 9 fix #1, blocker).
              -- rollover (adv-03) instantiates it later, once predecessor
                 actually completes.
    if Macrocycle.planning_state == AWAITING_NEXT_MESOCYCLE:
        Macrocycle.planning_state = ACTIVE
        log AdvancementLog(entity_type="macrocycle", entity_id=macrocycle.id,
          reason="SUCCESSOR_PLANNED", reconcile_run_id=None)  -- no run id,
          this happens outside any fixed-point loop
COMMIT
```
Print a summary on success (successor Mesocycle id/ordinal, whether Microcycle 1 was instantiated now or deferred, current `planning_state`).

## Edge cases (map to design §9)
- Run while the predecessor Mesocycle is still `ACTIVE`: successor created as `PLANNED` with zero Microcycles — no premature instantiation (this is THE headline regression guard for design revision 9's blocker #1).
- Run while the predecessor Mesocycle is already `COMPLETE`: Microcycle 1 instantiates immediately in the same transaction.
- Run a second time in a state where `ensure_first_microcycle_instantiated` would find an existing, consistent row: idempotent no-op, no error, no duplicate row (defends against an operator re-running the script, or a race with `adv-03`'s rollover discovering the same successor independently).
- Template cardinality mismatch: script exits non-zero with a clear message, creates nothing (no partial Mesocycle row left behind — the whole thing is one transaction).
- `Macrocycle.planning_state` flips to `ACTIVE` in the exact same transaction/commit as the successor row's creation — there is no observable window (even under a concurrent read) where the successor exists but `planning_state` still reads `AWAITING_NEXT_MESOCYCLE`.
- `SUCCESSOR_PLANNED` is logged distinctly from `MESOCYCLE_ADVANCED` (the latter is logged later, by `adv-03`'s reconciler, at actual activation — this script never logs `MESOCYCLE_ADVANCED`).

## Dependencies
`adv-01`, `adv-02`, `adv-03`.

## Verification
- `pytest tests/test_plan_next_mesocycle.py -q` covering every edge case above.
- Manual dry-run against a throwaway copy of the dev DB (never the live `ironlog.db` from this script's own tests) confirming the printed summary matches actual DB state afterward.
- `pytest -q` overall stays green.
