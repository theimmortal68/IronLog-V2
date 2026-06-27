# SQL Migrations Mechanism Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A lightweight forward-only SQL migration runner (`ironlog/migrate.py`) + ordered `deploy/migrations/*.sql` + a `schema_migrations` table + a parity test that proves the migration chain reconstructs exactly what `create_all` produces — making code↔schema drift impossible by construction. Per the approved design at `docs/superpowers/specs/2026-06-25-migrations-design.md`.

**Architecture:** Pure `pending()` + DB-touching `apply_pending`/`stamp_all`/`stamp` in `ironlog/migrate.py` (sibling to `db.py`, NOT `engine/`). One canonical runner, three callers (systemd ExecStartPre, `seed.py` stamp-all, dev). A keystone parity test diffs live-`create_all` schema against the `000+001+002` chain with an order- and affinity-correct column map.

**Tech Stack:** Python 3.14, SQLModel/SQLAlchemy, SQLite, pytest 8. No new dependencies.

## Global Constraints

Carried verbatim from the approved spec and `~/projects/IronLog-V2/CLAUDE.md`. Every task's requirements implicitly include this section.

- **The runner is NOT in `engine/`.** It is `ironlog/migrate.py`, sibling to `ironlog/db.py`. `engine/` stays pure.
- **No `from __future__ import annotations`** in any file importing SQLModel models with `Relationship(...)`.
- **One implementation, many callers.** Single `migrate.py`; ExecStartPre, `seed.py`, and dev all call the same entry point.
- **Forward-only.** No down-migrations. Idempotent by tracking (`schema_migrations`), not by SQL.
- **`schema_migrations`** schema: `CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)`.
- **Version = filename stem** (e.g. `001_add_movement_knee_modality`). Never rename an applied migration.
- **`apply_pending` executes only unapplied versions**, in order; records each after success; a failing `.sql` raises and records nothing for that version; `__main__` exits non-zero on failure (so ExecStartPre fails loud).
- **`stamp_all`/`stamp` record without executing.** Fresh DBs (create_all already built the schema) and the prod backfill use stamping.
- **`000_baseline.sql` is GENERATED, schema-only, pure DDL** (`CREATE TABLE IF NOT EXISTS`, no `INSERT`s): built from the v0.2.0 tag via `create_all`-only (no seed → no rows) and dumped with `sqlite3 .schema` (NOT `iterdump`).
- **Parity diff is order-independent and affinity-correct:** normalize each table to `{col_name: (type, notnull, dflt_value, pk)}` from `PRAGMA table_info`; compare those maps (never ordered tuples / `cid`).
- **Known `create_all` DDL (drives 001/002 — verified on myflix 2026-06-25):**
  - `movement.knee_modality` → type `VARCHAR(6)`, notnull `0`, dflt `None`, pk `0`.
  - `movementstate.consecutive_failed_progressions` → type `INTEGER`, notnull `1`, dflt `None`, pk `0` **as the model stands today** — Task 2 changes this via `server_default` so both paths carry a SQL default (see Task 2).
- **Test runner is myflix via SSH.** `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q [args]'`. Files NFS-sync workstation→myflix instantly.
- **Baseline: 100 tests pass.** After this work: ~108 (runner-logic + parity + seed-stamp).
- **`ExecStartPre` is added to the repo unit file but installed on myflix only during the post-merge gated rollout** (spec §8) — NOT a plan task.

---

## File structure

```
ironlog/migrate.py                                          NEW  (Task 1) — runner: pending/apply_pending/stamp_all/stamp + __main__
deploy/migrations/000_baseline.sql                          NEW  (Task 2) — GENERATED schema-only DDL (v0.2.0 era)
deploy/migrations/001_add_movement_knee_modality.sql         NEW  (Task 2) — ALTER, VARCHAR(6) to match create_all
deploy/migrations/002_add_movementstate_consecutive_failed_progressions.sql  NEW (Task 2) — ALTER, INTEGER NOT NULL DEFAULT 0
ironlog/models/library.py                                   MODIFY (Task 2) — server_default on consecutive_failed_progressions
tests/test_migrations.py                                    NEW  (Tasks 1+2) — runner-logic tests (T1) + parity test (T2)
ironlog/seed.py                                            MODIFY (Task 3) — call migrate.stamp_all after create_all
deploy/ironlogv2.service                                    MODIFY (Task 3) — add ExecStartPre line (installed at rollout, not here)
```

