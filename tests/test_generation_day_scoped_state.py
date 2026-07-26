"""test_generation_day_scoped_state.py — Task 5: day-scope MovementState load
in resolve_context so movements shared across days (Hip Thrust D2/D5/D6,
Reverse Hyper, Nordic, Cable Tib) don't collide to one last-wins row.

Anchors on seed_movement_baselines (Task 4), which seeds per-day
(movement_id, day_id) MovementState rows for HT: D2 Lower A=205,
D5 Lower B=205, D6 Weak Points=155 (see BASELINES d2_t1b/d5_t1b/d6_g1c
in ironlog/generation/baseline_seed.py). D2 and D5 share the same
baseline value (2026-07-06 athlete directive: D2 raised 180->205 to
match D5) — the day-scoping guarantee this test proves is that D2 and D5
resolve as INDEPENDENT rows that happen to prescribe the same seeded setup,
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
from datetime import date

from sqlmodel import select

from ironlog.engine.band_composite import Band
from ironlog.generation.assembler import assemble
from ironlog.generation.baseline_seed import seed_movement_baselines
from ironlog.generation.context import resolve_context
from ironlog.generation.fallback import program_selections
from ironlog.generation.skeleton import lay_skeleton
from ironlog.models.enums import (
    FeedbackTap, GroupType, Objective, Scheme, SessionStatus, SetRole,
)
from ironlog.models.library import BandPair, Movement, MovementState
from ironlog.models.program import TierExercise
from ironlog.models.session import (
    ExerciseGroup, PlannedExercise, PlannedSet, Session as IronSession, SetLog,
)
from ironlog.persistence.run_analysis import run_analysis

WEEK_KEYER = lambda d: (d.isocalendar()[0], d.isocalendar()[1])  # noqa: E731


def _stage_clean_ht_advance(db, movement_id, day_role, plates, config):
    session = IronSession(
        date=date(2026, 7, 20),
        day_role=day_role,
        phase="CUT",
        status=SessionStatus.COMPLETED,
    )
    db.add(session)
    db.flush()

    group = ExerciseGroup(
        session_id=session.id,
        order_index=0,
        group_type=GroupType.STRAIGHT,
        label="T1",
    )
    db.add(group)
    db.flush()

    exercise = PlannedExercise(
        group_id=group.id,
        movement_id=movement_id,
        order_index=0,
        scheme=Scheme.STRAIGHT,
        objective=Objective.PROGRESS,
    )
    db.add(exercise)
    db.flush()

    for i in range(3):
        planned_set = PlannedSet(
            planned_exercise_id=exercise.id,
            set_index=i,
            set_role=SetRole.WORKING,
            target_reps_low=8,
            target_reps_high=8,
            target_rpe=8.0,
            target_plates=plates,
            band_config=list(config),
        )
        db.add(planned_set)
        db.flush()
        db.add(SetLog(
            planned_set_id=planned_set.id,
            session_id=session.id,
            movement_id=movement_id,
            set_index=i,
            actual_reps=8,
            feedback_tap=FeedbackTap.ON_TARGET,
            actual_plates=plates,
            is_warmup=False,
        ))
    db.commit()
    run_analysis(session.id, db, WEEK_KEYER)


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
    # peak_lb=45]). assemble() now PRESCRIBES THE CURRENT (seeded) setup on the
    # planned sets — no auto-advance at prescription (2026-07-06 athlete
    # directive: Week 1 shows exactly the seeded setup). So the prescribed plates
    # equal the seeded baselines verbatim: D2=205, D5=205, D6=155, all still on
    # Orange. A clean analyzed session for the same day STAGES the next setup
    # for commit in prospective_ht_setups (the ht_next_setup values), checked
    # separately below.
    #
    # The prospective (next) setup is NOT a flat +5: ht_next_setup first tries
    # raising plates by one plate_step (5 lb) within the current band config, but
    # only if the resulting bottom-clamp (plates + band rest) stays <= 225;
    # otherwise it searches band inventory for the smallest peak strictly above
    # the current peak (tiebreak: fewest bands). D6 (155) stays on Orange at +5
    # (160: 160+18=178 <= 225). D2 and D5 (both 205) would need 210+18=228 > 225
    # on Orange, so both swap to band #1 Red [id=2, bottom_lb=36, peak_lb=90] at
    # 165 plates (165+90=255, the smallest peak exceeding the prior 205+45=250)
    # — confirmed by direct execution, not guessed. D2 and D5 share the same
    # seeded baseline (per the 2026-07-06 athlete directive raising D2 to match
    # D5); the day-scoping guarantee this test proves is that they are
    # independently-resolved rows, not that every day's number differs. Before
    # the original (Task 5) fix, all three days collapsed to one last-inserted
    # row's value regardless of which day was actually being generated.
    ht_movement = gen_db.exec(select(Movement).where(Movement.name == "Hip Thrust [HIP_THRUST]")).one()
    inventory = [Band(bp.id, bp.bottom_lb, bp.peak_lb, bp.usable)
                 for bp in gen_db.exec(select(BandPair)).all()]
    for role, cur_plates, next_plates in [
        ("D2 Lower A", 205, 165),
        ("D5 Lower B", 205, 165),
        # 2026-07-26 (spec 52): D6's HT is now a pure derived value (80% of the unified D2/D5 group)
        # and never earns an independent advance -- this test's expectation was updated accordingly, not weakened.
        ("D6 Weak Points", 155, 155),
    ]:
        _stage_clean_ht_advance(gen_db, ht_movement.id, role, cur_plates, [1])
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
        # Prescribed = CURRENT seeded setup (prescribe-current).
        assert all(ps.target_plates == cur_plates for ps in ht_sets), (
            f"{role} prescribed {cur_plates}, got {[ps.target_plates for ps in ht_sets]}"
        )
        # Staged for commit = NEXT setup (advancement happens at commit).
        staged_plates, _ = assembled.prospective_ht_setups[ht_movement.id]
        assert staged_plates == next_plates, (
            f"{role} staged-next {next_plates}, got {staged_plates}"
        )
