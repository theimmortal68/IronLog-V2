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


# ---------------------------------------------------------------------------
# Task 2 — the parity keystone: live create_all schema == 000+001+002 chain
# ---------------------------------------------------------------------------

from ironlog.db import create_db_and_tables  # noqa: E402
import ironlog.db as _db                       # noqa: E402


def _schema_map(engine) -> dict:
    """{table_name: {col_name: (type, notnull, dflt_value, pk)}} for all model
    tables — order-independent (ignores cid) and affinity-correct (compares the
    declared type string, nullability, default, and pk per column)."""
    out: dict = {}
    with engine.connect() as c:
        tables = [r[0] for r in c.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' AND name != 'schema_migrations'"
        ))]
        for t in tables:
            cols = {}
            for row in c.execute(text(f"PRAGMA table_info({t})")):
                # row = (cid, name, type, notnull, dflt_value, pk)
                cols[row[1]] = (row[2], row[3], row[4], row[5])
            out[t] = cols
    return out


def test_chain_matches_create_all():
    """A forgotten migration or a type/default/nullability mismatch between the
    live models (create_all) and the 000+001+002 chain fails HERE, not in prod."""
    # DB-A: live models via create_all
    eng_a = create_engine("sqlite://")
    from sqlmodel import SQLModel
    SQLModel.metadata.create_all(eng_a)

    # DB-B: empty -> apply the real migration chain in order
    eng_b = create_engine("sqlite://")
    migrate.apply_pending(eng_b)  # uses the real deploy/migrations/

    schema_a = _schema_map(eng_a)
    schema_b = _schema_map(eng_b)
    assert schema_a == schema_b, (
        "create_all schema != migration chain.\n"
        f"only in create_all: {_diff(schema_a, schema_b)}\n"
        f"only in chain: {_diff(schema_b, schema_a)}"
    )


def _diff(x: dict, y: dict) -> dict:
    """Per-table column entries in x not identical in y (for failure messages)."""
    out = {}
    for t, cols in x.items():
        ycols = y.get(t, {})
        delta = {c: v for c, v in cols.items() if ycols.get(c) != v}
        if delta:
            out[t] = delta
    return out


# ---------------------------------------------------------------------------
# Task 3 — seed stamps the whole chain on a fresh DB
# ---------------------------------------------------------------------------

def test_fresh_db_after_create_all_plus_stamp_all_runs_nothing():
    """The fresh-DB contract: create_all builds the schema, stamp_all records
    every migration as applied, so apply_pending then runs nothing (no attempt
    to re-execute 001/002 against columns create_all already made)."""
    eng = create_engine("sqlite://")
    from sqlmodel import SQLModel
    SQLModel.metadata.create_all(eng)              # what seed's create_db_and_tables does
    stamped = migrate.stamp_all(eng)               # what seed will call next
    assert set(stamped) == {p.stem for _, p in migrate.discover()}  # all real migrations
    assert migrate.apply_pending(eng) == []        # nothing left to run
