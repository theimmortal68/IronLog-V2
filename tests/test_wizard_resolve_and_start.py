"""Tests for POST /programs/{id}/wizard-resolve + POST /programs/{id}/start.

These are the wizard's WRITE side + the program-start completion gate — the THIRD
and final consumer of the compute_load_trust keystone. The spine test here proves
all three surfaces (generation resolver, wizard-state endpoint, completion gate)
share the SAME function and therefore CANNOT disagree: wizard-finishing ⇒ clean
generation, by construction.

NO from __future__ import annotations (project-wide constraint).
"""
from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlmodel import Session as DbSession, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from ironlog.api.app import app, get_session
from ironlog.generation.assembler import resolve_start_load
from ironlog.generation.load_trust import LoadTrust, compute_load_trust
from ironlog.models.library import EngineState, Movement, MovementState
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


def _program_with(engine, movements):
    """Seed one Program/Day/Tier with a TierExercise per (Movement, role).
    Returns the program id."""
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
        for i, (m, role) in enumerate(movements):
            te = TierExercise(tier_id=tier.id, slot_id=f"d1_t{i}", movement_id=m.id,
                              exercise_order=i, tier_role=role)
            s.add(te)
            s.commit()
        return prog.id


# ---------------------------------------------------------------------------
# §7.3 — confirmed_at is honest: stamp ONLY on touched movements
# ---------------------------------------------------------------------------

def test_resolve_stamps_confirmed_at_only_on_touched():
    client, engine = _client()
    b_confirmed = datetime.utcnow() - timedelta(days=5)   # FRESH, distinct sentinel
    with DbSession(engine) as s:
        a = _mv(s, "Bench Press [PB]")          # UNKNOWN — will be resolved
        b = _mv(s, "Squat [PB]")                # FRESH — untouched, must NOT change
        s.add(MovementState(movement_id=b.id, current_load=315.0,
                            confirmed_at=b_confirmed))
        s.commit()
        a_id, b_id = a.id, b.id
    with DbSession(engine) as s:
        rows = [(s.get(Movement, a_id), "anchor"), (s.get(Movement, b_id), "free")]
    pid = _program_with(engine, rows)

    r = client.post(f"/programs/{pid}/wizard-resolve",
                    json={"resolutions": [{"movement_id": a_id, "value": 185.0}]})
    assert r.status_code == 200
    body = r.json()
    assert body["resolved"] == 1
    assert body["needs_attention_count"] == 0     # A now FRESH, B already FRESH
    assert body["ready_to_start"] is True

    with DbSession(engine) as s:
        st_a = s.exec(select(MovementState)
                      .where(MovementState.movement_id == a_id)).first()
        st_b = s.exec(select(MovementState)
                      .where(MovementState.movement_id == b_id)).first()
        # A: load written + confirmed_at stamped (fresh, > b_confirmed)
        assert st_a is not None
        assert st_a.current_load == 185.0
        assert st_a.confirmed_at is not None
        assert st_a.confirmed_at > b_confirmed
        # B: UNTOUCHED — confirmed_at unchanged (catches stamp-everything)
        assert st_b.confirmed_at == b_confirmed
        assert st_b.current_load == 315.0

    app.dependency_overrides.clear()


def test_resolve_assisted_writes_assist_level_not_current_load():
    client, engine = _client()
    with DbSession(engine) as s:
        a = _mv(s, "Pull-up [TOWER]", mode=ProgressionMode.ASSISTED)
        a_id = a.id
    with DbSession(engine) as s:
        rows = [(s.get(Movement, a_id), "anchor")]
    pid = _program_with(engine, rows)

    r = client.post(f"/programs/{pid}/wizard-resolve",
                    json={"resolutions": [{"movement_id": a_id, "value": 20.0}]})
    assert r.status_code == 200
    assert r.json()["ready_to_start"] is True

    with DbSession(engine) as s:
        st = s.exec(select(MovementState)
                    .where(MovementState.movement_id == a_id)).first()
        # per load_field_for_mode(ASSISTED) -> assist_level, NOT current_load
        assert st.assist_level == 20.0
        assert st.current_load is None
        assert st.confirmed_at is not None

    app.dependency_overrides.clear()


