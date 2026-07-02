# Task 2 report — Phase-1 seed reconciliation

Commit: `f5c3c3b` on branch `feat/in-gym-logging`

## What changed

### `ironlog/seed.py` (MOVEMENTS + loader)

**Scheme flips (Movement-level, 2):**
| Movement | old scheme | new scheme |
|---|---|---|
| Belt Squat [GHR + FT] | TOPSET_BACKOFF | STRAIGHT |
| RDL [PB] | TOPSET_BACKOFF | STRAIGHT |

**Unilateral flags (8):** Meadows Row [OB + LM], Bulgarian Split Squat [DB], ATG Split
Squat (bracket-less base variant only — not `ATG Split Squat [BW]`), Cross-Body Cable
Rear Delt Fly [FT], Cross-Body Cable Lateral Raise [FT], Single-Arm DB Row [DB],
Poliquin Step-up, Staggered RDL [PB] — all now `unilateral=True`.

**Loader wiring:** `seed()`'s pass-1 `Movement(...)` constructor now passes
`unilateral=m.get("unilateral", False)` — previously the dict key was silently dropped.

### `ironlog/generation/program_seed.py` (TierExercise + Tier)

**13 rep target literal changes** (slot_id → new rep_low/rep_high): d1_t1 Bench Press
(8,8), d1_t2a Pendlay Row Narrow (8,8), d1_t2b Incline DB Press (10,10), d1_t2c
Face-Up Incline Knee Raise (15,15), d1_t3a Pull-up (8,8), d1_t3b Cross-Body Lateral
Raise (12,12), d1_t3c Cross-Body Rear Delt Fly (12,12), d1_t4a Seated Cable Row
(12,12), d1_t4b Ab Wheel Rollout (8,8), d1_t4c Lat Prayer (12,12), d4_t1 Assisted
Pull-up (5,8), d6_g1b Dips (5,8), d5_t3d Hyper Pro Calf Raise (10,12). All other
`_add_te` calls unchanged.

**RPE cap (1):** d6_g3c (Reverse Hyper Recovery, D6 GS3) gets `rpe_cap=6.0`.

**Tier.rest_seconds — all 18 seeded tiers**, by tier_label: T1=120, T1b=120,
T2 GS=90, GS1=90, GS2=90, T3=60, T3 GS=60, T4 GS=60, GS3=60. Coverage: D1 (T1/T2 GS/
T3 GS/T4 GS), D2 (T1/T1b/T2 GS/T3), D4 (T1/T2 GS/T3 GS), D5 (T1/T1b/T2 GS/T3 GS), D6
(GS1/GS2/GS3).

## Live-DB reconciliation (`scripts/reconcile_phase1.py`)

Blocking prerequisite found: migration `012_add_movement_unilateral.sql` existed in
the repo (committed 882f209) but had **not actually been applied** to the live DB —
`movement.unilateral` didn't exist as a column, so the reconcile script failed with
`no such column: movement.unilateral` on first run. Ran
`python -m ironlog.migrate` first (applied `012_add_movement_unilateral`), then
re-ran the reconcile script successfully. This contradicts the task framing's
"already applied live" claim for migration 012 — flagged for the orchestrator.

Reconciliation result (all live rows changed to target, no unexpected diffs on
spot-check):
- Movement.scheme flips: 2/2
- Movement.unilateral flags: 8/8
- TierExercise rep targets: 13/13
- TierExercise rpe_cap: 1/1
- Tier.rest_seconds rows: 18/18

Also generated `deploy/migrations/013_phase1_reconciliation.sql` (guarded, idempotent
UPDATEs, no schema change) so a future reseed/fresh-DB deploy picks up the same
reconciliation via the normal migration chain.

## 3 flagged discrepancies (no scope expansion — flagged only)

1. **Bench Press scheme not actually flipped.** The design doc (§S1) claims "flip the
   remaining Movement.scheme TOPSET_BACKOFF → STRAIGHT: Belt Squat, RDL (Bench already
   done)" — but `Bench Press [PB]` in `ironlog/seed.py` is still
   `scheme=Scheme.TOPSET_BACKOFF`. The doc's claim that Bench is "already done" is
   false against the actual seed state. Left untouched per explicit task scope
   (Belt Squat + RDL only) — follow-up item for a later task if intended.
2. **TierExercise.scheme left untouched.** `TierExercise.scheme` (string field on TE
   rows, e.g. `d2_t1` and `d5_t1` still pass `scheme="TOPSET_BACKOFF"` literally in
   `program_seed.py`) is a separate field from `Movement.scheme` and was explicitly
   out of scope — the acceptance test (`test_schemes_straight`) checks
   `Movement.scheme` only. Minor inconsistency between TE.scheme and the underlying
   Movement.scheme now exists for Belt Squat (d2_t1) and RDL (d5_t1) TEs; flagged for
   a later task.
