"""tests/test_note_apply_endpoints.py — /notes/{id}/apply + /overrides list + revert."""
from datetime import date

from fastapi.testclient import TestClient
from sqlmodel import Session as DBSession, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

import ironlog.models  # register tables
from ironlog.api.app import app, get_session
from ironlog.models.enums import NoteClass
from ironlog.models.library import Movement
from ironlog.models.program import Program, ProgramDay, Tier, TierExercise, TierKind, SlotMovementOverride
from ironlog.models.session import Note, Session as WorkoutSession


def _client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)

    def _override():
        with DBSession(engine) as s:
            yield s

    app.dependency_overrides[get_session] = _override
    return TestClient(app), engine


def _seed_bench_slot(engine):
    """Program day + T1 bench slot + incline movement (apply target) + a
    CONFIG_CHANGE note on bench tied to a session with the matching day_role."""
    with DBSession(engine) as s:
        prog = Program(name="Phase 1", phase="P1", duration_weeks=4)
        s.add(prog); s.commit(); s.refresh(prog)
        day = ProgramDay(program_id=prog.id, day_index=0, day_role="D1 Upper Push")
        s.add(day); s.commit(); s.refresh(day)
        tier = Tier(program_day_id=day.id, tier_label="T1", tier_order=1, tier_kind=TierKind.T1_STRAIGHT)
        s.add(tier); s.commit(); s.refresh(tier)

        bench = Movement(name="Bench Press [PB]", base_name="Bench Press")
        incline = Movement(name="Incline Bench [PB]", base_name="Incline Bench")
        s.add(bench); s.add(incline); s.commit()
        s.refresh(bench); s.refresh(incline)

        te = TierExercise(tier_id=tier.id, slot_id="d1_t1", movement_id=bench.id,
                           exercise_order=1, tier_role="anchor")
        s.add(te); s.commit(); s.refresh(te)

        ws = WorkoutSession(date=date(2026, 7, 1), day_role="D1 Upper Push", phase="P1")
        s.add(ws); s.commit(); s.refresh(ws)

        note = Note(session_id=ws.id, movement_id=bench.id, text="switch bench to incline",
                    classification=NoteClass.CONFIG_CHANGE)
        s.add(note); s.commit(); s.refresh(note)

        return {"note_id": note.id, "bench_id": bench.id, "incline_id": incline.id,
                "te_id": te.id, "tier_id": tier.id}


def test_apply_creates_active_override_and_marks_note():
    client, engine = _client()
    ctx = _seed_bench_slot(engine)
    resp = client.post(f"/notes/{ctx['note_id']}/apply", json={"target_movement_id": ctx["incline_id"]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["tier_exercise_id"] == ctx["te_id"]
    assert body["override_movement_id"] == ctx["incline_id"]

    with DBSession(engine) as s:
        ov = s.exec(select(SlotMovementOverride)).one()
        assert ov.active is True
        assert ov.tier_exercise_id == ctx["te_id"]
        assert ov.override_movement_id == ctx["incline_id"]
        n = s.get(Note, ctx["note_id"])
        assert n.confirmed is True
        assert n.applied is True
    app.dependency_overrides.clear()


def test_overrides_list_shows_from_to_names():
    client, engine = _client()
    ctx = _seed_bench_slot(engine)
    client.post(f"/notes/{ctx['note_id']}/apply", json={"target_movement_id": ctx["incline_id"]})
    body = client.get("/overrides").json()
    assert len(body) == 1
    row = body[0]
    assert row["day_role"] == "D1 Upper Push"
    assert row["tier_label"] == "T1"
    assert row["slot_id"] == "d1_t1"
    assert row["from_movement_name"] == "Bench Press [PB]"
    assert row["to_movement_name"] == "Incline Bench [PB]"
    app.dependency_overrides.clear()


def test_revert_deactivates_and_removes_from_list():
    client, engine = _client()
    ctx = _seed_bench_slot(engine)
    apply_resp = client.post(f"/notes/{ctx['note_id']}/apply", json={"target_movement_id": ctx["incline_id"]})
    ov_id = apply_resp.json()["id"]

    revert_resp = client.post(f"/overrides/{ov_id}/revert")
    assert revert_resp.status_code == 200
    assert revert_resp.json()["active"] is False

    with DBSession(engine) as s:
        ov = s.get(SlotMovementOverride, ov_id)
        assert ov.active is False

    assert client.get("/overrides").json() == []
    app.dependency_overrides.clear()


def test_revert_is_idempotent():
    client, engine = _client()
    ctx = _seed_bench_slot(engine)
    apply_resp = client.post(f"/notes/{ctx['note_id']}/apply", json={"target_movement_id": ctx["incline_id"]})
    ov_id = apply_resp.json()["id"]
    assert client.post(f"/overrides/{ov_id}/revert").status_code == 200
    assert client.post(f"/overrides/{ov_id}/revert").status_code == 200
    app.dependency_overrides.clear()


def test_apply_missing_note_404():
    client, engine = _client()
    ctx = _seed_bench_slot(engine)
    resp = client.post("/notes/999999/apply", json={"target_movement_id": ctx["incline_id"]})
    assert resp.status_code == 404
    app.dependency_overrides.clear()


def test_apply_unknown_target_movement_404():
    client, engine = _client()
    ctx = _seed_bench_slot(engine)
    resp = client.post(f"/notes/{ctx['note_id']}/apply", json={"target_movement_id": 999999})
    assert resp.status_code == 404
    app.dependency_overrides.clear()


def test_apply_ambiguous_slot_409():
    client, engine = _client()
    ctx = _seed_bench_slot(engine)
    with DBSession(engine) as s:
        s.add(TierExercise(tier_id=ctx["tier_id"], slot_id="d1_t1b", movement_id=ctx["bench_id"],
                            exercise_order=2, tier_role="semi"))
        s.commit()
    resp = client.post(f"/notes/{ctx['note_id']}/apply", json={"target_movement_id": ctx["incline_id"]})
    assert resp.status_code == 409
    app.dependency_overrides.clear()


def test_revert_missing_override_404():
    client, _ = _client()
    assert client.post("/overrides/999999/revert").status_code == 404
    app.dependency_overrides.clear()
