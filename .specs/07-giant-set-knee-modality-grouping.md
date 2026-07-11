**RETRACTED 2026-07-10 — diagnosis was wrong, do not implement.** Dispatched and generated; the resulting diff correctly implemented what this spec asked for, but broke 8 pre-existing tests via the validator's `GIANT_SET_CONCURRENCY` rule (max 3 exercises per giant set, "room geometry" — see `docs/06_generation_algorithm_spec.md:85`, `docs/superpowers/specs/2026-06-24-validator-design.md:197-199`). D5's T3 tier genuinely seeds 4 concurrent exercises (poliquin_step_up, reverse_nordic_assisted, cable_tib_raise_d5, hyper_pro_calf_raise — confirmed via `docs/program/phase1-seed-source.yaml:63-67`, no meso-rotation link between any pair, unlike T2's apparent 4th entry which IS a meso alternate for an existing slot). Forcing all 4 into one physical `GIANT_SET` group — what this spec asked for — is exactly what the validator exists to reject. The pre-existing "fragmentation" this spec set out to fix may be an accidental-but-load-bearing workaround for a genuine program-design inconsistency (a 4-exercise tier that exceeds the room's 3-station limit), not a bug. See the 2026-07-10 21:XX session's completion report for the corrected understanding and the question posed back to the user. Worktree abandoned (`abandoned/task-07-giant-set-grouping`), not merged.

---

# Spec 07: Fix giant-set fragmentation for knee-modality-tagged exercises

## Objective
Fix `assemble()` so that adaptive slots within a `GIANT_SET` tier stay grouped together in one physical giant-set block, even when one or more of those slots also carry a `knee_modality` value (used elsewhere purely for LLM candidate-menu filtering, not for physical grouping).

## Background — confirmed root cause (2026-07-10)
`_slot_kind` (`ironlog/generation/skeleton.py:191-197`) computes a slot's `kind` with `knee_modality is not None` taking precedence over `tier.tier_kind == GIANT_SET`. `assemble()` (`ironlog/generation/assembler.py:508`) then uses `slot.kind == "giant"` as its ONLY criterion for merging a slot into its tier's shared `GIANT_SET` `ExerciseGroup` — any slot whose `kind` came back `"knee"` instead falls to the `else` branch and gets its own standalone `STRAIGHT` group.

This is a real, DTO-visible bug. Confirmed live on D5 Lower B (T2 GS: Bulgarian Split Squat + Reverse Hyper correctly grouped together, but Nordic Curl — `knee_modality="NORDIC"` — split into its own group; T3 GS: only Hyper Pro Calf Raise correctly `giant`-classified, Poliquin/Reverse Nordic/Cable Tib Raise — all carrying `knee_modality` values `KOT`/`KOT`/`TIB` — each fragmented into their own standalone group). Confirmed this does NOT affect D1 (Face-Up Incline Knee Raise there has `knee_modality=None`, so it never triggers the bug) — the defect is specific to any GIANT_SET tier containing 2+ exercises where at least one has a non-null `knee_modality`, which is exactly D5's T2/T3 structure.

`kind == "knee"` has exactly one other real use, and it is unrelated to grouping: `build_candidate_menu` (`context.py:135`) uses it to restrict the LLM's alternative-movement menu to movements sharing the same `knee_modality` value. `resolve_context` (`context.py:337`) already builds candidate menus for slots with `kind in ("giant", "knee")` — treating them as two DIFFERENT-BUT-COEXISTING concerns, not mutually exclusive ones. The bug is that `assemble()` collapsed this into a false either/or.

