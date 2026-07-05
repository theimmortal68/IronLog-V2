# Task 2 Report: `_effective_movement_id` in lay_skeleton + dismiss sets applied

Status: **DONE**
Branch: `feat/note-apply`

## Changes

- `ironlog/generation/skeleton.py`:
  - Imported `SlotMovementOverride`.
  - Added `_effective_movement_id(db, te, meso_number) -> int`: precedence
    active `SlotMovementOverride` > `MesoRotation(meso_number)` > `te.movement_id`.
  - Anchor branch now calls `_effective_movement_id` instead of the inline
    `mr.movement_id if mr else te.movement_id` check.
  - Adaptive branch now sets `program_movement_id = _effective_movement_id(db, te, meso_number)`
    instead of the raw `te.movement_id`. This is an intentional behavior change per the
    brief: previously the adaptive branch never consulted `MesoRotation` at all. Investigation
    confirmed the seeded program (`program_seed.py`) already has meso-2 `MesoRotation` rows on
    non-anchor slots (`d4_t2a` semi, `d4_t3b` free), and an existing guard test
    (`test_program_seed_rotation_guard.py::test_new_meso_rotations_exist_and_resolve`) already
    asserts those rows should resolve — so this aligns `lay_skeleton` with pre-existing
    seed/test intent rather than introducing a regression. No existing test asserted the old
    "meso ignored for non-anchors" behavior via `lay_skeleton` directly, and the full suite
    stayed green.
  - Updated the `lay_skeleton` docstring to describe the new precedence instead of the stale
    "no meso override applied to non-anchors" note.
  - Base program (`TierExercise.movement_id`) is never mutated — confirmed by an explicit
    assertion in the new test.

- `ironlog/api/app.py` (`dismiss_note`): added `n.applied = True` alongside the existing
  `n.classification = NoteClass.JOURNAL` set, fixing the bug where a dismissed note kept
  `applied=False` and could still flag the movement as deviation-eligible.

- `tests/test_slot_override_skeleton.py` (new): 4 tests —
  1. `test_skeleton_emits_base_movement_with_no_override` — no-override/no-meso path returns
     `te.movement_id` unchanged (regression guard for the no-override case).
  2. `test_active_override_swaps_only_its_slot` — active override swaps only the overridden
     anchor slot (bench→incline); the unrelated adaptive slot (`close_grip`) is untouched;
     the base `TierExercise.movement_id` row is unmutated; `active=False` reverts to bench.
  3. `test_override_takes_precedence_over_meso_rotation` — full precedence chain: meso-2
     rotation (bench→overhead press) applies with no override; an active override on the same
     slot (bench→incline) wins over the meso rotation; dismissing the override falls back to
     the meso rotation (not straight to base) at `meso_number=2`; `meso_number=1` (no rotation
     row) falls all the way back to the base movement.
  4. `test_adaptive_slot_meso_rotation_fires_through_skeleton` (added post-review) — regression
     lock for the intentional adaptive-branch behavior change: uses the `gen_db` seed fixture,
     reads the real seeded meso-2 `MesoRotation` on D4's `d4_t2a` (Meadows Row [semi] → Pendlay
     Row) and asserts `lay_skeleton("D4 Upper Pull", ..., meso_number=2)` emits that seeded
     target's `movement_id` for the adaptive slot, while `meso_number=1` emits the base
     `te.movement_id`. Proves adaptive-slot meso rotation now fires through `lay_skeleton` AND
     is meso-gated (not always-on) — without this, a refactor could silently revert the
     adaptive branch to raw `te.movement_id` and stay green.

- `tests/test_notes_endpoints.py`: added `test_dismiss_sets_applied_true` — after
  `/notes/{id}/dismiss`, asserts `note.applied is True` and `note.classification == JOURNAL`.
  (Note: the brief named the file `test_notes_review_endpoints.py`; the real file covering
  `/notes/review` + confirm/dismiss is `tests/test_notes_endpoints.py` — extended that file
  instead of creating a duplicate, matching the fix already applied by the Task 1 implementer
  for other model/import discrepancies in the brief.)

