# Migrations

Ordered, forward-only SQL migrations applied by `ironlog/migrate.py`. Tracked
in the `schema_migrations(version, applied_at)` table; version = the filename
stem. See `docs/superpowers/specs/2026-06-25-migrations-design.md` for the full
design.

## Authoring rule (READ BEFORE WRITING A MIGRATION)

**Every migration file must be single-statement-atomic OR fully idempotent.**

The runner executes each `.sql` via `sqlite3`'s `executescript`, which issues an
implicit `COMMIT`. A multi-statement script that fails partway therefore leaves
the earlier statements **committed but the migration unrecorded** — the next
restart re-runs it and (for a non-idempotent statement like `ALTER ADD COLUMN`)
fails with a duplicate error, which fails `ExecStartPre` and the service won't
start. So:

- **Single statement per file** (one `ALTER`, one `CREATE`, …) — SQLite makes a
  single statement atomic. This is the default; prefer it.
- **OR fully idempotent** — every statement guarded (`CREATE TABLE IF NOT
  EXISTS`, `CREATE INDEX IF NOT EXISTS`). `000_baseline.sql` is the example.

Do **not** put multiple non-idempotent statements (e.g. two `ALTER ADD COLUMN`s)
in one file until the runner supports true per-statement transactional
execution. If you need several non-idempotent changes, use several files. This
contract is pinned by `tests/test_migrations.py::test_executescript_non_atomicity_first_stmt_persists_on_failure`.

### Carve-out: purely-additive schema changes may be multi-statement (2026-07-03)

The rule guards against **silent data corruption** — a data `UPDATE`/`INSERT`/`DELETE`
that half-applies leaves *wrong data with no error*, discovered months later. A
**purely-additive schema** change (`ADD COLUMN`, `CREATE INDEX`, `CREATE TABLE`)
does not have that failure mode: a partial failure is a **loud** `duplicate`
error at re-run, not silent corruption. So a migration whose statements are
**all additive schema** may be multi-statement (e.g. `016_progression_engine_schema.sql`
adds 10 columns in one file). **Data changes (`UPDATE`/`INSERT`/`DELETE`, or a
backfill after `ADD COLUMN`) remain strictly single-statement or idempotent**
until the runner supports per-statement atomicity (banked follow-up: split-on-`;`
+ record/savepoint each — do it before the first migration that needs
multi-statement data changes). The line is drawn at the *failure mode*, not the
syntactic shape of the file.

## Parity invariant

The model layer (`create_all`) and these migrations are two expressions of the
same schema. `tests/test_migrations.py::test_chain_matches_create_all` diffs a
live-`create_all` DB against the `000+001+002+…` chain as an order-independent
`{col: (type, notnull, dflt_value, pk)}` map. **A model schema change must come
with a migration**, and the migration's DDL must match what `create_all` emits
(declared type string, nullability, default) — the parity test fails CI
otherwise. (This is why `001` uses `VARCHAR(6)` not `TEXT`, and why the
`consecutive_failed_progressions` model field carries `server_default=text("0")`
so both paths emit `DEFAULT 0`.)

## Fresh DB vs existing DB

- **Fresh** (`seed.py`): `create_all` builds the current schema, then
  `migrate.stamp_all()` records every migration applied without executing — the
  runner then has nothing to do.
- **Existing**: `apply_pending` runs only the unapplied migrations in order.
- **Production rollout** (one-time, gated): back up the DB → `stamp-all` →
  verify `schema_migrations` has the expected row count → only then install the
  `ExecStartPre` hook and restart. Reversing that order bricks startup.