## The fix
`kind`/`_slot_kind` and its one legitimate consumer (`context.py:135`'s menu-filtering) must NOT change — only `assemble()`'s grouping decision needs a source of truth independent of `kind`.

1. Add a new field to `SlotSpec` (`ironlog/generation/skeleton.py`, wherever the dataclass/model is defined): `is_giant_tier: bool`, set directly from `tier.tier_kind == TierKind.GIANT_SET` at skeleton-construction time (`lay_skeleton`, alongside where `kind=_slot_kind(te, tier)` is currently set) — independent of `knee_modality`.
2. In `assemble()` (assembler.py:508), change the grouping condition from `if slot.kind == "giant":` to `if slot.is_giant_tier:` (or equivalent) so any slot belonging to a GIANT_SET tier joins its shared group, regardless of whether it ALSO carries a `knee_modality` value for menu-filtering purposes elsewhere.
3. `slot.kind` itself is untouched — it continues to return `"knee"` whenever `knee_modality` is set, exactly as today, so `context.py:135`'s menu-filtering behavior is unaffected.

## File targets
- Modify: `ironlog/generation/skeleton.py` — `SlotSpec` (add `is_giant_tier: bool` field), `lay_skeleton` (set it when constructing each `SlotSpec`, alongside the existing `kind=_slot_kind(te, tier)` line).
- Modify: `ironlog/generation/assembler.py` — `assemble()`'s giant/straight branch condition (line ~508).
- New test additions to whatever test file already covers giant-set assembly/skeleton construction (check `tests/` for the existing convention, e.g. `test_generation_fallback.py`'s sibling files or a dedicated skeleton/assembler test file — mirror it).

## Edge cases
- **A slot with `knee_modality` set but NOT in a GIANT_SET tier** (should not exist in current seed data, but guard for it): `is_giant_tier=False`, falls to the existing `else` branch (own `STRAIGHT` group) — unchanged behavior, this is the historical Face-Up-Knee-Raise-as-solo-slot case this bug's fix must not break if it ever recurs.
- **D1's Face-Up Incline Knee Raise** (knee_modality=None, already correctly grouped today): must remain grouped — this is the regression-guard case; a test asserting D1's T2 GS group still contains exactly 3 exercises (Pendlay Row, Incline DB Press, Face-Up Incline Knee Raise) in one `GIANT_SET` group is mandatory.
- **D5's T2/T3 GS tiers** (the broken case): after the fix, T2 GS must be ONE `GIANT_SET` group with exactly 3 exercises (Bulgarian Split Squat, Reverse Hyper, Nordic Curl); T3 GS must be ONE `GIANT_SET` group with exactly 4 exercises (Poliquin Step-up, Reverse Nordic, Cable Tib Raise, Hyper Pro Calf Raise).
- **`context.py:135`'s menu-filtering must be unaffected** — a regression test confirming `build_candidate_menu` for a `knee_modality`-tagged slot still restricts to matching-modality movements exactly as before is worth including, even though this spec doesn't touch that function, precisely because it's the thing this fix must not accidentally change.
- **This bug likely affects other days too** — check D2 (`assisted_nordic_curl_d2`, `knee_modality` likely set) and D5 was already confirmed broken; a broader regression sweep across all 5 training days' `/generate` output (assert giant-set group exercise counts match the seed source yaml's per-tier exercise counts) is worth including as a general safety net, not just the two specific days named above.

## Dependencies
None — standalone fix, no schema/API-surface change (no HUMAN GATE required).

## Verification
- New tests per the edge cases above: D1 regression (still grouped), D5 T2/T3 (now correctly grouped, exact exercise counts), a `build_candidate_menu` regression confirming `kind=="knee"` menu-filtering is unaffected.
- Broader sweep test: for every training day (D1, D2, D4, D5, D6), assert each `GIANT_SET`-tier group's exercise count matches the count of `ex:` entries under that tier in `docs/program/phase1-seed-source.yaml` (or the seeded `TierExercise` count for that tier, whichever is more direct to assert against in a test).
- Full suite green: `cd <worktree> && ~/projects/IronLog-V2/.venv/bin/python -m pytest -q`.
- Manual: `POST /generate` for D5 Lower B and D1 Upper Push; confirm group counts/composition match the corrected expectation.
