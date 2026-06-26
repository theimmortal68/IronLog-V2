# SQL Migrations Mechanism — Design

**Date:** 2026-06-25
**Repo:** `~/projects/IronLog-V2` (this repo)
**Status:** approved design; awaiting implementation plan
**Scope:** a lightweight forward-only SQL-file migration runner that makes code↔schema drift impossible by construction. NOT Alembic.

---

## 1. Purpose

The v0.4 deploy surfaced latent schema drift: v0.3's `movement.knee_modality` ALTER was never run on production. It stayed invisible because the running server kept serving pre-v0.3 code until the v0.4 restart loaded current `main` — the first time the deployed binary was newer than the deployed schema. The restart then 500'd on `no such column: movement.knee_modality`.

"Merge" and "deploy" had drifted apart across v0.2/v0.3/v0.4: all three merged + tagged, only v0.4 actually got deployed-and-restarted. Vigilance failed silently three times. The fix is structural, in the same spirit as the ledger's recompute-don't-store and the applier's resolve-all-first: **make the schema current before the service can serve, every restart, automatically** — so code and schema cannot separate.

This adds ordered `.sql` migrations in `deploy/migrations/`, a `schema_migrations` tracking table, and a single canonical runner (`python -m ironlog.migrate`) invoked from systemd `ExecStartPre` (and from `seed.py` and dev). A parity test proves the migration chain reconstructs exactly what the live models produce, so a forgotten future migration fails CI rather than production.

---

## 2. Constraints

From `~/projects/IronLog-V2/CLAUDE.md` and the brainstorm decisions:

- **Lightweight, not Alembic.** Plain ordered `.sql` files; a `schema_migrations(version, applied_at)` table; forward-only.
- **The runner is NOT in `engine/`** (which is pure logic). It is `ironlog/migrate.py`, sibling to `ironlog/db.py` (which owns the engine + `create_db_and_tables`). DB-touching infra, not engine.
- **One implementation, many callers.** A single `migrate.py` is invoked by ExecStartPre, by `seed.py`'s baseline-stamp, and by dev. No second implementation.
- **No `from __future__ import annotations`** in any file importing SQLModel models with `Relationship(...)`.
- **Idempotent by tracking, not by SQL.** SQLite `ALTER TABLE ADD COLUMN` is not idempotent; the runner only executes versions absent from `schema_migrations`.
- **Forward-only.** No down-migrations; revert = restore from backup (§9).

---

## 3. Architecture

**Pure pending-calc + DB-touching apply/stamp**, mirroring the project's compute/apply discipline.

```
deploy/migrations/000_baseline.sql                          NEW — GENERATED full pre-migration schema (§5)
deploy/migrations/001_add_movement_knee_modality.sql         NEW — ALTER (v0.3 column)
deploy/migrations/002_add_movementstate_consecutive_failed_progressions.sql  NEW — ALTER (v0.4 column)
ironlog/migrate.py                                           NEW — the single canonical runner
ironlog/seed.py                                             MODIFY — call migrate.stamp_all() after create_all
deploy/ironlogv2.service                                    MODIFY — add ExecStartPre (applied at rollout, §8)
tests/test_migrations.py                                    NEW — parity test + runner-logic tests
```

