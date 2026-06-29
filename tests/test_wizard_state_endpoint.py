"""Tests for GET /programs/{id}/wizard-state — the wizard read surface.

The endpoint renders compute_load_trust per program movement (the SECOND of the
three keystone consumers). It enumerates the program's distinct movements (via
TierExercises + MesoRotations), excludes bodyweight (load_field None), and reports
needs_attention_count (UNKNOWN + STALE) + ready_to_start.

NO from __future__ import annotations (project-wide constraint).
"""
from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlmodel import Session as DbSession, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from ironlog.api.app import app, get_session
from ironlog.generation.load_trust import compute_load_trust
from ironlog.models.library import Movement, MovementState
from ironlog.models.program import (
    MesoRotation, Program, ProgramDay, Tier, TierExercise, TierKind,
)
from ironlog.models.enums import ProgressionMode
import ironlog.models  # noqa: F401 — register all tables


def _client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)

    def _override():
        with DbSession(engine) as s:
            yield s

    app.dependency_overrides[get_session] = _override
    return TestClient(app), engine


def _mv(s, name, mode=ProgressionMode.LADDER):
    m = Movement(name=name, base_name=name, progression_mode=mode)
    s.add(m)
    s.commit()
    s.refresh(m)
    return m


def _program_with(engine, movements, extra_meso=None):
    """Seed one Program/Day/Tier; attach a TierExercise for each (movement, role).

    movements: list of (Movement, tier_role).
    extra_meso: optional Movement attached only via a MesoRotation row (no TE of
                its own) — proves meso-rotation enumeration.
    Returns the program id.
    """
    with DbSession(engine) as s:
        prog = Program(name="Phase 1", phase="P1", duration_weeks=4)
        s.add(prog)
        s.commit()
        s.refresh(prog)
        day = ProgramDay(program_id=prog.id, day_index=1, day_role="D1 Upper Push")
        s.add(day)
        s.commit()
        s.refresh(day)
        tier = Tier(program_day_id=day.id, tier_label="T1", tier_order=1,
                    tier_kind=TierKind.T1_STRAIGHT)
        s.add(tier)
        s.commit()
        s.refresh(tier)
        first_te = None
        for i, (m, role) in enumerate(movements):
            te = TierExercise(tier_id=tier.id, slot_id=f"d1_t{i}", movement_id=m.id,
                              exercise_order=i, tier_role=role)
            s.add(te)
            s.commit()
            s.refresh(te)
            if first_te is None:
                first_te = te
        if extra_meso is not None:
            s.add(MesoRotation(tier_exercise_id=first_te.id, meso_number=2,
                               movement_id=extra_meso.id))
            s.commit()
        return prog.id


def test_wizard_state_fresh_unknown_bodyweight():
    client, engine = _client()
    now = datetime.utcnow()
    with DbSession(engine) as s:
        fresh = _mv(s, "Bench Press [PB]")
        unknown = _mv(s, "Overhead Press [PB]")
        bw = _mv(s, "Ab Wheel", mode=ProgressionMode.PROTOCOL)
        # configured -> FRESH
        s.add(MovementState(movement_id=fresh.id, current_load=185.0, confirmed_at=now))
        s.commit()
        fresh_id, unknown_id, bw_id = fresh.id, unknown.id, bw.id

    with DbSession(engine) as s:
        rows = [(s.get(Movement, fresh_id), "anchor"),
                (s.get(Movement, unknown_id), "free"),
                (s.get(Movement, bw_id), "free")]
    pid = _program_with(engine, rows)

    r = client.get(f"/programs/{pid}/wizard-state")
    assert r.status_code == 200
    body = r.json()

    assert body["program_id"] == pid
    assert body["needs_attention_count"] == 1          # only the UNKNOWN one
    assert body["ready_to_start"] is False

    by_id = {m["movement_id"]: m for m in body["movements"]}
    # bodyweight excluded entirely
    assert bw_id not in by_id
    assert set(by_id) == {fresh_id, unknown_id}

    # UNKNOWN movement: prefill null, trust UNKNOWN, correct field/unit
    um = by_id[unknown_id]
    assert um["trust"] == "UNKNOWN"
    assert um["prefill_value"] is None
    assert um["load_field"] == "current_load"
    assert um["unit_hint"] == "lb"

    # FRESH movement: prefilled value, trust FRESH
    fm = by_id[fresh_id]
    assert fm["trust"] == "FRESH"
    assert fm["prefill_value"] == 185.0

    app.dependency_overrides.clear()


