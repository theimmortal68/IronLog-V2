# Spec 53 — HGC condensed week: one finisher per calendar day, not per mini-session

## Objective

The HGC condensed-week builder creates multiple mini-sessions per calendar
date (e.g. today: a D1 slice, a D2 slice, a D6 slice, in that order). Each
mini-session's `signature` carries its source day's `program_day_id`, and
both `/sessions/today` and `/sessions/{session_id}` unconditionally build and
attach that day's `DayFinisher` (`build_finisher_payload`) whenever
`program_day_id` is present. Since D1/D2/D6 each have their own distinct
finisher (`kb_swing`, `sled_push`, D6's own), the athlete was shown a
different finisher on every mini-session of the same real training day and
only performed one. Fix: only the LAST mini-session (by the existing
`MINI_SESSIONS` list order) for a given calendar date should carry a
finisher; earlier same-date mini-sessions must suppress it. Warmup and
everything else on those sessions is unaffected.

## File Targets

- `ironlog/api/app.py` — `get_today_session` (`/sessions/today`) and
  `get_session_detail` (`/sessions/{session_id}`) handlers.
- `scripts/build_hgc_condensed_week.py` — the `apply()` function's
  `signature=` construction for each mini-session `LogSession`.

## Changes

### `scripts/build_hgc_condensed_week.py`

In `apply()`, `MINI_SESSIONS` is iterated as
`for idx, (sess_date, day_role, m_names) in enumerate(MINI_SESSIONS, 1):`.
Before that loop (or inline per-iteration), compute, for each index `idx`
(1-based), whether it is the LAST entry in `MINI_SESSIONS` sharing the same
`sess_date` as another later entry — i.e. `show_finisher = True` iff no
later entry in `MINI_SESSIONS` has the same `sess_date`. Concretely:

```python
def _is_last_for_date(mini_sessions, idx0):
    """idx0: 0-based index into mini_sessions. True iff no LATER entry
    shares this entry's date."""
    this_date = mini_sessions[idx0][0]
    return not any(
        other_date == this_date
        for other_date, _, _ in mini_sessions[idx0 + 1:]
    )
```

Call this once per mini-session (using `idx - 1` for the 0-based index,
since the existing loop's `idx` is 1-based via `enumerate(MINI_SESSIONS, 1)`)
and thread the result into the `signature` dict when constructing the
`LogSession`:

```python
session = LogSession(
    date=sess_date,
    day_role=day_role,
    phase=phase,
    status=SessionStatus.PLANNED,
    signature={
        "program_day_id": program_day.id,
        "show_finisher": _is_last_for_date(MINI_SESSIONS, idx - 1),
    },
    rationale=rationale_str,
)
```

Given the CURRENT `MINI_SESSIONS` list (11 entries, dates 2026-07-27 /
2026-07-28 / 2026-07-29), the resulting `show_finisher` values by mini-session
index are:
```
1 (D1, 07-27): False   2 (D2, 07-27): False   3 (D6, 07-27): True
4 (D5, 07-28): False   5 (D2, 07-28): False   6 (D6, 07-28): False
7 (D1, 07-28): True
8 (D4, 07-29): False   9 (D6, 07-29): False   10 (D1, 07-29): False
11 (D5, 07-29): True
```
(only the LAST mini-session chronologically listed for each date gets
`True` — mirrors "last workout of the day" regardless of which training
day it happens to slice from).

### `ironlog/api/app.py`

In both `get_today_session` and `get_session_detail`, gate the finisher
build on the new `show_finisher` signature key, defaulting to `True` when
absent (so ordinary, non-HGC generated sessions — which never set this key
— are completely unaffected):

```python
finisher = (
    build_finisher_payload(db, program_day_id)
    if program_day_id is not None and (ws.signature or {}).get("show_finisher", True)
    else None
)
```

This replaces the existing:
```python
finisher = (
    build_finisher_payload(db, program_day_id)
    if program_day_id is not None
    else None
)
```
in BOTH handlers (they currently have identical finisher-gating logic,
duplicated). Do NOT touch the `/generate` endpoint's finisher handling
(~line 355, `finisher=outcome.assembled.finisher`) — that is the live,
non-HGC real-day generation path, uses the in-memory assembled result
directly, and has no `signature`/`show_finisher` concept at all.

Warmup (`build_warmup_payload`) is UNCHANGED in both handlers — it must
still build for every mini-session regardless of `show_finisher`.

## Edge Cases

- A calendar date with only ONE mini-session: `_is_last_for_date` is
  trivially `True` for it (no later same-date entry exists) — it still
  shows its finisher, same as before this fix.
- Ordinary (non-HGC) generated sessions from `/generate` — their
  `signature` (set elsewhere, e.g. `commit_session`/the wizard flow) never
  contains a `show_finisher` key, so `.get("show_finisher", True)` must
  default to `True` and preserve today's behavior exactly. This is the
  single most important edge case — a wrong default here would silently
  hide finishers for the entire live/non-HGC flow.
- `MINI_SESSIONS` reordering in the future: the fix must be computed from
  the list's *current* order at build time (not hardcoded per index), so a
  future edit to `MINI_SESSIONS` (e.g. task 33's "final rebuild" happening
  again after some future program change) recomputes correctly without a
  manual `show_finisher` update.

## Dependencies

None — single self-contained spec, one worktree.

## Verification

- `tests/test_hgc_condensed_week.py`: add a test asserting, for the full
  `MINI_SESSIONS` list, that `apply()`-created sessions carry
  `signature["show_finisher"]` matching the "last mini-session for this
  date" rule (use the concrete expected values table above as the
  assertion, keyed by session index/day_role/date — NOT by re-deriving the
  same logic under test, to keep the test a real check rather than a
  tautology).
- Add or extend an `ironlog/api/app.py` test (find the existing
  `/sessions/today` test module, e.g. `tests/test_wizard_state_endpoint.py`
  or a dedicated sessions test file — search for `get_today_session` usage)
  covering: (a) a session with `show_finisher: False` in `signature` →
  `/sessions/today` response has `finisher: null` even though
  `program_day_id` resolves to a real `DayFinisher`; (b) a session with
  `show_finisher: True` (or the key absent) → finisher IS populated,
  proving the default-True path for ordinary non-HGC sessions is intact.
- Full suite: `~/projects/IronLog-V2/.venv/bin/python -m pytest -q` must
  stay fully green (currently 695 passed, 0 failed) — this spec changes no
  existing test's expected outcome, only adds new coverage.
- Manual/functional: after merge, this session's own live DB retroactively
  needs `signature["show_finisher"]` backfilled onto the 11 already-created
  HGC sessions (ids 28-38) per the table above — Tier A does this directly
  as a data-only op (mirrors this session's established pattern for prior
  live-DB backfills), not part of this code spec.