3. **Two doc/seed name mismatches (nothing to change, no matching row exists):**
   - "Prone DB Rear Delt Fly" (design doc §S1, `10-12` row) — no Movement or
     TierExercise with that exact name exists. Closest candidate is `d4_t3a`
     ("Cross-Body Rear Delt Fly", D4 T3 GS) — a different movement name, already
     seeded at (10,12) so coincidentally matches the target value, but the doc's name
     doesn't resolve to any real TE.
   - "Face Pull" (design doc §S1, `12-15` row) — library `Movement` exists as
     "Face Pull w/ ER Hold [FT]" but **no TierExercise references it anywhere** in
     `program_seed.py` — it's not part of the seeded Phase-1 program at all.

## Test results

- `tests/test_phase1_reconciliation.py` (new, 6 tests): rep targets (changed + 3
  unchanged controls: d2_t1, d5_t1, d4_t3a), tier rests (all 9 labels, all rows
  checked per label), schemes (Belt Squat + RDL == STRAIGHT), unilateral (8 True + 1
  control False), rpe_cap (d6_g3c == 6.0). Confirmed FAIL against unmodified source
  first (5 failed / 1 passed — the passing one was the unchanged-controls guard,
  correctly already matching), then PASS after edits (6/6).
- `tests/test_migrations.py`: 12/12 passed, including the parity keystone
  `test_chain_matches_create_all` (013 is data-only, no schema drift).
- Full suite: **284 passed, 0 failed** (350 warnings, all pre-existing
  `datetime.utcnow()` deprecation noise, unrelated to this change).

### One pre-existing test required a fix (not in the original 5-file list)