def test_wizard_state_trust_matches_shared_function():
    """The per-movement trust the endpoint reports must equal compute_load_trust —
    the spine: the wizard surface and generation cannot disagree."""
    client, engine = _client()
    now = datetime.utcnow()
    with DbSession(engine) as s:
        fresh = _mv(s, "Squat [PB]")
        stale = _mv(s, "Deadlift [PB]")
        unknown = _mv(s, "Row [PB]")
        s.add(MovementState(movement_id=fresh.id, current_load=225.0, confirmed_at=now))
        s.add(MovementState(movement_id=stale.id, current_load=315.0,
                            confirmed_at=now - timedelta(days=45)))
        s.commit()
        ids = [fresh.id, stale.id, unknown.id]
    with DbSession(engine) as s:
        rows = [(s.get(Movement, i), "free") for i in ids]
    pid = _program_with(engine, rows)

    r = client.get(f"/programs/{pid}/wizard-state")
    assert r.status_code == 200
    by_id = {m["movement_id"]: m for m in r.json()["movements"]}

    with DbSession(engine) as s:
        for mid in ids:
            mv = s.get(Movement, mid)
            st = s.exec(select(MovementState)
                        .where(MovementState.movement_id == mid)).first()
            expected = compute_load_trust(mv, st, s, datetime.utcnow())
            assert by_id[mid]["trust"] == expected.trust.value

    app.dependency_overrides.clear()


def test_wizard_state_includes_meso_rotation_movements():
    """A movement referenced ONLY via a MesoRotation (not a TierExercise of its
    own) must still appear in the wizard."""
    client, engine = _client()
    with DbSession(engine) as s:
        anchor = _mv(s, "Belt Squat [PB]")
        meso2 = _mv(s, "Back Squat [PB]")          # appears only via MesoRotation
        ids = (anchor.id, meso2.id)
    with DbSession(engine) as s:
        a = s.get(Movement, ids[0])
        m2 = s.get(Movement, ids[1])
    pid = _program_with(engine, [(a, "anchor")], extra_meso=m2)

    r = client.get(f"/programs/{pid}/wizard-state")
    assert r.status_code == 200
    seen = {m["movement_id"] for m in r.json()["movements"]}
    assert ids[0] in seen and ids[1] in seen      # both enumerated

    app.dependency_overrides.clear()


def test_wizard_state_404_when_program_missing():
    client, engine = _client()
    assert client.get("/programs/9999/wizard-state").status_code == 404
    app.dependency_overrides.clear()


def test_wizard_state_ready_when_all_fresh():
    client, engine = _client()
    now = datetime.utcnow()
    with DbSession(engine) as s:
        a = _mv(s, "Incline Press [PB]")
        b = _mv(s, "Pull-up [TOWER]", mode=ProgressionMode.ASSISTED)
        s.add(MovementState(movement_id=a.id, current_load=135.0, confirmed_at=now))
        # assist_level == 0 is a VALID configured (FRESH) state, not UNKNOWN
        s.add(MovementState(movement_id=b.id, assist_level=0.0, confirmed_at=now))
        s.commit()
        ids = [a.id, b.id]
    with DbSession(engine) as s:
        rows = [(s.get(Movement, i), "free") for i in ids]
    pid = _program_with(engine, rows)

    body = client.get(f"/programs/{pid}/wizard-state").json()
    assert body["needs_attention_count"] == 0
    assert body["ready_to_start"] is True
    by_id = {m["movement_id"]: m for m in body["movements"]}
    assert by_id[ids[1]]["unit_hint"] == "assist"
    assert by_id[ids[1]]["load_field"] == "assist_level"

    app.dependency_overrides.clear()
