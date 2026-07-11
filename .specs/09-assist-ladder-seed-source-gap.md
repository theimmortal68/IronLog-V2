# Spec 09: Fix the assist_ladder seed-source gap (Nordic Curl, Nordic Curl - Volume)

## Objective
Close the root-cause gap behind the Reverse Nordic Curl fix (`ironlog/generation/reverse_nordic_ladder_fix.py`, already live): `ironlog/seed.py`'s master movement definitions never included `assist_ladder` for any ASSISTED movement, so a from-scratch seed reproduces the same "progression rule is correct but can never advance" bug. Fix the source data for the two remaining affected movements, and backfill the live DB for the one that's actually in active use.

## Background — confirmed scope (2026-07-10)
`ironlog/seed.py:188-201` defines four `ASSISTED`-mode movements with no `assist_ladder` key at all: `Nordic Curl [GHR]`, `Nordic Curl - Volume [GHR]`, `Reverse Nordic Curl [GHR]` (already live-patched directly, see below), and `Pull-up [TOWER + TUBES]`.

- **Pull-up is NOT affected and must NOT get an assist_ladder.** Its `progression_rule=PULL_UP_ROLLING_MAX` dispatches to `advance.py:_pull_up_rolling_max`, which never reads `movement.assist_ladder` — it's tracking-only (rolling max reps via `MovementState.unassisted_max_rolling`), a fundamentally different progression mechanism. Confirmed live: its `MovementState.assist_level` is `None` on both its day-scoped rows (D1, D4) — this is correct, expected state, not a gap.
- **Nordic Curl [GHR] (movement_id=15) IS affected and IS actively used** — both `assisted_nordic_curl_d2` (D2, `PROGRAM_TO_LIBRARY["Assisted Nordic"]`) and `assisted_nordic_curl_d5` (D5, `PROGRAM_TO_LIBRARY["Assisted Nordic (eccentric)"]`, `program_seed.py:47,61`) resolve to this SAME shared movement row — `assist_ladder` is a movement-level (not day-scoped) field, so one shared ladder must cover both days' independently-tracked `assist_level` starting points. Confirmed live: D2's `MovementState.assist_level=20`, D5's `=25` — both must be valid rungs in the chosen ladder. `[25, 20, 15, 10, 5, 0]` (the D5/eccentric variant's fuller yaml ladder, a strict superset containing 20) is the correct shared value — matches the pattern the already-applied Reverse Nordic fix used (adopting the yaml's own per-exercise ladder as source of truth).
- **Nordic Curl - Volume [GHR] (movement_id=16) is currently UNUSED** — not referenced by any `PROGRAM_TO_LIBRARY` entry or `TierExercise` in the active program, and has no `MovementState` row in the live DB. Fix the seed source for correctness/future-proofing (so it isn't silently broken if activated later), but no live-DB backfill is needed or possible (no state exists to fix).
- **Reverse Nordic Curl [GHR] is already live-patched** (`ironlog/generation/reverse_nordic_ladder_fix.py`, applied 2026-07-10) but its seed-SOURCE entry in `ironlog/seed.py` still lacks `assist_ladder` — meaning a from-scratch DB would still reproduce the bug for this movement too. Fix the source here for consistency, even though the live DB is already correct.

## File targets
- Modify: `ironlog/seed.py` — the four movement `dict(...)` entries at lines ~188-201:
  - `"Nordic Curl [GHR]"`: add `assist_ladder=[25, 20, 15, 10, 5, 0]`.
  - `"Nordic Curl - Volume [GHR]"`: add `assist_ladder=[25, 20, 15, 10, 5, 0]` (same as its sibling — no yaml reference exists since it's unused; this is the reasonable default given the shared "Nordic Curl" family).
  - `"Reverse Nordic Curl [GHR]"`: add `assist_ladder=[20, 15, 10, 5, 0]` (matches the already-live-patched value exactly, for source/live consistency).
  - `"Pull-up [TOWER + TUBES]"`: **do NOT add anything** — confirm by inspection this entry is untouched in your diff.
- New: `ironlog/generation/nordic_curl_ladder_fix.py` — a live-DB backfill script for `Nordic Curl [GHR]` ONLY (mirror `ironlog/generation/reverse_nordic_ladder_fix.py`'s exact idempotent pattern: check current value, no-op if already correct, `HALT-AND-FLAG` if the movement isn't found, single `assist_ladder` update, `apply(db)`/`main()` split). `Nordic Curl - Volume` needs no backfill script (no live state to fix — do not write one).
- New test additions: a test asserting `ironlog/seed.py`'s movement definitions for Nordic Curl / Nordic Curl - Volume / Reverse Nordic Curl all carry a non-empty `assist_ladder`, and that Pull-up's entry does NOT carry one (a regression guard for both directions — the fix AND the "don't touch Pull-up" constraint).

## Edge cases
- **Do not touch Pull-up.** This is the most important negative case in this spec — a test must assert `assist_ladder is None` (or key absent) for the Pull-up definition, so a future well-intentioned "fix all ASSISTED movements" pass doesn't regress this.
- **The two Nordic Curl day-tracks (D2=20, D5=25) must both remain valid rungs** in whatever ladder value is chosen — verify `20 in ladder and 25 in ladder` in a test, not just that a ladder exists.
- **`nordic_curl_ladder_fix.py` must be idempotent** — mirror the exact pattern of the already-applied `reverse_nordic_ladder_fix.py` (check-before-write, `HALT-AND-FLAG` on missing movement, safe to re-run).
- **Do not attempt to write a backfill script for Nordic Curl - Volume** — there is no live `MovementState` for it to fix; a script with nothing to do isn't useful and risks masking a future real gap if one appears.

## Dependencies
None — standalone. No schema/API-surface change (no HUMAN GATE required). The live-DB backfill script this spec produces will need a separate explicit human confirmation before being RUN (same as the already-completed Reverse Nordic fix) — that's an execution step after this spec merges, not part of the spec/dispatch/review cycle itself.

## Verification
- New test(s) per the edge cases above: Nordic Curl/Nordic Curl-Volume/Reverse Nordic Curl all have populated `assist_ladder`; Pull-up does not; both Nordic Curl day-track assist_levels (20, 25) are valid rungs in its ladder.
- Full suite green: `cd <worktree> && ~/projects/IronLog-V2/.venv/bin/python -m pytest -q`.
- Manual (post-merge, before running the live backfill): confirm `nordic_curl_ladder_fix.py` runs cleanly against a copy of the production DB and is idempotent (matches how `reverse_nordic_ladder_fix.py` was verified before its live run).
