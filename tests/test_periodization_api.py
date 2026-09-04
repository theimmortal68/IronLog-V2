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
    assert data["microcycle"] is None
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
    assert data["microcycle"]["id"] == micro_id
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