**Task boundaries (the circularity resolution):**
- **Task 1** is the runner *mechanism* — pure `pending()` + `apply_pending`/`stamp_all`/`stamp`, tested against a **temporary** migrations directory with throwaway `.sql`. It does not depend on the real `000/001/002` contents, so it ends green independently.
- **Task 2** is **one iterative unit**: generate `000`, draft `001/002`, add the model `server_default`, write the parity test, and iterate the DDL until the chain matches live `create_all`. The parity test *cannot* be green until the DDL is aligned, so "write the test" and "write the migrations" are **not** separable into two each-green tasks — they share a single green gate.
- **Task 3** wires the callers (seed stamp-all, the repo ExecStartPre line).

---

### Task 1: Runner core (`migrate.py`) + runner-logic tests

**Files:**
- Create: `ironlog/migrate.py`
- Create: `tests/test_migrations.py` (runner-logic tests only; the parity test is appended in Task 2)

**Interfaces:**
- Consumes: `ironlog.db.engine` (the SQLAlchemy engine).
- Produces:
  - `pending(all_versions: list[str], applied: set[str]) -> list[str]` (pure)
  - `discover(migrations_dir: Path | None = None) -> list[tuple[str, Path]]`
  - `ensure_table(engine) -> None`
  - `applied_versions(engine) -> set[str]`
  - `apply_pending(engine, migrations_dir: Path | None = None) -> list[str]`
  - `stamp_all(engine, migrations_dir: Path | None = None) -> list[str]`
  - `stamp(engine, versions: list[str]) -> None`
  - `__main__` dispatch: no-arg → `apply_pending`; `stamp-all` → `stamp_all`; `stamp <v…>` → `stamp`
  - The `migrations_dir` parameter (defaulting to `deploy/migrations/`) is what lets the tests inject a temp directory.

- [ ] **Step 1: Confirm baseline**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q 2>&1 | tail -1'`
Expected: `100 passed`.

- [ ] **Step 2: Write the runner-logic tests**

Create `tests/test_migrations.py`:

```python
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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_migrations.py 2>&1 | tail -8'`
Expected: collection error — `ModuleNotFoundError: No module named 'ironlog.migrate'`.

- [ ] **Step 4: Create `ironlog/migrate.py`**

```python
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
```

Note on `_exec_script`: migration `.sql` files may contain multiple statements; `text()`/`execute` runs a single statement only. Use sqlite3's `executescript` via the raw driver connection. The `hasattr(...)` guard in `apply_pending` is belt-and-suspenders; the canonical path is `_exec_script`. **Implementer:** if `conn.connection.driver_connection` is not the sqlite3 connection on this SQLAlchemy version, use `conn.connection.connection` or `engine.raw_connection()` — verify which exposes `.executescript` and use that; keep the single-tx-per-migration semantics (execute + record in one `engine.begin()` block).

- [ ] **Step 5: Run tests to verify they pass**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_migrations.py 2>&1 | tail -4'`
Expected: `9 passed`. Full suite: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q 2>&1 | tail -1'` → `109 passed`.

- [ ] **Step 6: Commit**

```bash
cd ~/projects/IronLog-V2
git add ironlog/migrate.py tests/test_migrations.py
git commit -m "feat(migrate): SQL migration runner core (pure pending + apply/stamp)

ironlog/migrate.py (sibling to db.py, not engine/): pure pending(),
DB-touching apply_pending/stamp_all/stamp over a schema_migrations table,
and a __main__ dispatch (apply / stamp-all / stamp <v...>). One tx per
migration (execute + record together); a failing .sql raises and records
nothing for that version. migrations_dir is injectable for tests.

9 runner-logic tests against a temp migrations dir (independent of the
real 000/001/002). Full suite 109.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: The chain + parity test (ONE iterative unit)

**This task has a single green gate: the parity test passes (the `000+001+002` chain matches live `create_all`).** Generating `000`, drafting `001/002`, adding the model `server_default`, writing the parity test, and tuning the DDL are interdependent — do them together and iterate until green. Do not expect intermediate green checkpoints within this task.

**Files:**
- Create: `deploy/migrations/000_baseline.sql` (generated)
- Create: `deploy/migrations/001_add_movement_knee_modality.sql`
- Create: `deploy/migrations/002_add_movementstate_consecutive_failed_progressions.sql`
- Modify: `ironlog/models/library.py` (server_default on `consecutive_failed_progressions`)
- Modify: `tests/test_migrations.py` (append the parity test)

**Interfaces:**
- Consumes: `migrate.apply_pending` (Task 1); `ironlog.db.create_db_and_tables`; `PRAGMA table_info`.
- Produces: the three real migration files; a model with a SQL-level default on the v0.4 counter; the parity test.

- [ ] **Step 1: Generate `000_baseline.sql` (schema-only, from v0.2.0)**

Run on myflix (the venv lives there; checking out the tag changes the NFS-shared working tree, so do it in a clean state — commit/stash any WIP first):

```
ssh myflix 'cd ~/projects/IronLog-V2 && \
  git stash list && echo "(ensure clean tree before checkout)" && \
  git checkout v0.2.0 && \
  rm -f /tmp/v020.db && \
  .venv/bin/python -c "from sqlmodel import SQLModel, create_engine; import ironlog.models; e=create_engine(\"sqlite:////tmp/v020.db\"); SQLModel.metadata.create_all(e)" && \
  sqlite3 /tmp/v020.db ".schema" > /tmp/000_raw.sql && \
  git checkout main && \
  echo "=== raw schema ===" && cat /tmp/000_raw.sql'
