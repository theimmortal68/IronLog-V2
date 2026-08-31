# Spec: Alternating-pair tiers (real T1/T1b interleaving)

## Objective

Make `TierKind.PAIR` mean something: give two paired non-giant tiers a real
assembler group type that interleaves their sets (set of A → rest → set of B
→ rest → repeat) instead of running each tier as its own complete
straight-set block.

## Background / why this exists

Confirmed this session by grep + read: `tier_kind` is currently checked in
exactly one place that changes behavior — `ironlog/generation/skeleton.py`
and `ironlog/generation/assembler.py` both branch only on
`tier_kind == TierKind.GIANT_SET`. `TierKind.PAIR` is never distinguished
from `TierKind.T1_STRAIGHT` anywhere. In `assembler.py` (~line 727-750,
`assemble()`), every non-giant tier becomes its own `ExerciseGroup` with
`group_type=STRAIGHT, rounds=1`, sorted into the session by `tier_order`.
Two tiers currently labeled T1 (Bench, `T1_STRAIGHT`) and T1b (Pendlay Row,
`PAIR`) therefore run as two back-to-back complete blocks — all bench sets,
then all row sets (or vice versa by `tier_order`) — with the tier's own
`rest_seconds` applied only *between same-exercise sets*, never between the
two exercises.

The athlete's program (D1) wants Pendlay Row and Bench Press to genuinely
alternate: Pendlay set → 90s → Bench set → 90s → repeat, for N sets each
(currently both are 4-6 rep straight-set anchors, so N is whatever the
generated set count for each turns out to be — see Edge cases for the
mismatched-set-count case).

This is deterministic-core generation logic (governs *session structure*,
not load/weight), so it belongs entirely in `ironlog/generation/`, per
`CLAUDE.md` invariant 1 ("rules dispose; the model proposes" — the LLM
proposer must never be involved in whether/how two tiers interleave).

## File targets

1. `ironlog/generation/assembler.py` — add a new `GroupType` (or equivalent
   discriminator already used for `ExerciseGroup.group_type`; read the
   existing `GroupType` enum near the top of this file before adding to it)
   for an alternating pair, and the assembly logic that builds it.
2. `ironlog/generation/skeleton.py` — needs to recognize a PAIR tier and its
   partner tier at `lay_skeleton` time, and mark the resulting `SlotSpec`s so
   `assembler.py` knows they belong together (mirror how `is_giant_tier` /
   `group_key` are currently threaded through `SlotSpec` for `GIANT_SET` —
   read lines ~130-210 of `skeleton.py` for the exact pattern, including the
   anchor-role / GIANT_SET carve-out at line 186).
3. `ironlog/models/program.py` — **data-shape decision, resolve before
   coding** (see below): either (a) no model change — two existing `Tier`
   rows both tagged `PAIR` are paired by a new linking field, or (b) a new
   field on `Tier` (e.g. `paired_tier_id: Optional[int]`) to make the pairing
   explicit rather than inferred. Prefer (b): inferring pairing from
   "two adjacent PAIR tiers on the same day" is fragile once a day has more
   than one PAIR relationship. If (b), add a migration.
4. `deploy/migrations/NNN_alternating_pair_tiers.sql` — additive-only, follow
   `deploy/migrations/041_week_parity_rotation.sql` style (`ALTER TABLE ...
   ADD COLUMN` with a matching `CREATE INDEX IF NOT EXISTS` if the new column
   is a FK). Also includes the **content** migration that repoints D1's
   existing T1 (Bench)/T1b (Pendlay) tiers at each other via the new field,
   sets both `rest_seconds=90`, and keeps Pendlay's `tier_order` before
   Bench's (per the athlete's confirmed "pull first" preference).
5. `tests/test_assembler_alternating_pair.py` — new test file. Mirror the
   fixture/assertion style of an existing assembler test (find one via
   `ls tests/ | grep -i assembl` and read it first) or of
   `tests/test_slot_override_skeleton.py` for `lay_skeleton`-level coverage.
6. `docs/06_generation_algorithm_spec.md` — per `CLAUDE.md`'s "when you
   change *behavior*, update the relevant spec in `docs/` in the same
   change" — document the new PAIR semantics here.

## Changes

1. **Data shape**: add `Tier.paired_tier_id: Optional[int] = Field(default=None, foreign_key="tier.id")`.
   Both tiers in a pair point at each other (or: only the *second* tier in
   `tier_order` points back at the first — pick one and be consistent; the
   spec author's recommendation is symmetric pointers, since assembler code
   needs to find the partner from either tier without a join direction
   assumption).