def test_resolve_preserves_other_movementstate_fields():
    """Two-writer boundary: resolve writes ONLY the load field + confirmed_at,
    never e1rm / calibration_status / counters."""
    from ironlog.models.enums import CalibrationStatus
    client, engine = _client()
    with DbSession(engine) as s:
        a = _mv(s, "Deadlift [PB]")
        s.add(MovementState(movement_id=a.id, e1rm=405.0,
                            calibration_status=CalibrationStatus.MEASURED,
                            current_increment_tier=3,
                            consecutive_ceiling_sessions=2))
        s.commit()
        a_id = a.id
    with DbSession(engine) as s:
        rows = [(s.get(Movement, a_id), "anchor")]
    pid = _program_with(engine, rows)

    client.post(f"/programs/{pid}/wizard-resolve",
                json={"resolutions": [{"movement_id": a_id, "value": 365.0}]})

    with DbSession(engine) as s:
        st = s.exec(select(MovementState)
                    .where(MovementState.movement_id == a_id)).first()
        assert st.current_load == 365.0          # written
        assert st.confirmed_at is not None       # stamped
        assert st.e1rm == 405.0                   # untouched
        assert st.calibration_status == CalibrationStatus.MEASURED
        assert st.current_increment_tier == 3
        assert st.consecutive_ceiling_sessions == 2

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Completion gate — /start refuses until ready, then activates
# ---------------------------------------------------------------------------

def test_start_gate_refuses_then_activates():
    client, engine = _client()
    with DbSession(engine) as s:
        a = _mv(s, "Overhead Press [PB]")       # UNKNOWN
        a_id = a.id
    with DbSession(engine) as s:
        rows = [(s.get(Movement, a_id), "anchor")]
    pid = _program_with(engine, rows)

    # gate closed: one UNKNOWN remains -> refuse
    r = client.post(f"/programs/{pid}/start")
    assert r.status_code == 200
    assert r.json() == {"program_id": pid, "started": False, "active": False}

    with DbSession(engine) as s:
        es = s.get(EngineState, 1)
        assert es is None or es.active_program_id != pid
        assert s.get(Program, pid).started_at is None

    # resolve the UNKNOWN -> gate clears
    client.post(f"/programs/{pid}/wizard-resolve",
                json={"resolutions": [{"movement_id": a_id, "value": 95.0}]})

    r = client.post(f"/programs/{pid}/start")
    assert r.status_code == 200
    assert r.json() == {"program_id": pid, "started": True, "active": True}

    with DbSession(engine) as s:
        es = s.get(EngineState, 1)
        assert es is not None and es.active_program_id == pid
        assert s.get(Program, pid).started_at is not None

    app.dependency_overrides.clear()


def test_start_404_when_program_missing():
    client, engine = _client()
    assert client.post("/programs/9999/start").status_code == 404
    assert client.post("/programs/9999/wizard-resolve",
                       json={"resolutions": []}).status_code == 404
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# §7.2 + §7.6 — THE SPINE: the three surfaces share compute_load_trust and so
# CANNOT disagree. Wizard-finishing ⇒ clean generation, by construction.
# ---------------------------------------------------------------------------

