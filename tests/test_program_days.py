# tests/test_program_days.py
"""GET /programs/{id}/days — training day_roles in day_index order, rest days excluded.

The brief's illustrative `client`/`seeded_program_id` fixtures don't exist in this
repo. Every API test file that needs a real seeded program (test_generate_preview.py)
builds its own StaticPool + check_same_thread=False in-memory engine and seeds it
via seed.seed() + seed_phase1_program() — mirroring tests/conftest.py's gen_db
fixture but safe for TestClient's worker-thread dispatch. This file follows that
same pattern.

Expected day_role strings are read verbatim from
ironlog/generation/program_seed.py's days_spec: 5 training days
("D1 Upper Push", "D2 Lower A", "D4 Upper Pull", "D5 Lower B", "D6 Weak Points");
day 3 and day 7 are rest days with day_role="" and must be excluded.

NO from __future__ import annotations (project-wide constraint).
"""
import importlib

from fastapi.testclient import TestClient
from sqlmodel import Session as DbSession, create_engine, select
from sqlmodel.pool import StaticPool

from ironlog.api.app import app, get_session
from ironlog.models.program import Program
import ironlog.models  # noqa: F401 — ensure all tables registered


def _client():
    """Seed a full library + Phase 1 program on a StaticPool in-memory engine,
    then wire the app's get_session dependency to it. Returns (client, program_id).
    """
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    import ironlog.db as db
    db.engine = engine
    import ironlog.seed as seed
    importlib.reload(seed)
    seed.engine = engine
    seed.seed()                                    # 103-movement library + EngineState/PhasePolicy

    from ironlog.generation.program_seed import seed_phase1_program
    with DbSession(engine) as s:
        seed_phase1_program(s)                     # Phase 1 program prior (D1 Upper Push, ...)
        program_id = s.exec(select(Program)).one().id

    def _override():
        with DbSession(engine) as s:
            yield s

    app.dependency_overrides[get_session] = _override
    return TestClient(app), program_id


def test_program_days_lists_training_days_in_order():
    client, program_id = _client()
    resp = client.get(f"/programs/{program_id}/days")
    assert resp.status_code == 200
    days = resp.json()
    assert days == ["D1 Upper Push", "D2 Lower A", "D4 Upper Pull",
                    "D5 Lower B", "D6 Weak Points"]
    assert "" not in days  # rest days excluded
    app.dependency_overrides.clear()


def test_program_days_empty_list_for_missing_program():
    """No ProgramDay rows match a nonexistent program_id -> empty list, not an error."""
    client, _program_id = _client()
    resp = client.get("/programs/999999/days")
    assert resp.status_code == 200
    assert resp.json() == []
    app.dependency_overrides.clear()
