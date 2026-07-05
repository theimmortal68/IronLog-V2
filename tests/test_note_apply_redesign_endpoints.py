"""tests/test_note_apply_redesign_endpoints.py — Task 3: explicit
/notes/{id}/apply (client sends the confirmed slot + adjustment), the new
/programs/{id}/slots read surface, and the generalized /overrides list.
Deterministic; NO LLM in this path."""
from datetime import date

from fastapi.testclient import TestClient
from sqlmodel import Session as DBSession, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

import ironlog.models  # register tables
from ironlog.api.app import app, get_session
from ironlog.models.enums import NoteClass, OverrideType
from ironlog.models.library import Movement
from ironlog.models.program import (Program, ProgramDay, SlotMovementOverride,
                                     Tier, TierExercise, TierKind)
from ironlog.models.session import Note, Session as WorkoutSession


def _client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)

    def _override():
        with DBSession(engine) as s:
            yield s

    app.dependency_overrides[get_session] = _override
    return TestClient(app), engine


def _seed(engine):
    """Program day D1 Upper Push with a T1 bench slot and a T2 hip-thrust slot,
    plus a CONFIG_CHANGE note (action_type LOAD_INCREASE, subject "hip thrust")
    tied to a session on that day."""
    with DBSession(engine) as s:
        prog = Program(name="Phase 1", phase="P1", duration_weeks=4)
        s.add(prog); s.commit(); s.refresh(prog)
        day = ProgramDay(program_id=prog.id, day_index=0, day_role="D1 Upper Push")
        s.add(day); s.commit(); s.refresh(day)
        t1 = Tier(program_day_id=day.id, tier_label="T1", tier_order=1, tier_kind=TierKind.T1_STRAIGHT)
        t2 = Tier(program_day_id=day.id, tier_label="T2", tier_order=2, tier_kind=TierKind.ACCESSORY)
        s.add(t1); s.add(t2); s.commit(); s.refresh(t1); s.refresh(t2)

        bench = Movement(name="Bench Press [PB]", base_name="Bench Press")
        hip_thrust = Movement(name="Hip Thrust [PB]", base_name="Hip Thrust")
        incline = Movement(name="Incline Bench [PB]", base_name="Incline Bench")
        s.add(bench); s.add(hip_thrust); s.add(incline); s.commit()
        s.refresh(bench); s.refresh(hip_thrust); s.refresh(incline)

        bench_te = TierExercise(tier_id=t1.id, slot_id="d1_t1", movement_id=bench.id,
                                 exercise_order=1, tier_role="anchor", rep_low=5, rep_high=5)
        ht_te = TierExercise(tier_id=t2.id, slot_id="d1_t2a", movement_id=hip_thrust.id,
                              exercise_order=1, tier_role="semi", rep_low=8, rep_high=12)
        s.add(bench_te); s.add(ht_te); s.commit(); s.refresh(bench_te); s.refresh(ht_te)

        ws = WorkoutSession(date=date(2026, 7, 1), day_role="D1 Upper Push", phase="P1")
        s.add(ws); s.commit(); s.refresh(ws)

        note = Note(session_id=ws.id, movement_id=hip_thrust.id, text="bump hip thrust +10",
                    classification=NoteClass.CONFIG_CHANGE,
                    classification_meta={"action_type": "LOAD_INCREASE"})
        s.add(note); s.commit(); s.refresh(note)

        return {"program_id": prog.id, "note_id": note.id, "bench_id": bench.id,
                "hip_thrust_id": hip_thrust.id, "incline_id": incline.id,
                "bench_te_id": bench_te.id, "ht_te_id": ht_te.id}


# ---------------------------------------------------------------------------
# GET /programs/{id}/slots
# ---------------------------------------------------------------------------

