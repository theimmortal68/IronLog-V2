# Spec 51: Home Gym Con condensed-week session builder

## Objective

Build a one-off idempotent script that materializes real, loggable `Session` rows for three specific upcoming calendar dates (Mon 2026-07-27, Tue 2026-07-28, Wed 2026-07-29), each broken into several small "mini-sessions" that carry their exercises' TRUE origin `day_role` — so progression tracking (current_load/assist_level/HT plates advances, etc.) reads and writes exactly as it would on a normal week, with zero risk of forking any movement's `MovementState`/`HtProgressionState` into a disconnected duplicate.

This is athlete-approved curated content for a real, imminent (2 days away) training constraint (Home Gym Con) — not a permanent program change. No schema change, no engine code change. Pure one-off data seeding, matching the established precedent of `ironlog/generation/live_seed_ramp_and_finishers.py` and `scripts/backfill_ht_unification.py`.

## The exact mini-sessions to build (11 total, in this exact chronological order — order matters, see "Ordering" below)

For each mini-session: `date`, true `origin_day_role` (this becomes the mini-session's `Session.day_role` — NOT a synthetic label), and the exact movement names to include (by their exact `Movement.name` string, e.g. `"Bench Press [PB]"` — resolve via `select(Movement).where(Movement.name == ...)`).

1. **2026-07-27, origin "D1 Upper Push"**: Bench Press [PB], Pendlay Row - Narrow [OB], Face-Up Incline Knee Raise
2. **2026-07-27, origin "D2 Lower A"**: Belt Squat [GHR + FT], ATG Split Squat, Cable Tibialis Raise
3. **2026-07-27, origin "D6 Weak Points"**: Face Pull [FT]
4. **2026-07-28, origin "D5 Lower B"**: RDL [PB], Hip Thrust [HIP_THRUST], Reverse Nordic Curl [GHR]
5. **2026-07-28, origin "D2 Lower A"**: Leg Curl [GHR]
6. **2026-07-28, origin "D6 Weak Points"**: DB Seal Row [DB + UTIL_SEAT], Lateral Raise [FT]
7. **2026-07-28, origin "D1 Upper Push"**: Ab Wheel [WHEEL]
8. **2026-07-29, origin "D4 Upper Pull"**: Standing OHP [PB], Pull-up [TOWER + TUBES], Dragon Flag
9. **2026-07-29, origin "D6 Weak Points"**: Dips [ANDREONI + FT], T-Bar Row - Wide [OB + KLEVA + LM], Cable V-Bar Pushdown [FT]
10. **2026-07-29, origin "D1 Upper Push"**: Incline DB Press [DB + BENCH]
11. **2026-07-29, origin "D5 Lower B"**: Nordic Curl [GHR]

Resolve exact `Movement.name` strings by querying, don't guess exact bracket-suffix spelling — match against the seeded names (the bracket suffixes above are best-effort transcriptions from a DB dump; verify each one resolves to exactly one row before using it, and if any name doesn't match exactly, resolve by fuzzy/prefix match on `base_name` and print what you matched for a human to confirm before the script actually writes anything).

## Why per-origin mini-sessions (not one session per calendar day)

Every `MovementState` row is keyed `(movement_id, day_id)`, and `day_id` for a logged/analyzed session comes from that session's own single `day_role` field — applied uniformly to EVERY movement in the session (see `run_analysis.py`'s `day_id = workout.day_role` and `loop.py`'s `commit_session`, same pattern). A session mixing exercises whose real homes are different days (e.g. Bench Press's real home is "D1 Upper Push", Belt Squat's is "D2 Lower A") would, if given one shared day_role, resolve the "wrong-day" movements against a brand-new, empty, disconnected `MovementState` row — silently forking their real progression history. Splitting into small mini-sessions, each carrying its exercises' single TRUE origin day_role, sidesteps this completely: every movement analyzed in a mini-session resolves against its real, existing progression row, exactly as if it had been logged on its real day. Zero new engine code, zero schema change — this is the reason this spec is pure data construction.

## How to source each set's target values (do not hand-compute — reuse the real engine)

For each of the 5 true origin day_roles (`"D1 Upper Push"`, `"D2 Lower A"`, `"D4 Upper Pull"`, `"D5 Lower B"`, `"D6 Weak Points"`), call the real generation engine ONCE to get correctly-computed, current prescriptions, then extract only this week's selected exercises from that result:

