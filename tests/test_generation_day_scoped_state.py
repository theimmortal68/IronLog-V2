"""test_generation_day_scoped_state.py — Task 5: day-scope MovementState load
in resolve_context so movements shared across days (Hip Thrust D2/D5/D6,
Reverse Hyper, Nordic, Cable Tib) don't collide to one last-wins row.

Anchors on seed_movement_baselines (Task 4), which seeds per-day
(movement_id, day_id) MovementState rows for HT: D2 Lower A=205,
D5 Lower B=205, D6 Weak Points=155 (see BASELINES d2_t1b/d5_t1b/d6_g1c
in ironlog/generation/baseline_seed.py). D2 and D5 share the same
baseline value (2026-07-06 athlete directive: D2 raised 180->205 to
match D5) — the day-scoping guarantee this test proves is that D2 and D5
resolve as INDEPENDENT rows that happen to compute the same next-setup,
not that every day's value is numerically distinct.

Uses lay_skeleton -> resolve_context -> program_selections -> assemble
directly (the same pattern as test_ht_write_boundary.py), NOT the full
generate_session() -> validate() path: build_validation_context()
(repair.py) leaves ValidationContext.band_bottom_lb at its empty-dict
default ("HT-safety evaluation is handled separately" per its docstring),
so ANY assembled HT set with a non-empty band_config fails validate()'s
HT_BAND_NOT_REGISTERED check today, independent of day-scoping and out of
this task's scope. Going through assemble() directly exercises exactly the
code this task touches (ctx.movement_states feeding assembler._build_exercise
at assembler.py:209) without tripping that unrelated, pre-existing gap.

NO from __future__ import annotations (project-wide constraint).
gen_db fixture auto-discovered from conftest.py.
"""
from sqlmodel import select

from ironlog.generation.assembler import assemble
from ironlog.generation.baseline_seed import seed_movement_baselines
from ironlog.generation.context import resolve_context
from ironlog.generation.fallback import program_selections
from ironlog.generation.skeleton import lay_skeleton
from ironlog.models.library import MovementState
from ironlog.models.program import TierExercise

WEEK_KEYER = lambda d: (d.isocalendar()[0], d.isocalendar()[1])  # noqa: E731


def test_ht_load_is_day_scoped(gen_db):
    seed_movement_baselines(gen_db)

    # Independence check FIRST, at the MovementState-row level: d2_t1b/d5_t1b/
    # d6_g1c all key off the SAME underlying HT movement, so if day-scoping
    # regressed to a single last-wins row we'd see 1 row, not 3. This stays
    # meaningful even though D2 and D5 now share the same seeded value (205)
    # and therefore compute the same next-setup below (165) — the guarantee is
    # independent rows, not distinct numbers.
    te = {t.slot_id: t for t in gen_db.exec(select(TierExercise)).all()}
    ht_movement_id = te["d2_t1b"].movement_id
    assert te["d5_t1b"].movement_id == ht_movement_id
    assert te["d6_g1c"].movement_id == ht_movement_id
    ht_states = gen_db.exec(
        select(MovementState).where(MovementState.movement_id == ht_movement_id)
    ).all()
    assert len(ht_states) == 3
    assert len({s.day_id for s in ht_states}) == 3
    assert {s.day_id: s.ht_plates for s in ht_states} == {
        "D2 Lower A": 205, "D5 Lower B": 205, "D6 Weak Points": 155,
    }

    # HT baselines by day: D2=205, D5=205, D6=155 (seed_movement_baselines,
    # BASELINES d2_t1b/d5_t1b/d6_g1c, all on band #0 Orange [id=1, bottom_lb=18,
    # peak_lb=45]). assemble() prescribes the NEXT HT setup via ht_next_setup,
    # which is NOT a flat +5 on every baseline: it first tries raising plates by
    # one plate_step (5 lb) within the current band config, but only if the
    # resulting bottom-clamp (plates + band rest) stays <= 220; otherwise it
    # searches band inventory for the smallest peak strictly above the current
    # peak (tiebreak: fewest bands). D6 (155) stays on Orange at +5 (160:
    # 160+18=178 <= 220). D2 and D5 (both 205) would need 210+18=228 > 220 on
    # Orange, so both swap to band #1 Red [id=2, bottom_lb=36, peak_lb=90] at
    # 165 plates (165+90=255, the smallest peak exceeding the prior 205+45=250)
    # — confirmed by direct execution, not guessed. D2 and D5 now land on the
    # SAME value (165) because they share the same seeded baseline (per the
    # 2026-07-06 athlete directive raising D2 to match D5); the day-scoping
    # guarantee this test proves is that they are independently-resolved rows
    # that happen to compute equal, not that every day's number differs. Before
    # the original (Task 5) fix, all three days collapsed to one last-inserted
    # row's value regardless of which day was actually being generated.
    for role, plates in [("D2 Lower A", 165), ("D5 Lower B", 165), ("D6 Weak Points", 160)]:
        sk = lay_skeleton(role, gen_db)
        ctx = resolve_context(role, sk, gen_db, WEEK_KEYER)
        sel = program_selections(sk)
        assembled = assemble(sel, sk, ctx, gen_db)
        sess = assembled.session
        ht_sets = [
            ps
            for g in sess.groups
            for ex in g.exercises
            for ps in ex.planned_sets
            if ps.target_plates is not None
        ]
        assert ht_sets, f"{role}: no HT set with plates"
        assert all(ps.target_plates == plates for ps in ht_sets), (
            f"{role} expected {plates}, got {[ps.target_plates for ps in ht_sets]}"
        )