2. **`skeleton.py` `lay_skeleton`**: when building `SlotSpec`s for a
   `PAIR`-kind tier with a non-null `paired_tier_id`, tag the resulting
   `SlotSpec` with a `pair_key` (e.g. `min(tier_id, paired_tier_id)` so both
   sides agree on the same key) instead of (or alongside) `group_key`. Do
   **not** reuse `is_giant_tier`/`group_key` verbatim — giant-set grouping
   assumes N≥2 *exercises inside one tier*; pair grouping is 2 exercises
   from *two different tiers*, one each. If a PAIR tier has more than one
   `TierExercise` (shouldn't normally happen — check and raise/log if it
   does, don't silently pick one), that's a data error to surface, not
   handle gracefully.
3. **`assembler.py` `assemble()`**: add a new branch (or extend the existing
   `slot.is_giant_tier` branch structure) that, when a slot carries a
   `pair_key`, accumulates into a new `ExerciseGroup` with a new
   `group_type` (e.g. `ALTERNATING`) instead of the `STRAIGHT` per-tier
   group each currently gets. The alternating group's `rest_seconds` should
   come from the tier (both tiers should have the same `rest_seconds` after
   the data migration — validate this at generation time and prefer the
   *first* tier's value with a log warning if they differ, rather than
   erroring the athlete's session generation).
4. **PlannedSet ordering** (wherever `ExerciseGroup` → `PlannedSet` rows are
   materialized downstream of `assemble()` — trace `ExerciseGroup` usage
   past `assembler.py` into whatever builds the actual session response/DB
   rows): an `ALTERNATING` group of exercises A (N_a sets) and B (N_b sets)
   must emit sets in true alternating order: A1, B1, A2, B2, ... For the
   common case N_a == N_b this is simple round-robin. See Edge cases for
   N_a != N_b.
5. Keep `GIANT_SET` behavior byte-for-byte unchanged — this spec adds a
   sibling group type, it does not touch giant-set code paths.

## Edge cases

- **Mismatched set counts.** Bench and Pendlay Row could resolve to a
  different number of prescribed sets (e.g. if one is mid-progression and
  autoregulation trims a set, or if their rep ranges ever diverge such that
  double-progression math produces a different set count). Decide and
  document the fallback: most natural is "alternate through
  `min(N_a, N_b)` rounds, then run the remainder of the longer exercise
  straight" — do not silently drop the extra sets, and do not crash
  generation over a set-count mismatch (autoregulation trimming a set
  mid-session is normal, expected behavior elsewhere in this engine).
- **A PAIR tier whose partner is missing/deleted.** If `paired_tier_id`
  points at a tier that no longer exists (deleted day, bad migration),
  fall back to treating the tier as `T1_STRAIGHT` rather than crashing
  `lay_skeleton` — log it. A live-training athlete generating tonight's
  session must never get a 500 from a data-integrity gap.
- **A PAIR tier used with an anchor `TierExercise` that also participates in
  `MesoRotation`/`WeekParityRotation`/`SlotMovementOverride`.** These
  existing per-slot override mechanisms operate on individual
  `TierExercise` rows and should keep working unmodified — the alternating
  group is a session-*structure* concern layered on top of whatever
  movement each slot resolves to, not a replacement for slot resolution.
  Add a test that a `SlotMovementOverride`-active exercise inside a PAIR
  group still resolves the overridden movement and still alternates
  correctly.
- **PlannedSet.feedback_tap / SetLog capture** (`CLAUDE.md` invariant 4):
  confirm the athlete still gets one `feedback_tap` prompt per working set
  regardless of alternating order — this is a client/API-layer concern, not
  purely generation, so trace whether the client assumes sets for one
  exercise arrive contiguously anywhere in the session-consumption path
  (`IronLog-V2-Client` repo is a separate consumer — if its DTOs assume
  grouped-by-exercise set ordering, that's a **breaking client change**,
  call it out per `CLAUDE.md`'s "Client contract" section rather than
  discovering it after ship).

## Dependencies

None — this is the first of the two specs in this batch and touches
different files than spec 59.

## Verification

- `pytest -q` stays green (744 passing baseline this session, before this
  change).
- New `tests/test_assembler_alternating_pair.py` covers: equal set counts
  alternate correctly; mismatched set counts fall back correctly; a
  deleted/missing partner tier degrades to STRAIGHT without crashing; a
  `SlotMovementOverride`-active exercise inside a pair still resolves and
  alternates.
- Manual check against the live program: generate (or dry-run generate,
  whichever this project's test client convenience supports —
  `fastapi.testclient.TestClient` per `CLAUDE.md`'s "How to verify your
  work") a D1 Upper A session post-migration and confirm the response's
  set ordering for Bench/Pendlay is A,B,A,B,... not A,A,A,B,B,B.
- Confirm `IronLog-V2-Client` impact is either "none" (server-shape
  unchanged, ordering is already just a list the client renders in order)
  or explicitly flagged as a client-side follow-up.
