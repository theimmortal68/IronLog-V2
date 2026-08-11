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

---

## Note-apply REDESIGN plan — Task 2: Classifier `action_type` (2026-07-05)

**This is a distinct task, unrelated to the slot-override work above.** The redesign plan
(`.superpowers/plans/2026-07-05-note-apply-redesign.md`) restarted task numbering; its own
Task 2 landed here because the brief (`task-2-brief.md`) pointed at this report path. Appended
per this repo's established pattern of appending fix-wave sections to the same `task-N-report.md`
rather than creating a new file per micro-task.

Status: **DONE**
Branch: `feat/note-apply-redesign`

### Objective
The apply UI needs to route by action (swap vs load vs reps) deterministically. The Gemini note
classifier (`ironlog/notes/classify.py`) previously returned only free-text `action` inside
`proposed_change`; it now also emits a structured `action_type` enum so the UI can route
without parsing free text.

### Changes
- `ironlog/notes/classify.py`:
  - `NOTE_CLASSIFICATION_SCHEMA`: added `action_type` (`enum`: `SWAP`, `LOAD_INCREASE`,
    `LOAD_DECREASE`, `REP_CHANGE`, `OTHER`), added to `required`.
  - `NOTE_SYSTEM_INSTRUCTION`: instructs the model to classify the action into that enum
    (SWAP = replace movement; LOAD_INCREASE/DECREASE = load too light/heavy;
    REP_CHANGE = different rep target; OTHER = anything else, incl. non-CONFIG_CHANGE).
    `proposed_change.movement` stays the extracted subject movement regardless of `action_type`.
  - `NoteClassification` dataclass: added `action_type: str = "OTHER"` (new field, default
    keeps old 4-positional-arg call sites — e.g. `tests/test_note_classify_persist.py` prior to
    this change — working unchanged... except that test was itself extended, see below).
  - `NoteClassifier.classify()`: parses `obj.get("action_type")`, maps any value not in
    `{SWAP, LOAD_INCREASE, LOAD_DECREASE, REP_CHANGE, OTHER}` (including absent/`None`) to
    `"OTHER"` — mirrors the existing unknown-`classification`→error / missing-field defaulting
    pattern used elsewhere in this function.
  - `classify_session_notes()`: `classification_meta` now includes an `"action_type"` key
    alongside `proposed_change`/`confidence`/`rationale`.
- `tests/test_note_classifier.py`: extended —
  - `test_config_change_extracts_proposed_change` now also asserts `action_type == "SWAP"`.
  - `test_action_type_round_trips_for_each_enum_value` (parametrized SWAP/LOAD_INCREASE/
    LOAD_DECREASE/REP_CHANGE/OTHER) — each canned Gemini response round-trips through `classify()`.
  - `test_unknown_action_type_defaults_to_other` — a bogus `action_type` string from Gemini
    degrades to `"OTHER"` rather than raising.
  - `test_missing_action_type_defaults_to_other` — an absent `action_type` key (e.g. an older
    prompt/response shape) defaults to `"OTHER"`.
- `tests/test_note_classify_persist.py`: `test_classify_session_notes_persists_classification_and_meta`
  extended to pass `action_type="SWAP"` into the `NoteClassification(...)` call and assert
  `note.classification_meta["action_type"] == "SWAP"` is persisted.

`GeminiProposer` (the generation path, `ironlog/generation/gemini.py`) was not touched.

### TDD sequence
1. Wrote the failing tests first (7 new/extended assertions across the two test files).
2. Confirmed failure:
   ```
   ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_note_classifier.py tests/test_note_classify_persist.py'
   ```
   → 9 failed, 9 passed (all failures were `AttributeError: no attribute 'action_type'` or the
   `NoteClassification.__init__()` positional-arg-count `TypeError`, as expected pre-implementation).
3. Implemented the schema/instruction/dataclass/parse/persist changes above.
4. Re-ran targeted + regression:
   ```
   ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_note_classifier.py tests/test_note_classify_persist.py tests/test_generation_gemini.py'
   ```
   → **24 passed**, 0 failed.
