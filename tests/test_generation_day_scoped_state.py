"""test_generation_day_scoped_state.py — Task 5: day-scope MovementState load
in resolve_context so movements shared across days (Hip Thrust -- D5/D6 as
of 2026-08-11, D2's T1b tier was removed entirely in the STAB maintenance-
block redesign's Task 2 -- plus Reverse Hyper, Nordic, Cable Tib) don't
collide to one last-wins row.

2026-08-12 (STAB maintenance-block redesign, Task 4): D5's Hip Thrust T1b
tier is ALSO removed entirely now (2nd of 3 Hip Thrust removals across this
redesign, D6 still to come) -- D5 no longer has a real Hip Thrust
TierExercise or seed_movement_baselines-populated MovementState row (the old
d5_t1b BASELINES entry is gone). This test's day-scoping proof needs a
SECOND real independent HT track alongside D6's real d6_g1c to prove two
distinct (movement_id, day_id) rows resolve independently through
generation, so it attaches a synthetic, test-only, PLAIN Hip-Thrust
TierExercise onto D5's real ProgramDay (mirrors test_ht_composite_wiring.py's
`_synthetic_plain_ht_slot` / test_ht_unification.py's `_synthetic_ht_slot`
pattern) and manually seeds its MovementState at the same 205+Orange values
the old real d5_t1b baseline used, instead of relying on
seed_movement_baselines to populate D5's row.

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

from tests.test_ht_composite_wiring import _synthetic_plain_ht_slot
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

    orange = gen_db.exec(select(BandPair).where(BandPair.label == "#0 Orange")).one()
    ht_mv_for_synthetic = gen_db.exec(
        select(Movement).where(Movement.name == "Hip Thrust [HIP_THRUST]")
    ).one()
    # D5's Hip Thrust T1b tier was removed entirely (Task 4, 2026-08-12) --
    # attach a synthetic, test-only PLAIN slot on D5's real ProgramDay so
    # this test still has two independent real (movement_id, day_id) rows
    # to prove day-scoping against (see module docstring).
    _synthetic_plain_ht_slot(gen_db, "D5 Lower B", ht_mv_for_synthetic.id, "test_dayscope_d5_ht")
    gen_db.add(MovementState(
        movement_id=ht_mv_for_synthetic.id, day_id="D5 Lower B",
        ht_plates=205.0, ht_band_config=[orange.id],
    ))
    gen_db.commit()

    # Independence check FIRST, at the MovementState-row level: the
    # synthetic D5 slot / d6_g1c key off the SAME underlying HT movement, so
    # if day-scoping regressed to a single last-wins row we'd see 1 row, not 2.
    te = {t.slot_id: t for t in gen_db.exec(select(TierExercise)).all()}
    ht_movement_id = te["test_dayscope_d5_ht"].movement_id
    assert te["d6_g1c"].movement_id == ht_movement_id
    ht_states = gen_db.exec(
        select(MovementState).where(MovementState.movement_id == ht_movement_id)
    ).all()
    assert len(ht_states) == 2
    assert len({s.day_id for s in ht_states}) == 2
    assert {s.day_id: s.ht_plates for s in ht_states} == {
        "D5 Lower B": 205, "D6 Weak Points": 155,
    }

    # HT baselines by day: D5=205, D6=155 (seed_movement_baselines,
    # BASELINES d5_t1b/d6_g1c, both on band #0 Orange [id=1, bottom_lb=18,
    # peak_lb=45]). assemble() now PRESCRIBES THE CURRENT (seeded) setup on the
    # planned sets — no auto-advance at prescription (2026-07-06 athlete
    # directive: Week 1 shows exactly the seeded setup). So the prescribed plates
    # equal the seeded baselines verbatim: D5=205, D6=155, both still on
    # Orange. A clean analyzed session for the same day STAGES the next setup
    # for commit in prospective_ht_setups (the ht_next_setup values), checked
    # separately below.
    #
    # The prospective (next) setup is NOT a flat +5: ht_next_setup first tries
    # raising plates by one plate_step (5 lb) within the current band config, but
    # only if the resulting bottom-clamp (plates + band rest) stays <= 225;
    # otherwise it searches band inventory for the smallest peak strictly above
    # the current peak (tiebreak: fewest bands). D6 (155) stays on Orange at +5
    # (160: 160+18=178 <= 225). D5 (205) would need 210+18=228 > 225 on Orange,
    # so it swaps to band #1 Red [id=2, bottom_lb=36, peak_lb=90] at 165 plates
    # (165+90=255, the smallest peak exceeding the prior 205+45=250) —
    # confirmed by direct execution, not guessed. Before the original (Task 5)
    # fix, both days collapsed to one last-inserted row's value regardless of
    # which day was actually being generated.
    ht_movement = gen_db.exec(select(Movement).where(Movement.name == "Hip Thrust [HIP_THRUST]")).one()
    inventory = [Band(bp.id, bp.bottom_lb, bp.peak_lb, bp.usable)
                 for bp in gen_db.exec(select(BandPair)).all()]
    for role, cur_plates, next_plates in [
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
