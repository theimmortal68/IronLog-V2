# Spec: WeekParityRotation — automatic week-A/B movement rotation

## Objective

Add a new, automatic (no manual toggle) week-alternation mechanism so a single
program slot (TierExercise) can rotate between two movements — and optionally
two different rep targets — based on the current calendar's ISO-week parity,
with zero new state to maintain and zero manual intervention required at
generation time.

## Background / why this exists

`ironlog/generation/skeleton.py` already has a `MesoRotation` mechanism that
lets a `TierExercise` slot resolve to a *different* movement for a given
`meso_number` (a long training-block counter). But `meso_number` is **never
actually supplied** at the real `/generate` call site (`ironlog/api/app.py`'s
`generate()` calls `lay_skeleton(req.day_role, db)` with no `meso_number` —
it always defaults to `1`). MesoRotation is effectively dormant in production.

We need a *shorter*-granularity rotation: alternate a slot's movement (and
optionally its rep target) week-to-week, driven automatically from the
current date — no manual DB edit, no note-apply flow, no meso tracking.

## Files to touch

1. `ironlog/models/program.py` — new `WeekParityRotation` SQLModel table.
2. `ironlog/generation/skeleton.py` — resolution logic + `lay_skeleton`
   signature change.
3. `deploy/migrations/041_week_parity_rotation.sql` — new forward-only
   migration (see `deploy/migrations/021_slot_movement_override.sql` for the
   exact style/conventions this project's migration runner expects —
   `CREATE TABLE IF NOT EXISTS`, explicit FK clauses, a separate
   `CREATE INDEX IF NOT EXISTS` statement on the FK column matching SQLModel's
   own auto-generated index naming, e.g. `ix_weekparityrotation_tier_exercise_id`).
4. `tests/test_skeleton_week_parity.py` — new test file (see existing
   `tests/test_slot_override_skeleton.py` for the pattern this project uses
   to test `SlotMovementOverride` resolution in `lay_skeleton` — mirror its
   style/fixtures).

## 1. New model — `ironlog/models/program.py`

Add near `MesoRotation` (read that class first for the exact conventions —
docstring style, `Field` usage, no `from __future__ import annotations` in
this file, matching every other file in this project):

```python
class WeekParityRotation(SQLModel, table=True):
    """Per-slot movement (+ optional rep-target) override keyed by ISO-week
    parity, resolved automatically from the current date at generation time
    -- no manual toggle, no note-apply flow. Two rows per rotating slot: one
    week_parity="A" (even ISO week), one week_parity="B" (odd ISO week).

    Precedence in lay_skeleton's _effective_movement_id: an active
    SlotMovementOverride still wins first (explicit live-state swap always
    takes priority), then a matching WeekParityRotation row for the current
    date's parity, then MesoRotation(meso_number), then te.movement_id
    (unchanged fallback order, WeekParityRotation inserted as a new tier).

    rep_low/rep_high are optional: when set, they override the TierExercise's
    own rep_low/rep_high for the SlotSpec/AnchorSpec built for the matched
    week (so two rotating movements can carry genuinely different rep
    targets, not just different movement identities). When left None, the
    TierExercise's own rep_low/rep_high apply unchanged.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    tier_exercise_id: int = Field(foreign_key="tierexercise.id", index=True)
    week_parity: str  # "A" or "B" -- validated by callers, not a DB constraint
    movement_id: int = Field(foreign_key="movement.id")
    rep_low: Optional[int] = None
    rep_high: Optional[int] = None
```

## 2. Resolution logic — `ironlog/generation/skeleton.py`

- Add `from datetime import date` to imports; add `WeekParityRotation` to the
  `from ironlog.models.program import (...)` import.
- New pure helper:
  ```python
  def week_parity(as_of: date) -> str:
      """"A" for even ISO week numbers, "B" for odd. No stored anchor --
      purely a function of the calendar date."""
      return "A" if as_of.isocalendar()[1] % 2 == 0 else "B"
  ```
- `lay_skeleton(day_role: str, db: Session, meso_number: int = 1, program_id: Optional[int] = None, as_of: Optional[date] = None) -> Skeleton`:
  add the new `as_of` parameter (keyword, after the existing ones — do not
  reorder existing params, callers use them positionally in places). Inside
  the function body, resolve `effective_as_of = as_of if as_of is not None else date.today()`
  once, near the top (do NOT call `date.today()` more than once per
  `lay_skeleton` call — determinism/consistency within one generation).
- `_effective_movement_id` needs the resolved week-rotation row (or `None`)
  threaded in, plus needs to also return rep_low/rep_high overrides. Cleanest
  shape: replace the single `_effective_movement_id(db, te, meso_number) -> int`
  helper with a small resolved-value struct so callers get movement_id AND
  optional rep overrides from ONE lookup pass (avoid two separate DB queries
  disagreeing). Something like:
  ```python
  @dataclass
  class _ResolvedSlot:
      movement_id: int
      rep_low: Optional[int] = None
      rep_high: Optional[int] = None

  def _resolve_slot(db: Session, te: TierExercise, meso_number: int, as_of: date) -> _ResolvedSlot:
      ov = db.exec(select(SlotMovementOverride).where(
          SlotMovementOverride.tier_exercise_id == te.id,
          SlotMovementOverride.override_type == OverrideType.MOVEMENT,
          SlotMovementOverride.active == True)).first()  # noqa: E712
      if ov is not None:
          return _ResolvedSlot(movement_id=ov.override_movement_id)

      wpr = db.exec(select(WeekParityRotation).where(
          WeekParityRotation.tier_exercise_id == te.id,
          WeekParityRotation.week_parity == week_parity(as_of))).first()
      if wpr is not None:
          return _ResolvedSlot(movement_id=wpr.movement_id, rep_low=wpr.rep_low, rep_high=wpr.rep_high)

      mr = db.exec(select(MesoRotation).where(
          MesoRotation.tier_exercise_id == te.id,
          MesoRotation.meso_number == meso_number)).first()
      if mr is not None:
          return _ResolvedSlot(movement_id=mr.movement_id)

      return _ResolvedSlot(movement_id=te.movement_id)
  ```
  Then in `lay_skeleton`'s loop, replace the two call sites
  (`_effective_movement_id(db, te, meso_number)`) with
  `resolved = _resolve_slot(db, te, meso_number, effective_as_of)`, use
  `resolved.movement_id` where `movement_id` was used, and when building each
  `AnchorSpec`/`SlotSpec`, use `resolved.rep_low if resolved.rep_low is not None else te.rep_low`
  (same pattern for `rep_high`) instead of the bare `te.rep_low`/`te.rep_high`.
