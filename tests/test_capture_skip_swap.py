"""tests/test_capture_skip_swap.py — mid-workout skip/swap endpoints (Tasks 2-4).

Follows the repo's established API-test pattern (see test_generate_preview.py
/ test_submit_endpoint.py): TestClient dispatches through a worker thread
pool, so the `gen_db` fixture's plain in-memory engine (no
`check_same_thread=False` / no StaticPool) can't be handed directly to
`app.dependency_overrides[get_session]` as `lambda: gen_db` — that raises
"SQLite objects created in a thread can only be used in that same thread."
Instead: build a StaticPool engine with `check_same_thread=False`, seed it
with the same recipe `gen_db` uses (seed.seed() + seed_phase1_program), and
override `get_session` with a fresh-session-per-request generator.

NO from __future__ import annotations (project-wide constraint).
"""
import importlib
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session as DbSession, create_engine, select
from sqlmodel.pool import StaticPool

from ironlog.api.app import app, get_session
from ironlog.models.library import Movement
from ironlog.models.program import TierExercise
from ironlog.models.session import (
    ExerciseGroup, PlannedExercise, PlannedSet, SetLog, Session as WorkoutSession,
)
from ironlog.models.enums import (
    FeedbackTap, GroupType, Objective, Scheme, SessionStatus, SetRole,
)
import ironlog.models  # noqa: F401 — ensure all tables registered


@pytest.fixture
def api_client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    import ironlog.db as db
    db.engine = engine
    import ironlog.seed as seed
    importlib.reload(seed)
    seed.engine = engine
    seed.seed()                                    # 103-movement library
    from ironlog.generation.program_seed import seed_phase1_program
    with DbSession(engine) as s:
        seed_phase1_program(s)                     # Phase 1 program prior (TierExercise rows etc.)

    def _override():
        with DbSession(engine) as s:
            yield s

    app.dependency_overrides[get_session] = _override
    yield TestClient(app), engine
    app.dependency_overrides.clear()


def _make_planned_session(db, movement_name="Bench Press [PB]"):
    mv = db.exec(select(Movement).where(Movement.name == movement_name)).one()
    ws = WorkoutSession(date=date.today(), day_role="D1 Upper Push",
                        phase="STAB", status=SessionStatus.PLANNED)
    db.add(ws); db.commit(); db.refresh(ws)
    grp = ExerciseGroup(session_id=ws.id, order_index=0, group_type=GroupType.STRAIGHT, rounds=1)
    db.add(grp); db.commit(); db.refresh(grp)
    pe = PlannedExercise(group_id=grp.id, movement_id=mv.id, order_index=0,
                         scheme=Scheme.STRAIGHT, objective=Objective.PROGRESS)
    db.add(pe); db.commit(); db.refresh(pe)
    for i in range(3):
        db.add(PlannedSet(planned_exercise_id=pe.id, set_index=i, set_role=SetRole.WORKING,
                          target_load=100.0, target_reps_low=4, target_reps_high=6))
    db.commit()
    db.refresh(pe)
    return ws, pe


def test_skip_marks_only_unlogged_sets_and_is_idempotent(api_client):
    client, engine = api_client
    with DbSession(engine) as db:
        ws, pe = _make_planned_session(db)
        session_id, exercise_id = ws.id, pe.id

    resp = client.post(f"/sessions/{session_id}/exercises/{exercise_id}/skip")
    assert resp.status_code == 200
    body = resp.json()
    assert all(s["is_skipped"] for s in body["planned_sets"])

    # Idempotent: calling again on an already-fully-skipped exercise is a no-op, still 200.
    resp2 = client.post(f"/sessions/{session_id}/exercises/{exercise_id}/skip")
    assert resp2.status_code == 200
    assert all(s["is_skipped"] for s in resp2.json()["planned_sets"])


def test_skip_leaves_already_logged_sets_untouched(api_client):
    client, engine = api_client
    with DbSession(engine) as db:
        ws, pe = _make_planned_session(db)
        session_id, exercise_id = ws.id, pe.id
        logged_set = pe.planned_sets[0]
        logged_planned_set_id = logged_set.id
        db.add(SetLog(
            planned_set_id=logged_planned_set_id,
            session_id=ws.id,
            movement_id=pe.movement_id,
            set_index=0,
            actual_load=100.0,
            actual_reps=5,
            feedback_tap=FeedbackTap.ON_TARGET,
        ))
        db.commit()

    resp = client.post(f"/sessions/{session_id}/exercises/{exercise_id}/skip")
    assert resp.status_code == 200
    body = resp.json()
    for s in body["planned_sets"]:
        if s["id"] == logged_planned_set_id:
            assert s["is_skipped"] is False
        else:
            assert s["is_skipped"] is True


