# Tier-order fix — completion report

## Root cause confirmation

Confirmed exactly as described in the task. `ironlog/generation/assembler.py`'s
`assemble()` built `ExerciseGroup`s in three hardcoded phases regardless of the
source `Tier.tier_order`:

1. ALL anchor-tier-role exercises first (from `skeleton.anchor_movement_ids`/`anchor_meta`)
2. Giant-set adaptive groups next
3. Non-giant adaptive (straight) groups last

Neither `SlotSpec` nor `AnchorSpec` carried the source `Tier.tier_order` — only
`tier_label`/`group_key` (display strings). D2 "Lower A" and D5 "Lower B" both
have a trailing anchor tier "T4" (`tier_role="anchor"`, `TierKind.T1_STRAIGHT`)
seeded AFTER two GIANT_SET tiers in real `Tier.tier_order` (T1=1, T2 GS=2,
T3 GS=3, T4=4). Because phase 1 always ran first, T4's exercise (Ab Trainer
Decline Sit-up on D2, Ab Trainer Russian Twist on D5) rendered as the SECOND
exercise of the day instead of last.

**Live read-only confirmation against the real production DB**
(`~/projects/IronLog-V2/ironlog.db`, opened `mode=ro`, no writes attempted):
`lay_skeleton('D2 Lower A', ...)` on production data returns T1 anchor at
`tier_order=1` and T4 anchor at `tier_order=4`, with the giant slots (T2 GS,
T3 GS) at `tier_order=2`/`3` in between — exactly the shape described in the
bug report.

## What changed and why

**`ironlog/generation/skeleton.py`**
- Added `tier_order: Optional[int] = None` to both `AnchorSpec` and `SlotSpec`.
- Populated `tier_order=tier.tier_order` at both `SlotSpec`/`AnchorSpec`
  construction sites inside `lay_skeleton()`'s single tier/TierExercise loop
  (there is only one construction site for each dataclass in this codebase,
  not multiple as the task description speculated — confirmed by reading the
  whole function before editing).

**`ironlog/generation/assembler.py`**
- Replaced the three-phase, three-separate-counter `order_index` assignment
  in `assemble()` with a single unified ordering pass:
  1. Every group built (anchor STRAIGHT, giant GIANT_SET, non-giant adaptive
     STRAIGHT) is now collected into a `pending_groups` list tagged with
     `(tier_order, construction_order, group)` instead of being appended to
     `session.groups` / given an `order_index` immediately.
  2. After all groups are built, `pending_groups` is sorted by
     `(tier_order, construction_order)` — `tier_order` is the TRUE source-Tier
     position; `construction_order` is a stable, defensive tie-break (each
     `Tier` normally has a unique `tier_order`, so ties shouldn't occur in
     practice, but the sort handles it gracefully rather than raising).
  3. `order_index` is then assigned sequentially in that sorted order and
     each group is appended to `session.groups`.
  - Giant-set groups accumulate multiple `SlotSpec`s (same source tier) into
    one `ExerciseGroup`; `giant_group_rank` records each such group's
    `(tier_order, construction_order)` at first-creation time so later slots
    of the same tier don't re-rank it.
  - Defensive `None`-safe sort key (`tier_order if not None else inf`) in case
    a manually-constructed `SlotSpec`/`AnchorSpec` (e.g. in an unrelated test)
    omits `tier_order` — confirmed via grep that no such construction site
    currently flows into `assemble()`, but this avoids a `TypeError` if one
    ever does.
  - No changes to `_build_exercise`, HT/prospective-load logic, warmup/finisher
    payload logic, or anything else in the file — scope kept to the ordering
    fix only, per the task's discipline instruction.

## Test results

**New tests added** (`tests/test_generation_assembler.py`):
- `test_d1_group_order_is_unaffected_by_tier_order_fix` — no-regression case:
  D1 Upper Push (T1, T1b genuinely first in `tier_order`) must produce the
  exact same group layout as before (`T1, T1b, T2 GS, T3 GS`).
- `test_d2_trailing_anchor_tier_sorts_last_by_true_tier_order` — reproduces
  the real bug on D2 Lower A using the actual seeded program
  (`gen_db_calibrated` fixture, no synthetic fixture needed — the seed data
  in `program_seed.py` already has D2's real T4 trailing-anchor shape).
  Asserts the group layout is `T1, T2 GS, T3 GS, T4` (T4 last) and the T4
  group contains "Ab Trainer Decline Sit-up".
- `test_d5_trailing_anchor_tier_sorts_last_by_true_tier_order` — D5 twin
  (Ab Trainer Russian Twist), same shape.

**RED confirmed pre-fix**: stashed `assembler.py`/`skeleton.py` back to the
pre-fix state and re-ran the three new tests — the D1 no-regression test
passed (expected, since D1 was never buggy) and both trailing-anchor tests
FAILED with the exact bug signature:
```
D5's trailing anchor tier (T4) must sort LAST by true tier_order, got
[('T1','STRAIGHT'), ('T4','STRAIGHT'), ('T2 GS','GIANT_SET'), ('T3 GS','GIANT_SET')]
```
Then `git stash pop` restored the fix and re-ran: **GREEN**, all 8 tests in
`test_generation_assembler.py` pass.

**Full suite**: `~/projects/IronLog-V2/.venv/bin/python -m pytest -q` from
inside the worktree, using the main checkout's `.venv` interpreter (worktree
has no `.venv` of its own) — **716 passed** (baseline 713 + 3 new tests = 716,
exact match), 0 failed.

## Live-generation sanity check

Performed as a read-only tier_order confirmation (see Root Cause Confirmation
above) rather than a full `assemble()` run against production data — a full
run wasn't necessary to confirm the fix's behavior (the unit tests already
exercise `assemble()` end-to-end against the real seeded D2/D5 tier shapes
via `gen_db_calibrated`, which mirrors the same `program_seed.py` composition
as production), and this kept the live check strictly read-only with zero
risk to the production DB (`sqlite3`/SQLAlchemy connection opened
`mode=ro`/`uri=true`, no engine writes ever attempted, no file copied or
mutated).

## Files changed

- `ironlog/generation/skeleton.py` — added `tier_order` field to `AnchorSpec`
  and `SlotSpec`; populated at both construction sites in `lay_skeleton()`.
- `ironlog/generation/assembler.py` — replaced the three-phase order_index
  assignment in `assemble()` with a unified true-tier_order sort.
- `tests/test_generation_assembler.py` — 3 new tests (no-regression + 2 bug
  reproductions).

## Self-review findings

- **Completeness**: every day's session ordering now reflects true
  `tier_order`, not just D2/D5 — the fix is fully general (no D2/D5-specific
  branching anywhere). Verified D1's ordering is byte-for-byte unchanged.
- **Quality**: single unified sort replaces three separate counters/loops —
  simpler control flow, not more complex, despite fixing a real bug.
- **Discipline**: no refactor beyond what the ordering fix required. Did not
  touch `_build_exercise`, HT logic, warmup/finisher payloads, or any other
  file.
- **Testing**: both the no-regression case (D1) and the trailing-anchor case
  (D2 AND D5, both real production shapes) are covered, with a genuine
  RED→GREEN cycle confirmed via git stash, not just written-then-passing.
- Confirmed only one `SlotSpec`/`AnchorSpec` construction site each exists in
  `lay_skeleton()` — the task description's caveat about "there may be more
  than one place" didn't apply here.

## Concerns

None. The fix is narrow, general, fully tested (unit + full-suite + live
read-only production-data confirmation), and scope-clean (only the two
target files + the test file changed).
