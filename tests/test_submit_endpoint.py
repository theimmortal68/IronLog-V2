# tests/test_submit_endpoint.py
from datetime import date
from fastapi.testclient import TestClient
from sqlmodel import Session as DbSession, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from ironlog.api.app import app, get_session
from ironlog.models.session import Session, SetLog, ExerciseSurvey, Note
from ironlog.models.enums import NoteClass, SessionStatus
import ironlog.models  # ensure all tables registered

# movement_id used by the three gate tests — seeded explicitly in _seed_analysis_state.
_TEST_MOVEMENT_ID = 3


def _client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    def _override():
        with DbSession(engine) as s:
            yield s
    app.dependency_overrides[get_session] = _override
    return TestClient(app), engine


def _seed_analysis_state(engine, movement_id=_TEST_MOVEMENT_ID):
    """Seed the minimal rows run_analysis needs on a bare in-memory DB.

    Inserts: EngineState (singleton, CALIBRATION phase) + PhasePolicy for that
    phase + Movement (explicit id so existing test bodies stay stable) +
    MovementState.  Without this, run_analysis raises NoResultFound on
    EngineState / MovementState and submit would return a 500.
    """
    from ironlog.models.library import EngineState, Movement, MovementState, PhasePolicy
    from ironlog.models.enums import CalibrationStatus, Objective, Phase
    with DbSession(engine) as s:
        s.add(EngineState(id=1, current_phase=Phase.CALIBRATION))
        s.add(PhasePolicy(
            phase=Phase.CALIBRATION,
            default_objective=Objective.PROGRESS,
            rpe_band_low=7.0, rpe_band_high=9.0,
            hard_cap=10.0, top_set_rpe=9.0,
            progression_attempted=True,
            volume_posture="standard",
        ))
        # Explicit id so existing test bodies referencing movement_id=3 stay stable.
        s.add(Movement(id=movement_id, name=f"Lift {movement_id}", base_name="Lift"))
        s.add(MovementState(movement_id=movement_id,
                            calibration_status=CalibrationStatus.INHERITED))
        s.commit()


def _planned_session(engine):
    with DbSession(engine) as s:
        ws = Session(date=date(2026, 7, 1), day_role="D1 Upper Push", phase="P1",
                     status=SessionStatus.PLANNED)
        s.add(ws); s.commit(); s.refresh(ws)
        return ws.id


def test_submit_writes_setlogs_and_completes():
    client, engine = _client()
    _seed_analysis_state(engine)          # run_analysis must not NoResultFound
    sid = _planned_session(engine)
    body = {"set_logs": [{"planned_set_id": None, "movement_id": _TEST_MOVEMENT_ID,
                          "set_index": 0, "set_role": "WORKING", "is_warmup": False,
                          "actual_load": 100.0, "actual_reps": 8,
                          "feedback_tap": "ON_TARGET"}],
            "surveys": [], "notes": []}
    r = client.post(f"/sessions/{sid}/submit", json=body)
    assert r.status_code == 200
    assert r.json()["status"] == "COMPLETED"
    assert r.json()["set_logs_written"] == 1
    with DbSession(engine) as s:
        assert s.get(Session, sid).status == SessionStatus.COMPLETED
        assert len(s.exec(select(SetLog).where(SetLog.session_id == sid)).all()) == 1
    app.dependency_overrides.clear()


def test_submit_rejects_working_set_without_tap_422_and_writes_nothing():
    # 422 fires before any write or run_analysis — no analysis state needed.
    client, engine = _client()
    sid = _planned_session(engine)
    body = {"set_logs": [{"planned_set_id": None, "movement_id": _TEST_MOVEMENT_ID,
                          "set_index": 0, "set_role": "WORKING", "is_warmup": False,
                          "actual_load": 100.0, "actual_reps": 8,
                          "feedback_tap": None}],   # tapless working set
            "surveys": [], "notes": []}
    r = client.post(f"/sessions/{sid}/submit", json=body)
    assert r.status_code == 422
    with DbSession(engine) as s:
        assert s.exec(select(SetLog).where(SetLog.session_id == sid)).all() == []
        assert s.get(Session, sid).status == SessionStatus.PLANNED   # untouched
    app.dependency_overrides.clear()


def test_submit_idempotent_lost_ack_retry_writes_nothing_new():
    """The real-world path: submit succeeds, ack lost, client retries."""
    client, engine = _client()
    _seed_analysis_state(engine)          # first submit reaches run_analysis
    sid = _planned_session(engine)
    body = {"set_logs": [{"planned_set_id": None, "movement_id": _TEST_MOVEMENT_ID,
                          "set_index": 0, "set_role": "WORKING", "is_warmup": False,
                          "actual_load": 100.0, "actual_reps": 8,
                          "feedback_tap": "ON_TARGET"}],
            "surveys": [], "notes": []}
    r1 = client.post(f"/sessions/{sid}/submit", json=body)
    assert r1.status_code == 200 and r1.json()["already_completed"] is False
    r2 = client.post(f"/sessions/{sid}/submit", json=body)   # lost-ack retry
    assert r2.status_code == 200 and r2.json()["already_completed"] is True
    with DbSession(engine) as s:
        # exactly ONE SetLog — no duplicate from the retry
        assert len(s.exec(select(SetLog).where(SetLog.session_id == sid)).all()) == 1
    app.dependency_overrides.clear()


