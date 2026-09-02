# Spec: Duration-based TierExercise support + Suitcase Dreadmill Carry

## Objective

Give the definition layer a real duration-based prescription type (seconds
per set, not reps) so a timed unilateral carry can be a normal giant-set
slot — then use it to add a "Suitcase Dreadmill Carry" accessory to D2 T3
GS, as a 3rd member alongside ATG Split Squat and Hybrid Board Tib Raise.

**2026-09-01 update (target moved, athlete directive):** this spec
originally targeted D5 GS1 in place of Ab Trainer Russian Twist. That
Russian Twist removal already happened independently and unconditionally
(migration `055_d5_remove_russian_twist.sql`, no replacement — D5 GS1 is
now a real, final 3-member giant set: Lying Leg Curl / Hybrid Board Tib
Raise / Reverse Nordic Curl, athlete explicitly does not want it re-filled).
The Suitcase Carry instead lands on **D2's T3 GS** (tier_id 8), as a 3rd
member — D2 T3 currently holds ATG Split Squat (`d2_t3a`, order 1) and
Hybrid Board Tib Raise `[D2]` (`d2_t3e`, order 2) after Ab Trainer Decline
Sit-up was moved out to D2 T2 GS (migration `059_...`, exercise_order 4
there). Everywhere below that says "D5 GS1" / "in place of Ab Trainer
Russian Twist" should be read as "D2 T3 GS, as a new 3rd member (order 3)"
instead — the file targets, schema/engine work, and edge cases are
otherwise unchanged; only the destination slot differs.

## Background / why this exists

`TierExercise` (`ironlog/models/program.py`) only prescribes reps
(`rep_low`/`rep_high: Optional[int]`). The only duration concept in the
schema today is scoped to the **finisher** path: `DayFinisher`/`FinisherLog`
and `MovementState.current_duration_seconds`/`duration_ladder`, wired to
`ProgressionRule.FINISHER_DURATION_THEN_ROPE` (confirmed via grep this
session — that rule also couples to `MovementState.current_rope`, a
jump-rope-specific concept irrelevant here). `DayFinisher` is a single
per-day slot outside the `Tier`/`TierExercise` definition layer that drives
normal multi-exercise giant sets — it cannot represent "one more accessory
inside D5's existing GS1 giant set."

The athlete wants: 3 sets × 20-30 seconds per side, one arm loaded at a
time on a Dreadmill (a Bells-of-Steel-style unilateral-lever carry
trainer), progressing by **carried load**, not belt resistance — i.e. "hit
30s clean on both sides at the current load, then add load and reset
toward ~20s" (a load-ladder, exactly analogous to how every other LADDER
movement here progresses load on a stall/clean-set trigger — the only
difference is the *set* is measured in seconds instead of reps).

## File targets

1. `ironlog/models/program.py` — add duration fields to `TierExercise`.
2. `ironlog/models/enums.py` — check whether `ProgressionMode.LADDER` +
   existing `ProgressionRule.RPE_8_STANDARD`-style rules can drive a timed
   LADDER movement as-is, or whether a new `ProgressionRule` value is
   needed (see Changes #2 below — this needs a read of
   `ironlog/engine/` progression logic before deciding, not a guess).
3. `ironlog/engine/` — wherever `PlannedSet`/set-count generation and
   RPE/rep-based progression math live (read `docs/03_progression_model_spec.md`
   first, then find the concrete module — likely near wherever
   `rep_low`/`rep_high` currently drive double-progression / ladder
   advancement) — needs a parallel duration-based path. **This is the
   deterministic core** (`CLAUDE.md` invariant 1) — the LLM proposer must
   never compute a duration or a load step; this is pure rule logic exactly
   like the existing rep-based LADDER path.
4. `ironlog/models/session.py` (or wherever `PlannedSet`/`SetLog` live —
   confirm exact file) — both need a duration-per-set field alongside reps,
   nullable, so a timed set's prescribed/performed value isn't forced into
   the reps column (which would be a straight-up unit lie: "20 reps" when
   the athlete braced for 20 seconds). Per `CLAUDE.md` invariant 3
   (Planned vs Logged must never collapse), this applies to **both**
   `PlannedSet.duration_seconds` and `SetLog.duration_seconds`-or-equivalent
   — the delta between prescribed and performed duration is exactly as much
   a training signal as the existing rep delta.
5. `ironlog/generation/skeleton.py` / `assembler.py` — wherever `SlotSpec`
   carries `rep_low`/`rep_high` into the assembled session, needs a
   duration-carrying equivalent for timed slots.
6. `deploy/migrations/NNN_timed_tier_exercise.sql` — additive-only:
   `ALTER TABLE tierexercise ADD COLUMN duration_low_seconds INTEGER`,
   `duration_high_seconds INTEGER`; equivalent additive columns on whatever
   table(s) `PlannedSet`/`SetLog` resolve to. Follow
   `deploy/migrations/040_planned_set_skip_and_exercise_slot.sql` for the
   exact style used when this project last touched `PlannedSet`.