- **Keep `_effective_movement_id` as a thin wrapper** if anything outside
  `skeleton.py` imports it directly (`grep -rn "_effective_movement_id"
  ironlog/ tests/` first to check) — if nothing external depends on it,
  it's fine to remove it and inline `_resolve_slot` calls directly, but
  check before deleting.
- Docstring on `lay_skeleton` needs a short update describing the new
  precedence order and the `as_of` param (mirror the existing docstring's
  style for `anchor_movement_ids`/`adaptive_slots`).

## 3. Migration — `deploy/migrations/041_week_parity_rotation.sql`

Follow `021_slot_movement_override.sql` exactly for style. Comment header,
`CREATE TABLE IF NOT EXISTS weekparityrotation (...)` with all 6 columns
(`id`, `tier_exercise_id`, `week_parity`, `movement_id`, `rep_low`,
`rep_high`), correct `NOT NULL` on `id`/`tier_exercise_id`/`week_parity`/
`movement_id` (rep_low/rep_high nullable), `PRIMARY KEY (id)`, two
`FOREIGN KEY` clauses, then a separate `CREATE INDEX IF NOT EXISTS
ix_weekparityrotation_tier_exercise_id ON weekparityrotation
(tier_exercise_id);` line. Match column types to what SQLModel's
`create_all()` would generate for this table (INTEGER for the two rep
columns, TEXT for week_parity) — verify column types against how
`021_slot_movement_override.sql`'s BOOLEAN/DATETIME choices map to the
SQLModel field types in `SlotMovementOverride`, and use the equivalent
mapping for this new table's field types.

## 4. Tests — `tests/test_skeleton_week_parity.py`

Read `tests/test_slot_override_skeleton.py` first and mirror its fixture
style (it builds a minimal Tier/TierExercise/Movement setup directly against
`gen_db`, not the full seeded program — do the same here, don't depend on
D2/D5's real program wiring). Cover:

1. No `WeekParityRotation` rows for a slot → `lay_skeleton` resolves the
   slot's own `te.movement_id`/`rep_low`/`rep_high` unchanged (regression
   guard — the new code path must be a no-op when unused).
2. A slot with both an "A" and a "B" `WeekParityRotation` row → calling
   `lay_skeleton(..., as_of=<some date in an even ISO week>)` resolves to the
   "A" row's `movement_id`/`rep_low`/`rep_high`; calling with an odd-ISO-week
   date resolves to "B"'s. Pick two literal, uncontroversial dates for this
   (e.g. any Monday of an ISO-even week and any Monday of an ISO-odd week —
   compute/verify their `isocalendar()[1]` values in the test itself with a
   comment, don't hand-guess).
3. A `WeekParityRotation` row with `rep_low`/`rep_high` left `None` → the
   `SlotSpec`/`AnchorSpec` still carries the TierExercise's own rep values
   (partial-override case).
4. An active `SlotMovementOverride` on the same slot as a matching
   `WeekParityRotation` row → the `SlotMovementOverride` wins (precedence
   regression guard).
5. `week_parity(as_of)` unit-tested directly for a couple of literal dates
   (even/odd ISO week), independent of `lay_skeleton`.

## Explicitly out of scope for this dispatch

- Do NOT touch `ironlog/generation/program_seed.py`, `ironlog/seed.py`,
  `docs/program/phase1-seed-source.yaml`, `ironlog/generation/baseline_seed.py`,
  or `ironlog/generation/rule_wiring.py`. Wiring D2/D5's actual Nordic Curl
  slot to use this new mechanism, and creating the new angle-based Movement,
  happens in a separate follow-up pass by the orchestrator directly — this
  dispatch is the generic mechanism only.
- Do NOT change `lay_skeleton`'s call sites in `ironlog/generation/loop.py`
  or `ironlog/api/app.py` — no caller needs to pass `as_of` explicitly for
  this to work correctly (it defaults to `date.today()`).

## Acceptance criteria

- `~/projects/IronLog-V2-wt-week-rotation/.venv/bin/pytest -q` — wait, this
  worktree has no `.venv` (worktrees don't carry it). Run tests via:
  `cd /home/jstout/projects/IronLog-V2-wt-week-rotation && ~/projects/IronLog-V2/.venv/bin/python -m pytest -q`
  Full suite must pass, including the new test file.
- `git log` in the worktree shows a real commit (or commits) — this task's
  finished-state contract is "committed", not just "diff exists".
- Scope check: only the 4 files listed in "Files to touch" above should
  appear in `git diff main..HEAD --stat` (plus the new test file).
