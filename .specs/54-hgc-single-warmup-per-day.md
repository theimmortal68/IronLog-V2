# Spec 54 — HGC condensed week: one warmup per calendar day, not per mini-session

## Objective

Mirror of spec 53, opposite direction. Like the finisher fix, every HGC
condensed-week mini-session carries its source day's `program_day_id` in
`signature`, and both `/sessions/today` and `/sessions/{session_id}`
unconditionally build and attach a warmup (`build_warmup_payload`) whenever
`program_day_id` is present. The athlete only wants ONE warmup per real
training day, on the FIRST mini-session (unlike the finisher, which belongs
on the LAST). Fix: only the FIRST mini-session (by the existing
`MINI_SESSIONS` list order) for a given calendar date should carry a warmup;
later same-date mini-sessions must suppress it. Finisher gating (spec 53,
already merged/live) and everything else is unaffected.

## File Targets

- `ironlog/api/app.py` — `get_today_session` (`/sessions/today`) and
  `get_session_detail` (`/sessions/{session_id}`) handlers.
- `scripts/build_hgc_condensed_week.py` — the `apply()` function's
  `signature=` construction for each mini-session `LogSession`
  (already has a `show_finisher` key from spec 53 — ADD a sibling
  `show_warmup` key alongside it, do not remove or restructure the existing
  key).

## Changes

### `scripts/build_hgc_condensed_week.py`

Add a `_is_first_for_date` helper, mirroring the existing `_is_last_for_date`
(already present from spec 53) but scanning backward instead of forward:

```python
def _is_first_for_date(mini_sessions, idx0):
    """idx0: 0-based index into mini_sessions. True iff no EARLIER entry
    shares this entry's date."""
    this_date = mini_sessions[idx0][0]
    return not any(
        other_date == this_date
        for other_date, _, _ in mini_sessions[:idx0]
    )
```

Thread it into the existing `signature` dict alongside `show_finisher`
(do not touch the `show_finisher` line itself):

```python
signature={
    "program_day_id": program_day.id,
    "show_finisher": _is_last_for_date(MINI_SESSIONS, idx - 1),
    "show_warmup": _is_first_for_date(MINI_SESSIONS, idx - 1),
},
```

Given the CURRENT `MINI_SESSIONS` list (11 entries), the resulting
`show_warmup` values by mini-session index are:
```
1 (D1, 07-27): True    2 (D2, 07-27): False   3 (D6, 07-27): False
4 (D5, 07-28): True    5 (D2, 07-28): False   6 (D6, 07-28): False
7 (D1, 07-28): False
8 (D4, 07-29): True    9 (D6, 07-29): False   10 (D1, 07-29): False
11 (D5, 07-29): False
```
(only the FIRST mini-session chronologically listed for each date gets
`True` — the opposite selection from `show_finisher`, which is why this
needs its own independent flag rather than reusing/inverting
`show_finisher`: a date could in principle have only one mini-session, in
which case BOTH flags are `True` for that same single entry.)

### `ironlog/api/app.py`

In both `get_today_session` and `get_session_detail`, gate the warmup build
on the new `show_warmup` signature key, defaulting to `True` when absent
(same reasoning as spec 53's `show_finisher` default — ordinary non-HGC
`/generate` sessions never set this key and must be completely unaffected):

```python
warmup = (
    build_warmup_payload(db, program_day_id)
    if program_day_id is not None and (ws.signature or {}).get("show_warmup", True)
    else None
)
```

This replaces the existing:
```python
warmup = (
    build_warmup_payload(db, program_day_id)
    if program_day_id is not None else None
)
```
in BOTH handlers. The `finisher` block directly below it (spec 53's fix) is
UNCHANGED — do not touch it.

## Edge Cases

- A calendar date with only ONE mini-session: both `show_warmup` and
  `show_finisher` are `True` for it (no earlier AND no later same-date
  entry) — it shows both, same as before either fix.
- Ordinary (non-HGC) generated sessions from `/generate`: `signature` never
  contains a `show_warmup` key, so `.get("show_warmup", True)` must default
  to `True` and preserve today's behavior exactly — the same critical
  invariant spec 53 protected for `show_finisher`.
- `MINI_SESSIONS` reordering in the future: `_is_first_for_date` must be
  computed from the list's *current* order at build time (mirrors
  `_is_last_for_date`'s existing recompute-from-current-order property).

## Dependencies

None (spec 53 is already merged and live; this spec only adds a sibling
key/gate, does not modify anything spec 53 introduced).

## Verification

- `tests/test_hgc_condensed_week.py`: extend the existing
  `test_hgc_condensed_week_marks_only_last_mini_session_per_date_for_finisher`
  test (or add a sibling test) asserting `signature["show_warmup"]` matches
  the "first mini-session for this date" table above, for all 11 entries —
  by real values, not by re-deriving the same logic under test.
- `tests/test_session_read_endpoints.py`: add tests mirroring the existing
  `show_finisher` ones — (a) `show_warmup: False` in `signature` →
  `/sessions/today` response has `warmup: null` even though a real warmup
  would otherwise build; (b) `show_warmup` key absent → warmup IS
  populated, proving the default-True path for ordinary non-HGC sessions.
- Full suite: `~/projects/IronLog-V2/.venv/bin/python -m pytest -q` must
  stay fully green (baseline 698 passed after spec 53) — this spec changes
  no existing test's expected outcome, only adds new coverage.
- Manual/functional: after merge, backfill `signature["show_warmup"]` onto
  the 11 already-created live HGC sessions (ids 28-38) per the table above
  — Tier A does this directly as a data-only op, same as spec 53's backfill.
