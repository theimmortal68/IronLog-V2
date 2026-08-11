# Task 2 (D2 — Lower Squat + new core tier) — NEEDS_CONTEXT

Status: **NEEDS_CONTEXT** (blocked before any code was written — no commits made, worktree untouched beyond this report file)

## What was done

No production code changes were made. All work so far was verification/cross-referencing, per the task's own instruction to cross-check the brief against `docs/program/source/2026-08-10-maintenance-block-seed-data-FINAL.md`'s D2 section *before* writing any code, and to stop rather than guess on a genuine discrepancy.

Read in full:
- `/home/jstout/projects/IronLog-V2-wt-stab-d2/.superpowers/sdd/task-2-brief.md`
- `docs/program/source/2026-08-10-maintenance-block-seed-data-FINAL.md` — D2 section (lines 207-353), "Weekly Volume Check" (35-53), "Key Nordic Curl Update — Ares Weighted Assist" (848-882), "Progression Priorities Wk 2" (882-922)
- `docs/superpowers/plans/2026-08-10-stab-maintenance-block-redesign.md` — Task 2 section (272-onward), confirmed byte-identical in substance to `task-2-brief.md`
- Current `_seed_d2` in `ironlog/generation/program_seed.py` (lines 503-538), the current `d2:` block in `docs/program/phase1-seed-source.yaml` (34-45), the current D2 entries in `PROGRAM_TO_LIBRARY`, `rule_wiring.YAML_M_TO_LIBRARY`, `test_program_seed_yaml_parity.YAML_M_TO_LIBRARY`, `baseline_seed.BASELINES`, `test_golive_phase1.EXPECTED_NEEDS_CAL`
- Task 1's actual merged diff (`git show d36890d`, `16d6a3f`) as the style/convention template
- `ironlog/generation/skeleton.py`, `ironlog/generation/context.py` (`_PATTERN_LIFT_CATEGORIES`, `KNEE_TARGETS`), `ironlog/engine/advance.py` (`_rep_ladder`), `ironlog/models/library.py` (`Movement.rep_ladder`)

## Discrepancies found, and how each was resolved (or not)

### 1. Tier order for the new T4 straight tier — RESOLVED, not blocking
The brief's Step 3 body says "tier_order 5" but its own closing sentence says "Tier orders renumber sequentially (T1=1, T2 GS=2 [T1b gone], T3 GS=3, T4=4)". These conflict. Resolved in favor of the explicit, later, more specific statement: **tier_order=4**. The "tier_order 5" mention reads as a drafting artifact not updated when the renumbering note was added. This is purely an internal ordering field (`skeleton.py` sorts tiers by `Tier.tier_order`); it doesn't affect any athlete-facing weight/rep value. Confirmed via advisor consultation — treat as resolved, document, don't escalate.

### 2. D2 T3 GS rest_seconds: 75 (current code) vs 60 (FINAL doc) — RESOLVED, not blocking, but real fallout
The FINAL doc states "### T3 GS — 3 items, **60s rest**, 3 rounds" for D2 (line 281). Current `_seed_d2` has `rest_seconds=75` for T3 GS (unrelated to any change the brief calls out). The brief is silent on this field entirely. This is corroborated by an independent primary source: **D5's T3 GS already uses `rest_seconds=60`** in the live codebase (`program_seed.py` line ~648), and the FINAL doc's D5 T3 GS also states 60s (line 616) — i.e., 60s is the actual, already-implemented target convention for this specific tier shape in this program, and D2's current 75s is pre-existing staleness this task is meant to reconcile away (same category of miss as Task 1's missed T4 GS tier). **Resolution: change D2 T3 GS rest_seconds 75 → 60** as part of this task's rewrite.
- Mechanical consequence not mentioned by the brief: the yaml `d2:` block's `T3_GIANT` line (`rest: 75`) must also change to `rest: 60` in the same commit, or `tests/test_program_seed_yaml_parity.py` (which asserts tier-level `rest_seconds` parity against the yaml) will fail.