5. Full suite:
   ```
   ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q'
   ```
   → **415 passed** (baseline 408 + 7 new tests), 0 failed, 0 regressions.

### Concerns
None. `action_type` is additive to the schema/dataclass/persisted JSON — no existing consumer
of `classification_meta` asserts an exact key set, and the `NoteClassification` field has a
default so no other call site needed updating besides the one deliberately extended above.

---

## STAB maintenance-block redesign — Task 2: D2 Lower Squat + new core tier (2026-08-11)

**Distinct task, unrelated to the sections above** (this repo's established pattern of reusing
`task-N-report.md` across unrelated task-numbering restarts — this is Task 2 of the 7-task STAB
maintenance-block redesign plan, `docs/superpowers/plans/2026-08-10-stab-maintenance-block-
redesign.md` / `.superpowers/sdd/task-2-brief.md`, not the earlier note-apply or config-seed
go-live chunks above).

Status: **DONE**
Branch: `feat/stab-d2-lower-squat`
Commit: `d7d1558` — "feat(program): D2 reconciled to maintenance-block FINAL doc (STAB redesign, Task 2)"

### Objective
Rewrite D2 Lower A's tier wiring to match `docs/program/source/2026-08-10-maintenance-block-
seed-data-FINAL.md`'s D2 session: drop the Hip Thrust T1b tier entirely, turn over T2 GS to two
new movements (Matrix Machine Sissy Squat, Nordic Curl Max [Ares]), add a new movement to T3 GS
(Hybrid Board Calf Raise [D2]), and add a new T4 straight tier (Ab Trainer Decline Sit-up, D2's
mandatory core slot).

### Pre-implementation verification (cross-checking the brief against the FINAL doc, per the
task's own instruction to catch a Task-1-style miss before writing code)

Read in full: the task brief, the FINAL doc's D2 section (lines 207-353) plus "Weekly Volume
Check" (35-53) and "Key Nordic Curl Update — Ares Weighted Assist" (848-882), the plan doc's
Task 2 section (confirmed byte-identical to the brief), the CURRENT (pre-Task-2) `_seed_d2` in
full, and Task 1's actual merged diff as a style/convention template.

Found three discrepancies between the brief and the FINAL doc / current codebase, resolved as
follows:

1. **Tier order for the new T4 tier.** The brief's Step 3 body says "tier_order 5" but its own
   closing sentence says "Tier orders renumber sequentially ... T4=4" — internally
   contradictory. Resolved via the later, more specific statement: **tier_order=4**. Purely an
   internal ordering field, no athlete-facing effect. Confirmed via advisor consultation.
2. **D2 T3 GS `rest_seconds`: 75 (current code) vs 60 (FINAL doc).** The brief doesn't mention
   this field. The FINAL doc explicitly states "T3 GS — 3 items, 60s rest" for D2; corroborated
   by D5's already-implemented `rest_seconds=60` for the identical tier shape (also matching its
   own FINAL-doc entry). Resolved: **75 → 60**, with the mechanical yaml-parity consequence
   (`T3_GIANT: rest: 75 → 60`) applied in the same commit.
3. **Belt Squat's yaml `rep_ladder`/`rep_target`/`ceiling` fields under the new 4-6 rep range.**
   Traced whether these are live engine inputs — confirmed (via grep + reading
   `rule_wiring._iter_yaml_rules` and `engine/advance.py::_rep_ladder`) they are pure
   documentation/comments; the only live `Movement.rep_ladder` field is never populated by
   `seed.py` for any movement, pre-existing this task. Left unchanged.

A fourth discrepancy — **knee_modality wiring for the two new lower-body movements** — was
genuinely unresolved by either the brief or the FINAL doc (neither states a weekly Nordic/SISSY
frequency target), involved a real design decision (the program's `KNEE_TARGETS`
{NORDIC:2, TIB:2, KOT:2, SISSY:1} weekly targets in `context.py`, previously unmet for both
NORDIC and SISSY program-wide), and had a compounding effect into Task 4/D5 (same
`Nordic Curl Max [Ares]` Movement row referenced again there). **Escalated as NEEDS_CONTEXT**
mid-session rather than guessed; the coordinator/plan owner resolved it: `nordic_curl_max_d2`'s
TierExercise gets `knee_modality=NORDIC` (Task 4 must independently tag its own D5
TierExercise — the tag lives per-slot on TierExercise, not on Movement, so it does not carry
forward automatically), and `matrix_machine_sissy_squat`'s TierExercise gets
`knee_modality=SISSY` (matches the FINAL doc's own `knee_health_note` on that movement, and is
the program's first-ever wired SISSY slot). Implementation resumed with this fix folded in.

### What changed

- `ironlog/seed.py`: 4 new movements — Matrix Machine Sissy Squat, Nordic Curl Max [Ares] (Ares
  cable weighted assist, 60lb locked, supersedes the old monster-bands recommendation),
  Hybrid Board Calf Raise [D2], Ab Trainer Decline Sit-up.
- `ironlog/generation/program_seed.py`: `PROGRAM_TO_LIBRARY` entries for the 4 new movements;
  `_seed_d2` rewritten per the target structure below.
- `docs/program/phase1-seed-source.yaml`: `d2:` block rewritten to match.
- `ironlog/generation/rule_wiring.py` + its test-file copy
  (`tests/test_program_seed_yaml_parity.py`): new `m:` ids for the 4 new movements added to
  `YAML_M_TO_LIBRARY`.
- `ironlog/generation/baseline_seed.py`: `d2_t1b` (Hip Thrust), `d2_t2b` (Scout Reverse Hyper),
  `d2_t3c` (Reverse Nordic) baseline entries REMOVED — their slots are unwired from D2's real
  program; underlying MovementState rows left in place, not deleted. New `d2_t3d`/`d2_t4a` get
  NO baseline entry (needs-calibration, zero prior history). `d2_t1`/`d2_t3a`/`d2_t3b`
  unchanged.

### Target D2 structure (as implemented, dev + production)

| Tier | order | rest | Movement | Reps | Rule | knee |
|---|---|---|---|---|---|---|
| T1 | 1 | 150 | Belt Squat [GHR + FT] (anchor) | 4-6 | REP_LADDER | — |
| T2 GS | 2 | 90 | Matrix Machine Sissy Squat (new, d2_t2d) | 8-12 | RPE_8_STANDARD | SISSY |
| T2 GS | 2 | 90 | Nordic Curl Max [Ares] (new, d2_t2e) | 6-8 | ASSISTANCE_REDUCTION | NORDIC |
| T3 GS | 3 | 60 | ATG Split Squat (unchanged, d2_t3a) | 8-12 | RPE_8_STANDARD | KOT |
| T3 GS | 3 | 60 | Hybrid Board Calf Raise [D2] (new, d2_t3d) | 10-15 | RPE_8_STANDARD | — |
| T3 GS | 3 | 60 | Cable Tibialis Raise (unchanged, d2_t3b) | 10-15 | RPE_8_STANDARD | TIB |
| T4 | 4 | 90 | Ab Trainer Decline Sit-up (new tier, d2_t4a, anchor) | 10-15 | REP_LADDER | — |

Removed: T1b (Hip Thrust) tier entirely — first of three Hip Thrust removals across this
redesign (D5/D6 follow in later tasks). Old `d2_t2a`/`d2_t2b` (Lying Leg Curl, Scout Reverse
Hyper) and `d2_t3c` (Reverse Nordic) vacated, not reused.

### Test fallout beyond the brief's declared file list

Fixed because they were direct, foreseeable consequences of D2's Hip Thrust T1b tier removal
and T2/T3 turnover, not scope creep:

- `tests/test_baseline_seed.py`, `tests/test_commit_day_scoped_state.py`,
  `tests/test_generation_day_scoped_state.py` — all three 3-way D2/D5/D6 Hip Thrust
  day-scoping checks drop to 2-way D5/D6 (row count, day-key set, per-day value assertions all
  updated; module docstrings updated).
- `tests/test_ht_composite_wiring.py`, `tests/test_ht_assembler_reconciliation.py`,
  `tests/test_ht_write_boundary.py` — generic HT-plumbing tests that used "D2 Lower A" purely as
  a vehicle (none assert anything D2-specific) repointed to D5's still-live Hip Thrust slot.
- `tests/test_ht_d6_derived.py` — the synthetic "unified" source slot (monkey-patched
  `unified_ht_group="main"` onto a real TierExercise for test purposes only) repointed from D2's
  real Hip Thrust TierExercise (no longer exists) to D5's.
- `tests/test_ht_unification.py` — D2's leg of the D2/D5 unified-group mechanism test now uses a
  throwaway synthetic TierExercise (`_synthetic_ht_slot` helper) attached to D2's still-real
  ProgramDay, since D2 no longer has a real Hip Thrust slot to repurpose. Repurposing D6's real
  slot instead was considered and rejected: D6's TierExercise carries
  `derived_from_unified_group` (not `unified_ht_group`) as a load-bearing production invariant,
  asserted directly by this same file's `test_d6_ht_is_not_unified` — tagging it
  `unified_ht_group` here would contradict that invariant's premise.
- `tests/test_ht_generate_banded.py` — D2 dropped from three multi-day loops; one single-day
  test (`test_commit_advances_ht_state`) repointed D2 → D5.
- `tests/test_phase1_reconciliation.py` — `d2_t1` moves from `UNCHANGED_REP_TARGETS` to
  `CHANGED_REP_TARGETS` at (4,6); `TIER_REST_MAP`'s D2 T1b entry removed, T3 GS updated to 60,
  new T4 entry added.
- `scripts/build_hgc_condensed_week.py` — `MINI_SESSIONS`' single-movement 7/28 D2 entry
  repointed from the now-unwired Lying Leg Curl `[GHR]` to Nordic Curl Max [Ares] (closest-role
  replacement, hamstring-focused T2 GS accessory).
- `tests/test_library_seed.py` — counts: 118→122 movements, ACTIVE 111→115.
- `tests/test_golive_phase1.py` — `EXPECTED_NEEDS_CAL["D2 Lower A"]` changed from
  `{"Lying Leg Curl [GHR]"}` to the 4 new movements (Lying Leg Curl drops out of the program
  entirely, no merge needed).

### Test commands + output

```
cd /home/jstout/projects/IronLog-V2-wt-stab-d2
/home/jstout/projects/IronLog-V2/.venv/bin/python -m pytest -q
```
Before any fixes: 27 failed, 674 passed (the fallout enumerated above).
After all fixes: **701 passed, 0 failed** — matches the stated 701-passing baseline exactly.

### Local dev smoke check (fresh in-memory DB, worktree code)

`lay_skeleton("D2 Lower A", ...)` + `generate_session(...)` via `StubProposer` against a
from-scratch seeded DB confirmed: Hip Thrust absent, all 4 new movements present, tier
order/rest/knee_modality exactly matching the target table above. (First attempt at this smoke
check silently ran against the WRONG `ironlog` package — invoking a script by file path omits
cwd from `sys.path`, and a stray path entry resolved `ironlog` to the main checkout instead of
the worktree; running with `PYTHONPATH=$(pwd)` fixed it. Flagging since this bit the production
deploy step too, see below.)

### Production deployment

Pre-flight:
- `ssh myflix "systemctl is-active ironlogv2"` → `active`
- Checked for active use: `journalctl -u ironlogv2 --since "-2 hours"` showed only a `/docs` GET,
  no recent SetLog/session activity. Most recent logged session in the DB was dated 2026-07-29,
  over a week stale relative to today (2026-08-11) — no in-progress-athlete-use conflict.
- Confirmed `main`/production checkout (`~/projects/IronLog-V2`) at `55a727d`, identical to this
  branch's fork point — no rebase needed.
- Backup: `cp ironlog.db ironlog.db.bak-task2-20260811-110721` on the production checkout before
  any write.

Applied via a one-off, idempotent Python/SQLModel script (`_deploy_task2_d2.py`, not committed
to the repo, run then deleted), executed with the production checkout's `.venv` interpreter but
from **the worktree's own directory** (`cd ~/projects/IronLog-V2-wt-stab-d2 && ~/projects/
IronLog-V2/.venv/bin/python _deploy_task2_d2.py`) — this matters: a first attempt run from the
production checkout's own directory picked up the OLD (un-merged) `ironlog.seed.MOVEMENTS`
(same `sys.path` gotcha as the dev smoke check above, just biting the other direction — it
correctly resolved to the checkout it was run from, which didn't have the new movements yet),
failed partway through (`KeyError: 'Matrix Machine Sissy Squat'`) after already deleting the T1b
tier and updating T1's rep range. Restored from the pre-write backup and re-ran cleanly from the
worktree directory (worktree code, same production DB file via an absolute
`sqlite:////home/jstout/projects/IronLog-V2/ironlog.db` URL) — this is schema-safe since Task 2
makes no schema changes, only definition-row content.