```

Then transform `/tmp/000_raw.sql` into `deploy/migrations/000_baseline.sql`:
- Convert each `CREATE TABLE` → `CREATE TABLE IF NOT EXISTS`.
- Drop any `sqlite_sequence` line and any `CREATE INDEX` that SQLModel didn't define via models (keep only the model-defined tables/indexes; `.schema` of a create_all DB should already be just the model tables + their indexes).
- Confirm there are **no `INSERT` statements** (there won't be — we never seeded; `.schema` is schema-only by construction). This is the belt-and-suspenders guarantee.
- Save as `deploy/migrations/000_baseline.sql`.

**Critical verification (the gap must be exactly the two columns):**
```
# 000 must NOT contain knee_modality or consecutive_failed_progressions:
grep -c "knee_modality\|consecutive_failed_progressions" deploy/migrations/000_baseline.sql   # expect 0
```
If non-zero, the chosen tag is wrong — stop and reconcile (the baseline must predate both columns).

- [ ] **Step 2: Inspect what current `create_all` emits, then draft `001`/`002`**

The verified `create_all` DDL (from recon, re-confirm if unsure):
- `movement.knee_modality`: `VARCHAR(6)`, nullable, no default.
- `movementstate.consecutive_failed_progressions`: `INTEGER`, NOT NULL, no SQL default (Python-side `0`).

Create `deploy/migrations/001_add_movement_knee_modality.sql`:
```sql
ALTER TABLE movement ADD COLUMN knee_modality VARCHAR(6);
```
(`VARCHAR(6)` to match create_all's emitted type, NOT `TEXT`. Nullable, no default — matches; and an ALTER adding a nullable column needs no default.)

Create `deploy/migrations/002_add_movementstate_consecutive_failed_progressions.sql`:
```sql
ALTER TABLE movementstate ADD COLUMN consecutive_failed_progressions INTEGER NOT NULL DEFAULT 0;
```
(An ALTER adding a `NOT NULL` column REQUIRES a `DEFAULT` in SQLite — `NOT NULL` without a default is rejected even on empty tables. So `002` carries `DEFAULT 0`.)

- [ ] **Step 3: Reconcile the NOT NULL default — add `server_default` to the model**

Because `002` must carry `DEFAULT 0` but current `create_all` emits `consecutive_failed_progressions INTEGER NOT NULL` with **no** SQL default (`dflt_value=None`), the two paths would diverge on `dflt_value` in the parity diff. Fix by making `create_all` ALSO emit the SQL default:

Edit `ironlog/models/library.py`. Find:
```python
    consecutive_failed_progressions: int = 0
```
Replace with:
```python
    consecutive_failed_progressions: int = Field(default=0, sa_column_kwargs={"server_default": text("0")})
```
Add the needed imports at the top of `library.py` if not present: `from sqlalchemy import text` (and ensure `Field` is already imported from sqlmodel — it is). This makes `create_all` emit `INTEGER NOT NULL DEFAULT 0` (DB-level default), matching `002`'s ALTER. (Functionally identical for the app — the column already defaulted to 0 Python-side; now the DB enforces it too.)

**`knee_modality` needs no model change** — it's nullable with no default in both `create_all` and `001`.

- [ ] **Step 4: Write the parity test (the keystone)**

Append to `tests/test_migrations.py`:

```python
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
```

- [ ] **Step 5: Iterate DDL until the parity test is GREEN**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_migrations.py::test_chain_matches_create_all 2>&1 | tail -30'`

