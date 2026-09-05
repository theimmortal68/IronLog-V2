from datetime import date
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool
import pytest

import ironlog.models
from ironlog.api.app import app, get_session
from ironlog.models.periodization import (
    Macrocycle, Mesocycle, MesocycleTemplate, Microcycle,
    BodyCompState, BodyCompStateValue, RecoveryStatus, RecoveryStatusValue,
    DeloadState, PlanStatus, MicrocycleLifecycleStatus, MicrocycleDriftStatus
)
from ironlog.models.enums import Objective

@pytest.fixture
def engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    return eng

@pytest.fixture
def client(engine):
    def get_session_override():
        with Session(engine) as session:
            yield session
    app.dependency_overrides[get_session] = get_session_override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()

def test_get_current_plan_empty(client):
    response = client.get("/training/plan/current")
    assert response.status_code == 200
    data = response.json()
    assert data["macrocycle"] is None
    assert data["mesocycle"] is None
    assert data["current_active_microcycle"] is None
    assert data["body_comp_state"] is None
    assert data["recovery_status"] is None
    assert data["deload_state"] is None
    assert data["resolver_trace"] is None

def test_get_current_plan_populated(client, engine):
    today = date.today()
    with Session(engine) as db:
        macro = Macrocycle(
            goal="Get huge",
            planned_start_date=date(2026, 1, 1),
            planned_end_date=date(2026, 12, 31),
            status=PlanStatus.ACTIVE
        )
        db.add(macro)
        db.commit()
        db.refresh(macro)
        
        tmpl = MesocycleTemplate(name="Block 1", postures=["PUSH", "PUSH", "PUSH", "DELOAD"])
        db.add(tmpl)
        db.commit()
        db.refresh(tmpl)
        
        meso = Mesocycle(
            template_id=tmpl.id,
            macrocycle_id=macro.id,
            ordinal=1,
            planned_start_date=date(2026, 1, 1),
            planned_end_date=date(2026, 1, 31),
            status=PlanStatus.ACTIVE
        )
        db.add(meso)
        db.commit()
        db.refresh(meso)
        
        micro = Microcycle(
            mesocycle_id=meso.id,
            ordinal=1,
            planned_start_date=today,
            planned_end_date=today,
            expected_sessions=4,
            completed_sessions=0,
            lifecycle_status=MicrocycleLifecycleStatus.ACTIVE,
            drift_status=MicrocycleDriftStatus.ON_TIME,
            drift_days=0,
            planned_posture="PUSH",
        )
        db.add(micro)
        
        bc = BodyCompState(
            state=BodyCompStateValue.CUT,
            effective_from=today,
        )
        db.add(bc)
        
        rec = RecoveryStatus(
            as_of_date=today,
            status=RecoveryStatusValue.CAUTION,
        )
        db.add(rec)
        
        db.commit()
        macro_id = macro.id
        meso_id = meso.id
        micro_id = micro.id

    response = client.get("/training/plan/current")
    assert response.status_code == 200
    data = response.json()
    assert data["macrocycle"]["id"] == macro_id
    assert data["mesocycle"]["id"] == meso_id
    assert data["current_active_microcycle"]["id"] == micro_id
    assert data["body_comp_state"] == "CUT"
    assert data["recovery_status"] == "CAUTION"
    assert data["deload_state"] is None
    
    assert data["resolver_trace"] is not None
    assert len(data["resolver_trace"]) > 0

def test_get_macrocycle_not_found(client):
    response = client.get("/training/macrocycles/999")
    assert response.status_code == 404

