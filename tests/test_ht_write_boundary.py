"""test_ht_write_boundary.py — Task 4 guardrail: Option-C two-writer boundary
for the HT (band-composite) setup.

commit_session (approval-time) is the SOLE writer of ht_plates/ht_band_config,
exactly mirroring current_load (Fork 7c). run_analysis/apply_analysis compute
and write bookkeeping (e1rm, tier, ceiling/stall counters, etc.) but must NEVER
touch ht_plates/ht_band_config.

This test drives the real flow — generate (assemble) -> approve (commit_session,
which writes the setup once) -> log a completed session against the committed
plan -> run_analysis (which must leave the setup byte-identical) — rather than
asserting the invariant in isolation, so it proves the setup "advances only
through generate->approve" per the Option-C contract.

NO from __future__ import annotations (project-wide constraint).
gen_db_calibrated fixture auto-discovered from conftest.py.
"""
from sqlmodel import select

from ironlog.generation.assembler import assemble
from ironlog.generation.context import resolve_context
from ironlog.generation.fallback import program_selections
from ironlog.generation.loop import commit_session
from ironlog.generation.skeleton import lay_skeleton
from ironlog.models.enums import FeedbackTap, SessionStatus
from ironlog.models.library import Movement, MovementState
from ironlog.models.session import PlannedSet, SetLog
from ironlog.persistence.run_analysis import run_analysis

from tests.test_ht_composite_wiring import _synthetic_plain_ht_slot

WEEK_KEYER = lambda d: (d.isocalendar()[0], d.isocalendar()[1])  # noqa: E731


def test_run_analysis_never_writes_ht_setup(gen_db_calibrated):
    gen_db = gen_db_calibrated
    # 2026-08-11 (STAB maintenance-block redesign, Task 2): was "D2 Lower A"
    # -- D2's Hip Thrust T1b tier was removed entirely, so this generic HT
    # write-boundary test used D5's still-live Hip Thrust slot.
    # 2026-08-12 (Task 4): D5's Hip Thrust T1b tier was ALSO removed entirely
    # (2nd of 3 removals across this redesign) -- repointed to D6 Weak
    # Points' real d6_g1c slot, the last one left. This test only asserts
    # the setup is UNCHANGED by run_analysis, which holds regardless of
    # d6_g1c also being a derived slot.
    #
    # 2026-08-12 (Task 5): D6's real d6_g1c slot is ALSO removed entirely now
    # -- the LAST real Hip Thrust TierExercise anywhere in the program is
    # gone. Uses a synthetic plain HT slot on D6's real ProgramDay instead
    # (mirrors test_ht_composite_wiring.py's test_commit_persists_ht_setup).
    ht_mv = gen_db.exec(
        select(Movement).where(Movement.name == "Hip Thrust [HIP_THRUST]")
    ).one()
    _synthetic_plain_ht_slot(gen_db, "D6 Weak Points", ht_mv.id, "test_write_boundary_d6_ht")
    gen_db.add(MovementState(
        movement_id=ht_mv.id, day_id="D6 Weak Points",
        ht_plates=155.0, ht_band_config=[],
    ))
    gen_db.commit()

    sk = lay_skeleton("D6 Weak Points", gen_db)
    ctx = resolve_context("D6 Weak Points", sk, gen_db, WEEK_KEYER)
    sel = program_selections(sk)
    assembled = assemble(sel, sk, ctx, gen_db)

    # Approve: commit_session is the sole writer of the HT setup.
    session = commit_session(
        assembled, gen_db,
        approval_mode="auto", prompt={},
        selections_dict={}, clamps=[], repairs=[], fallback_used=False,
    )

    before = gen_db.exec(
        select(MovementState).where(
            MovementState.movement_id == ht_mv.id,
            MovementState.day_id == "D6 Weak Points",
        )
    ).one()
    before_setup = (before.ht_plates, before.ht_band_config)
    assert before_setup[0] is not None and before_setup[1] is not None, (
        "commit_session must have written a real HT setup to assert against"
    )

    # Find the HT PlannedSet actually persisted on the committed session.
    ht_planned_set = None
    for g in session.groups:
        for ex in g.exercises:
            for ps in ex.planned_sets:
                if ps.target_plates is not None:
                    ht_planned_set = ps
                    break
    assert ht_planned_set is not None

    # Log the session: one working set on the HT movement, feedback tapped.
    session.status = SessionStatus.COMPLETED
    gen_db.add(session)
    gen_db.add(SetLog(
        planned_set_id=ht_planned_set.id, session_id=session.id,
        movement_id=ht_mv.id, set_index=ht_planned_set.set_index,
        actual_load=ht_planned_set.target_plates, actual_reps=8,
        feedback_tap=FeedbackTap.ON_TARGET, is_warmup=False,
    ))
    gen_db.commit()

    # Analyze: must NOT touch ht_plates/ht_band_config (Option-C boundary).
    run_analysis(session.id, gen_db, WEEK_KEYER)

    after = gen_db.exec(
        select(MovementState).where(
            MovementState.movement_id == ht_mv.id,
            MovementState.day_id == "D6 Weak Points",
        )
    ).one()
    after_setup = (after.ht_plates, after.ht_band_config)
    assert after_setup == before_setup, (
        "run_analysis must never write ht_plates/ht_band_config — "
        "only commit_session (approval-time) may"
    )
