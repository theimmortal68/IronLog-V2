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

WEEK_KEYER = lambda d: (d.isocalendar()[0], d.isocalendar()[1])  # noqa: E731


def test_run_analysis_never_writes_ht_setup(gen_db_calibrated):
    gen_db = gen_db_calibrated
    sk = lay_skeleton("D2 Lower A", gen_db)
    ctx = resolve_context("D2 Lower A", sk, gen_db, WEEK_KEYER)
    sel = program_selections(sk)
    assembled = assemble(sel, sk, ctx, gen_db)

    ht_mv = gen_db.exec(
        select(Movement).where(Movement.name == "Hip Thrust [HIP_THRUST]")
    ).one()

    # Approve: commit_session is the sole writer of the HT setup.
    session = commit_session(
        assembled, gen_db,
        approval_mode="auto", prompt={},
        selections_dict={}, clamps=[], repairs=[], fallback_used=False,
    )

    before = gen_db.exec(
        select(MovementState).where(MovementState.movement_id == ht_mv.id)
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
        select(MovementState).where(MovementState.movement_id == ht_mv.id)
    ).one()
    after_setup = (after.ht_plates, after.ht_band_config)
    assert after_setup == before_setup, (
        "run_analysis must never write ht_plates/ht_band_config — "
        "only commit_session (approval-time) may"
    )
