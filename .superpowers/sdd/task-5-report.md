# Task 5 Report — Typed stall signal

**Status:** DONE
**Branch:** `feat/progression-engine`
**Scope:** `ironlog/engine/stall.py` enrichment only — pure module, no DB/HTTP.

## Note on this file

The working tree had a stale, uncommitted version of `task-5-report.md` on
disk before this task started, titled "phase_intent + per-slot rep_scheme in
payload" on branch `feat/payload-enrichment` — an unrelated feature, not part
of the 6-task progression-engine plan (`docs/superpowers/plans/2026-07-03-progression-engine.md`)
this repo is currently building. It did not match `task-5-brief.md` (typed
stall signal) and was overwritten. Flagging in case it was orphaned
in-progress work from a concurrent session sharing this NFS-mounted repo —
nothing from that content was folded in here since it belongs to a different
feature/branch.

## What Changed

### `ironlog/engine/stall.py`

- Added `StallSeverity`/`StallType` imports from `ironlog.models.enums` (both
  enums already existed there, defined by Task 1's schema work).
- Added constant `STALL_FAILED_HIGH_MULT = 2` next to the existing
  `STALL_WINDOW` / `STALL_MIN_SESSIONS` / `STALL_EPSILON_PCT` /
  `STALL_FAILED_THRESHOLD`.
- Added `_window_trend_pct(progress_e1rms)` — percent change from the start to
  the end of the same trailing `STALL_WINDOW` slice `detect_stall` inspects.
- Added `_is_extended_flat(progress_e1rms)` — True when the *whole* history
  (not just the last window) is flat within `STALL_EPSILON_PCT`, used to
  upgrade a plateau to `high` severity.
- Added `build_stall_signal(movement_id, day_id, consecutive_failed,
  progress_e1rms, current_load, limiting_muscle) -> Optional[dict]`:
  1. Calls the existing `detect_stall(progress_e1rms, consecutive_failed,
     Objective.PROGRESS)` for the core stalled/not-stalled gate — reuses it
     verbatim, no reimplementation of the two-arm logic.
  2. Returns `None` immediately if `signal.stalled` is `False`.
  3. Otherwise classifies:
     - `consecutive_failed >= STALL_FAILED_THRESHOLD` → `FAILED_PROGRESSION`;
       severity `high` at `>= STALL_FAILED_THRESHOLD * STALL_FAILED_HIGH_MULT`
       (4), else `low`. This branch takes priority over the trend arm (a
       lift can be both failed- and trend-stalled simultaneously; the failed
       signal is the more actionable one).
     - Else if the window's trend is negative beyond `STALL_EPSILON_PCT` →
       `REGRESSION` (severity `high` if the decline is beyond
       `2×STALL_EPSILON_PCT`, else `medium`).
     - Else → `PLATEAU` (severity `high` on an extended flat window per
       `_is_extended_flat`, else `medium`).
  4. Returns a dict with keys `movement_id`, `day_id`, `stall_type`,
     `severity`, `duration_sessions`, `current_load`, `e1rm_trend`,
     `limiting_muscle` — **no `is_swappable` key**, per the brief.
- `stall_type`/`severity` are emitted as the enum `.value` strings (plain
  `"FAILED_PROGRESSION"` / `"low"` etc.), matching the test assertions and the
  existing `str, Enum` convention (JSON/DB-clean).
- No `from __future__ import annotations` added (project-wide constraint);
  module stays pure — no imports beyond `dataclasses`, `typing`, and the
  sibling enums module.

### `tests/test_stall_signal.py` (new, verbatim from the brief)

5 tests: failed-progression low→high severity escalation, plateau from a flat
e1RM trend, regression from a negative trend, no-stall returns `None`, and a
guard that `is_swappable` is never a key in the signal.

## TDD Process

- **RED:** wrote the test file first; `pytest tests/test_stall_signal.py -q`
  failed at collection with `ImportError: cannot import name
  'build_stall_signal'` — confirmed failing for the right reason (function
  didn't exist yet), not a typo.
- **GREEN:** implemented `build_stall_signal` per the priority order above;
  reran — all 5 pass.
- **Full suite:** reran the whole repo's pytest — all pre-existing tests still
  green, no regressions.

## pytest tails

```
$ ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest tests/test_stall_signal.py -q'
.....                                                                    [100%]
5 passed in 0.06s

$ ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q'
325 passed, 432 warnings in 5.47s
```

(Warnings are pre-existing `datetime.utcnow()` deprecation noise repo-wide,
unrelated to this change.)

## Concerns

- None on the implementation. `duration_sessions` and `e1rm_trend`'s exact
  numeric semantics aren't pinned by the brief's tests (only `stall_type`,
  `severity`, `limiting_muscle`, and the absence of `is_swappable` are
  asserted) — I picked reasonable values (consecutive_failed count for the
  failed arm; trailing-window length for the trend arm; `e1rm_trend` as a
  percent change) documented inline. If the caller (a later task wiring this
  into `run_analysis`/persistence) expects a different shape for those two
  fields, that's a one-line adjustment, not a redesign.
- Confirmed the stray pre-existing `task-5-report.md` content (see note above)
  was unrelated to this task before overwriting it.

## Commit

`feat(engine): typed stall signal (severity taxonomy over detect_stall, no is_swappable)`
