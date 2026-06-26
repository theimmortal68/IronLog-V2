"""Tests for ironlog.migrate (the SQL migration runner).

Task 1: runner-logic tests against a TEMPORARY migrations directory with
throwaway .sql, independent of the real 000/001/002 contents.
Task 2 appends the parity test (real chain vs live create_all).
"""
from pathlib import Path

import pytest
from sqlmodel import create_engine, text

from ironlog import migrate


# --- pure pending() ---

def test_pending_returns_unapplied_in_order():
    assert migrate.pending(["001_a", "002_b", "003_c"], {"001_a"}) == ["002_b", "003_c"]


def test_pending_empty_when_all_applied():
    assert migrate.pending(["001_a", "002_b"], {"001_a", "002_b"}) == []


def test_pending_preserves_given_order():
    # input is already sorted by the caller (discover sorts); pending keeps it
    assert migrate.pending(["001_a", "002_b", "003_c"], {"002_b"}) == ["001_a", "003_c"]


# --- DB-touching apply/stamp against a temp migrations dir ---

@pytest.fixture
def tmp_migrations(tmp_path: Path) -> Path:
    d = tmp_path / "migrations"
    d.mkdir()
    (d / "001_make_widgets.sql").write_text(
        "CREATE TABLE IF NOT EXISTS widget (id INTEGER PRIMARY KEY, name TEXT);"
    )
    (d / "002_add_widget_color.sql").write_text(
        "ALTER TABLE widget ADD COLUMN color TEXT;"
    )
    return d


@pytest.fixture
def mem_engine():
    return create_engine("sqlite://")  # in-memory


def _cols(engine, table):
    with engine.connect() as c:
        return {r[1] for r in c.execute(text(f"PRAGMA table_info({table})"))}


def test_apply_pending_executes_and_records(mem_engine, tmp_migrations):
    applied = migrate.apply_pending(mem_engine, tmp_migrations)
    assert applied == ["001_make_widgets", "002_add_widget_color"]
    assert _cols(mem_engine, "widget") == {"id", "name", "color"}
    assert migrate.applied_versions(mem_engine) == {"001_make_widgets", "002_add_widget_color"}


def test_apply_pending_is_noop_second_run(mem_engine, tmp_migrations):
    migrate.apply_pending(mem_engine, tmp_migrations)
    assert migrate.apply_pending(mem_engine, tmp_migrations) == []  # nothing left to do


def test_apply_pending_failing_sql_raises_and_records_nothing_for_it(mem_engine, tmp_path):
    d = tmp_path / "m"; d.mkdir()
    (d / "001_ok.sql").write_text("CREATE TABLE IF NOT EXISTS a (id INTEGER PRIMARY KEY);")
    (d / "002_bad.sql").write_text("ALTER TABLE nonexistent ADD COLUMN x TEXT;")
    with pytest.raises(Exception):
        migrate.apply_pending(mem_engine, d)
    # 001 recorded (ran before the failure); 002 not recorded
    assert migrate.applied_versions(mem_engine) == {"001_ok"}


def test_stamp_all_records_without_executing(mem_engine, tmp_migrations):
    stamped = migrate.stamp_all(mem_engine, tmp_migrations)
    assert set(stamped) == {"001_make_widgets", "002_add_widget_color"}
    assert migrate.applied_versions(mem_engine) == {"001_make_widgets", "002_add_widget_color"}
    # the widget table was NOT created (stamp does not execute)
    with mem_engine.connect() as c:
        names = {r[0] for r in c.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
    assert "widget" not in names


def test_stamp_specific_versions(mem_engine):
    migrate.stamp(mem_engine, ["001_make_widgets"])
    assert migrate.applied_versions(mem_engine) == {"001_make_widgets"}


def test_ensure_table_idempotent(mem_engine):
    migrate.ensure_table(mem_engine)
    migrate.ensure_table(mem_engine)  # no error on second call
    assert migrate.applied_versions(mem_engine) == set()