7. `deploy/migrations/NNN+1_suitcase_dreadmill_carry.sql` — the **content**
   migration, depends on NNN landing first:
   - `INSERT INTO equipment (...)` — new "Dreadmill" row. **Real numbers,
     confirmed by the athlete 2026-08-31** (do not substitute the
     `Dumbbells (MX100)` analog previously floated in this spec — that was
     a placeholder pending confirmation, and the real unit is materially
     different): plate-loaded, one arm loaded at a time (not a simultaneous
     per-hand implement like the dumbbells), so `load_unit='LB'` (a single
     loaded figure per set, not `LB_PER_HAND` — `Movement.unilateral=True`
     already carries the "one side at a time" fact; don't double-encode it
     in the load unit). `load_floor=NULL` — plate-loaded arms have no
     minimum floor weight the way a fixed-bar barbell does. `min_step=5.0`
     lb per plate-loading increment. Athlete expects to start this exercise
     loaded above 75lbs — this is calibration guidance for the wizard's
     first-session measurement, **not** a value to pre-seed into
     `MovementState.current_load` directly (this project calibrates new
     movements through the wizard flow, per how the D6 Standing OHP
     addition in migration 044 was deliberately left uncalibrated rather
     than guessed — same treatment here). If this spec's implementer wants
     to leave a breadcrumb for the athlete's first calibration session,
     put ">75 lb starting expectation" in the Movement's `notes` field, not
     in `MovementState`.
   - `INSERT INTO movement (...)` — "Suitcase Dreadmill Carry", `region=CORE`,
     `unilateral=1`, `progression_mode=LADDER`, `load_equipment_id` = the
     new equipment row, `increment_ladder='[5]'`, `min_step=5.0`,
     `load_floor=NULL` (mirrors the equipment row — this movement's own
     load_floor/min_step columns are self-contained and equipment-derived
     values don't propagate automatically, per this session's `045_...sql`
     finding that Movement-level load fields are independent of
     `load_equipment_id` — set both explicitly, don't assume one implies
     the other), `duration_low_seconds`/`duration_high_seconds`-style
     defaults if the Movement-level equivalents exist (mirror whatever
     `rep_low`/`rep_high`-at-Movement-level pattern exists, if any — check
     before assuming there's a Movement-level duration default; the
     TierExercise-level fields may be sufficient).
   - **INSERT** (not UPDATE — this is a new slot, not a repointed one; the
     old target, `id=65`/Ab Trainer Russian Twist, was already deleted by
     migration `055_d5_remove_russian_twist.sql` and D5 GS1 is not touched
     by this spec at all) a new `tierexercise` row into **D2's T3 GS**
     (`tier_id=8` as of this session — re-verify before writing the
     migration, the definition layer may have changed since): fresh slot_id
     (grep git history for the `d2_t3` namespace first, per this program's
     never-reassign-slot_id convention — `d2_t3a`/`d2_t3d`/`d2_t3e` are
     already live), `exercise_order=3` (after ATG Split Squat=1, Hybrid
     Board Tib Raise `[D2]`=2), `tier_role='free'`,
     `duration_low_seconds=20, duration_high_seconds=30, rep_low=NULL,
     rep_high=NULL`.
8. `tests/test_timed_tier_exercise.py` (or extend an existing progression
   test file) — new coverage for the duration-ladder progression path.
9. `docs/03_progression_model_spec.md` and
   `docs/04_exercise_library_schema.md` — update per `CLAUDE.md`'s
   "update the relevant spec in `docs/` in the same change" rule; this adds
   real new vocabulary to both the library schema and the progression model.

## Changes

1. **Schema**: additive nullable `duration_low_seconds`/`duration_high_seconds`
   on `TierExercise`, and on `PlannedSet`/`SetLog` (exact field name TBD by
   whoever implements — check existing naming convention, e.g. does this
   project use `_lb` suffixes consistently enough that `_seconds` should
   mirror it). Existing rep-based rows are unaffected (all-NULL on the new
   columns); a `TierExercise` should have **either** rep fields **or**
   duration fields populated, never both — the implementer should decide
   whether to enforce this at the API/validator layer (per invariant 1, the
   validator is the right place, not a bare DB constraint SQLite would need
   a `CHECK` for) or leave it as a convention documented in
   `04_exercise_library_schema.md`.