Script output (clean re-run):
```
CREATED movement: Nordic Curl Max [Ares] (id=124)
CREATED movement: Matrix Machine Sissy Squat (id=125)
CREATED movement: Hybrid Board Calf Raise [D2] (id=126)
CREATED movement: Ab Trainer Decline Sit-up (id=127)
UPDATED d2_t1 (Belt Squat) reps -> 4-6
DELETED T1b tier (id=6) + d2_t1b TierExercise (id=12)
REBUILT T2 GS: d2_t2a/d2_t2b removed, d2_t2d/d2_t2e added, tier_order=2
REBUILT T3 GS: d2_t3c removed, d2_t3d added, d2_t3b reordered to 3, tier_order=3, rest_seconds=60
CREATED T4 tier (id=22) + d2_t4a TierExercise
CLEARING stale pending_load_delta=2.5 on Cable Tibialis Raise / D2 Lower A MovementState (id=15)
Task 2 D2 deploy script complete.
```

The `pending_load_delta` clear is the exact class of bug the brief's own reminder flagged
(pre-existing `MovementState` row carrying a stale delta from unrelated prior production
activity, on Cable Tibialis Raise's D2 row — +2.5, unrelated to this task). Belt Squat, ATG
Split Squat, and Cable Tibialis Raise's real progressed `current_load` values (265, 32.5, 37.5 —
all above the dev-seed baselines of 260/25/25, confirming real athlete progression since
go-live) were left untouched; `seed_movement_baselines()` was deliberately NOT re-run against
production (it would have reset these to the stale dev-seed values).

