# Spec 04: Ramp sets for heavy barbell anchors (build-plan item A)

## Objective
Auto-derive a 3-set warm-up ramp (~40/60/80% of the day's working load, reps 5/3/2, rounded to an achievable plate value) for the heavy-barbell T1 anchor lifts (Bench Press, Belt Squat, RDL, and their meso-cycle swaps), and surface it on the session response ahead of the working sets — closing the gap `GenerateResponse.scope` currently documents as "warmups/finishers/Z2 per program doc, not yet in-app."

## Background / design decision
No existing model field distinguishes "heavy barbell anchor eligible for a ramp" from any other movement. **Decision: add `Movement.ramp_eligible: bool = False`**, seeded `true` only for: `bench_press`, `belt_squat`, `back_squat` (d2 meso-2 swap), `rdl_d5`, `rdl_conventional`, `rdl_staggered`. This is additive, matches the existing `Movement.min_step`/`load_floor`-style per-movement flag pattern, and avoids brittle name-matching in the assembler. Do not reinterpret "heavy barbell" any more broadly than this explicit list — Hip Thrust, Pull-up, and all giant-set accessories are excluded by construction (their `ramp_eligible` stays `False`).

Ramp sets reuse the existing `SetRole.RAMP` enum member (already defined, never constructed) and set `is_warmup=True` so they're excluded from RPE/ledger analysis exactly like any other warmup set — no new exclusion logic needed. Round via the existing `round_to_achievable()` helper (`ironlog/engine/loading.py:11`), same `step`/`floor` the movement's working sets already use (`_step_and_floor`, assembler.py:75-82). Use negative `set_index` values (`-3, -2, -1`) so ramp sets sort before the working sets' `0, 1, 2` without renumbering anything downstream (`set_index` is used as part of a `(group_index, movement_id, set_index)` lookup key in `repair.py` and for ordering in `_serialize_session` — negative values are safe and collision-free).

## File targets
- Migration: `deploy/migrations/025_ramp_eligible.sql` — single-statement additive `ALTER TABLE movement ADD COLUMN ramp_eligible BOOLEAN NOT NULL DEFAULT 0;` (follow `024_pending_load_delta.sql`'s comment-block style).
- Modify: `ironlog/models/library.py` — `Movement` class: add `ramp_eligible: bool = False` field (near the other per-movement scalar flags, e.g. next to `cap`/`min_step`).
- Modify: `ironlog/generation/program_seed.py` — set `ramp_eligible=True` on exactly the six movements named above (wherever each is constructed/seeded); leave every other movement's default (`False`) untouched.
- Modify: `ironlog/generation/assembler.py`:
  - `_build_exercise` (assembler.py:231-309): after `sets = _sets_for_scheme(...)` (line 266-267), if `is_anchor and movement.ramp_eligible and load is not None`, compute 3 ramp `PlannedSet`s (40/60/80% of `load`, reps 5/3/2, each rounded via `round_to_achievable(pct_load, floor, step)` using the same `step`/`floor` already resolved at line 243) and prepend them to `sets` before constructing `ex.planned_sets.extend(sets)`. Each ramp set: `set_index` ∈ {-3, -2, -1} (40%→-3, 60%→-2, 80%→-1), `set_role=SetRole.RAMP`, `is_warmup=True`, `target_reps_low=target_reps_high=`{5,3,2} respectively, `target_rpe=None` (ramp sets aren't RPE-targeted).
  - `assemble()` already passes `is_anchor=True` for T1 anchors (verify at the anchor-construction call site, assembler.py ~343+) — no signature change needed to `_build_exercise` itself since `is_anchor` and `movement` are both already parameters; just read `movement.ramp_eligible` inside the existing anchor branch.
- No change to `ironlog/api/schemas_capture.py` — `PlannedSetOut` already carries `set_index`, `set_role`, `is_warmup`, `target_load`, `target_reps_low/high`, `target_rpe`; ramp sets serialize through the existing `ExerciseOut`/`PlannedSetOut` path with no new fields.

## Edge cases
- **`load is None` (needs-calibration or bodyweight anchor)**: skip ramp generation entirely for that exercise this session — never fabricate a ramp off a placeholder/floor value. (Bodyweight anchors like Pull-up are excluded anyway via `ramp_eligible=False`, but the guard must be explicit regardless.)
- **A LOAD `SlotMovementOverride` is active on this slot**: ramp percentages must be computed off the load `_apply_slot_override` returns (post-override), matching what the working sets actually prescribe this session — not the pre-override base. (I.e. compute ramp sets AFTER `_apply_slot_override` runs, using its returned `load`, not the earlier `resolve_start_load`/bridge value.)
- **Rounding collisions**: if 40%/60%/80% round to the same achievable plate value at a low working load (e.g. early calibration), that's acceptable — do not add dedup logic; three identical-looking ramp sets at a genuinely low load is correct behavior, not a bug.
- **Meso-cycle swap mid-block** (e.g. d2 rotates Belt Squat → Back Squat at meso 2): `ramp_eligible` must be set on BOTH movement rows (`belt_squat` and `back_squat`) so the ramp survives the swap — verify both seed sites in `program_seed.py`.
- **HT movements**: never applicable (`is_anchor` T1 barbell lifts and HT are disjoint movement sets in this program), but the ramp branch must sit before/outside the `_is_ht_movement(...)` block (assembler.py:270+) so it can never accidentally interact with `target_plates`/`band_config` fields.

## Dependencies
None functionally, but **sequence after none / before Spec 05** — both this spec and `05-finisher-emom.md` touch `ironlog/generation/assembler.py`. Merge this one first; Spec 05 should branch from this spec's merged state, not run concurrently against the same file.

## Verification
- New test in `tests/test_generate_preview.py` (or a new `tests/test_ramp_sets.py`): generate a D2 session (Belt Squat anchor), assert the assembled `PlannedExercise` for Belt Squat has exactly 3 extra `RAMP`/`is_warmup=True` sets at indices -3/-2/-1 with loads ≈ 40/60/80% of the working load (rounded), ahead of the 3 working sets at indices 0/1/2.
- Negative test: a non-ramp-eligible anchor (Pull-up, d4) or a needs-calibration movement produces zero ramp sets.
- Full suite green: `cd <worktree> && ~/projects/IronLog-V2/.venv/bin/python -m pytest -q` (baseline: 482 passing as of 2026-07-09).
- Manual: `POST /generate` for a D2 session, confirm the JSON response's Belt Squat exercise carries 6 total planned_sets (3 ramp + 3 working), ramp ones flagged `is_warmup: true`.
