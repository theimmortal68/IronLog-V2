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

from fastapi.testclient import TestClient
from sqlmodel import Session as DbSession, create_engine, select
from sqlmodel.pool import StaticPool

from ironlog.api.app import app, get_session
from ironlog.models.library import Movement
from ironlog.models.session import (
    ExerciseGroup, PlannedExercise, PlannedSet, Session as WorkoutSession,
)
from ironlog.models.enums import GroupType, Objective, Scheme, SessionStatus, SetRole
import ironlog.models  # noqa: F401 — ensure all tables registered


def _client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    import ironlog.db as db
    db.engine = engine
    import ironlog.seed as seed
    importlib.reload(seed)
    seed.engine = engine
    seed.seed()                                    # 103-movement library

    def _override():
        with DbSession(engine) as s:
            yield s

    app.dependency_overrides[get_session] = _override
    return TestClient(app), engine


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


def test_skip_marks_only_unlogged_sets_and_is_idempotent():
    client, engine = _client()
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
    app.dependency_overrides.clear()