```python
from ironlog.generation.loop import generate_session
from ironlog.api.app import _make_proposer, _week_keyer
from ironlog.generation.skeleton import lay_skeleton

sk = lay_skeleton(day_role, db)
proposer = _make_proposer(sk)
outcome = generate_session(day_role, db, proposer, _week_keyer)
assert outcome.assembled is not None, f"{day_role}: generation exhausted (rejections: {outcome.rejections})"
```

`outcome.assembled.session` is the real, in-memory (uncommitted) `Session` for that day_role, with `.groups[i].exercises[j].planned_sets[k]` carrying every field already correctly computed (target_load, target_reps_low/high, target_rpe, target_plates, band_config, target_felt_peak, target_unassisted_reps, target_assisted_reps — whatever applies to that movement's scheme). For each mini-session's selected movement names, find the matching `PlannedExercise` (by `movement_id`) inside that generated result and copy its **entire `planned_sets` list verbatim** (same `set_index`, `set_role`, `is_warmup`, and every target_* field) into the new mini-session's own `PlannedExercise`. This guarantees a Reverse Nordic Curl's DOUBLE_PROGRESSION rep range, an HT movement's plates+band_config+felt_peak, ramp sets on a T1 anchor, etc. are all copied exactly as the real engine would prescribe them today — never hand-derived.

**Do not commit any of these 5 generation calls via `commit_session`** — they are read-only preview calls used purely as a data source. Nothing about the athlete's real D1/D2/D4/D5/D6 `MovementState`/`HtProgressionState` rows should be touched by this script; only NEW mini-session rows get written.

## Building each mini-session (real, persisted rows)

For each of the 11 mini-sessions, in order:

```python
session = Session(
    date=<the mini-session's date>,
    day_role=<the mini-session's TRUE origin day_role>,
    phase=<current EngineState.phase, e.g. via `db.exec(select(EngineState)).one().phase`>,
    status=SessionStatus.PLANNED,
    signature={"program_day_id": <the real ProgramDay.id for that origin day_role>},
)
db.add(session)
db.flush()
```

Resolve `program_day_id` via `db.exec(select(ProgramDay).where(ProgramDay.day_role == origin_day_role)).one().id` — this is what makes `_tier_exercise_for_session_movement` (run_analysis.py) correctly resolve Hip Thrust's `unified_ht_group` for mini-session 4 (Tuesday's D5-origin session containing Hip Thrust), instead of falling back to a day_role-string lookup that could behave differently. Get this right for ALL 11 mini-sessions, not just the Hip Thrust one — it's what feeds warmup/finisher payload building too (`build_warmup_payload`/`build_finisher_payload` in `/sessions/today`), so every mini-session should show correct warmup/finisher content for its origin day if that day has one configured.