This is the iteration loop. The failure message prints per-column deltas. Expected reconciliation work:
- If `knee_modality` shows `VARCHAR(6)` (create_all) vs `TEXT` (chain) → fix `001` to `VARCHAR(6)`.
- If `consecutive_failed_progressions` shows a `dflt_value` mismatch (e.g. create_all `None` vs chain `'0'`, or quoting differences like `0` vs `'0'`) → the model `server_default` (Step 3) should make create_all emit a default; align the exact `DEFAULT` literal in `002` to the string SQLite reports for the model's `server_default` (run `PRAGMA table_info` on a fresh create_all DB to see the exact `dflt_value` string, then match it in `002`). If create_all reports `dflt_value='0'` (unquoted) and the ALTER `DEFAULT 0` reports `'0'` too, they match; if quoting differs, adjust the `server_default` text or the ALTER literal until the reported strings are identical.
- Repeat until `schema_a == schema_b`.

Document, in a comment at the top of `002`, the exact create_all dflt_value string it was aligned to, so a future reader knows the coupling.

- [ ] **Step 6: Full suite green (confirm the model change broke nothing)**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q 2>&1 | tail -3'`
Expected: `110 passed` (109 from Task 1 + the parity test). The `server_default` model change must not break the v0.4 analysis/applier tests (they create_all in-memory; the column still defaults to 0). If any fail, reconcile before committing.

- [ ] **Step 7: Commit**

```bash
cd ~/projects/IronLog-V2
git add deploy/migrations/000_baseline.sql \
        deploy/migrations/001_add_movement_knee_modality.sql \
        deploy/migrations/002_add_movementstate_consecutive_failed_progressions.sql \
        ironlog/models/library.py tests/test_migrations.py
git commit -m "feat(migrate): migration chain (000 baseline + 001/002) + parity test

000_baseline.sql GENERATED schema-only from the v0.2.0 tag (create_all,
no seed, sqlite3 .schema) — verified gap to current models is exactly the
two columns. 001 adds movement.knee_modality VARCHAR(6) (matching
create_all's emitted type, not TEXT). 002 adds
movementstate.consecutive_failed_progressions INTEGER NOT NULL DEFAULT 0
(an ALTER NOT NULL column requires a default in SQLite).

To make create_all and the ALTER converge on dflt_value, the model gains
a server_default=text('0') on consecutive_failed_progressions (DB-level
default; functionally identical to the prior Python-side default).

Parity test diffs live create_all vs the 000+001+002 chain as an
order-independent, affinity-correct {col:(type,notnull,dflt,pk)} map — a
forgotten migration or a type/default mismatch fails CI. Full suite 110.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Wire the callers (seed stamp-all + repo ExecStartPre)

**Files:**
- Modify: `ironlog/seed.py` — call `migrate.stamp_all` after `create_db_and_tables`
- Modify: `deploy/ironlogv2.service` — add the `ExecStartPre` line (in the repo; installed on myflix only at rollout)
- Modify: `tests/test_migrations.py` — a seed-stamps-all test

**Interfaces:**
- Consumes: `migrate.stamp_all`, `migrate.applied_versions` (Task 1); `create_db_and_tables` (existing).
- Produces: a freshly-seeded DB has all migrations stamped applied (so the runner never re-executes them).

- [ ] **Step 1: Write the seed-stamp test**

Append to `tests/test_migrations.py`:

```python
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
```

- [ ] **Step 2: Run it — confirm it passes already (Task 1+2 code suffices)**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_migrations.py::test_fresh_db_after_create_all_plus_stamp_all_runs_nothing 2>&1 | tail -4'`
Expected: PASS (this test exercises existing `migrate` functions; it documents the seed contract before we wire seed itself).

- [ ] **Step 3: Wire `seed.py`**

Edit `ironlog/seed.py`. Find the `seed()` body where it calls `create_db_and_tables()`:
```python
    create_db_and_tables()
```
Add the stamp-all immediately after, and import migrate at the top. At the top of `seed.py`, after the existing `from .db import create_db_and_tables, get_session` line, add:
```python
from . import migrate
from .db import engine
```
Then change the create call site:
```python
    create_db_and_tables()
    migrate.stamp_all(engine)   # fresh DB: schema built by create_all; record all migrations applied
```

- [ ] **Step 4: Verify seed still runs end-to-end (fresh temp DB, no prod impact)**

