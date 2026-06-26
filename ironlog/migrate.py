"""
migrate.py — the single canonical SQL migration runner (sibling to db.py).

Forward-only, lightweight (no Alembic). Ordered .sql in deploy/migrations/,
tracked in a schema_migrations table. One implementation, three callers:
systemd ExecStartPre (`python -m ironlog.migrate`), seed.py's stamp_all,
and dev. See docs/superpowers/specs/2026-06-25-migrations-design.md.

NOT in engine/ (which is pure logic) — this is DB-touching infra.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Set, Tuple

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .db import engine as default_engine

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "deploy" / "migrations"


def pending(all_versions: List[str], applied: Set[str]) -> List[str]:
    """Pure: versions not yet applied, preserving the given (sorted) order."""
    return [v for v in all_versions if v not in applied]


def discover(migrations_dir: Optional[Path] = None) -> List[Tuple[str, Path]]:
    """(version, path) for each *.sql in the migrations dir, sorted by version
    (the filename stem). Empty list if the directory does not exist."""
    d = migrations_dir or MIGRATIONS_DIR
    if not d.exists():
        return []
    files = sorted(d.glob("*.sql"), key=lambda p: p.stem)
    return [(p.stem, p) for p in files]


def ensure_table(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        ))


def applied_versions(engine: Engine) -> Set[str]:
    ensure_table(engine)
    with engine.connect() as conn:
        return {row[0] for row in conn.execute(text("SELECT version FROM schema_migrations"))}


def _record(conn, version: str) -> None:
    conn.execute(
        text("INSERT INTO schema_migrations (version, applied_at) VALUES (:v, :t)"),
        {"v": version, "t": datetime.now(timezone.utc).isoformat()},
    )


def apply_pending(engine: Engine, migrations_dir: Optional[Path] = None) -> List[str]:
    """Execute each unapplied migration in order, recording it after success.
    A failing .sql raises (its version is NOT recorded); already-applied
    versions are skipped. Returns the versions applied this run."""
    ensure_table(engine)
    discovered = discover(migrations_dir)
    by_version = dict(discovered)
    todo = pending([v for v, _ in discovered], applied_versions(engine))
    done: List[str] = []
    for version in todo:
        sql = by_version[version].read_text()
        with engine.begin() as conn:           # one tx per migration: execute + record together
            conn.exec_driver_sql_script(sql) if hasattr(conn, "exec_driver_sql_script") else _exec_script(conn, sql)
            _record(conn, version)
        done.append(version)
    return done


def _exec_script(conn, sql: str) -> None:
    """Execute a multi-statement .sql via the raw DBAPI cursor (sqlite3
    executescript), which handles multiple statements that text() will not."""
    raw = conn.connection.driver_connection  # underlying sqlite3 connection
    raw.executescript(sql)


def stamp_all(engine: Engine, migrations_dir: Optional[Path] = None) -> List[str]:
    """Record every discovered version as applied WITHOUT executing.
    For fresh DBs (create_all already built the schema) and the prod backfill."""
    ensure_table(engine)
    versions = [v for v, _ in discover(migrations_dir)]
    already = applied_versions(engine)
    to_stamp = [v for v in versions if v not in already]
    if to_stamp:
        with engine.begin() as conn:
            for v in to_stamp:
                _record(conn, v)
    return to_stamp


def stamp(engine: Engine, versions: List[str]) -> None:
    """Record specific versions as applied without executing."""
    ensure_table(engine)
    already = applied_versions(engine)
    with engine.begin() as conn:
        for v in versions:
            if v not in already:
                _record(conn, v)


def _main(argv: List[str]) -> int:
    if not argv:
        applied = apply_pending(default_engine)
        print(f"applied: {applied}" if applied else "nothing to apply")
        return 0
    if argv[0] == "stamp-all":
        stamped = stamp_all(default_engine)
        print(f"stamped: {stamped}" if stamped else "nothing to stamp")
        return 0
    if argv[0] == "stamp":
        stamp(default_engine, argv[1:])
        print(f"stamped: {argv[1:]}")
        return 0
    print(f"unknown command: {argv[0]}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
