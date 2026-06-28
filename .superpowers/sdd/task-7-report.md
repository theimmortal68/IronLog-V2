# Task 7 Completion Report — Two-Tier Fallback (NAMED GATE c)

## Status
**DONE — gate-c FAILING (real assembler issue surfaced, per brief instruction)**

## Commit
- **SHA**: `d6beff5`
- **Subject**: `feat(gen): two-tier fallback; cold-start emits program (gate c)`

## Files
- Created: `ironlog/generation/fallback.py`
- Created: `tests/test_generation_fallback.py`

## Pytest Output (tail)
```
FAILED tests/test_generation_fallback.py::test_cold_start_emits_program_valid_and_trainable
1 failed, 187 passed, 60 warnings in 1.58s
```
188 total tests (185 prior + 3 new). 2 of 3 new tests PASS.

## Gate c Result: FAIL — Root Cause Investigation

Gate-c asserts `validate(fb.session, build_validation_context(ctx, gen_db)).is_structurally_valid`.
It fails with two distinct violations:

### Violation 1: GIANT_SET_CONCURRENCY (real assembler design issue)

The assembler (Task 5) creates a **single shared `giant_group`** for ALL "giant"-kind slots.
D1 Upper Push has 9 giant slots across 3 tiers (T2 GS × 3, T3 GS × 3, T4 GS × 3).
`program_selections` correctly emits all 9 slots per spec.
All 9 land in the one `giant_group` → GIANT_SET_CONCURRENCY REJECT: "GIANT_SET has 9 exercises, expected 1-3 (room geometry)".

**Root cause:** The assembler's `giant_group` is tier-blind. In practice D1 has three separate
3-exercise circuits; the assembler collapses them into one. Fixing this requires the assembler
to create one ExerciseGroup(GIANT_SET) per source tier. This is a Task 5 assembler issue;
`program_selections` and `fallback_session` are correct per spec.

### Violation 2: KNEE_FREQUENCY (cross-session aggregate — test design issue)

`build_validation_context(ctx, gen_db)` passes `ctx.tallies` which has
`knee_targets = {"NORDIC": 2, "TIB": 2, "KOT": 2, "SISSY": 1}` (set by `resolve_context`)
and `knee_counts = {}` (fresh DB, no SetLogs). D1 Upper Push has no knee exercises.
Result: 4 KNEE_FREQUENCY REJECTs fire unconditionally.

`KNEE_FREQUENCY` is a cross-session weekly aggregate rule. A single D1 session cannot
retroactively satisfy weeks of missing knee frequency. The per-session structural gate and
the weekly aggregate validator are conflated by passing full tallies to `build_validation_context`.

Gate left exactly as written per brief instruction ("don't weaken the gate").

## Recommended Fixes (not in scope for Task 7)

1. **Assembler (Task 5):** Add `tier_id` to `SlotSpec`; the assembler groups giant slots by
   `tier_id` → one ExerciseGroup per tier. D1 produces 3 groups of 3 → GIANT_SET_CONCURRENCY satisfied.

2. **Gate-c test:** For pure structural validity, use `ValidationContext(tallies=None)` or seed
   the DB with SetLogs satisfying knee targets before the assertion. Alternatively adjust gate-c
   to validate structural rules only (pass `tallies=None`).

## Hand-off
Branch tip: `d6beff5`. 187/188 tests passing. Gate-c blocked on Task 5 assembler tier-grouping gap.

---

## Task 7 fix wave

**Commit**: `ad2feaa` — `fix(gen): assembler one-giant-group-per-tier + per-session validate structural-only (tallies=None); gate c green`

### Fix 1 — One GIANT_SET group per source program tier (skeleton → assembler)

**Root cause**: `assembler.py` put every "giant"-kind slot into a single `ExerciseGroup(GIANT_SET)`. D1 Upper Push has 9 giant slots (T2 GS × 3, T3 GS × 3, T4 GS × 3) → one 9-exercise group → `GIANT_SET_CONCURRENCY` REJECT (max 3).

**group_key flow (skeleton → assembler)**:
1. Added `group_key: str = ""` to `SlotSpec` in `skeleton.py` (default `""` so all existing call sites stay valid).
2. `lay_skeleton` now sets `group_key=tier.tier_label` when building each `SlotSpec`.
3. The assembler replaces the single `giant_group` variable with `giant_groups: Dict[str, ExerciseGroup]` (Python dict, insertion-order-preserving). For each "giant" slot, it uses `slot.group_key` (or `slot_id` as fallback for manually-constructed `SlotSpec`s with empty `group_key`) to key into the dict.
4. First time a `group_key` appears → create a fresh `ExerciseGroup(GIANT_SET, rounds=3)`. Subsequent slots with the same key → append into the existing group.

**order_index cleanup**: The reviewer-flagged collision risk (giant group `order_index` colliding with straight groups) is resolved. All `order_index` values start at 0/placeholder and are assigned in a single clean pass at the end: anchors (already set 0..N-1) → giant tier groups in first-appearance order → knee/accessory STRAIGHT groups. No collisions possible.

**Result for D1**: T2 GS (3 ex), T3 GS (3 ex), T4 GS (3 ex) → 3 GIANT_SET groups, each with exactly 3 exercises → `GIANT_SET_CONCURRENCY` satisfied.

### Fix 2 — Per-session validate is structural-only (tallies=None)