def test_program_slots_lists_day_tier_movement():
    client, engine = _client()
    ctx = _seed(engine)
    resp = client.get(f"/programs/{ctx['program_id']}/slots")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    by_slot = {row["slot_id"]: row for row in body}

    bench_row = by_slot["d1_t1"]
    assert bench_row["tier_exercise_id"] == ctx["bench_te_id"]
    assert bench_row["day_role"] == "D1 Upper Push"
    assert bench_row["tier_label"] == "T1"
    assert bench_row["movement_id"] == ctx["bench_id"]
    assert bench_row["movement_name"] == "Bench Press [PB]"
    assert bench_row["current_rep_low"] == 5
    assert bench_row["current_rep_high"] == 5

    ht_row = by_slot["d1_t2a"]
    assert ht_row["tier_exercise_id"] == ctx["ht_te_id"]
    assert ht_row["tier_label"] == "T2"
    assert ht_row["movement_id"] == ctx["hip_thrust_id"]
    assert ht_row["movement_name"] == "Hip Thrust [PB]"
    assert ht_row["current_rep_low"] == 8
    assert ht_row["current_rep_high"] == 12
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# POST /notes/{id}/apply — explicit slot + override
# ---------------------------------------------------------------------------

def test_apply_load_override_on_explicit_slot_not_notes_own_movement():
    client, engine = _client()
    ctx = _seed(engine)
    # The note's own movement_id is hip_thrust, and we apply to that same slot's
    # tier_exercise_id explicitly (not derived from the note) — proves the
    # server trusts the client-sent slot, not any resolve-from-note inference.
    resp = client.post(f"/notes/{ctx['note_id']}/apply", json={
        "tier_exercise_id": ctx["ht_te_id"], "override_type": "LOAD", "load_delta": 10,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["tier_exercise_id"] == ctx["ht_te_id"]
    assert body["override_type"] == "LOAD"

    with DBSession(engine) as s:
        ov = s.exec(select(SlotMovementOverride)).one()
        assert ov.active is True
        assert ov.tier_exercise_id == ctx["ht_te_id"]
        assert ov.override_type == OverrideType.LOAD
        assert ov.load_delta == 10
        assert ov.load_absolute is None
        # Placeholder convention (Task 1): override_movement_id is NOT NULL, so a
        # LOAD/REPS row sets it to the slot's own movement_id — never read for
        # non-MOVEMENT types.
        assert ov.override_movement_id == ctx["hip_thrust_id"]
        n = s.get(Note, ctx["note_id"])
        assert n.confirmed is True
        assert n.applied is True
    app.dependency_overrides.clear()


def test_apply_movement_override():
    client, engine = _client()
    ctx = _seed(engine)
    resp = client.post(f"/notes/{ctx['note_id']}/apply", json={
        "tier_exercise_id": ctx["bench_te_id"], "override_type": "MOVEMENT",
        "override_movement_id": ctx["incline_id"],
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["override_type"] == "MOVEMENT"

    with DBSession(engine) as s:
        ov = s.exec(select(SlotMovementOverride)).one()
        assert ov.override_type == OverrideType.MOVEMENT
        assert ov.tier_exercise_id == ctx["bench_te_id"]
        assert ov.override_movement_id == ctx["incline_id"]
    app.dependency_overrides.clear()


def test_apply_reps_override():
    client, engine = _client()
    ctx = _seed(engine)
    resp = client.post(f"/notes/{ctx['note_id']}/apply", json={
        "tier_exercise_id": ctx["ht_te_id"], "override_type": "REPS",
        "rep_low": 10, "rep_high": 15,
    })
    assert resp.status_code == 200
    with DBSession(engine) as s:
        ov = s.exec(select(SlotMovementOverride)).one()
        assert ov.override_type == OverrideType.REPS
        assert ov.rep_low == 10
        assert ov.rep_high == 15
        assert ov.override_movement_id == ctx["hip_thrust_id"]
    app.dependency_overrides.clear()


def test_apply_load_both_delta_and_absolute_400():
    client, engine = _client()
    ctx = _seed(engine)
    resp = client.post(f"/notes/{ctx['note_id']}/apply", json={
        "tier_exercise_id": ctx["ht_te_id"], "override_type": "LOAD",
        "load_delta": 10, "load_absolute": 100,
    })
    assert resp.status_code == 400
    app.dependency_overrides.clear()


def test_apply_load_neither_delta_nor_absolute_400():
    client, engine = _client()
    ctx = _seed(engine)
    resp = client.post(f"/notes/{ctx['note_id']}/apply", json={
        "tier_exercise_id": ctx["ht_te_id"], "override_type": "LOAD",
    })
    assert resp.status_code == 400
    app.dependency_overrides.clear()


def test_apply_reps_neither_low_nor_high_400():
    client, engine = _client()
    ctx = _seed(engine)
    resp = client.post(f"/notes/{ctx['note_id']}/apply", json={
        "tier_exercise_id": ctx["ht_te_id"], "override_type": "REPS",
    })
    assert resp.status_code == 400
    app.dependency_overrides.clear()


def test_apply_movement_missing_target_movement_404():
    client, engine = _client()
    ctx = _seed(engine)
    resp = client.post(f"/notes/{ctx['note_id']}/apply", json={
        "tier_exercise_id": ctx["bench_te_id"], "override_type": "MOVEMENT",
        "override_movement_id": 999999,
    })
    assert resp.status_code == 404
    app.dependency_overrides.clear()


def test_apply_unknown_tier_exercise_404():
    client, engine = _client()
    ctx = _seed(engine)
    resp = client.post(f"/notes/{ctx['note_id']}/apply", json={
        "tier_exercise_id": 999999, "override_type": "LOAD", "load_delta": 10,
    })
    assert resp.status_code == 404
    app.dependency_overrides.clear()


def test_apply_unknown_note_404():
    client, engine = _client()
    ctx = _seed(engine)
    resp = client.post("/notes/999999/apply", json={
        "tier_exercise_id": ctx["ht_te_id"], "override_type": "LOAD", "load_delta": 10,
    })
    assert resp.status_code == 404
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# GET /overrides — generalized rendering
# ---------------------------------------------------------------------------

def test_overrides_list_renders_load_without_bogus_movement_name():
    client, engine = _client()
    ctx = _seed(engine)
    client.post(f"/notes/{ctx['note_id']}/apply", json={
        "tier_exercise_id": ctx["ht_te_id"], "override_type": "LOAD", "load_delta": 10,
    })
    body = client.get("/overrides").json()
    assert len(body) == 1
    row = body[0]
    assert row["override_type"] == "LOAD"
    assert row["movement_name"] == "Hip Thrust [PB]"
    assert row["load_delta"] == 10
    assert row["load_absolute"] is None
    # A LOAD override does NOT render a to_movement_name — override_movement_id
    # is only a NOT-NULL placeholder for this type, never a real target.
    assert row.get("to_movement_name") is None
    assert row["source_note_text"] == "bump hip thrust +10"
    app.dependency_overrides.clear()


def test_overrides_list_renders_movement_with_to_movement_name():
    client, engine = _client()
    ctx = _seed(engine)
    client.post(f"/notes/{ctx['note_id']}/apply", json={
        "tier_exercise_id": ctx["bench_te_id"], "override_type": "MOVEMENT",
        "override_movement_id": ctx["incline_id"],
    })
    row = client.get("/overrides").json()[0]
    assert row["override_type"] == "MOVEMENT"
    assert row["movement_name"] == "Bench Press [PB]"
    assert row["to_movement_name"] == "Incline Bench [PB]"
    app.dependency_overrides.clear()


def test_overrides_list_renders_reps():
    client, engine = _client()
    ctx = _seed(engine)
    client.post(f"/notes/{ctx['note_id']}/apply", json={
        "tier_exercise_id": ctx["ht_te_id"], "override_type": "REPS",
        "rep_low": 10, "rep_high": 15,
    })
    row = client.get("/overrides").json()[0]
    assert row["override_type"] == "REPS"
    assert row["movement_name"] == "Hip Thrust [PB]"
    assert row["rep_low"] == 10
    assert row["rep_high"] == 15
    assert row.get("to_movement_name") is None
    app.dependency_overrides.clear()


def test_overrides_revert_still_works():
    client, engine = _client()
    ctx = _seed(engine)
    apply_resp = client.post(f"/notes/{ctx['note_id']}/apply", json={
        "tier_exercise_id": ctx["ht_te_id"], "override_type": "LOAD", "load_delta": 10,
    })
    ov_id = apply_resp.json()["id"]
    revert_resp = client.post(f"/overrides/{ov_id}/revert")
    assert revert_resp.status_code == 200
    assert revert_resp.json()["active"] is False
    assert client.get("/overrides").json() == []
    app.dependency_overrides.clear()