Then one `ExerciseGroup` per mini-session (a single `STRAIGHT`-type group is fine — these are hand-picked exercises, not simulating the real day's giant-set grouping; use `group_type=GroupType.STRAIGHT, rounds=1, rest_seconds=<copy from the exercise's real group in the generated source, or a sane default like 120 if the source used a giant-set rest value that doesn't apply to a single-exercise mini-session>` — read the real generated group's `rest_seconds` for a sensible default, don't invent a number). Order exercises within a mini-session in the same relative order they appeared in the real day's full session (so e.g. Monday's D1-origin mini-session keeps Bench Press before Pendlay Row before Knee Raise, matching D1's real tier order).

For each selected exercise: one `PlannedExercise` (copy `scheme`/`objective` verbatim from the real generated `PlannedExercise`), then its full `planned_sets` list copied verbatim (new `PlannedSet` rows, same field values, new `planned_exercise_id`).

## Ordering (critical — this is what makes `/sessions/today` serve them in the right sequence)

`/sessions/today` (already-existing, unmodified endpoint) returns "the most-recently-approved PLANNED, unanalyzed session (greatest id)". This means: insert the 11 mini-sessions in EXACTLY the chronological/priority order listed above (mini-session 1 first, mini-session 11 last) so their auto-incrementing `id`s land in that same order — the app will then naturally serve mini-session 1 as "today", and each subsequent mini-session becomes the new "today" only after the previous one is completed+reviewed (status becomes COMPLETED and `analyzed_at` gets set by the existing review/confirm flow). Insert them in one script run, in a single loop over the ordered list above — do not parallelize or reorder.

**Athlete-facing safety check**: before this script inserts anything, print the CURRENT most-recent PLANNED+unanalyzed session (if any) — if one already exists (e.g. session 15 from 2026-07-24, if for some reason it were still PLANNED/unanalyzed, though it should be COMPLETED per production state as of this spec's writing), the script must refuse to proceed and print a clear error rather than inserting 11 new sessions with higher ids that would silently supersede an in-progress real session. Verify against production before running: the last known session (id=15, 2026-07-24) is COMPLETED — confirm this is still true immediately before running the script for real.

## File Targets

- `scripts/build_hgc_condensed_week.py` — new one-off idempotent script.
- `tests/test_hgc_condensed_week.py` — new file, unit/integration tests against `gen_db`/`gen_db_calibrated` fixtures (NOT against production — the script itself is never run in tests, only its pure/helper logic, or the whole script run against a fully-seeded test DB fixture).

## Idempotency

Running this script twice must not create duplicate mini-sessions. Before inserting, check for an existing PLANNED session whose `date` + `day_role` + `signature.program_day_id` match one of the 11 planned mini-sessions above (or a simpler robust marker: store a distinguishing value in `Session.rationale`, e.g. `f"HGC condensed week — mini-session {n}/11"`, and check for that exact string before inserting that mini-session). If any of the 11 already exist, skip creating that one and log "already exists, skipping" — but still validate/print the full plan so a human sees a complete picture of what exists vs. what's about to be created.

## Edge Cases

- **A movement's real day_role generation is `exhausted`** (`outcome.assembled is None`, `outcome.rejections` populated): the script must fail loudly for that day_role with the rejection reasons printed, not silently skip the mini-sessions that depend on it. Do not fall back to a fabricated/guessed set of targets.
- **A selected movement name doesn't appear in its real day_role's generated session** (e.g. a novelty-swap/adaptive-slot deviation replaced it with a different movement this week): fail loudly, printing what movements WERE present in that day's assembled exercises, so a human can reconcile (the movement's own progression may have moved to a different exercise via the adaptive engine since this spec was written).
- **Hip Thrust's unified progression**: mini-session 4 (2026-07-28, D5 origin) is the only mini-session carrying Hip Thrust this week. Confirm via a real query before/after building: `HtProgressionState` for Hip Thrust's `movement_id` + `unified_ht_group="main"` — this row must be the SAME one D2/D5's real progression normally shares (already verified live in production tonight, both at 180+Red). The generated D5 source session's Hip Thrust `PlannedSet`s should already reflect this shared value correctly (since `generate_session` calls the real assembler, which already branches on `unified_ht_group` as of spec 50) — this spec does not need its own HT-specific logic, just correct copying.
- **Reverse Nordic Curl appears on both D2 and D5** with independent (non-unified) progressions — mini-session 1 doesn't include it; mini-session 4 (D5 origin) does. Its `PlannedSet`s must come from the D5-origin `generate_session` call (not D2's), so it correctly resolves/writes D5's own `MovementState` row, not D2's.
- **Nordic Curl appears only on D5** in this week's plan (mini-session 11) — straightforward, single source day.

## Dependencies

None — standalone, depends only on already-merged/deployed code (spec 49/50's HT unification, already live).

## Verification

- `~/projects/IronLog-V2/.venv/bin/python -m pytest -q tests/test_hgc_condensed_week.py -v` — new tests pass.
- `~/projects/IronLog-V2/.venv/bin/python -m pytest -q` — full suite green, zero regressions (baseline 683 passing after specs 49/50).
- **Dry-run mode**: the script's main entry point must support a `--dry-run` flag (or equivalent, e.g. a `dry_run: bool` parameter on its main function) that prints the full plan (every mini-session's date/day_role/movements/set count) WITHOUT writing anything to the DB — Tier A will run this first against a scratch copy of production (mirroring the exact verification precedent from spec 50's backfill script tonight) before ever running it for real.
- Manual, against a scratch copy of production (not production directly, initially): run with `--dry-run` first, inspect the full printed plan, THEN run for real against the same scratch copy, confirm exactly 11 new Session rows exist with the correct dates/day_roles/exercises/set counts, confirm a second run is a no-op (idempotent, "already exists" for all 11). Only after this passes does Tier A run it against real production (Class 2-adjacent: writes real session data the athlete will train from — human-gated, confirm before running for real, per this repo's established Deploy Gate discipline for data-writing one-off scripts).
