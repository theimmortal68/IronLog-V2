# Spec 55 — HGC condensed week: finisher once per REAL training day, not per calendar date

## Objective

**Corrects spec 53's rule, which shipped with the wrong scoping.** Spec 53
made `show_finisher` True for the LAST mini-session of each *calendar date*.
This is wrong because a single real training day's content can be split
across multiple calendar dates in `MINI_SESSIONS` (e.g. "D1 Upper Push"
appears as mini-session 1 on 2026-07-27 AND mini-session 7 on 2026-07-28,
two different exercise slices of the same real day). Under spec 53's rule,
D1's finisher (`kb_swing`) would be shown again on 2026-07-28 even though
the athlete already performed it on 2026-07-27's D1 slice — the same real
day's finisher recurring mid-week.

Corrected rule (confirmed with the athlete): each real training day's
finisher is done ONCE, total, across the whole condensed week — attached to
that day's FIRST occurrence in `MINI_SESSIONS` (by list order, regardless of
which calendar date it falls on), never on any later occurrence of the same
`day_role`. `show_warmup` (spec 54, already merged/live) is UNCHANGED —
warmup stays scoped to "first mini-session of the calendar date" (a
physical per-day warm-up need, independent of which real program day's
content follows), not to `day_role` recurrence.

## File Targets

- `scripts/build_hgc_condensed_week.py` — replace `_is_last_for_date`'s use
  for `show_finisher` with a new `day_role`-scoped helper. `_is_last_for_date`
  itself becomes dead code once this lands (only ever used for
  `show_finisher`) — remove it rather than leave it unused.
- `tests/test_hgc_condensed_week.py` — the existing
  `test_hgc_condensed_week_marks_only_last_mini_session_per_date_for_finisher`
  test's expected `show_finisher` values are now WRONG per the corrected
  rule and must be rewritten (see the corrected table below). Do not touch
  the `show_warmup` assertions/test — those are still correct.
- No change needed to `ironlog/api/app.py` — the endpoint-side gating logic
  (`.get("show_finisher", True)`) is unaffected; only how the flag's VALUE
  is computed in the build script changes.

## Changes

### `scripts/build_hgc_condensed_week.py`

Remove `_is_last_for_date` (now dead). Add:

```python
def _is_first_occurrence_of_day_role(mini_sessions, idx0):
    """idx0: 0-based index into mini_sessions. True iff no EARLIER entry
    (any date) shares this entry's day_role."""
    this_role = mini_sessions[idx0][1]
    return not any(
        other_role == this_role
        for _, other_role, _ in mini_sessions[:idx0]
    )
```

Update the `signature` construction:

```python
signature={
    "program_day_id": program_day.id,
    "show_finisher": _is_first_occurrence_of_day_role(MINI_SESSIONS, idx - 1),
    "show_warmup": _is_first_for_date(MINI_SESSIONS, idx - 1),
},
```

(`show_warmup`'s line is UNCHANGED — still `_is_first_for_date`, the
calendar-date-scoped helper from spec 54. Only the `show_finisher` line's
source function changes.)

Given the CURRENT `MINI_SESSIONS` list (11 entries), the corrected
`show_finisher` values by mini-session index (day_role in parens):
```
1 (D1, 07-27): True    2 (D2, 07-27): True    3 (D6, 07-27): True
4 (D5, 07-28): True    5 (D2, 07-28): False   6 (D6, 07-28): False
7 (D1, 07-28): False
8 (D4, 07-29): True    9 (D6, 07-29): False   10 (D1, 07-29): False
11 (D5, 07-29): False
```
(True exactly once per distinct `day_role` — at its first appearance in the
list, i.e. indices 1/2/3/4/8, corresponding to the first-ever occurrence of
D1/D2/D6/D5/D4 respectively. This happens to coincide with `show_warmup`'s
True positions for this particular week's data — both are True only on
indices 1, 4, 8 — but that is a property of this week's specific ordering,
not a guarantee; the two flags are computed by genuinely independent rules
and must stay that way.)

## Edge Cases

- A `day_role` that appears only once in the whole list: trivially `True`
  at its one occurrence (no earlier entry can share its role).
- Do NOT conflate this with `show_warmup`'s date-scoped rule — they answer
  different questions (`show_finisher`: "has this REAL day's finisher
  already been done this week?"; `show_warmup`: "is this the first
  mini-session of THIS CALENDAR DATE?"). Keep them as two independently
  computed values even where their outputs happen to coincide.
- `MINI_SESSIONS` reordering in the future: `_is_first_occurrence_of_day_role`
  must be computed from the list's *current* order at build time (same
  recompute-from-current-order property as the date-scoped helpers).

## Dependencies

None — spec 53's `ironlog/api/app.py` gating and spec 54's `show_warmup`
logic are both already merged/live and untouched by this fix.

## Verification

- `tests/test_hgc_condensed_week.py`: rewrite
  `test_hgc_condensed_week_marks_only_last_mini_session_per_date_for_finisher`
  (rename it to reflect the corrected rule, e.g.
  `test_hgc_condensed_week_marks_only_first_occurrence_of_day_role_for_finisher`)
  to assert the corrected table above (real values, not re-derived from the
  code under test). Leave the sibling `show_warmup` assertions as they are.
- Full suite: `~/projects/IronLog-V2/.venv/bin/python -m pytest -q` must
  stay fully green (baseline 701 passed after spec 54) — no other existing
  test's expected outcome should change.
- Manual/functional: after merge, re-backfill `signature["show_finisher"]`
  (and re-set `show_warmup` unchanged) onto the 11 already-created live HGC
  sessions (ids 28-38) with the CORRECTED table above — this supersedes
  spec 53's earlier backfill for `show_finisher` specifically. Tier A does
  this directly as a data-only op, same pattern as specs 53/54.