def test_skip_404s_on_cross_session_exercise_id(api_client):
    client, engine = api_client
    with DbSession(engine) as db:
        ws1, pe1 = _make_planned_session(db)
        ws2, pe2 = _make_planned_session(db)
        session1_id, exercise2_id = ws1.id, pe2.id

    resp = client.post(f"/sessions/{session1_id}/exercises/{exercise2_id}/skip")
    assert resp.status_code == 404


def test_swap_recomputes_remaining_sets_and_updates_movement(api_client):
    client, engine = api_client
    with DbSession(engine) as db:
        ws, pe = _make_planned_session(db, movement_name="Bench Press [PB]")
        session_id, exercise_id = ws.id, pe.id
        new_mv = db.exec(
            select(Movement).where(Movement.name == "Incline DB Press [DB + BENCH]")
        ).one()
        new_mv_id = new_mv.id

    resp = client.post(
        f"/sessions/{session_id}/exercises/{exercise_id}/swap",
        json={"new_movement_id": new_mv_id, "make_permanent": False},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["movement_id"] == new_mv_id
    assert body["movement_name"] == "Incline DB Press [DB + BENCH]"
    assert len(body["planned_sets"]) == 3


def test_swap_leaves_already_logged_setlog_movement_id_untouched(api_client):
    client, engine = api_client
    with DbSession(engine) as db:
        ws, pe = _make_planned_session(db, movement_name="Bench Press [PB]")
        session_id, exercise_id = ws.id, pe.id
        old_mv_id = pe.movement_id
        logged_set = pe.planned_sets[0]
        logged_planned_set_id = logged_set.id
        db.add(SetLog(planned_set_id=logged_planned_set_id, session_id=ws.id, movement_id=old_mv_id,
                      set_index=0, actual_load=100.0, actual_reps=5,
                      feedback_tap=FeedbackTap.ON_TARGET))
        db.commit()
        new_mv_id = db.exec(
            select(Movement).where(Movement.name == "Incline DB Press [DB + BENCH]")
        ).one().id

    resp = client.post(f"/sessions/{session_id}/exercises/{exercise_id}/swap",
                       json={"new_movement_id": new_mv_id, "make_permanent": False})
    assert resp.status_code == 200

    with DbSession(engine) as db:
        row = db.exec(select(SetLog).where(SetLog.planned_set_id == logged_planned_set_id)).one()
        assert row.movement_id == old_mv_id


def test_swap_make_permanent_writes_slot_override(api_client):
    from ironlog.models.program import SlotMovementOverride
    from ironlog.models.enums import OverrideType

    client, engine = api_client
    with DbSession(engine) as db:
        ws, pe = _make_planned_session(db, movement_name="Bench Press [PB]")
        session_id, exercise_id = ws.id, pe.id
        te = db.exec(select(TierExercise).where(TierExercise.slot_id == "d1_t1")).first()
        te_id = te.id
        pe.tier_exercise_id = te_id
        db.add(pe); db.commit()
        new_mv_id = db.exec(
            select(Movement).where(Movement.name == "Incline DB Press [DB + BENCH]")
        ).one().id

    resp = client.post(f"/sessions/{session_id}/exercises/{exercise_id}/swap",
                       json={"new_movement_id": new_mv_id, "make_permanent": True})
    assert resp.status_code == 200

    with DbSession(engine) as db:
        ov = db.exec(select(SlotMovementOverride).where(
            SlotMovementOverride.tier_exercise_id == te_id,
            SlotMovementOverride.override_type == OverrideType.MOVEMENT,
            SlotMovementOverride.active == True)).first()  # noqa: E712
        assert ov is not None
        assert ov.override_movement_id == new_mv_id


def test_swap_make_permanent_without_tier_exercise_id_409s(api_client):
    client, engine = api_client
    with DbSession(engine) as db:
        ws, pe = _make_planned_session(db, movement_name="Bench Press [PB]")
        session_id, exercise_id = ws.id, pe.id
        assert pe.tier_exercise_id is None
        new_mv_id = db.exec(
            select(Movement).where(Movement.name == "Incline DB Press [DB + BENCH]")
        ).one().id

    resp = client.post(f"/sessions/{session_id}/exercises/{exercise_id}/swap",
                       json={"new_movement_id": new_mv_id, "make_permanent": True})
    assert resp.status_code == 409


def test_swap_ht_composite_target_recomputed(api_client):
    """Extra coverage (beyond the brief's 4 tests): swap into a real HT
    (band-composite) movement with a configured MovementState, proving
    prescribe_swap_sets' reuse of _build_exercise actually branches into the
    HT-composite path (target_plates/band_config populated, target_load left
    None) rather than only ever exercising the plain-load path."""
    from ironlog.models.library import MovementState

    client, engine = api_client
    with DbSession(engine) as db:
        ws, pe = _make_planned_session(db, movement_name="Bench Press [PB]")
        session_id, exercise_id = ws.id, pe.id
        ht_mv = db.exec(
            select(Movement).where(Movement.name == "Hip Thrust [HIP_THRUST]")
        ).one()
        ht_mv_id = ht_mv.id
        # Simulate a post-wizard configured HT setup so _build_exercise takes
        # the composite (has_current_ht_setup) branch instead of falling
        # through to needs-calibration.
        db.add(MovementState(movement_id=ht_mv_id, current_load=100.0))
        db.commit()

    resp = client.post(f"/sessions/{session_id}/exercises/{exercise_id}/swap",
                       json={"new_movement_id": ht_mv_id, "make_permanent": False})
    assert resp.status_code == 200
    body = resp.json()
    assert body["movement_id"] == ht_mv_id
    for s in body["planned_sets"]:
        assert s["target_load"] is None
        assert s["target_plates"] is not None


def test_swap_rejects_mismatched_structure_ramp_sets_untouched(api_client):
    """Opus review of df64b8d: the copy loop matches remaining PlannedSets to
    freshly-prescribed sets purely by set_index and silently `continue`s on
    any index it can't match. If a ramp-eligible anchor is swapped before its
    unlogged ramp sets (set_index -3/-2/-1) are logged, prescribe_swap_sets
    (called with is_anchor=False) never produces matching ramp indices, so
    those rows would silently keep the OLD movement's ramp loads while
    pe.movement_id points at the new movement. Prove the endpoint now 409s
    instead, and that the exercise is left completely untouched (reject
    before mutate, not partial-then-fail)."""
    client, engine = api_client
    with DbSession(engine) as db:
        ws, pe = _make_planned_session(db, movement_name="Bench Press [PB]")
        session_id, exercise_id = ws.id, pe.id
        original_movement_id = pe.movement_id
        # Extra unlogged ramp set alongside the helper's normal 0/1/2 rows,
        # simulating a ramp-eligible anchor that hasn't started its ramp yet.
        db.add(PlannedSet(planned_exercise_id=pe.id, set_index=-1, set_role=SetRole.RAMP,
                          is_warmup=True, target_load=60.0, target_reps_low=5,
                          target_reps_high=5))
        db.commit()
        original_targets = {
            ps.id: (ps.set_index, ps.target_load, ps.target_reps_low, ps.target_reps_high)
            for ps in db.exec(
                select(PlannedSet).where(PlannedSet.planned_exercise_id == exercise_id)
            ).all()
        }
        new_mv_id = db.exec(
            select(Movement).where(Movement.name == "Incline DB Press [DB + BENCH]")
        ).one().id

    resp = client.post(f"/sessions/{session_id}/exercises/{exercise_id}/swap",
                       json={"new_movement_id": new_mv_id, "make_permanent": False})
    assert resp.status_code == 409

    with DbSession(engine) as db:
        pe_after = db.get(PlannedExercise, exercise_id)
        assert pe_after.movement_id == original_movement_id
        current_targets = {
            ps.id: (ps.set_index, ps.target_load, ps.target_reps_low, ps.target_reps_high)
            for ps in db.exec(
                select(PlannedSet).where(PlannedSet.planned_exercise_id == exercise_id)
            ).all()
        }
        assert current_targets == original_targets


def test_swap_same_set_count_still_succeeds(api_client):
    """Confirms the mismatch guard doesn't false-positive on the ordinary
    case: 3 remaining WORKING sets swapping to a plain movement whose
    _build_exercise also produces 3 sets. (Largely re-covers
    test_swap_recomputes_remaining_sets_and_updates_movement; kept as an
    explicit companion to the new guard's negative case.)"""
    client, engine = api_client
    with DbSession(engine) as db:
        ws, pe = _make_planned_session(db, movement_name="Bench Press [PB]")
        session_id, exercise_id = ws.id, pe.id
        new_mv_id = db.exec(
            select(Movement).where(Movement.name == "Incline DB Press [DB + BENCH]")
        ).one().id

    resp = client.post(
        f"/sessions/{session_id}/exercises/{exercise_id}/swap",
        json={"new_movement_id": new_mv_id, "make_permanent": False},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["movement_id"] == new_mv_id
    assert len(body["planned_sets"]) == 3
