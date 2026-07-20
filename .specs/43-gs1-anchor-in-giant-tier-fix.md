# Spec 43: Fold GS1's Pull-up anchor into its shared giant-set group

## Objective
D6 Weak Points' GS1 tier is seeded as a 3-exercise `GIANT_SET` (Pull-up, Dips, Hip Thrust — `program_seed.py::_seed_d6`, tier "GS1") but the most recently generated session (id=11, 2026-07-19) split it into two separate `ExerciseGroup` rows: a `STRAIGHT` group containing only Pull-up, and a separate `GIANT_SET` group containing Dips + Hip Thrust — both labeled "GS1". Athlete-confirmed intent (2026-07-20): all three exercises must rotate together in one physical giant set (3 rounds, one shared rest block), matching what's actually seeded.

## Root cause (confirmed via direct investigation, not guessed)
- `ironlog/generation/skeleton.py::lay_skeleton()` routes EVERY `TierExercise` with `tier_role == "anchor"` into a separate `anchor_movement_ids`/`anchor_meta` list, unconditionally — regardless of the source `Tier.tier_kind`.
- `ironlog/generation/assembler.py::assemble()` then builds one `STRAIGHT` `ExerciseGroup` per anchor (docstring: "1. Anchor movements — STRAIGHT groups, T1 ordering"), and separately groups all `adaptive_slots` with `is_giant_tier=True` sharing a `group_key` into one shared `GIANT_SET` group.
- GS1's Pull-up (`TierExercise` id=33, `d6_g1a`) is seeded `tier_role="anchor"` (confirmed: `program_seed.py` line ~601) — the ONLY anchor-role exercise inside any `GIANT_SET` tier in the entire program (checked all of GS1/GS2/GS3/T2 GS/T3 GS/T4 GS; every other giant tier uses only "free"/"semi" roles). This unconditional anchor/giant split was never built to handle that combination.
- This is deliberate seed data, not a typo: Pull-up carries `scheme="REP_RATIO"`, `rep_low=5, rep_high=8` (a "Set 1 unassisted max test"), genuinely different from Dips (`DOUBLE_PROGRESSION`, 8-12) and Hip Thrust (`FIXED`, 12-12) — so the fix must preserve Pull-up's distinct scheme/rep-target/non-substitutability, not just relabel it as a generic accessory.

## The fix

### 1. `ironlog/generation/skeleton.py`

In `lay_skeleton()`'s main loop, change the anchor/adaptive branch condition so an anchor-role `TierExercise` whose `Tier.tier_kind == TierKind.GIANT_SET` is routed into `adaptive_slots` instead of the `anchor_movement_ids`/`anchor_meta` lists:

```python
for te in exercises:
    if te.tier_role == "anchor" and tier.tier_kind != TierKind.GIANT_SET:
        movement_id = _effective_movement_id(db, te, meso_number)
        anchor_movement_ids.append(movement_id)
        anchor_meta.append(AnchorSpec(
            rep_low=te.rep_low, rep_high=te.rep_high,
            rpe_cap=te.rpe_cap, rest_seconds=tier.rest_seconds,
            tier_label=tier.tier_label, shoe=tier.shoe,
            tier_exercise_id=te.id,
        ))
    else:
        adaptive_slots.append(SlotSpec(
            slot_id=te.slot_id,
            kind=_slot_kind(te, tier),
            pattern=te.pattern,
            tier_role=te.tier_role,
            knee_modality=te.knee_modality,
            program_movement_id=_effective_movement_id(db, te, meso_number),
            is_giant_tier=tier.tier_kind == TierKind.GIANT_SET,
            group_key=tier.tier_label,
            rep_low=te.rep_low, rep_high=te.rep_high, rpe_cap=te.rpe_cap,
            rest_seconds=tier.rest_seconds, shoe=tier.shoe,
            tier_exercise_id=te.id,
        ))
```

(Only the `if` condition changes — add `and tier.tier_kind != TierKind.GIANT_SET`. The rest of both branches is unchanged from current code.)

Update `_slot_kind()` so an anchor-role exercise inside a `GIANT_SET` tier does NOT get classified `"giant"` (which would make it candidate-menu-eligible / LLM-substitutable — see "Why this matters" below). It must fall through to `"accessory"`, matching the existing non-deviable/no-candidate-menu treatment anchors already get via `tier_role`:

```python
def _slot_kind(te: TierExercise, tier: Tier) -> str:
    """Compute the slot kind for a non-anchor TierExercise."""
    if te.knee_modality is not None:
        return "knee"
    if tier.tier_kind == TierKind.GIANT_SET and te.tier_role != "anchor":
        return "giant"
    return "accessory"
```

Update the module docstring's `adaptive_slots:` section (the `kind = ...` description, currently 3 lines) to mention the anchor-in-giant-tier case, and update the `is_giant_tier` doc comment on `SlotSpec` (currently says "so knee-priority slots inside GIANT_SET tiers still assemble into the shared tier group" — extend to mention anchor-role slots too).