Ran `rule_wiring.wire_progression_rules()` against production (`_rule_wiring_prod.py`, same
worktree-directory pattern): `Movement.progression_rule changed: 4 / 40` (the 4 new movements).

Verified the resulting DB structure directly (`_verify_prod_d2.py`) — matches the target table
above exactly, tier-order/rest/knee_modality/rule all correct.

### Live verification: real `generate_session("D2 Lower A", ...)` against production

Two passes:

**Pass 1 — direct DB script, before restart** (confirms the DB rows themselves are correct):
```
=== live generate_session('D2 Lower A') ===
-- T1 (GroupType.STRAIGHT) --
   'Belt Squat [GHR + FT]' reps=[(5, 5)] load/plates=[(105.0, None)] n_sets=6
-- T4 (GroupType.STRAIGHT) --
   'Ab Trainer Decline Sit-up' reps=[(10, 15)] load/plates=[(None, None)] n_sets=3
-- T2 GS (GroupType.GIANT_SET) --
   'Matrix Machine Sissy Squat' reps=[(8, 12)] load/plates=[(None, None)] n_sets=3
   'Nordic Curl Max [Ares]' reps=[(6, 8)] load/plates=[(None, None)] n_sets=3
-- T3 GS (GroupType.GIANT_SET) --
   'ATG Split Squat' reps=[(8, 12)] load/plates=[(32.5, None)] n_sets=3
   'Hybrid Board Calf Raise [D2]' reps=[(10, 15)] load/plates=[(None, None)] n_sets=3
   'Cable Tibialis Raise' reps=[(10, 15)] load/plates=[(37.5, None)] n_sets=3

Hip Thrust present: False
All 4 new movements present: True
```