### 3. Belt Squat's `rep_ladder`/`rep_target`/`ceiling` yaml fields under the new 4-6 rep range — CHECKED, confirmed inert, not blocking
The current yaml line for `belt_squat` carries `rep_target: 8, rep_ladder: [8,10,12,15], ceiling: true`, built for the old 6-8 rep range. Traced whether these yaml keys are live engine inputs: `rule_wiring.py`'s `_iter_yaml_rules()` only reads `ex["m"]` and `ex.get("rule")` from each yaml exercise entry — `rep_target`, `rep_ladder`, and `ceiling` are never read by any parser in the codebase (confirmed via grep across `ironlog/` and `scripts/`). The only live `rep_ladder` field is `Movement.rep_ladder` (a JSON column read by `engine/advance.py::_rep_ladder`), and **no `seed.py` movement dict populates it for Belt Squat or any other movement** — it is always `None`/`[]` from a fresh seed today, for every movement, including Belt Squat, pre-existing this task. Conclusion: these three yaml keys are pure documentation/comments, not live inputs. Leaving them unchanged (still `[8,10,12,15]`/`rep_target: 8`) is correct — they don't need to move to match 4-6, and fixing this pre-existing dead-metadata/engine gap is out of this task's scope.

### 4. Knee-modality wiring for the two new lower-body T2/T3 movements — **BLOCKING, this is the NEEDS_CONTEXT**

The brief's Step 1 `dict(...)` literals for `Matrix Machine Sissy Squat` and `Nordic Curl Max [Ares]` do **not** set `knee_modality` on the Movement, and Step 3 gives no `knee_modality=` kwarg for either `TierExercise` either. Taken completely literally, this produces a real, material regression:

- **Current D2** has 3 knee-tagged `TierExercise` slots: `d2_t3a` (ATG Split Squat, KOT), `d2_t3b` (Cable Tibialis Raise, TIB), `d2_t3c` (Reverse Nordic, **KOT**).
- **Brief's literal instructions** drop `d2_t3c` (Reverse Nordic) with no replacement, and add no knee_modality to either new movement → D2 would end up with only **2** knee-tagged slots (KOT + TIB), and **zero** NORDIC- or SISSY-tagged slots anywhere in D2.
- This directly collides with `ironlog/generation/context.py`'s `KNEE_TARGETS = {"NORDIC": 2, "TIB": 2, "KOT": 2, "SISSY": 1}` (weekly, program-wide targets). Traced the current whole-program wiring: **exactly one** `TierExercise` in the entire program currently carries `knee_modality=NORDIC` — `d5_t2c` ("Assisted Nordic (eccentric)"), against a target of 2/week — and **zero** carry `SISSY` anywhere, ever.
- The two new movements are exactly the ones that could plausibly close these gaps: `Nordic Curl Max [Ares]` is literally a Nordic curl variant (assist ladder, same shape as `Nordic Curl [GHR]`, which *does* carry `knee_modality=NORDIC` at the Movement level), and the FINAL doc gives `Matrix Machine Sissy Squat` an explicit `knee_health_note: sissy_squat_trains_vmo_deep_knee_flexion` (line 252) — the only narrative "knee" annotation given to any D2 movement in the whole FINAL doc — strongly suggesting SISSY relevance, paralleling the existing (but currently unwired-anywhere) `Sissy Squat` movement that already carries `knee_modality=SISSY`.
- I also read the two FINAL-doc sections most likely to settle this explicitly — "Weekly Volume Check" (lines 35-53) and "Key Nordic Curl Update — Ares Weighted Assist" (848-882) — per advisor's suggested decision rule. **Neither states a weekly Nordic/knee-modality frequency target or says anything about `KneeModality` tagging.** They describe real-world exercise/volume content, not the internal engine's knee-modality bookkeeping (that's a `docs/06 §4` construct this FINAL doc never references). So the FINAL doc does not settle the question either way.
- **This has a real compounding effect into Task 4 (D5)**: `Nordic Curl Max [Ares]` "is referenced again by Task 4 (D5) — same Movement row, shared identity" per this brief's own Interfaces section. If Task 2 wires it untagged and Task 4 also wires it untagged (because Task 4's brief likely inherits the same silence), the *entire program* could end up with zero NORDIC-tagged slots against a weekly target of 2 — with each task's implementer independently relaxing/adjusting whatever test currently encodes the count (`tests/test_generation_skeleton.py::test_d2_has_knee_slots_in_adaptive` asserts `len(knee_slots) >= 3` for D2 specifically), each in isolation, no single point catching that the *combined* result silently drops a design invariant nobody explicitly decided to drop.

