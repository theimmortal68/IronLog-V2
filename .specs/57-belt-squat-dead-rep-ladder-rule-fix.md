# 57 — Belt Squat stuck on dead REP_LADDER rule

## Objective
Belt Squat has not advanced load in weeks because it's wired to a progression rule
(`REP_LADDER`) that can never fire for it; switch it to the same rule its sibling
primary anchors already use correctly (`RPE_8_STANDARD`), live and in the seed source.

## Root cause (confirmed, not a guess)
- `Movement.rep_ladder` is `None` for **every** movement in the codebase — grep confirms
  `rep_ladder=` is never assigned in `ironlog/seed.py` or `ironlog/generation/*.py`.
- `advance.py`'s `_rep_ladder` dispatch only ever mutates `current_rep_target`; it never
  touches `current_load`. With no `rep_ladder` to seed from, `current_rep_target` stays
  `None` forever (`_rep_ladder`'s own "freshly transitioned onto the ladder" branch keeps
  re-returning `advanced=False`).
- Belt Squat's live `MovementState` (day `D2 Lower A`) confirms the stuck state:
  `active_rule='REP_LADDER'`, `current_rep_target=None`, `consecutive_advance_count=0`,
  `current_load=325.0`, `current_increment_tier=0` — unchanged for at least the last
  several sessions.
- `docs/program/phase1-seed-source.yaml` line 62 wires `belt_squat` as
  `rule: rep_ladder` with extra keys (`load: 260, rep_target: 8,
  rep_ladder: [8,10,12,15], ceiling: true`) that `rule_wiring.py`'s `_iter_yaml_rules()`
  never reads (it only reads `m` and `rule`) — this looks like an aspirational
  "hit a ceiling, then switch to rep-ladder" design that was never actually wired: no
  code path sets `Movement.cap` or `Movement.rep_ladder` from this YAML at all.
- Every other heavy-barbell primary anchor with the same shape (Bench Press, Standing
  OHP, RDL — all `Scheme.STRAIGHT`, `increment_ladder=[...]`, `rpe_capped=True`) uses
  `rule: rpe_8_standard`, which correctly raises `current_load` by
  `increment_ladder[current_increment_tier]` on a clean top-of-range session via the
  already-working `_rpe8`/`RPE_8_STANDARD` dispatch, with `step_down_tier` dropping the
  step size (10 → 5 → 2.5) after repeated failures — exactly the behavior the athlete
  described wanting ("+10 until first failure, then +5").

## File targets
- `docs/program/phase1-seed-source.yaml` — line 62, `belt_squat` entry: change
  `rule: rep_ladder` → `rule: rpe_8_standard`. Leave `load`, `rep_target`, `rep_ladder`,
  `ceiling`, `meso` keys untouched (unconsumed metadata, out of scope — see below).
- `ironlog/generation/rule_wiring.py` — no code change expected; re-running
  `wire_progression_rules(db)` after the YAML edit should update
  `Movement.progression_rule` for "Belt Squat [GHR + FT]" from `'REP_LADDER'` to
  `'RPE_8_STANDARD'` via the existing idempotent UPDATE path. Confirm this happens;
  if `wire_progression_rules` for some reason doesn't pick it up, investigate before
  writing a workaround.
- A new idempotent one-off live-DB fix script under `ironlog/generation/`, e.g.
  `belt_squat_rule_fix.py` (pattern-match `nordic_curl_ladder_fix.py` /
  `reverse_nordic_ladder_fix.py`) — must directly correct the **already-live**
  `MovementState.active_rule` field for Belt Squat (currently the string
  `'REP_LADDER'`) to `'RPE_8_STANDARD'` for every existing MovementState row on this
  movement (currently just day `D2 Lower A`, but write the fix keyed by movement, not
  hardcoded to one day, in case more rows exist by the time this runs). Idempotent:
  safe to re-run, no-ops if already corrected.
- `tests/test_rule_wiring.py` — add a Belt Squat assertion. Extend
  `test_named_movements_map_to_expected_rules` (or add a new small test function
  alongside it) asserting `by_name["Belt Squat [GHR + FT]"].progression_rule ==
  ProgressionRule.RPE_8_STANDARD.value`. No existing test currently pins Belt Squat's
  rule at all (confirmed via grep) — this closes that coverage gap.
- **[Added post-dispatch, scope-check correction]** `tests/test_advance_load_bridge.py`
  — this file was missing from the original file-target list (a spec-writing gap, not
  a worker overreach): its load-ratchet floor tests hardcode Belt Squat's OLD
  REP_LADDER-specific behavior (`active_rule == REP_LADDER`, floor-only
  `pending_load_delta == 5.0`, prescribed `265`) as fixture assertions. Once Belt
  Squat's rule changes to RPE_8_STANDARD, those exact values are no longer correct —
  the floor (+5, from logging 265 against a 260 seed) now stacks with the earned
  RPE_8_STANDARD load step (+10), giving `pending_load_delta == 15.0` and a prescribed
  `275`. Updating these assertions to match the new (correct) behavior is a necessary
  consequence of this spec's own change, not an unrelated edit — confirmed via reading
  the diff and the pre-existing test's own comments, which explicitly called out the
  old REP_LADDER assumption by name.

## Changes
1. YAML: flip `belt_squat`'s `rule:` value only.
2. Live-DB one-off script: update `MovementState.active_rule` for Belt Squat's rows.
3. Confirm `Movement.progression_rule` gets updated via the normal
   `wire_progression_rules` path (re-run it, or fold into the one-off script — worker's
   judgment on the cleanest way to guarantee both `Movement.progression_rule` and every
   live `MovementState.active_rule` end up consistent).
4. New/extended test in `tests/test_rule_wiring.py`.

## Edge cases
- Belt Squat's `MovementState.current_increment_tier` is currently `0` (the 10lb rung).
  Do not reset or touch it — the fix only changes which rule dispatches, not any
  in-progress tier/streak state.
- Do not touch `Movement.cap` or `Movement.rep_ladder` for Belt Squat — leave both
  `None`. Do NOT invent a ceiling value or implement the ceiling→rep-ladder handoff
  design; that requires an athlete-supplied number (e.g. an equipment stack max) that
  doesn't exist anywhere in this codebase. The YAML's `ceiling`/`rep_ladder`/`load`/
  `rep_target` keys stay in place as unconsumed metadata, unchanged.
- Verify no other TierExercise/day references `belt_squat` with a conflicting rule
  (there's a `meso: {2: back_squat}` rotation — Back Squat currently has
  `progression_rule=None` since it isn't wired anywhere; leave it untouched, it's out
  of scope for this spec).

## Dependencies
None — standalone.

## Verification
- `python -m ironlog.seed` still succeeds (a from-scratch reseed produces
  `progression_rule='RPE_8_STANDARD'` for Belt Squat).
- `.venv/bin/pytest -q` green, including the new/extended test.
- Run the new one-off script against `ironlog.db` (back up first per this project's
  standing backup convention) and confirm via direct query that Belt Squat's live
  `MovementState.active_rule == 'RPE_8_STANDARD'` afterward.
- Manual sanity check: with `active_rule='RPE_8_STANDARD'`, a clean top-of-range D2
  session should raise `current_load` by `increment_ladder[current_increment_tier]`
  (10lb, since tier is currently 0) at next analysis — i.e. 325 → 335, matching what
  the athlete expected this week.