**Pass 2 — real `POST /generate` call through the RUNNING service, after restart** (the actual
completion-criteria smoke call):
```
curl -s -X POST http://localhost:8000/generate -H 'Content-Type: application/json' \
     -d '{"day_role": "D2 Lower A"}'
```
Response `preview.groups` (movement names + tier labels/rest, extracted from the real JSON):
```
-- T1 (STRAIGHT) rest=150 --
   'Belt Squat [GHR + FT]'
-- T4 (STRAIGHT) rest=90 --
   'Ab Trainer Decline Sit-up'
-- T2 GS (GIANT_SET) rest=90 --
   'Matrix Machine Sissy Squat'
   'Nordic Curl Max [Ares]'
-- T3 GS (GIANT_SET) rest=60 --
   'ATG Split Squat'
   'Hybrid Board Calf Raise [D2]'
   'Cable Tibialis Raise'

Hip Thrust present: False
All 4 new movements present: True
```
T1's `planned_sets` show the real ramp ladder (105/160/212.5 lb, RAMP/warmup) followed by the
real WORKING set at **265 lb × 4-6 reps** (the athlete's actual current progressed load, not the
dev-seed baseline of 260) — confirms both the new 4-6 rep range and that real athlete state was
correctly preserved through the deploy.

### Merge