def test_submit_writes_surveys_and_notes():
    """Fork-4 B/C: non-empty surveys + notes write branch is covered.

    Locks the JOURNAL/unclassified storage contract: Note.classification == JOURNAL,
    confirmed == False, applied == False on first capture (deferred classification).
    """
    client, engine = _client()
    _seed_analysis_state(engine)
    sid = _planned_session(engine)

    body = {
        "set_logs": [{
            "planned_set_id": None,
            "movement_id": _TEST_MOVEMENT_ID,
            "set_index": 0,
            "set_role": "WORKING",
            "is_warmup": False,
            "actual_load": 100.0,
            "actual_reps": 8,
            "feedback_tap": "ON_TARGET",
        }],
        "surveys": [{
            "movement_id": _TEST_MOVEMENT_ID,
            "sticking_point": "BOTTOM",
            "asymmetry_flag": True,
            "technique_flag": False,
        }],
        "notes": [{
            "movement_id": _TEST_MOVEMENT_ID,
            "text": "felt unstable at the bottom",
        }],
    }
    r = client.post(f"/sessions/{sid}/submit", json=body)
    assert r.status_code == 200, f"submit failed: {r.text}"
    assert r.json()["already_completed"] is False

    with DbSession(engine) as s:
        # SetLog sanity
        logs = s.exec(select(SetLog).where(SetLog.session_id == sid)).all()
        assert len(logs) == 1

        # ExerciseSurvey row written with the sticking_point/flags we sent
        surveys = s.exec(
            select(ExerciseSurvey).where(ExerciseSurvey.session_id == sid)
        ).all()
        assert len(surveys) == 1
        sv = surveys[0]
        assert sv.movement_id == _TEST_MOVEMENT_ID
        assert sv.sticking_point == "BOTTOM"
        assert sv.asymmetry_flag is True
        assert sv.technique_flag is False

        # Note: JOURNAL classification, confirmed=False, applied=False
        notes = s.exec(select(Note).where(Note.session_id == sid)).all()
        assert len(notes) == 1
        nt = notes[0]
        assert nt.movement_id == _TEST_MOVEMENT_ID
        assert nt.text == "felt unstable at the bottom"
        assert nt.classification == NoteClass.JOURNAL
        assert nt.confirmed is False
        assert nt.applied is False

    app.dependency_overrides.clear()


def test_submit_fires_run_analysis():
    """Gate #4 seam: submit stamps analyzed_at — proves run_analysis ran.

    Seeds a full planned graph (Movement → Session(PLANNED) → ExerciseGroup →
    PlannedExercise → PlannedSet) plus EngineState/PhasePolicy/MovementState.
    Submits one SetLog with planned_set_id set and feedback_tap ON_TARGET.
    Asserts Session.analyzed_at is not None after the response — this FAILS if
    the run_analysis(...) call is removed from the handler (delete-call check
    was confirmed during authoring: test went red, call restored).
    """
    from ironlog.models.library import EngineState, Movement, MovementState, PhasePolicy
    from ironlog.models.enums import (
        CalibrationStatus, GroupType, Objective, Phase, Scheme, SetRole,
    )
    from ironlog.models.session import ExerciseGroup, PlannedExercise, PlannedSet

    client, engine = _client()

    with DbSession(engine) as s:
        # Global engine state
        s.add(EngineState(id=1, current_phase=Phase.CALIBRATION))
        s.add(PhasePolicy(
            phase=Phase.CALIBRATION,
            default_objective=Objective.PROGRESS,
            rpe_band_low=7.0, rpe_band_high=9.0,
            hard_cap=10.0, top_set_rpe=9.0,
            progression_attempted=True,
            volume_posture="standard",
        ))
        s.flush()

        # Movement + per-movement state
        mv = Movement(name="Seam Test Lift", base_name="Seam Test Lift")
        s.add(mv); s.flush()
        s.add(MovementState(movement_id=mv.id,
                            calibration_status=CalibrationStatus.CALIBRATING,
                            current_load=80.0))
        s.flush()

        # Planned session graph
        ws = Session(date=date(2026, 7, 2), day_role="D1 Upper Push",
                     phase="CALIBRATION", status=SessionStatus.PLANNED)
        s.add(ws); s.flush()

        grp = ExerciseGroup(session_id=ws.id, order_index=0,
                            group_type=GroupType.STRAIGHT)
        s.add(grp); s.flush()

        pex = PlannedExercise(group_id=grp.id, movement_id=mv.id, order_index=0,
                              scheme=Scheme.STRAIGHT, objective=Objective.PROGRESS)
        s.add(pex); s.flush()

        pset = PlannedSet(planned_exercise_id=pex.id, set_index=0,
                          set_role=SetRole.WORKING,
                          target_rpe=8.0, target_reps_low=5, target_reps_high=8)
        s.add(pset); s.flush()

        sid = ws.id
        pset_id = pset.id
        mv_id = mv.id
        s.commit()

    body = {"set_logs": [{"planned_set_id": pset_id, "movement_id": mv_id,
                          "set_index": 0, "set_role": "WORKING", "is_warmup": False,
                          "actual_load": 80.0, "actual_reps": 6,
                          "feedback_tap": "ON_TARGET"}],
            "surveys": [], "notes": []}
    r = client.post(f"/sessions/{sid}/submit", json=body)
    assert r.status_code == 200, f"submit failed: {r.text}"

    with DbSession(engine) as s:
        ws = s.get(Session, sid)
        assert ws.analyzed_at is not None, (
            "run_analysis seam did not fire: Session.analyzed_at is None after submit"
        )
    app.dependency_overrides.clear()
