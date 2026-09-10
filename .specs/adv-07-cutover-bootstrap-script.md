## Objective
Build the one-time, production-critical bootstrap that gives the already-live Microcycle #1 its `MicrocycleSlot` rows, `slot_topology_hash`, and backfills existing `Session.microcycle_id`/`plan_status` — the last piece needed before the reconciler can be invoked against live data for the first time.

## PRODUCTION-CRITICAL — extra care required
This script runs exactly once, against the live `ironlog.db` (NFS-shared with the running `ironlogv2.service`). It must:
- Support a `--dry-run` mode that prints exactly what it would do without writing anything, and this spec's dispatch/review MUST verify dry-run output against the actual live DB before `--apply` is ever discussed with the user.
- Never actually run with `--apply` as part of this spec's own build/test/review cycle — `--apply` against live data is a separate, explicit, human-confirmed step, same precedent as `scripts/migrate_phase_to_periodization.py`'s original cutover.
- Be idempotent-safe to inspect (re-running `--dry-run` after a partial or aborted run should not crash) but does not need to be idempotent to actually re-`--apply` — a fresh backup before `--apply` is the real safety net, per this project's standing production-data rules.

## Context
Source: design doc §2b (the full bootstrap procedure) and §1's backfill rule (`plan_status=LEGACY` for anything pre-cutover, hard-fail for anything post-cutover that's unmappable — design revision 8 fix #5).

Depends on `adv-01` (schema — `MicrocycleSlot`, `Session.microcycle_id`/`plan_status`, `Microcycle.slot_topology_hash`), `adv-02` (`compute_slot_topology_hash`), `adv-03` (reconciler module — this script does NOT invoke the reconciler, but reuses its enums/types for consistency), `adv-04` (the binding/resolution split logic this bootstrap must replicate for already-generated sessions).

## File targets
- `scripts/bootstrap_microcycle_one.py` (new).
- `tests/test_bootstrap_microcycle_one.py` (new, runs against an in-memory/test DB fixture, never the live DB).

## Changes
```
python -m scripts.bootstrap_microcycle_one [--dry-run | --apply]
```
Logic (design §2b, in order):
1. Snapshot Microcycle #1's expected `TRAINING`/`REST` slots from the `Program` its (now-`program_id`-bound, per `adv-01`'s backfill) Mesocycle uses. Compute and store `Microcycle.slot_topology_hash` from that exact snapshot (design revision 9 fix #4 — this is the omission that revision 8 introduced and revision 9 fixed; do not skip this step).
2. For every `Session` generated since the 2026-09-04 cutover date: attempt to resolve `microcycle_id` from `prescription_snapshot.microcycle_id` JSON where resolvable, and set `plan_status=PLANNED`.
3. For each resolved session, resolve its matching `day_code` slot per the binding/resolution split (`adv-04`'s logic): check the real `Session.status` — a session generated but never completed binds the slot without resolving it (`slot.session_id` set, `resolution` stays `PENDING`); a session already `COMPLETED` resolves the slot as `COMPLETED`.
4. **Any post-cutover `Session` that cannot be deterministically mapped to a `day_code`/slot HALTS the bootstrap** (design revision 8 fix #5) — print the specific session(s) needing operator resolution, exit non-zero, write nothing. Do NOT fall back to `plan_status=PLANNED`/`microcycle_id=NULL` as a silent default.
5. Verify the resulting slot count matches the expected `TRAINING` count before considering the bootstrap successful — a zero-slot result is a hard failure, not a warning.

`--dry-run` performs steps 1-5 entirely in a rolled-back transaction (or an in-memory copy) and prints what would have changed; `--apply` commits.

## Edge cases (map to design §9)
- A post-cutover `Session` that cannot be deterministically mapped halts the bootstrap for operator resolution — assert it does NOT silently backfill as `plan_status=PLANNED`/`microcycle_id=NULL`.
- `slot_topology_hash` is present and correct immediately after this script runs, not just after normal §3b activation — `acknowledge_program_drift.py` must be able to evaluate a topology-drift check against Microcycle #1 without hitting a missing-field error.
- Zero resulting slots is a hard failure (this Microcycle #1 in particular has zero slots today, per the design doc's own note that this bug was caught in review before shipping the original periodization cutover — this bootstrap is precisely what fixes that).
- A session already `COMPLETED` at bootstrap time resolves its slot to `COMPLETED` with `resolution_source=SESSION`, not left `PENDING`.

## Dependencies
`adv-01`, `adv-02`, `adv-03`, `adv-04`.

## Verification
- `pytest tests/test_bootstrap_microcycle_one.py -q` against a test DB fixture seeded to resemble the live cutover shape (constructed from what's actually in `ironlog.db` today, read-only, never written to by the test).
- `pytest -q` overall stays green.
- **Before any `--apply` discussion**: run `--dry-run` against a fresh copy of the live `ironlog.db` (NFS copy, not the live file) and manually review the printed plan against the actual live Session/Microcycle rows.
- `--apply` against the real live `ironlog.db` is explicitly OUT OF SCOPE for this spec's dispatch/review — it requires a separate, explicit human-confirmed step after this spec merges, per this project's standing production-data rules and the precedent set by the original periodization cutover.
