"""Shared pytest fixtures for the generation layer tests.

gen_db — creates an in-memory SQLite DB, seeds the library (103 movements)
         via seed.seed(), then seeds the Phase 1 program via seed_phase1_program().
         Yields the live Session so tests can query directly.

Placed in conftest.py so pytest auto-discovers it for all test modules in tests/.
_gen_fixtures.py re-exports this fixture for explicit import in test modules.

NO from __future__ import annotations (project-wide constraint).
"""
import importlib

import pytest
from sqlmodel import Session, create_engine


@pytest.fixture
def gen_db():
    eng = create_engine("sqlite://")
    import ironlog.db as db
    db.engine = eng
    import ironlog.seed as seed
    importlib.reload(seed)
    seed.engine = eng
    seed.seed()                                    # 103-movement library
    from ironlog.generation.program_seed import seed_phase1_program
    with Session(eng) as s:
        seed_phase1_program(s)                     # the Phase 1 program prior
        yield s
