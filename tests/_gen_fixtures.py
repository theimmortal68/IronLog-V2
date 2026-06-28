"""Shared pytest fixtures for the generation layer tests.

The canonical gen_db fixture lives in tests/conftest.py (auto-discovered by pytest).
This file is kept as the spec reference; tests use the fixture via conftest auto-discovery.

gen_db — creates an in-memory SQLite DB, seeds the library (108 movements)
         via seed.seed(), then seeds the Phase 1 program via seed_phase1_program().
         Yields the live Session so tests can query directly.

NO from __future__ import annotations (project-wide constraint).
"""
# The gen_db fixture is defined in conftest.py and auto-discovered by pytest.
# No import needed here — conftest.py discovery is the working path.
# (The previous re-export via `from tests.conftest import gen_db` was dead code:
#  tests/ has no __init__.py, so that import would fail at runtime.)
