"""Shared pytest fixtures for the generation layer tests.

The canonical gen_db fixture lives in tests/conftest.py (auto-discovered by pytest).
This file is kept as the spec reference; tests import the fixture via conftest.

gen_db — creates an in-memory SQLite DB, seeds the library (103 movements)
         via seed.seed(), then seeds the Phase 1 program via seed_phase1_program().
         Yields the live Session so tests can query directly.

NO from __future__ import annotations (project-wide constraint).
"""
# Fixture defined in conftest.py — imported here for reference only.
# Re-export so callers that do:
#   from tests._gen_fixtures import gen_db  # noqa: F401
# can still work once tests/ is a package.
from tests.conftest import gen_db  # noqa: F401
