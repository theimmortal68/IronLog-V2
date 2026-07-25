"""test_commit_day_scoped_state.py — Fix 5: day-scope commit_session's
MovementState writer by (movement_id, day_id).

commit_session (ironlog/generation/loop.py) is the SOLE writer of
current_load / ht_plates / ht_band_config (Fork 7c, Option-C). Before this
fix its get-or-create lookup was day-blind (`.where(movement_id == mid)`,
no day_id filter), so committing a session for one day could silently
overwrite a DIFFERENT day's MovementState row for a movement shared across
days (Hip Thrust D2/D5/D6, Reverse Hyper, Nordic, Cable Tib — see
UniqueConstraint("movement_id", "day_id") on MovementState). The read path
(resolve_context / _resolve_movement_state in run_analysis.py, Task 5) was
already day-scoped; this closes the write-side gap using the SAME key:
day_id = the committing session's day_role (assembled.session.day_role),
exactly mirroring run_analysis.py's `day_id = workout.day_role`.

Two tests:
  1. test_commit_advances_only_the_committing_days_ht_row — real path (HT,
     band-composite). Seeds D2/D5/D6 Hip Thrust baselines via
     seed_movement_baselines (Task 4), generates + commits a REAL D6
     session, and asserts: D6's row advances (155 -> 160, staged-next per
     ht_next_setup) while D2's and D5's rows are byte-identical to before.
  2. test_commit_advances_only_the_committing_days_scalar_row — handcrafted,
     cheap scalar (current_load) confirmation. Deliberately inserts the
     OTHER day's row first (lower id) so a day-blind `.first()` would pick
     it over the committing day's own row — exactly the corruption pattern
     this fix closes.

NO from __future__ import annotations (project-wide constraint).
gen_db fixture auto-discovered from conftest.py.
"""
from datetime import date

from sqlmodel import select

from ironlog.generation.assembler import AssembledSession
from ironlog.generation.assembler import assemble
from ironlog.generation.baseline_seed import seed_movement_baselines
from ironlog.generation.context import resolve_context
from ironlog.generation.fallback import program_selections
from ironlog.generation.loop import commit_session
from ironlog.generation.skeleton import lay_skeleton
from ironlog.models.enums import (
    FeedbackTap, GroupType, Objective, Scheme, SessionStatus, SetRole,
)
from ironlog.models.library import Movement, MovementState
from ironlog.models.session import Session as IronSession
from ironlog.models.session import (
    ExerciseGroup, PlannedExercise, PlannedSet, SetLog,
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


def test_commit_advances_only_the_committing_days_ht_row(gen_db):
    seed_movement_baselines(gen_db)

    ht_mv = gen_db.exec(
        select(Movement).where(Movement.name == "Hip Thrust [HIP_THRUST]")
    ).one()

    def _rows_by_day():
        return {
            s.day_id: (s.ht_plates, s.ht_band_config)
            for s in gen_db.exec(
                select(MovementState).where(MovementState.movement_id == ht_mv.id)
            ).all()
        }

    before = _rows_by_day()
    assert before == {
        "D2 Lower A": (205.0, [1]),
        "D5 Lower B": (205.0, [1]),
        "D6 Weak Points": (155.0, [1]),
    }

    _stage_clean_ht_advance(gen_db, ht_mv.id, "D6 Weak Points", 155.0, [1])

    sk = lay_skeleton("D6 Weak Points", gen_db)
    ctx = resolve_context("D6 Weak Points", sk, gen_db, WEEK_KEYER)
    sel = program_selections(sk)
    assembled = assemble(sel, sk, ctx, gen_db)
    assert assembled.session.day_role == "D6 Weak Points"

    commit_session(
        assembled, gen_db,
        approval_mode="auto", prompt={},
        selections_dict={}, clamps=[], repairs=[], fallback_used=False,
    )

    after = _rows_by_day()
    # D6 committed → its own row advances to the staged-next setup.
    assert after["D6 Weak Points"] == (160.0, [1]), (
        f"D6's own row should advance on its own commit, got {after['D6 Weak Points']}"
    )
    # D2 and D5 must be byte-identical to before — a D6 commit must NEVER
    # touch another day's MovementState row.
    assert after["D2 Lower A"] == before["D2 Lower A"], (
        "committing D6 corrupted D2's HT row — day-blind write regression"
    )
    assert after["D5 Lower B"] == before["D5 Lower B"], (
        "committing D6 corrupted D5's HT row — day-blind write regression"
    )
    # Still exactly 3 rows — no stray row created, no collapse to fewer rows.
    assert len(after) == 3


def test_commit_advances_only_the_committing_days_scalar_row(gen_db):
    """Handcrafted, cheap scalar (current_load) confirmation of the same fix.

    A real Movement (any seeded one) shares state across two synthetic days.
    "Day B" is inserted FIRST (lower id) — a day-blind `.first()` lookup
    would return Day B's row regardless of which day is being committed,
    exactly reproducing the corruption this fix closes. We commit for
    "Day A" and assert only Day A's row changes.
    """
    # Pick a movement with NO pre-existing MovementState row (gen_db seeds
    # the 103-movement library + Phase 1 program, which stamps a handful of
    # legacy day_id=None rows) — this test wants exactly the two rows it
    # creates itself, no legacy-adoption interaction.
    seeded_mids = {s.movement_id for s in gen_db.exec(select(MovementState)).all()}
    mv = next(m for m in gen_db.exec(select(Movement)).all() if m.id not in seeded_mids)

    day_b_state = MovementState(movement_id=mv.id, day_id="Day B", current_load=200.0)
    gen_db.add(day_b_state)
    gen_db.commit()
    day_a_state = MovementState(movement_id=mv.id, day_id="Day A", current_load=100.0)
    gen_db.add(day_a_state)
    gen_db.commit()
    assert day_b_state.id < day_a_state.id, "Day B must be inserted (and thus id-ordered) first"

    session = IronSession(date=date.today(), day_role="Day A", phase="CUT")
    assembled = AssembledSession(
        session=session,
        prospective_current_loads={mv.id: 150.0},
    )

    commit_session(
        assembled, gen_db,
        approval_mode="auto", prompt={},
        selections_dict={}, clamps=[], repairs=[], fallback_used=False,
    )

    rows = {
        s.day_id: s.current_load
        for s in gen_db.exec(
            select(MovementState).where(MovementState.movement_id == mv.id)
        ).all()
    }
    assert rows["Day A"] == 150.0, "committing Day A should advance Day A's own row"
    assert rows["Day B"] == 200.0, (
        "committing Day A corrupted Day B's row — day-blind write regression"
    )
    assert len(rows) == 2