def test_spine_wizard_finish_guarantees_clean_generation():
    """THE SPINE — red-against-reimplementation.

    The three surfaces (wizard-state endpoint, compute_load_trust, generation's
    resolve_start_load) must AGREE because they share the one keystone function.
    To make this genuinely red against a naive reimplementation (and not merely
    green on trivially-FRESH inputs), the cross-surface comparison spans the two
    DIVERGENCE EDGES where a reimpl most likely drifts:

      1. ASSISTED assist_level == 0 (the IS-NULL-not-falsy edge): 0.0 is a REAL
         value -> FRESH. A naive `if value:` / falsy presence check would call it
         UNKNOWN -> diverges from compute_load_trust.
      2. Derived-ratio movement (start_ratio + derived_from_id, own current_load
         None, anchor MovementState.e1rm set): compute_load_trust resolves
         start_ratio * anchor.e1rm -> FRESH (value present). A reimpl missing the
         derived-ratio path would call it UNKNOWN -> diverges.

    A surface that secretly reimplemented trust would fail one of these.
    """
    client, engine = _client()
    with DbSession(engine) as s:
        ladder = _mv(s, "Incline Press [PB]")               # current_load
        assisted = _mv(s, "Nordic Curl [TOWER]", mode=ProgressionMode.ASSISTED)
        # EDGE 1: ASSISTED resolved to assist_level == 0 (IS-NULL-not-falsy).
        assisted_zero = _mv(s, "Ring Dip [TOWER]", mode=ProgressionMode.ASSISTED)
        bodyweight = _mv(s, "Ab Wheel", mode=ProgressionMode.PROTOCOL)  # never asked
        # Anchor for the derived movement — NOT part of the program. Its
        # MovementState carries the e1rm the derived ratio multiplies against.
        anchor = _mv(s, "Back Squat [PB] (anchor)")
        s.add(MovementState(movement_id=anchor.id, e1rm=200.0))
        s.commit()
        # EDGE 2: derived-ratio LADDER movement — no own current_load, resolves
        # via start_ratio * anchor.e1rm (0.8 * 200 = 160.0).
        derived = Movement(
            name="Front Squat [PB]", base_name="Front Squat [PB]",
            progression_mode=ProgressionMode.LADDER,
            start_ratio=0.8, derived_from_id=anchor.id)
        s.add(derived)
        s.commit()
        s.refresh(derived)
        # Pre-seed derived's OWN state: current_load None (forces the derived-ratio
        # path) + recent confirmed_at so _recency makes it FRESH (not STALE).
        s.add(MovementState(movement_id=derived.id, current_load=None,
                            confirmed_at=datetime.utcnow() - timedelta(days=1)))
        s.commit()
        ids = [ladder.id, assisted.id, assisted_zero.id, bodyweight.id, derived.id]
    with DbSession(engine) as s:
        rows = [(s.get(Movement, i), "free") for i in ids]
    pid = _program_with(engine, rows)
    ladder_id, assisted_id, assisted_zero_id, bw_id, derived_id = ids

    # Before resolving: 3 UNKNOWN (ladder, assisted, assisted_zero); derived is
    # already FRESH via the derived-ratio path; bodyweight excluded -> needs == 3.
    state0 = client.get(f"/programs/{pid}/wizard-state").json()
    assert state0["ready_to_start"] is False
    assert state0["needs_attention_count"] == 3
    assert client.post(f"/programs/{pid}/start").json()["started"] is False

    # Resolve the three UNKNOWN load-bearing movements — note assist_level == 0.
    r = client.post(f"/programs/{pid}/wizard-resolve", json={"resolutions": [
        {"movement_id": ladder_id, "value": 145.0},
        {"movement_id": assisted_id, "value": 10.0},
        {"movement_id": assisted_zero_id, "value": 0.0},   # IS-NULL-not-falsy edge
    ]})
    assert r.json()["resolved"] == 3
    assert r.json()["needs_attention_count"] == 0
    assert r.json()["ready_to_start"] is True

    # (a) wizard-state endpoint now reports ready / 0 needs-attention.
    state1 = client.get(f"/programs/{pid}/wizard-state").json()
    assert state1["needs_attention_count"] == 0
    assert state1["ready_to_start"] is True

    # (b) /start succeeds — the completion gate clears.
    assert client.post(f"/programs/{pid}/start").json() == {
        "program_id": pid, "started": True, "active": True}

    # (c) cross-surface: for EVERY load-bearing movement (including the two
    #     divergence edges) the wizard-state endpoint's trust EQUALS
    #     compute_load_trust's verdict (FRESH), and generation's resolver returns
    #     a real value (NOT None). A reimpl that mis-handled assist_level==0
    #     (falsy presence) or omitted the derived-ratio path would diverge here.
    wiz_trust = {m["movement_id"]: m["trust"] for m in state1["movements"]}
    with DbSession(engine) as s:
        for mid in (ladder_id, assisted_id, assisted_zero_id, derived_id):
            mv = s.get(Movement, mid)
            st = s.exec(select(MovementState)
                        .where(MovementState.movement_id == mid)).first()
            verdict = compute_load_trust(mv, st, s, datetime.utcnow())
            # spine: wizard surface verdict == generation surface verdict == FRESH
            assert wiz_trust[mid] == verdict.trust.value == LoadTrust.FRESH.value
            # generation prescribes a real number, not needs-calibration.
            # `is not None` is load-bearing: assisted_zero resolves to 0.0 (falsy
            # but VALID) and derived resolves to 160.0.
            assert resolve_start_load(mv, st, s) is not None
        # derived specifically: generation returns the derived value, not None.
        derived_mv = s.get(Movement, derived_id)
        derived_st = s.exec(select(MovementState)
                            .where(MovementState.movement_id == derived_id)).first()
        assert resolve_start_load(derived_mv, derived_st, s) == 160.0
        # bodyweight: legitimately carries no load (None), is NOT needs-calibration
        bw = s.get(Movement, bw_id)
        bw_st = s.exec(select(MovementState)
                       .where(MovementState.movement_id == bw_id)).first()
        assert compute_load_trust(bw, bw_st, s, datetime.utcnow()).trust == LoadTrust.FRESH

    app.dependency_overrides.clear()