`engine/` is untouched (stays pure). `api/` is untouched (no startup hook — that's why ExecStartPre, not lifespan).

### 3.1 `ironlog/migrate.py` API

**Pure (unit-testable, no DB):**
```python
def pending(all_versions: list[str], applied: set[str]) -> list[str]:
    """Versions in all_versions (sorted) not in applied, preserving order."""
```

**DB-touching:**
```python
def discover() -> list[tuple[str, Path]]:
    """(version, path) for each deploy/migrations/*.sql, sorted by version
    (the filename stem, e.g. '001_add_movement_knee_modality')."""

def ensure_table(engine) -> None:
    """CREATE TABLE IF NOT EXISTS schema_migrations(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)."""

def applied_versions(engine) -> set[str]:
    """SELECT version FROM schema_migrations (after ensure_table)."""

def apply_pending(engine) -> list[str]:
    """ensure_table; for each pending() version in order: execute its .sql,
    then INSERT the version with applied_at=now. Returns the versions applied.
    A failing .sql raises (nothing recorded for that version) → caller exits non-zero."""

def stamp_all(engine) -> list[str]:
    """ensure_table; record EVERY discovered version as applied WITHOUT executing.
    For fresh DBs (create_all already built the schema) and the prod backfill."""

def stamp(engine, versions: list[str]) -> None:
    """ensure_table; record specific versions as applied without executing."""
```

**`__main__`:**
- `python -m ironlog.migrate` → `apply_pending` (ExecStartPre, dev, existing-DB incremental)
- `python -m ironlog.migrate stamp-all` → `stamp_all`
- `python -m ironlog.migrate stamp <v…>` → `stamp`
- Non-zero exit on any failure, so ExecStartPre fails loud and the old process keeps serving.

`migrate.py` imports `db.engine` (and `db.create_db_and_tables` is NOT its concern). It uses `sqlalchemy.text` / a raw connection to run the `.sql` and the tracking writes.

### 3.2 Migration files

Version = filename stem; **never rename an applied migration** (the stem is the tracking key).

- `000_baseline.sql` — full `CREATE TABLE IF NOT EXISTS …` for the schema as of v0.2.0 (before `knee_modality`, before `consecutive_failed_progressions`). **Generated** (§5), not hand-written. Executes only in the parity test from an empty DB; every real DB already passed this point via `create_all` and has it stamped, never run. `IF NOT EXISTS` makes it a safe no-op even if somehow run against populated tables.
- `001_add_movement_knee_modality.sql` — `ALTER TABLE movement ADD COLUMN …`, DDL authored to match exactly what `create_all` emits for that column (type string, nullability, default — see §6).
- `002_add_movementstate_consecutive_failed_progressions.sql` — likewise for the v0.4 column.

---

## 4. Callers (one implementation)

1. **systemd `ExecStartPre`** (the real deploy path): `ExecStartPre=/home/jstout/projects/IronLog-V2/.venv/bin/python -m ironlog.migrate`. Runs `apply_pending` before uvicorn binds. If a migration fails, the unit fails to start (loud in `systemctl`/`journalctl`) and the OLD process is not replaced by a broken one. This is the failure mode that would have prevented the v0.4 incident.
2. **`seed.py`**: after `create_db_and_tables()` (create_all builds the current schema from the models), call `migrate.stamp_all(engine)` — a fresh DB stamps 000/001/002 applied, so the runner never tries to re-execute them.
3. **dev / manual**: `python -m ironlog.migrate` to apply, `stamp-all` for a hand-built DB.

---

## 5. Generating `000_baseline.sql` (a recorded fact, not a reconstruction)

Do NOT hand-author 000 from the current models — a subtle type/default/nullability mismatch would fail the parity test on day one (or tempt neutering the test). Generate it from the actual v0.2.0 schema:

```bash
git stash    # if needed
git checkout v0.2.0          # 32e8ec0 — last schema before knee_modality/consecutive_failed
# build a fresh DB with that era's create_all and dump its schema:
.venv/bin/python -c "
import sqlite3, tempfile, os
from sqlmodel import SQLModel, create_engine
import ironlog.models  # noqa  (registers all tables as of v0.2.0)
p = tempfile.mktemp(suffix='.db')
eng = create_engine(f'sqlite:///{p}')
SQLModel.metadata.create_all(eng)
con = sqlite3.connect(p)
for line in con.iterdump():
    if line.startswith('CREATE TABLE'):
        print(line + ';' if not line.rstrip().endswith(';') else line)
" > /tmp/000_raw.sql
git checkout main
# Convert CREATE TABLE -> CREATE TABLE IF NOT EXISTS, drop sqlite_sequence noise,
# save as deploy/migrations/000_baseline.sql.
```

**Verification (design requirement):** confirm the generated 000 has `movement` WITHOUT `knee_modality` and `movementstate` WITHOUT `consecutive_failed_progressions` — i.e., the gap between 000 and current models is *exactly* the two ALTER columns. (Between v0.2.0 and HEAD, only those two columns were added — the validator added no columns, the ledger added knee_modality, the analysis hook added consecutive_failed_progressions.) The parity test (§7) is the binding check; this is the sanity check during generation.

---

## 6. The create_all-vs-ALTER divergence (why the parity test exists)

`create_all` (from the live models) and the incremental ALTERs are two independent expressions of the schema. They can silently diverge — add a model field, forget the migration, and fresh DBs get the column while upgraded DBs don't. That is the exact drift this project is killing, in a subtler form. Two specific divergence sources, both handled by the parity test forcing alignment:

- **Column order.** `create_all` positions `knee_modality` where the model declares it (after `status`); an `ALTER … ADD COLUMN` appends it last. So the schemas have the same columns in different order. The parity diff is therefore **order-independent** (§7).
- **Declared type / default / nullability.** SQLite stores the *declared* type string, so `create_all`'s `VARCHAR` vs an ALTER's `TEXT`, or a Python-side default vs a SQL `DEFAULT 0`, are reported differently by `PRAGMA table_info` even when semantically equivalent in SQLite. So `001`/`002`'s DDL must be authored to match exactly what `create_all` emits for those columns. The implementer inspects `create_all`'s output (e.g. via `iterdump` on a fresh DB) and aligns the ALTER text until the parity test passes. **The test is the enforcer** — alignment is verified, not assumed.

---

## 7. Tests — `tests/test_migrations.py`

### 7.1 The parity test (the keystone)

```
DB-A: create_db_and_tables() on a fresh in-memory SQLite  (live models → current schema)
DB-B: empty in-memory SQLite → apply 000_baseline.sql + 001 + 002 in order (via the runner)
assert schema(DB-A) == schema(DB-B)
```

`schema(db)` normalizes each table to `{column_name: (type, notnull, dflt_value, pk)}` from `PRAGMA table_info` — **order-independent** (ignores `cid`), comparing type + nullability + default + pk per column. A future model field with no migration → DB-A has the column, DB-B doesn't → the dict comparison fails. A type/default mismatch between `create_all` and an ALTER → the tuple comparison fails. This is what makes the coexistence by-construction-safe rather than vigilance-dependent.

**Caveat (documented, not covered):** the parity test proves *current live models == migration chain* as evaluated by the **test's** SQLite engine. It does NOT prove test-env↔prod-env DDL parity across database engines. Negligible here (SQLite→SQLite, two trivial columns), but named so a future Postgres swap does not inherit a false sense of cross-engine coverage — at that point the chain would need re-validation against Postgres DDL.

### 7.2 Runner-logic tests

- `pending([...], {...})` — pure: returns sorted unapplied versions; empty when all applied; preserves order.
- `apply_pending` on an empty in-memory DB with a tiny temp migration set: executes each, records each, returns the list; a second call is a no-op (returns `[]`).
- `apply_pending` records nothing for a `.sql` that errors, and raises (→ non-zero exit).
- `stamp_all` records every discovered version without executing (assert the columns are NOT created by it — only recorded).
- `stamp(versions)` records exactly the named versions.
- `ensure_table` is idempotent (safe to call when the table already exists).

(Runner-logic tests may use a temporary migrations directory fixture so they don't depend on the real 000/001/002 contents; the parity test uses the real files.)

---

## 8. Prod rollout — hard gated sequence

Reversing this bricks startup, so it is ordered with a gate.

0. **Back up the prod DB first** (restore-from-backup is the only revert path, §9):
   `ssh myflix 'cp ~/projects/IronLog-V2/ironlog.db ~/projects/IronLog-V2/ironlog.db.bak-$(date +%Y%m%d-%H%M)'` (or `sqlite3 … ".backup …"`).
1. **stamp-all** (prod already has all three from create_all history + the two hand-run ALTERs):
   `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/python -m ironlog.migrate stamp-all'`
2. **GATE:** `ssh myflix 'cd ~/projects/IronLog-V2 && sqlite3 ironlog.db "SELECT count(*) FROM schema_migrations"'` must be **3**. Do not proceed otherwise.
3. **Only then** add `ExecStartPre` to `deploy/ironlogv2.service`, reinstall the unit (`sudo install …`), `sudo systemctl daemon-reload`, `sudo systemctl restart ironlogv2.service`.
4. Verify: service active, `apply_pending` logged "nothing to apply" (or no migration output), all endpoints healthy.

**Why the gate:** if ExecStartPre is added before stamp-all, the first restart runs `apply_pending` against an empty `schema_migrations`, tries to execute `001`/`002` on a DB that already has those columns → duplicate-column error → ExecStartPre exits non-zero → the service won't start. The gate makes that impossible.

The `ExecStartPre` line is added to the unit file **in the repo** as part of this work, but is only *installed on myflix* during step 3 (after the stamp-all gate passes).

---

## 9. Revert / rollback

Forward-only. To revert: stop the service, restore the pre-rollout DB backup from step 0, redeploy the prior code revision, restart. There are no down-migrations (out of scope, §10). For a one-user hobby DB this is the right weight.

---

## 10. Out of scope (explicit YAGNI)

- **Down/rollback migrations** — forward-only; revert = restore from backup.
- **Alembic** — graduate later only if migration count, branching, or autogenerate demands it.
- **Postgres / cross-engine DDL** — the `.sql` is SQLite-flavored; `db.py` already notes the future URL swap. The parity caveat (§7.1) flags the cross-engine gap.
- **Auto-baselining on an arbitrary existing DB** — the runner does not introspect live columns to self-decide applied/not (the fragile PRAGMA-probe path Alembic exists to avoid). Fresh DBs stamp via seed; the one existing prod DB stamps via the §8 rollout.
- **Migrating the client repo** — unrelated.

---

## 11. Architecture invariants honored

| Invariant | How this honors it |
|---|---|
| **Drift impossible by construction** | ExecStartPre runs `apply_pending` before every serve; the parity test fails CI on a forgotten migration. Not vigilance — structure. |
| **`engine/` is pure logic** | The runner is `ironlog/migrate.py` (sibling to `db.py`), never in `engine/`. `pending()` is pure and unit-tested; DB-touching apply/stamp are isolated. |
| **One implementation, many callers** | A single `migrate.py` serves ExecStartPre, seed, and dev. |
| **Fail loud** | A bad migration aborts ExecStartPre non-zero; the old process keeps serving; the failure is visible in journal — the opposite of this week's silent drift. |
| **Locked reference data** | Migrations change *schema*, not seeded reference values; `seed.py` still owns reference data and now stamps migrations after building it. |

---

## 12. Approvals

| Step | Status | Date |
|---|---|---|
| Mechanism: schema_migrations + ordered .sql + canonical runner | approved | 2026-06-25 |
| Hook: systemd ExecStartPre (not lifespan); one entry point for all callers | approved | 2026-06-25 |
| Fresh-DB: create_all + stamp_all; same stamp primitive for prod backfill | approved | 2026-06-25 |
| Module shape: pure pending() + DB apply/stamp in ironlog/migrate.py | approved | 2026-06-25 |
| Parity test: live create_all vs 000+001+002, order/affinity-correct map diff | approved | 2026-06-25 |
| 000_baseline GENERATED from v0.2.0 (32e8ec0), not hand-written | approved | 2026-06-25 |
| Gated prod rollout: backup → stamp-all → verify 3 rows → ExecStartPre + restart | approved | 2026-06-25 |
| Parity-caveat doc line (no cross-engine coverage) + backup step 0 | approved | 2026-06-25 |
| Spec written | this commit | 2026-06-25 |
| User review of spec | pending | — |
| Implementation plan (`writing-plans` skill) | not yet started | — |
