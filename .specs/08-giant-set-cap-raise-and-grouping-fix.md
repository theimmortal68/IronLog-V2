# Spec 08: Raise giant-set concurrency cap to 4 + fix knee-modality grouping (combines/supersedes retracted spec 07)

## Objective
Raise the validator's `GIANT_SET_CONCURRENCY` cap from 3 to 4 (athlete directive, 2026-07-10: T3's 4 exercises are all small-muscle/easy-recovery accessories that don't need the same room-geometry margin as heavy compound work), then implement the grouping fix retracted spec 07 described — so D5's T3 GS tier (Poliquin Step-up, Reverse Nordic Assisted, Cable Tib Raise, Hyper Pro Calf Raise) assembles as ONE physical `GIANT_SET` group of 4, not one group-of-1 plus three standalone singles.

## Background
Spec 07 correctly diagnosed a real code defect (`slot.kind == "knee"` incorrectly excludes a slot from its `GIANT_SET` tier's shared group in `assemble()`) but its fix was retracted after breaking 8 tests: raising the giant-set exercise count to 4 tripped the validator's `_check_giant_set_concurrency` (`ironlog/engine/validator.py:182-198`), which hard-rejects any `GIANT_SET` group outside `1..=3` exercises ("room geometry" — `docs/06_generation_algorithm_spec.md:85`). The athlete has since confirmed the cap itself should rise to 4 for this case — this spec does both changes together so the grouping fix doesn't reject at the validator.

**Only T2/T3 GIANT tiers with knee_modality-tagged exercises are affected structurally** — confirmed live: D5's T2 GS has 3 real concurrent slots (its apparent 4th YAML entry is a meso-rotation alternate for the SAME slot, not a 4th concurrent exercise) and stays under the new cap either way; D5's T3 GS has 4 genuinely distinct concurrent slots and is the case this spec fixes. D1's T2 GS (Face-Up Incline Knee Raise, `knee_modality=None`) already groups correctly today and must keep doing so.

## The fix
1. **Raise the cap**: `_check_giant_set_concurrency` in `ironlog/engine/validator.py` — change `1 <= n <= 3` to `1 <= n <= 4` and update the violation message/docstring accordingly (`"expected 1-4"`). Update `docs/06_generation_algorithm_spec.md:85`'s "≤3 items usable at once" line to "≤4 items usable at once" — this is the current authoritative reference doc; the dated 2026-06-24 historical design-spec/plan docs (`docs/superpowers/specs/2026-06-24-validator-design.md`, `docs/superpowers/plans/2026-06-24-validator-impl.md`) are historical record and do NOT need retroactive edits.
2. **Fix the grouping** (unchanged from spec 07's design): add `SlotSpec.is_giant_tier: bool` (`ironlog/generation/skeleton.py`), set from `tier.tier_kind == TierKind.GIANT_SET` at skeleton construction (`lay_skeleton`), independent of `_slot_kind`'s existing `kind`/`knee_modality` logic. Change `assemble()`'s grouping condition (`ironlog/generation/assembler.py`, currently `if slot.kind == "giant":`) to use `slot.is_giant_tier` instead. Do NOT change `_slot_kind` or the `kind` field — it has a separate, legitimate consumer (`context.py:135`'s candidate-menu filtering) that must be unaffected.

## File targets
- Modify: `ironlog/engine/validator.py` — `_check_giant_set_concurrency` (cap 3→4, message text).
- Modify: `docs/06_generation_algorithm_spec.md` — the "≤3 items usable at once" line (line ~85).
- Modify: `ironlog/generation/skeleton.py` — `SlotSpec` (add `is_giant_tier: bool`), `lay_skeleton` (set it).
- Modify: `ironlog/generation/assembler.py` — `assemble()`'s grouping condition.
- Modify/add test files: `tests/test_generation_assembler.py`, `tests/test_generation_context.py`, `tests/test_generation_skeleton.py` (or whichever the implementer finds is the existing convention), plus the validator's own test file for the cap-raise (check `tests/` for the `_check_giant_set_concurrency`/`GIANT_SET_CONCURRENCY` test coverage and update the existing "4 exercises rejects" test to "5 exercises rejects" / "4 exercises passes", matching the new boundary).

## Edge cases
- **The validator's existing tests almost certainly assert "4 exercises → REJECT"** (matching the old 3-max boundary) — these must be updated to assert "5 exercises → REJECT" / "4 exercises → PASS", not just left broken. Grep `tests/` for `GIANT_SET_CONCURRENCY` and `_check_giant_set_concurrency` before writing new tests.
- **D1's Face-Up Incline Knee Raise regression**: must remain grouped in its 3-exercise T2 GS `GIANT_SET` (unaffected by the cap raise, but must not regress from the grouping-logic change).
- **D5 T2 GS**: must resolve to exactly 3 exercises in its `GIANT_SET` group (BSS + whichever Scout Reverse Hyper meso-variant + Nordic Curl) — the cap raise doesn't change this count, but the grouping fix must correctly include Nordic Curl (currently excluded by the `kind=="knee"` bug) alongside the other two.
- **D5 T3 GS**: must resolve to exactly 4 exercises in ONE `GIANT_SET` group (Poliquin, Reverse Nordic, Cable Tib Raise, Hyper Pro Calf Raise) — this is the actual fix target.
- **No other current tier has 4+ concurrent exercises** (verify this against the seed source before assuming the cap raise is otherwise inert) — if another tier is discovered at exactly 4, confirm it's likewise intended to pass under the new cap; if any tier is discovered above 4, that's out of scope for this spec and should be flagged, not silently accommodated.

## Dependencies
None — supersedes the retracted spec 07 (which is not merged; its worktree was abandoned without a commit). No schema/API-surface change (no HUMAN GATE required).

## Verification
- Validator test: 4-exercise `GIANT_SET` group passes; 5-exercise `GIANT_SET` group still rejects (`GIANT_SET_CONCURRENCY`).
- D1 regression test: T2 GS group still contains exactly 3 exercises (Pendlay Row, Incline DB Press, Face-Up Incline Knee Raise) in one `GIANT_SET`.
- D5 fix test: T2 GS resolves to exactly 3 exercises in one `GIANT_SET`; T3 GS resolves to exactly 4 exercises in one `GIANT_SET`.
- `build_candidate_menu` regression test: a `knee_modality`-tagged slot's candidate menu is still restricted to matching-modality movements (unaffected by this change) — per spec 07's original edge-case note, still applies here.
- Full suite green: `cd <worktree> && ~/projects/IronLog-V2/.venv/bin/python -m pytest -q`.
- Manual: `POST /generate` for D5 Lower B and D1 Upper Push; confirm group composition matches the corrected expectation (D5: T1, T1b, T2 GS×3, T3 GS×4 — 4 groups total, not 7).