**Root cause**: `build_validation_context` in `repair.py` passed `tallies=ctx.tallies` which carries `knee_targets = {NORDIC: 2, TIB: 2, KOT: 2, SISSY: 1}` and `knee_counts = {}` (fresh weekly ledger). D1 Upper Push has no knee exercises → 4 `KNEE_FREQUENCY` REJECTs fired unconditionally on every D1 session.

**Decision (final)**: Per-session generation validate is structural-only. `build_validation_context` now passes `tallies=None`. The validator already guards this: `_check_knee_frequency` and `_check_pull_push_ratio` both return `[]` when `ctx.tallies is None`. Weekly frequency is guaranteed at program-design time (`test_knee_frequencies_are_satisfiable`, Task 2) and soft-biased via owed-requirements (Fork 3); it is not a per-session structural hard reject.

**Updated repair test**: `test_repair_exhausted_after_max_retries` previously relied on `KNEE_FREQUENCY` to exhaust the loop. With `tallies=None`, that no longer fires. The test now triggers exhaustion via `PRIMARY_NOT_FIRST` — the anchor movement (Bench Press, `is_primary=True`) is placed in a giant adaptive slot. The validator always rejects "primary movement inside GIANT_SET group". The `StubProposer` always returns the same selection → always violates → exhausts at `max_retries=3`. Test intent (loop-bound verification) preserved.

### Gate-c result: GREEN

`test_cold_start_emits_program_valid_and_trainable` now PASSES. The cold-start D1 fallback assembles 3 GIANT_SET groups (T2/T3/T4 GS, 3 exercises each) + 1 anchor STRAIGHT group → structurally valid. `build_validation_context` uses `tallies=None` → no spurious `KNEE_FREQUENCY` rejects.

### New tests (3 added)

| Test | Purpose |
|------|---------|
| `test_d1_giant_groups_are_per_tier` | Proves Fix 1: ≥2 GIANT_SET groups, each with 1-3 exercises |
| `test_build_validation_context_is_structural_only` | Proves Fix 2: `vc.tallies is None` |
| `test_cold_start_d1_no_spurious_knee_frequency_reject` | D1 session structurally valid, no `KNEE_FREQUENCY` reject |

### Pytest tails

Affected files only (19 tests):
```
19 passed, 13 warnings in 0.87s
```

Full suite:
```
191 passed, 62 warnings in 1.83s
```

**191 passed, 0 red.**

### Hand-off (updated)
Branch tip: `ad2feaa`. Gate-c GREEN. Full suite 191/191. Ready for Task 8.

---

## Menu-membership enforcement

**Commit:** `087b196`

### New function: `check_menu_membership`

`ironlog/generation/repair.py` — `check_menu_membership(selections: Selections, ctx: GenerationContext) -> List[str]`

Iterates every `SlotSelection` in `selections.slots`. For each slot that has an entry in `ctx.candidate_menus` (the "giant" and "knee" adaptive slots populated by Fork 3), it checks whether `movement_id` is in that menu. If not, it appends an outcome-only reason:

```
MENU_MEMBERSHIP: slot <slot_id> movement <movement_id> not in candidate menu
```

Slots absent from `candidate_menus` (anchors, conditioning) are skipped. Returns `[]` when all selections are in-menu.

### Wired into the loop

In `propose_validate_repair`, immediately after `sel = proposer.propose(...)` and **before** `assemble(...)`:

```python
membership_reasons = check_menu_membership(sel, ctx)
if membership_reasons:
    reasons = membership_reasons
    continue
```

Off-menu selections are structurally rejected without assembling. The outcome-only membership reasons become the feedback payload for the next attempt, bounded by the same `max_retries`. An off-menu session is never assembled or committed.

Because a knee slot's candidate menu contains only movements with the matching `knee_modality`, this guarantees by construction that any accepted session has the knee slot filled with a knee-modality movement — the "knee-slot-filled per-session" invariant.

### Two new tests (`tests/test_generation_repair.py`)

**Test 4 — `test_check_menu_membership_flags_off_menu_and_passes_in_menu`** (pure unit, no DB):
- Off-menu `movement_id=99` against menu `[10, 20, 30]` → non-empty list; reason contains "MENU_MEMBERSHIP"; no remedy words ("add ", "swap ", "use ", "put ").
- In-menu `movement_id=10` → returns `[]`.
- Slot absent from `candidate_menus` → skipped, returns `[]`.

**Test 5 — `test_repair_loop_rejects_off_menu_knee_selection`** (integration, `gen_db`):
- Lays skeleton for `D2 Lower A` (has NORDIC, KOT, TIB knee slots in the seeded DB).
- Picks the first knee slot; identifies an off-menu movement (any movement not in the knee slot's candidate menu).
- `StubProposer` always returns this off-menu selection.
- `propose_validate_repair(..., max_retries=3)` → `exhausted=True`, `assembled=None`, `attempts=3`, rejections contain "MENU_MEMBERSHIP".
- **Positive control:** `check_menu_membership` with `knee_menu[0]` (the program anchor, in-menu) returns `[]`.

### Pytest tail

New tests:
```
7 passed, 3 warnings in 0.20s   (test_generation_repair.py)
```

Full suite:
```
193 passed, 59 warnings in 1.84s
```

**193 passed, 0 red.** (191 prior + 2 new menu-membership tests.)

### Hand-off (updated)
Branch tip: `087b196`. Gate-c GREEN. Full suite 193/193. Menu-membership enforcement complete — off-menu knee-slot selections are now hard-rejected and never accepted.
