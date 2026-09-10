# Spec 20: Fix `ht_refine.py`'s EMA to apply once per session, not once per SetLog

## Objective
`refine_from_logged_ht`'s own docstring states: "Multiple qualifying sets logged within the same session collapse to ONE observation for that session (their mean) — a single high-rep session can't substitute for corroboration across separate training days." That collapsing is correctly implemented in `_single_band_session_observations` (used only for the MODELED→MEASURED consistency gate), but the actual `peak_lb` EMA update loop in `refine_from_logged_ht` does NOT collapse — it iterates every qualifying `SetLog` in the session and applies the EMA once per log. A 3-set HT exercise logged in one session therefore applies the EMA three times in a row for what should be a single one-time nudge, compounding the drift.

**Confirmed via live data (2026-07-16/17):** the athlete reported the app showing 170lb plates + Red band = 263.2lb felt, when Red's real peak should read ~90lb. Live `BandPair` query showed Red's `peak_lb` had drifted from the seed default 90.0 to 93.2. Tracing the two Red-band sessions in the live DB: session 4 (3 identical SetLogs, felt_peak=255, plates=165 → observed=90.0 each — no drift, matches the then-current peak_lb) and session 8 (3 identical SetLogs, felt_peak=260, plates=165 → observed=95.0 each). Replaying the CURRENT buggy per-SetLog loop against session 8's three identical logs starting from peak_lb=90.0: `round(0.7*90+0.3*95,1)=91.5` → `round(0.7*91.5+0.3*95,1)≈92.6` → `round(0.7*92.6+0.3*95,1)≈93.3` — three compounding EMA applications from ONE session's three sets, landing almost exactly on the observed live value of 93.2 (small variance from my hand-rounding). The CORRECT one-observation-per-session behavior would apply the EMA exactly once using that session's mean observed value (95.0), yielding `round(0.7*90+0.3*95,1) = 91.5` — a single, modest, intended nudge, not a tripled one.

## File targets
- Modify: `ironlog/persistence/ht_refine.py`
- New/modify tests: `tests/test_ht_refine.py` (this file does not currently exist — there is NO existing test coverage for `ht_refine.py` at all; confirm this via `find tests -iname '*ht_refine*'` before starting, and create it)

## The fix
In `refine_from_logged_ht`, restructure the `peak_lb` update loop so that when a session has MULTIPLE qualifying `SetLog`s resolving to the SAME single band, they collapse to ONE mean observation before the EMA is applied — exactly the same collapsing logic `_single_band_session_observations` already implements for its own purposes (mean of `felt_peak - plates` per session per band). Concretely:
1. Group the session's qualifying `ht_logs` by `band_id` (a log qualifies for a `band_id` iff its resolved config, via `_resolved_band_config`, is a single-element list `[band_id]`, and it has a resolvable plates reference per the existing fallback rules).
2. For each band_id group, compute the MEAN of `felt_peak - plates` across that group's logs (not each log separately).
3. Apply the EMA (`peak_lb = round(0.7 * peak_lb + 0.3 * mean_observed, 1)`) EXACTLY ONCE per band_id, using that mean.
4. `touched_band_ids` still ends up the same set as today (any band with ≥1 qualifying log in the session) — only the NUMBER of times the EMA fires per session changes (once per band, not once per log).

You may extract a small shared helper for "mean observed value across a group of logs for one band" if that avoids duplicating logic already present in `_single_band_session_observations` — but do not change `_single_band_session_observations` itself or the `_is_consistent`/MEASURED-flip logic, which are unaffected by this bug (they already operate on the correctly-collapsed per-session means).

## Edge cases
- **A session with only ONE qualifying log for a band** must behave exactly as today (one EMA application) — this is the common case and must not regress.
- **A session with multiple qualifying logs for a band that have DIFFERENT observed values** (e.g. a lifter's felt_peak varies slightly set-to-set) must use the mean of all of them, not just the first or last.
- **A session with qualifying logs for TWO DIFFERENT bands** (e.g. one set used Orange, another used Red, in the same session) must still update BOTH bands independently, each once, with each band's own mean.
- Do not touch `_resolved_band_config`, `_load_planned_sets`, `_single_band_session_observations`, `_is_consistent`, or the MEASURED-flip block at the end of `refine_from_logged_ht` — only the EMA-application loop itself changes.
- Do not touch `current_load`/`ht_plates`/`ht_band_config` anywhere — this file's docstring already establishes those are a completely different (Option-C generation-time) concern; this spec doesn't change that boundary.
- `NO from __future__ import annotations` (project-wide constraint — this file already correctly omits it, keep it that way).

## Dependencies
None — standalone within the server repo.

## Verification
- New tests in `tests/test_ht_refine.py`, at minimum:
  - A session with THREE identical qualifying single-band logs (mirroring the real session-8 case: felt_peak=260, plates=165, starting peak_lb=90.0) — assert the resulting `peak_lb` after `refine_from_logged_ht` is `91.5` (one EMA step using the mean observed=95.0), NOT the ~93.2-93.3 the current buggy per-log loop would produce.
  - A session with a SINGLE qualifying log — assert identical behavior to a naive one-shot EMA (regression safety net; the common case must be unaffected).
  - A session with two qualifying logs for the same band with DIFFERING felt_peak values — assert the EMA used the mean, not either individual value.
  - A session with qualifying logs for two DIFFERENT single-band configs — assert both bands update independently, each via their own one-time EMA using their own mean.
  - A session with a multi-band (2+ band) `PlannedSet.band_config` log — assert it's skipped entirely (unchanged from today), not accidentally decomposed.
- Full server suite green: `.venv/bin/pytest -q` on myflib (baseline 526 passing before this change).