`tests/test_library_seed.py::test_topset_backoff_is_exactly_the_six` asserted
`Movement.scheme == TOPSET_BACKOFF` was exactly a hardcoded 6-movement set including
Belt Squat and RDL. That invariant is now false by design (this task's whole point).
Updated: renamed to `test_topset_backoff_scheme_is_exactly_the_four`, added a new
`TOPSET_BACKOFF_SCHEME_FOUR` constant (Bench Press, Back Squat, Front Squat, Standing
OHP) distinct from the still-6-member `TOPSET_SIX` (which the separate
`rpe_capped`-based test still correctly uses — `rpe_capped` is a different field and
was NOT touched by this task, so it's still true for all 6 original movements
including Belt Squat and RDL). Included in the commit (6 files total, not the
original 5) since leaving it broken would violate the "0 failed" full-suite
requirement for this same change.

## Commit

`f5c3c3b` — `feat(seed): Phase-1 reconciliation — literal rep targets, tier rests,
straight schemes, unilateral flags, RevHyper rpe_cap`

Files: `ironlog/seed.py`, `ironlog/generation/program_seed.py`,
`scripts/reconcile_phase1.py` (new), `deploy/migrations/013_phase1_reconciliation.sql`
(new), `tests/test_phase1_reconciliation.py` (new), `tests/test_library_seed.py`.

Unrelated pre-existing changes (`.superpowers/sdd/task-5-report.md`, `.env.bak-*`,
`docs/superpowers/plans/2026-06-30-payload-enrichment.md`, `ironlog.db.*-bak-*`) were
left untouched, not staged, not committed.

## FIX (Task 2 review)

The review of this task found the two flagged discrepancies above ("3 flagged
discrepancies" #1 and #2) were real gaps, not out-of-scope — both are fixed here.

### Fix 1 — Bench Press seed-source scheme (Important)

`ironlog/seed.py` line 117 (Bench Press [PB] `dict(...)`): `scheme=Scheme.TOPSET_BACKOFF`
→ `scheme=Scheme.STRAIGHT`. Live Bench had already been hotfixed to STRAIGHT directly
against the DB at some earlier point (confirmed by live query pre-migration: Movement
row already read STRAIGHT), but the seed **source** still said TOPSET_BACKOFF — a
from-scratch reseed would have silently regressed Bench back to a 2-set top+backoff
scheme (the class of bug that produced the 148.5 mis-generation). Back Squat, Front
Squat, and Standing OHP remain `TOPSET_BACKOFF` — they're out-of-Phase-1 alternates,
dormant, explicitly out of scope.

### Fix 2 — TierExercise.scheme sync (Minor-but-real)

`ironlog/generation/program_seed.py`: the three T1 anchor `_add_te(...)` calls whose
`Movement.scheme` was reconciled to STRAIGHT still passed the literal string
`scheme="TOPSET_BACKOFF"` for the `TierExercise` row:
- `d1_t1` (Bench Press [PB]) → `scheme="STRAIGHT"`
- `d2_t1` (Belt Squat) → `scheme="STRAIGHT"`
- `d5_t1` (RDL) → `scheme="STRAIGHT"`

`ironlog/generation/context.py` (`build_context_payload`, ~lines 345-359) reads
`te.scheme` into `slot_rep_schemes[slot.slot_id]["scheme"]`, which flows into the
injected LLM prompt payload. The deterministic session assembler reads
`Movement.scheme` (already correct pre-fix) and never touches `TierExercise.scheme`,
so there was no session-plan corruption — but the model was being shown a stale
`TOPSET_BACKOFF` label for these three slots. No change was needed in `context.py`
itself; it correctly just relays whatever is on the TE row.

### Migration 014

New `deploy/migrations/014_scheme_consistency.sql` — data-only, guarded/idempotent
(matches the `013` pattern: every `UPDATE` has a `WHERE col != 'target'` guard):
- `movement.scheme = 'STRAIGHT' WHERE name = 'Bench Press [PB]' AND scheme != 'STRAIGHT'`
- `tierexercise.scheme = 'STRAIGHT' WHERE slot_id IN ('d1_t1','d2_t1','d5_t1') AND scheme != 'STRAIGHT'` (written as 3 separate guarded `UPDATE` statements, one per slot_id, following 013's one-statement-per-fact style)

### Live apply + ledger

Live DB (`~/projects/IronLog-V2/ironlog.db` on myflix, `ironlogv2.service`) pre-check
via direct SQL showed `schema_migrations` stopped at `012_add_movement_unilateral`
(matches the review's note that "012 may have just been applied" — `013` had never
been stamped, even though its guarded UPDATEs had apparently already been applied to
the live rows out-of-band, since `Movement.scheme` for Bench/Belt Squat/RDL and
`TierExercise.scheme` for `d1_t1` already read STRAIGHT pre-migrate; `d2_t1` and
`d5_t1` TE rows were still `TOPSET_BACKOFF` pre-migrate).

Ran `.venv/bin/python -m ironlog.migrate` on myflix: `applied: ['013_phase1_reconciliation', '014_scheme_consistency']`.
Post-apply ledger is `000`...`014` fully stamped, contiguous, no gaps. Post-apply live
query confirms: `Movement.scheme` STRAIGHT for Bench/Belt Squat/RDL; `TierExercise.scheme`
STRAIGHT for `d1_t1`/`d2_t1`/`d5_t1`.

### Tests

- `tests/test_library_seed.py`: `test_topset_backoff_scheme_is_exactly_the_four` →
  renamed `test_topset_backoff_scheme_is_exactly_the_three`; `TOPSET_BACKOFF_SCHEME_FOUR`
  (Bench, Back Squat, Front Squat, Standing OHP) → `TOPSET_BACKOFF_SCHEME_THREE` (Back
  Squat, Front Squat, Standing OHP; Bench dropped out). `TOPSET_SIX` (`rpe_capped`-based,
  unaffected) left as-is.
- `tests/test_phase1_reconciliation.py`: extended `test_schemes_straight` with an
  assertion that `Bench Press [PB].scheme == Scheme.STRAIGHT`; added new
  `test_te_schemes_synced_to_straight` asserting `TierExercise.scheme == "STRAIGHT"`
  for `d1_t1`/`d2_t1`/`d5_t1`.
- Results (all run on myflix, `.venv/bin/pytest`):
  - `tests/test_library_seed.py tests/test_phase1_reconciliation.py`: 17 passed, 0 failed
  - `tests/test_migrations.py` (parity keystone `test_chain_matches_create_all` included): 12 passed, 0 failed
  - Full suite: **285 passed, 0 failed** (350 warnings, all pre-existing `datetime.utcnow()`
    deprecation noise, unrelated to this change; +1 vs. the prior 284 baseline from the
    one new test function added)

### Commit

Files: `ironlog/seed.py`, `ironlog/generation/program_seed.py`,
`deploy/migrations/014_scheme_consistency.sql` (new), `tests/test_library_seed.py`,
`tests/test_phase1_reconciliation.py`, this report.

Unrelated pre-existing working-tree changes (`.superpowers/sdd/task-5-report.md`,
`.env.bak-*`, `docs/superpowers/plans/2026-06-30-payload-enrichment.md`,
`ironlog.db.*-bak-*`) were left untouched, not staged, not committed — same as Task 2.