`git merge --no-edit feat/stab-d2-lower-squat` on the production checkout (`~/projects/
IronLog-V2`) initially blocked: `.superpowers/sdd/task-2-report.md` had uncommitted local
changes in the checkout (an unrelated, never-committed addendum from the earlier config-seed
go-live chunk's own "Task 2"). Investigated rather than force-overwriting: confirmed this file
is an append-only cross-chunk log reused across unrelated task-numbering restarts (matches
`[[feedback_append_only_files_read_then_edit]]`), and — separately — caught that my own
worktree's copy of this file had been fully overwritten (via `Write`, not append) earlier in
this session, discarding 204 lines of genuine prior history. Fixed by reconstructing this file
as history + this section appended, before merging, so the merge doesn't destroy either the
committed history or (once the uncommitted config-seed addendum is separately preserved by its
own owner) any other pending content.

### Deploy Gate

Class 1 (code-only restart, no schema/data touch beyond the additive definition rows already
applied above): `sudo systemctl restart ironlogv2` → health check
`curl -sf http://myflix:8000/health` → smoke call: real `generate_session("D2 Lower A", ...)`
through the running service, confirming the newly-deployed structure. **DEPLOYED.**

### Rollback

- `git revert d7d1558` in the IronLog-V2 repo (code).
- DB: `cp ironlog.db.bak-task2-20260811-110721 ironlog.db` on the production checkout, then
  `sudo systemctl restart ironlogv2` (restores the pre-Task-2 D2 wiring + the one Cable
  Tibialis Raise `pending_load_delta` that was cleared).

### Open items

- The knee_modality decision for Task 4/D5's `nordic_curl_max_d5` TierExercise must be applied
  independently when that task runs — flagged explicitly in the commit message and in
  `program_seed.py`'s inline comment, since the tag lives per-slot on TierExercise, not on the
  shared Movement row, and does not carry forward automatically.
- `pending_load_delta` sweep was scoped to the movements this task actually touches (Belt Squat,
  ATG Split Squat, Cable Tibialis Raise); one stale value found and cleared (Cable Tibialis
  Raise, +2.5). Not a full-program sweep — out of this task's scope.

### Hand-off
Ready for: Task 4 (D5).
