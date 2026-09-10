# Spec 11: Generate + surface the per-day warmup block

## Objective
The `/generate` endpoint's own scope marker admits this gap explicitly: `"main-work-only; warmups/Z2 per program doc, not yet in-app"`. Warmup content already has a complete, athlete-authored source (`docs/program/phase1-warmup-finisher-source.yaml`, `d1`-`d6` keys, each with a `movement_flow_seconds` + `items` list and an `activation_seconds` + `items_activation` list). Load this into the DB once, and surface it on the session response exactly like the existing `finisher` field, so the client can render it.

## Background — why this is simpler than the finisher, and how it should be modeled
Unlike the finisher (`DayFinisher` + `_finisher_state`'s progression logic in `ironlog/generation/assembler.py`), warmup content is **static and unprogressed** — the yaml itself says so per item (`progression: none` for the one item that calls it out explicitly, and no other warmup item in the source file has any progression/ladder concept at all — that's exclusively a finisher trait, see `finisher_d6_progression`). Warmup items also aren't tied to `Movement` rows — `scap_cars`, `floor_slides`, `worlds_greatest`, etc. are mobility/activation drill names, not tracked lifts with progression state — so a `DayFinisher`-style relational table (FK to `movement.id`) is the wrong shape here.

**Model choice: a single JSON column on `ProgramDay`**, not a new table. This matches existing precedent in this codebase for flexible, non-relational per-row payloads (`Session.signature: dict` via `Column(JSON)`, `PlannedSet.band_config: Optional[list]` via `Column(JSON)`, `Note.classification_meta: Optional[dict]` via `Column(JSON)`) — warmup's item shape varies per entry (`sets`+`reps`, or `seconds`, or `reps_per_side`, or `hold_seconds`, etc.), which a rigid relational schema would force into a sparse, mostly-null table for no benefit given there's no query, join, or progression need against individual warmup items.

## The fix

### 1. Migration (additive, single-statement)
New file `deploy/migrations/027_program_day_warmup_config.sql`:
```sql
ALTER TABLE programday ADD COLUMN warmup_config JSON;
```
Follow the existing single-statement-atomic rule in `deploy/migrations/README.md` — one `ALTER ADD COLUMN`, nothing else in this file. Add the parity-keystone update: `tests/test_migrations.py::test_chain_matches_create_all` must stay green (mirrors how migration 026 was verified for `DayFinisher`).

