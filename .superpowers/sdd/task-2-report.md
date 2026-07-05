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

- `tests/test_slot_override_skeleton.py` (new): 3 tests —
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
   Result: **8 passed** (4 dismiss/review tests including the new one + 4 new skeleton tests... actually 3 new skeleton tests + 1 dismiss addition against the existing 4-test file = 8 total). 0 failed.

2. Full suite:
   ```
   ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q'
   ```
   Result: **391 passed** (baseline 387 + 4 new tests: 3 skeleton + 1 dismiss-applied), 0 failed,
   0 regressions.

## Concerns

None. The adaptive-branch behavior change (now honoring `MesoRotation` for non-anchor slots)
is a deliberate widening of `_effective_movement_id`'s use per the brief, and cross-checked
against the seed data + an existing guard test that already implies this rotation should be
honored — flagged above for visibility, not as an open risk.