### 2. Why this matters (do not skip this reasoning when implementing)
- `SlotSpec.is_giant_tier` is set directly from `tier.tier_kind == TierKind.GIANT_SET`, independent of `kind` — this is what makes `assembler.py::assemble()` group the slot into the shared `GIANT_SET` `ExerciseGroup` (its grouping decision at line ~522 keys off `slot.is_giant_tier`, not `slot.kind`). No assembler.py change is needed — once Pull-up is an adaptive slot with `is_giant_tier=True` and `group_key="GS1"`, it flows through the EXISTING giant-grouping code path automatically, same as Dips/Hip Thrust already do.
- `kind` must NOT become `"giant"` for this slot because `ironlog/generation/context.py` gates BOTH candidate-menu construction (`if slot.kind in ("giant", "knee")`, line ~356) and the LLM-invocation deviation check (`should_invoke_llm`, which separately already excludes `tier_role not in ("semi","free")` and no `knee_modality` — this part is unaffected since we preserve `tier_role="anchor"` on the SlotSpec). If `kind` became `"giant"`, Pull-up would gain a candidate menu and become substitutable by the LLM proposer — breaking its fixed "Set 1 unassisted max test" identity. Keeping `kind="accessory"` (via the fallthrough) correctly excludes it from `ctx.candidate_menus`, so `slot_has_deviation_signal` returns `False` for it (menu-less slots are non-deviable by existing design) and its `program_movement_id` (Pull-up) always stands.
- `movement.ramp_eligible` for Pull-up (movement_id=18) is `False` (confirmed via direct DB read) — the anchor-only `is_anchor=True` ramp-set behavior in `assembler.py::_build_exercise` never fires for this movement regardless, so nothing behavioral is lost by no longer passing `is_anchor=True` for it.
- `fallback.py::program_selections()` (cold-start / quiet-week path) iterates ALL `skeleton.adaptive_slots` unconditionally and defaults every slot with a non-None `program_movement_id` to itself — Pull-up will correctly appear here once it's an adaptive slot.
- `fallback.py::last_valid_selections()` (the "replay last completed session" fallback) filters to `slot.kind in ("giant", "knee")` only — a genuine, PRE-EXISTING gap that already silently drops ordinary `"accessory"`-kind slots from its output in that one fallback branch (not introduced by this spec; Pull-up will inherit the same pre-existing exposure as every other accessory slot already has today). **Do not fix this in this spec** — it's a separate, already-existing issue affecting all accessory slots, out of scope here. Note it in the commit message as a discovered-but-deferred follow-up.

## Edge cases
- D6 Weak Points is the ONLY day where an anchor's tier is `GIANT_SET` (confirmed: every other `GIANT_SET` tier — GS2/GS3 on D6, T2 GS/T3 GS/T4 GS elsewhere — uses only "free"/"semi" roles). This fix must NOT change behavior for any other day's anchors (all still route through the unchanged `tier.tier_kind != TierKind.GIANT_SET` branch).
- After this fix, D6 Weak Points has **zero** anchor movements (`sk.anchor_movement_ids == []`) — GS1's Pull-up is its only would-be anchor, and it's now folded into GS1's adaptive slots. This is the CORRECT new behavior, not a bug.
- Pull-up's exercise order within GS1 must stay first (seed `exercise_order=1`, vs. Dips=2, Hip Thrust=3) — `lay_skeleton()` already sorts `exercises` by `_effective_exercise_order(db, te)` before the anchor/adaptive branch, so this ordering is preserved automatically; verify it in the test rather than assuming.

## Dependencies
None.

## Verification
- Existing test `tests/test_generation_skeleton.py::test_d6_has_anchor` currently asserts `sk.anchor_movement_ids` is truthy for D6 — this assertion is now WRONG per the corrected design and must be rewritten (not just deleted) to assert the new correct behavior: `sk.anchor_movement_ids == []` for D6 Weak Points, AND a new assertion that Pull-up (movement_id=18) appears in `sk.adaptive_slots` with `is_giant_tier=True`, `group_key=="GS1"`, `kind=="accessory"`, `tier_role=="anchor"`. Rename the test to reflect the corrected behavior (e.g. `test_d6_gs1_anchor_folds_into_giant_tier`).
- New test mirroring `test_knee_slots_in_giant_tiers_remember_giant_tier`'s shape: confirm all three GS1 slots (`d6_g1a`, `d6_g1b`, `d6_g1c`) share `is_giant_tier=True` and `group_key=="GS1"`.
- New test: confirm Pull-up's slot has `kind=="accessory"` (NOT `"giant"`) — this is the specific assertion that guards against the candidate-menu/substitution regression described above.
- Full server suite green: `~/projects/IronLog-V2/.venv/bin/pytest -q` (current main baseline: 645 passing — expect the same count minus 1 rewritten test plus 2-3 new ones, net additions, zero unrelated regressions).
- Manual/live verification (not part of the merge gate, deploy-time only): after deploy, generate a fresh D6 session and confirm via direct DB read that GS1 produces exactly ONE `ExerciseGroup` row (type `GIANT_SET`, 3 exercises: Pull-up, Dips, Hip Thrust in that order), not two.