## Test commands + results

1. Targeted:
   ```
   ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_slot_override_skeleton.py tests/test_notes_endpoints.py'
   ```
   Result (initial): **8 passed** (existing 4 dismiss/review + new dismiss-applied + 3 new
   skeleton tests). 0 failed.

2. Post-review coverage add (`test_adaptive_slot_meso_rotation_fires_through_skeleton`):
   ```
   ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_slot_override_skeleton.py'
   ```
   Result: **4 passed** (3 original skeleton tests + 1 new adaptive-meso coverage test).

3. Full suite (final):
   ```
   ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q'
   ```
   Result: **392 passed** (baseline 387 + 5 new tests: 3 override skeleton + 1 adaptive-meso
   coverage + 1 dismiss-applied), 0 failed, 0 regressions.

## Concerns

None. The adaptive-branch behavior change (now honoring `MesoRotation` for non-anchor slots)
is a deliberate widening of `_effective_movement_id`'s use per the brief, and cross-checked
against the seed data + an existing guard test that already implies this rotation should be
honored — flagged above for visibility, not as an open risk.

## Fix pass (whole-branch review, 2026-07-05)

The whole-branch review found one Important latent bug that Task 2 *exposed* (did not
introduce), plus requested an end-to-end integration test.

**Bug — rep-scheme lookup keyed on movement, not slot identity** (`ironlog/generation/context.py`
~line 345). `resolve_context` built `te_by_mid = {te.movement_id: te}` and looked up
`te_by_mid.get(slot.program_movement_id)`. Pre–Task 2 the adaptive branch always emitted the
base `te.movement_id`, so base == effective and the lookup always hit. Once adaptive-slot meso
rotations and `SlotMovementOverride`s went live, a slot's `program_movement_id` can differ from
its base movement → the movement-keyed lookup **misses** (`rep_scheme` becomes `None`) or, if the
swapped movement coincides with a different slot's base, returns the **wrong** TE's rep scheme.
Durable (survives reconcile), bites at meso 2.

- **Fix:** re-keyed the lookup on the slot's stable identity —
  `te_by_slot = {te.slot_id: te}`, looked up via `te_by_slot.get(slot.slot_id)`. `slot_id`
  (e.g. `"d4_t2a"`) is globally unique across the program (day+tier+position) and carried on
  both `SlotSpec` and `TierExercise`, so it is unaffected by movement swaps. No production
  behavior change for the no-swap path; correct resolution for the swapped path.
- **Regression test:** `tests/test_generation_context.py::test_slot_rep_scheme_resolves_at_meso2_with_adaptive_rotation`
  — at `meso_number=2`, D4's `d4_t2a` (Meadows Row → Pendlay Row rotation) resolves its
  `rep_scheme` to that slot's own TE (`rep_low`/`rep_high`/`scheme`), not `None`/wrong.
  **Verified the test catches the bug**: temporarily reverting `context.py` to the movement-keyed
  lookup made this test fail (`rep_scheme is None`); restoring the slot-keyed fix makes it pass.

**Integration test (Minor)** — `tests/test_note_apply_endpoints.py::test_apply_then_generate_slot_emits_target_movement`
exercises the feature's central seam end to end: seeds a program day with a bench anchor slot
+ an unrelated accessory slot + a `CONFIG_CHANGE` note on bench; `POST /notes/{id}/apply` with
an incline target via `TestClient`; then `lay_skeleton("D1 Upper Push", db, meso_number=1)` and
asserts the bench slot emits incline, the accessory slot is unchanged, and the base
`TierExercise.movement_id` row is unmutated.

### Fix-pass test results
```
ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_generation_context.py tests/test_note_apply_endpoints.py'
```
→ **16 passed**.
```
ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q'
```
→ **403 passed** (baseline 401 + 2 new tests), 0 failed, 0 regressions.