Run on myflix against a throwaway DB path so the real `ironlog.db` is untouched:
```
ssh myflix 'cd ~/projects/IronLog-V2 && rm -f /tmp/seedcheck.db && \
  .venv/bin/python -c "
import ironlog.db as db
from sqlmodel import create_engine
db.engine = create_engine(\"sqlite:////tmp/seedcheck.db\")
import ironlog.seed as s
s.seed()
from ironlog import migrate
print(\"applied after seed:\", sorted(migrate.applied_versions(db.engine)))
print(\"pending after seed:\", migrate.apply_pending(db.engine))
"'
```
Expected: seed prints its normal "Seeded ironlog.db" output (or equivalent), `applied after seed` lists `000_baseline, 001_..., 002_...`, and `pending after seed` is `[]`.

(Note: `seed.py` imports `engine` and `create_db_and_tables` from `.db`; the throwaway override above rebinds `db.engine` before importing seed — if seed binds `engine` at import time, the implementer adjusts the check to set the env/URL before import. The substantive assertion is: after `seed()`, all migrations are stamped and nothing is pending.)

- [ ] **Step 5: Add the `ExecStartPre` line to the repo unit file**

Edit `deploy/ironlogv2.service`. After the `WorkingDirectory=` line and before `ExecStart=`, add:
```
ExecStartPre=/home/jstout/projects/IronLog-V2/.venv/bin/python -m ironlog.migrate
```
This lands in the repo now; it is **installed on myflix only during the gated rollout** (spec §8) — this step does not touch the running service.

- [ ] **Step 6: Full suite green**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q 2>&1 | tail -3'`
Expected: `111 passed`.

- [ ] **Step 7: Commit**

```bash
cd ~/projects/IronLog-V2
git add ironlog/seed.py deploy/ironlogv2.service tests/test_migrations.py
git commit -m "feat(migrate): seed stamp-all + repo ExecStartPre (rollout-installed)

seed.py calls migrate.stamp_all(engine) after create_db_and_tables, so a
fresh DB records the whole chain applied and the runner never re-executes
001/002 against columns create_all already built. The unit file gains an
ExecStartPre that runs `python -m ironlog.migrate` before uvicorn binds —
present in the repo, installed on myflix only during the gated rollout
(spec §8). Seed-contract test confirms fresh-DB -> nothing pending.
Full suite 111.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-review (against the spec)

**Spec coverage** — every spec section maps to a task:
- §3.1 migrate.py API (pure pending + apply/stamp + __main__) → Task 1.
- §3.2 migration files (000/001/002) → Task 2.
- §4 callers (ExecStartPre, seed, dev) → Task 3 (ExecStartPre line + seed); dev is the bare `python -m ironlog.migrate` from Task 1's `__main__`.
- §5 000 generation (schema-only, v0.2.0, .schema not iterdump, gap-verification) → Task 2 Step 1.
- §6 divergence handling (column order, VARCHAR vs TEXT, Python vs SQL default) → Task 2 Steps 2-3 + the parity diff in Step 4; the NOT-NULL/default reconciliation via model server_default is made explicit.
- §7.1 parity test (order-independent, affinity-correct map) → Task 2 Step 4; §7.2 runner-logic tests → Task 1.
- §8 gated rollout → explicitly NOT a plan task (post-merge deploy); the ExecStartPre line lands in the repo (Task 3 Step 5) but is installed at rollout.
- §9 revert, §10 out-of-scope, §11 invariants → respected by absence (no down-migrations, no Alembic, runner outside engine/).

**Placeholder scan** — no TBDs. Two places defer a concrete value to verified inspection rather than guessing: the `_exec_script` driver-attribute (Task 1 Step 4 names the alternatives and the selection criterion) and the exact `dflt_value` string for `002` (Task 2 Step 5 is an explicit iterate-against-the-test loop — that's the circularity the user called out, modeled as one task with one green gate, not a hand-wave). These are the genuinely environment-determined values; the plan tells the implementer exactly how to resolve each.

**Type consistency** — `migrate.pending/discover/ensure_table/applied_versions/apply_pending/stamp_all/stamp` signatures are identical across Task 1 (definition + tests), Task 2 (parity test calls `apply_pending(eng_b)`), and Task 3 (seed calls `stamp_all(engine)`; test calls `discover()`/`applied_versions`). `migrations_dir` optional param consistent. `_schema_map` shape `{table: {col: (type, notnull, dflt, pk)}}` consistent within the parity test. Test counts: 100 baseline → 109 (Task 1: +9) → 110 (Task 2: +1 parity) → 111 (Task 3: +1 seed). Consistent.

**Circularity check** — the user's constraint is honored: Task 2 is one unit with a single green gate (parity passes after DDL iteration). "Write the parity test" and "write 001/002" are not separate each-green tasks. Task 1 (runner mechanism, temp-dir tests) is genuinely independent and ends green on its own.