def test_get_macrocycle_happy(client, engine):
    with Session(engine) as db:
        macro = Macrocycle(
            goal="Get huge",
            planned_start_date=date(2026, 1, 1),
            planned_end_date=date(2026, 12, 31),
            status=PlanStatus.ACTIVE
        )
        db.add(macro)
        db.commit()
        db.refresh(macro)
        
        tmpl1 = MesocycleTemplate(name="Block 1", postures=["PUSH"])
        tmpl2 = MesocycleTemplate(name="Block 2", postures=["INTENSIFY"])
        db.add(tmpl1)
        db.add(tmpl2)
        db.commit()
        db.refresh(tmpl1)
        db.refresh(tmpl2)
        
        meso1 = Mesocycle(
            template_id=tmpl1.id,
            macrocycle_id=macro.id,
            ordinal=1,
            planned_start_date=date(2026, 1, 1),
            planned_end_date=date(2026, 1, 31),
            status=PlanStatus.COMPLETE
        )
        meso2 = Mesocycle(
            template_id=tmpl2.id,
            macrocycle_id=macro.id,
            ordinal=2,
            planned_start_date=date(2026, 2, 1),
            planned_end_date=date(2026, 2, 28),
            status=PlanStatus.ACTIVE
        )
        db.add(meso1)
        db.add(meso2)
        db.commit()
        macro_id = macro.id
        
    response = client.get(f"/training/macrocycles/{macro_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == macro_id
    assert data["goal"] == "Get huge"
    assert len(data["mesocycles"]) == 2
    assert data["mesocycles"][0]["template_name"] == "Block 1"
    assert data["mesocycles"][1]["template_name"] == "Block 2"
    assert data["mesocycles"][0]["ordinal"] == 1
    assert data["mesocycles"][1]["ordinal"] == 2

def test_get_current_plan_blocked_nullifies_deload_and_resolver_trace(client, engine):
    """Design doc: resolve_envelope() (and therefore deload_state/resolver_trace)
    must NEVER be computed when blocked_reason is set -- even if a `micro` object
    with real BodyCompState/RecoveryStatus data is available. Reuses the same
    monkeypatch-at-source pattern as test_get_current_plan_waiting_for_microcycle_start
    (get_current_plan imports reconcile_current_training_state locally, so the
    patch target is ironlog.engine.advancement, not ironlog.api.app)."""
    from ironlog.models.periodization import (
        Macrocycle, Mesocycle, MesocycleTemplate, Microcycle, MicrocycleLifecycleStatus,
        MacroPlanningState, PlanStatus, BodyCompState, BodyCompStateValue, RecoveryStatus,
        RecoveryStatusValue,
    )
    import ironlog.engine.advancement
    from ironlog.engine.advancement import ReconcileResult
    from sqlmodel import Session

    with Session(engine) as db:
        mac = Macrocycle(goal="Test", planned_start_date=date(2026, 1, 1),
                         planning_state=MacroPlanningState.ACTIVE, status=PlanStatus.ACTIVE)
        db.add(mac)
        db.commit()
        db.refresh(mac)

        tmpl = MesocycleTemplate(name="Temp Blocked", postures=["PUSH"])
        db.add(tmpl)
        db.commit()
        db.refresh(tmpl)

        meso = Mesocycle(macrocycle_id=mac.id, template_id=tmpl.id, ordinal=1,
                         planned_start_date=date(2026, 1, 1), planned_end_date=date(2026, 1, 31))
        db.add(meso)
        db.commit()
        db.refresh(meso)

        micro = Microcycle(mesocycle_id=meso.id, ordinal=1,
                           planned_start_date=date(2026, 1, 1),
                           planned_end_date=date(2026, 1, 7),
                           expected_sessions=4,
                           lifecycle_status=MicrocycleLifecycleStatus.INCOMPLETE,
                           planned_posture="PUSH")
        db.add(micro)
        db.commit()
        db.refresh(micro)

        # Real BodyCompState/RecoveryStatus rows exist -- if the blocked_reason
        # guard were missing, resolve_envelope() would have real inputs to run
        # against and would NOT naturally return None on its own.
        bc = BodyCompState(state=BodyCompStateValue.MAINTENANCE, effective_from=date(2026, 1, 1))
        rec = RecoveryStatus(as_of_date=date.today(), status=RecoveryStatusValue.NORMAL)
        db.add(bc)
        db.add(rec)
        db.commit()

        micro_id = micro.id
        meso_id = meso.id

    original = ironlog.engine.advancement.reconcile_current_training_state
    try:
        def mock_reconcile(db):
            return ReconcileResult(
                blocked_reason="INCOMPLETE_MICROCYCLE",
                final_microcycle_id=micro_id,
                final_mesocycle_id=meso_id,
            )
        ironlog.engine.advancement.reconcile_current_training_state = mock_reconcile

        response = client.get("/training/plan/current")
        assert response.status_code == 200
        data = response.json()
        assert data["blocked_reason"] == "INCOMPLETE_MICROCYCLE"
        assert data["resolver_trace"] is None
        assert data["deload_state"] is None
    finally:
        ironlog.engine.advancement.reconcile_current_training_state = original