2. **Progression rule**: read `ironlog/engine/`'s actual double-progression
   /LADDER implementation before deciding, but the working hypothesis is a
   parallel `duration_low`/`duration_high` double-progression: all sets hit
   `duration_high` at target load → advance load, reset target toward
   `duration_low` (mirrors existing rep-based double progression exactly,
   substituting seconds for reps). This likely does **not** need a new
   `ProgressionRule` enum value if the existing LADDER `ProgressionMode` +
   a `progression_rule` of e.g. `RPE_8_STANDARD`-equivalent can be
   generalized to read "the prescribed unit" (reps or seconds) rather than
   assuming reps. If the existing code hardcodes "reps" in a way that can't
   be generalized cheaply, add a new `ProgressionRule.DURATION_LADDER`
   value instead of forcing a bigger refactor — smaller, additive,
   consistent with how this project added `FINISHER_DURATION_THEN_ROPE`
   as its own value rather than generalizing an existing one.
3. **Do not touch `FINISHER_DURATION_THEN_ROPE` / `DayFinisher` at all** —
   this is a parallel, independent path for timed *tier* slots. The
   finisher mechanism stays exactly as-is.
4. **Library content**: new Equipment + Movement rows, and the D2 T3 GS
   `TierExercise` swap, as detailed in File targets #7 above.

## Edge cases

- **RPE capture on a timed set.** This program's `SetLog.feedback_tap` is
  mandatory on working sets (`CLAUDE.md` invariant 4) regardless of rep- or
  duration-based prescription — confirm the client/API capture flow doesn't
  assume a numeric rep count is present when validating a `SetLog` write
  for a timed exercise.
- **`is_warmup` inference.** Invariant 4 also bars inferring warmup status
  from exercise name — make sure nothing in the new duration path
  special-cases "carry-type movements never have a warmup set" as a name
  check; if warmups are genuinely inapplicable to a 20-30s core carry,
  that should fall out of the movement's own configuration, not a string
  match on "Carry" in the name.
- **Two-sided (per-side) prescription vs. two separate `PlannedSet` rows.**
  Decide and document: does "20-30s per side" mean one `PlannedSet` with an
  implicit both-sides-per-set convention (like this project's existing
  unilateral movements — check how `Kickstand RDL [DB]`, itself unilateral,
  represents "did both sides" today, and mirror that exactly rather than
  inventing a new unilateral convention for this one movement), or two
  `PlannedSet` rows per round (left/right)? This has real UX and
  progression-math consequences — resolve by matching the existing
  unilateral pattern, not by picking whichever is easier to code.
- **Equipment `load_unit=LB_PER_HAND` semantics on a single-arm-loaded
  carry.** `Dumbbells (MX100)` uses `LB_PER_HAND` for a movement where both
  hands are typically loaded equally; a suitcase carry loads **one** arm at
  a time by design (per the athlete's program review — this is deliberate,
  not incidental). Confirm `LB_PER_HAND` doesn't imply "always both hands"
  anywhere downstream (e.g. any UI or engine logic that doubles a
  `LB_PER_HAND` load for display) before reusing that unit rather than
  introducing e.g. `LB` (single load figure, already unilateral by the
  movement's own `unilateral=True` flag).

## Dependencies

**Depends on spec 58 (`58-alternating-pair-tiers.md`).** Conceptually the
two are independent (session *structure*/ordering vs. prescription
*vocabulary*), but they overlap in file surface — both add real logic
inside `assembler.py`'s `assemble()` and `skeleton.py`'s `lay_skeleton()`
`SlotSpec` construction, and both extend `TierExercise`/`Tier` in
`models/program.py`. Per `/verify-plan`'s 2026-08-31 review, this spec's
worktree must be created from `main` only *after* spec 58 merges, with a
rebase check (and re-review if the rebase changes anything non-trivial)
before its own review — not built/merged in parallel or in either order.
Its own two migrations (schema, then content) additionally must land in
that internal order — the content migration references the schema
migration's new columns.

## Verification

- `pytest -q` stays green.
- New test coverage: a timed LADDER movement advances load correctly when
  all sets hit `duration_high` at RPE within cap; holds when it doesn't;
  never touches `rep_low`/`rep_high` progression code paths (regression
  guard — a bug here should not be able to corrupt an unrelated rep-based
  movement's progression).
- Manual check: after both migrations, generate a D2 session and confirm
  the suitcase carry slot appears in T3 GS with a duration prescription
  (not "0-0 reps" or similar silent unit-mismatch symptom), alongside ATG
  Split Squat and Hybrid Board Tib Raise `[D2]`. D5 is untouched by this
  spec — Ab Trainer Russian Twist's removal from D5 GS1 already happened
  independently (migration 055), not part of this spec's work.
- Confirm `IronLog-V2-Client` DTOs need updating for the new
  `PlannedSet`/`SetLog` duration field(s) — new nullable fields are
  additive/safe per `CLAUDE.md`'s client contract section, but call out
  explicitly what the client needs to add to actually *display* seconds
  instead of reps for these sets, since "the client ignores unknown keys"
  only means it won't break, not that it'll render correctly.
