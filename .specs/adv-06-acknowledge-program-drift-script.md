## Objective
Build `scripts/acknowledge_program_drift.py` — the administrative recovery tool for a `PROGRAM_DRIFT` block, including the topology-vs-prescription distinction that protects an already-`ACTIVE` Microcycle from a replan disguised as an ordinary acknowledgment.

## Context
Source: design doc §5c (recovery mechanism, `slot_topology_hash` refusal rule, revision 8 fix #2).

Depends on `adv-01` (schema: `program_prescription_hash`, `slot_topology_hash`, `AdvancementLog`), `adv-02` (hash functions), `adv-03` (for locating the relevant `Mesocycle`/`Microcycle` and understanding `MicrocycleLifecycleStatus`).

## File targets
- `scripts/acknowledge_program_drift.py` (new).
- `tests/test_acknowledge_program_drift.py` (new).

## Changes
CLI script:
```
python -m scripts.acknowledge_program_drift --mesocycle <id> [--accept-current-program-revision]
```
Logic:
```
mesocycle = load Mesocycle by id
current_hash = compute_program_prescription_hash(mesocycle.program)  -- adv-02
planned_hash = mesocycle.program_prescription_hash

print planned_hash vs current_hash side by side (best-effort diff summary
  if cheaply derivable -- not required to be sophisticated)

if not --accept-current-program-revision:
    exit 0  -- status check only, no mutation

active_microcycle = the Microcycle on this Mesocycle with
  lifecycle_status == ACTIVE (may be None)

if active_microcycle is not None:
    current_topology_hash = compute_slot_topology_hash(mesocycle.program)
    if current_topology_hash != active_microcycle.slot_topology_hash:
        REFUSE -- print a clear message that this is a topology change under
          running training, not a prescription update, and requires the
          explicit interruption/replan path (mark_microcycle_incomplete,
          adv-03) instead; exit non-zero; do NOT update anything

BEGIN TRANSACTION
    mesocycle.program_prescription_hash = current_hash
    log AdvancementLog(entity_type="mesocycle", entity_id=mesocycle.id,
      reason="PROGRAM_DRIFT_ACKNOWLEDGED",
      details_json={"old_hash": planned_hash, "new_hash": current_hash})
COMMIT
```

## Edge cases (map to design §9)
- Refuses acknowledgment against an `ACTIVE` Microcycle when `slot_topology_hash` no longer matches the live `Program`, even with `--accept-current-program-revision` passed.
- Accepts a prescription-only change (topology hash unchanged) against that same `ACTIVE` Microcycle.
- No `ACTIVE` Microcycle yet (Mesocycle still `PLANNED`, or its pending Microcycle hasn't activated) — acknowledgment proceeds without any topology check, since nothing has been snapshotted yet for a mismatch to matter against.
- Running without `--accept-current-program-revision` never mutates anything, regardless of whether a mismatch exists (bare status check).
- The `AdvancementLog` row records both the old and new hash values, not just "acknowledged."

## Dependencies
`adv-01`, `adv-02`, `adv-03`.

## Verification
- `pytest tests/test_acknowledge_program_drift.py -q` covering every edge case above.
- `pytest -q` overall stays green.