### 2. Model field
`ironlog/models/program.py`'s `ProgramDay`: add
```python
warmup_config: Optional[dict] = Field(default=None, sa_column=Column(JSON))
```
(mirror the existing `Column(JSON)` import/usage already present in this models package, e.g. in `session.py`'s `Session.signature`).

### 3. Seed data
- **Seed source** (`ironlog/generation/program_seed.py`): for each of D1-D6 (skip D3/D7 rest days — the yaml has no `d3`/`d7` keys, confirm `ProgramDay.is_rest` rows get `warmup_config=None`), set `warmup_config` to a dict matching the yaml's per-day shape exactly:
  ```python
  {
      "movement_flow_seconds": <int>,
      "items": [ {..each item dict verbatim, e.g. {"name": "scap_cars", "sets": 2, "reps": 5}..} ],
      "activation_seconds": <int>,
      "items_activation": [ {..verbatim..} ],
  }
  ```
  Parse `docs/program/phase1-warmup-finisher-source.yaml`'s `d1`.."d6" top-level keys' `warmup` sub-dict directly (use `yaml.safe_load` reading the file at seed time, or hand-transcribe verbatim into the Python seed source — follow whichever pattern `program_seed.py` already uses for the finisher/main-work seed data one function above/below the insertion point; do NOT invent new field names or reshape the yaml's structure).
- **Live-DB one-off script** (new, e.g. `ironlog/generation/live_seed_warmup.py`, idempotent — mirror `live_seed_ramp_and_finishers.py`'s pattern of "update if warmup_config is null, no-op if already set"): backfills the 6 existing `ProgramDay` rows on the running production DB, since `program_seed.py`'s full seeder cannot run against production (same constraint noted for the finisher/ramp rollout).

### 4. Assembler wiring
`ironlog/generation/assembler.py`: add
```python
def build_warmup_payload(db: Session, program_day_id: int) -> Optional[Dict[str, Any]]:
    """Static, unprogressed per-day warmup block — no state, no movement FK."""
    program_day = db.get(ProgramDay, program_day_id)
    if program_day is None or program_day.warmup_config is None:
        return None
    return dict(program_day.warmup_config)
```
Call it alongside the existing `build_finisher_payload(db, program_day_id)` call site(s) in the assembler's session-build function (same function that currently sets `finisher=finisher` around assembler.py:561/568).

### 5. Schema + API wiring
- `ironlog/api/schemas_capture.py`: add
  ```python
  class WarmupOut(BaseModel):
      movement_flow_seconds: int
      items: List[Dict[str, Any]]
      activation_seconds: int
      items_activation: List[Dict[str, Any]]
  ```
  and add `warmup: Optional[WarmupOut] = None` to `SessionDetailResponse`, following `finisher`'s exact placement/pattern.
- `ironlog/api/app.py`: thread `warmup=build_warmup_payload(db, program_day_id)` through `_serialize_session` and both call sites that currently pass `finisher=...` (lines ~223, ~596-597, ~633-634 per the finisher pattern) — same shape, same parallel structure.

## File targets
- New: `deploy/migrations/027_program_day_warmup_config.sql`
- New: `ironlog/generation/live_seed_warmup.py`
- Modify: `ironlog/models/program.py` (`ProgramDay.warmup_config`)
- Modify: `ironlog/generation/program_seed.py` (D1-D6 `warmup_config` values)
- Modify: `ironlog/generation/assembler.py` (`build_warmup_payload`, wire into session-build call site)
- Modify: `ironlog/api/schemas_capture.py` (`WarmupOut`, `SessionDetailResponse.warmup`)
- Modify: `ironlog/api/app.py` (thread `warmup=` through `_serialize_session` and its callers)
- New/modify tests: `tests/test_migrations.py` (parity keystone still green), a new test asserting `build_warmup_payload` returns the correct dict for a seeded D1..D6 `program_day_id` and `None` for a rest day.

## Edge cases
- **Rest days (D3, D7) must return `warmup=None`** on the session response — there is no `d3`/`d7` key in the source yaml, so `ProgramDay.warmup_config` stays `NULL` for those rows; don't synthesize an empty block.
- **No progression, ever** — do not add any state/ladder concept to warmup; if a future athlete request wants warmup progression, that's a new spec, not something to half-build here.
- **Verbatim item transcription** — do not rename, reshape, or "clean up" the yaml's item dicts (e.g. don't rename `reps_per_side` to `repsPerSide` or split `seconds` into `duration_seconds`) — the client will consume these keys directly, so drift here becomes a client-side parsing bug.
- **`FINISHER_DURATION_THEN_ROPE`-style progression stays completely untouched** — this spec does not touch `_finisher_state`, `build_finisher_payload`, or anything under `DayFinisher`.

## Dependencies
None — additive schema change, no HUMAN GATE required (additive-only carve-out per `deploy/migrations/README.md`). Independent of any other in-flight spec.

## Verification
- `tests/test_migrations.py::test_chain_matches_create_all` green (parity keystone).
- New test: `build_warmup_payload` for each of D1-D6's `program_day_id` returns the exact yaml-sourced dict; a rest-day `program_day_id` returns `None`.
- Full server suite green: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q'`.
- Manual: after live-seeding, `curl http://localhost:8000/sessions/<id>` for a real D1-D6 session shows a populated `warmup` object matching the yaml.