**The actual question for the plan owner:** should `nordic_curl_max_d2`'s `TierExercise` (and/or the D5 copy in Task 4) carry `knee_modality=KneeModality.NORDIC`, and should `matrix_machine_sissy_squat`'s `TierExercise` carry `knee_modality=KneeModality.SISSY`? (TierExercise-level only — not proposing to add `knee_modality` to the Movement dicts themselves, which the brief gives verbatim and I have not touched.) Or is dropping D2's knee-slot count from 3 to 2 (and leaving NORDIC/SISSY at their current under-target counts) the intended, accepted maintenance-block tradeoff, in which case `test_d2_has_knee_slots_in_adaptive`'s threshold should just be relaxed to `>= 2` with a comment explaining why?

I did not guess at this because: it's a real design decision the brief is silent on, not resolvable from either the brief or the FINAL doc, and picking wrong could either (a) silently under-serve a stated engine invariant (KNEE_TARGETS) for a live athlete's actual weekly movement variety, or (b) invent tagging behavior not authorized by the brief's literal Step 1/3 text. Both are things this task's instructions specifically told me not to do without asking.

## Additional test fallout discovered (not blocking, but scoped larger than the brief's file list — flagging for whoever resumes)

Beyond the brief's declared step 7-9 test updates, grepping for every D2-specific identifier being removed (`d2_t1b`, `d2_t2a`, `d2_t2b`, `d2_t3c`, `hip_thrust_d2`, `leg_curl_d2`, `scout_reverse_hyper_bilateral_d2`, `reverse_nordic_assisted_d2`) turned up:

- **`tests/test_generation_day_scoped_state.py`** — `test_ht_load_is_day_scoped` is built entirely around a **3-way** day-scoped independence check for Hip Thrust across D2/D5/D6 (`te["d2_t1b"]`, asserts `len(ht_states) == 3`, asserts exact per-day plate values including D2's 205). Since D2's Hip Thrust tier is being dropped entirely (per the brief's explicit "Removed: Hip Thrust (D2 T1b — the whole tier is dropped, not just the movement)"), this test's premise breaks and needs restructuring to a 2-way (D5/D6) independence check, not just a value update. This is legitimate, brief-authorized fallout (not itself ambiguous) but is more invasive than a field tweak — flagging so it isn't missed.
- **`tests/test_ht_generate_banded.py::test_week1_prescribes_seeded_current_setup`** — also asserts D2's `d2_t1b` HT baseline (205, Orange) through the real `generate_session` path; same fallout category, needs D2 dropped from its per-day loop.
- **`tests/test_generation_repair.py`** — references `d2_t2a` in a comment ("Pick the first knee slot (NORDIC — d2_t2a 'Assisted Nordic')") but the comment is already stale relative to current `main` (current `d2_t2a` is Lying Leg Curl, not Assisted Nordic) and the test logic picks the first knee slot generically, not by name — likely needs no code change, only comment cleanup. Low risk either way.
- **`tests/test_note_resolver.py`** — uses `slot_id="d2_t2a"` but on a synthetic fixture movement (`"shared_row"`), not tied to the real seeded program — checked, does not appear to need any change.

## What's needed to unblock

A decision on the knee_modality question in item 4 above. Once that's settled, implementation proceeds exactly per the brief otherwise (Steps 1-2 have no open questions; Step 3's tier_order and T3 rest are resolved per items 1-2 above; Steps 4-9 follow directly with the additional fallout noted above folded in).

## Files touched by this report

- `/home/jstout/projects/IronLog-V2-wt-stab-d2/.superpowers/sdd/task-2-report.md` (this file) — new
- No other files were modified. `git status` in the worktree shows only this new untracked file.
