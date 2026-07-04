# Engine Hardening — Design

**Date:** 2026-07-04
**Repo:** server `~/projects/IronLog-V2` (Python/FastAPI/SQLModel). Pure engine/persistence + pytest. NO client, NO schema/migration, NO D6 seed data.
**Status:** Approved design → spec for implementation planning.

## Goal

Fix two confirmed correctness gaps in the just-merged HT band-composite engine before the config-seed lights it up for Week 1. Both are deterministic, server-only, and independently testable.

## Scope

| IN | OUT (deferred) |
|---|---|
| Band **wear-gate**: `ht_next_setup` search + assembler honor `BandPair.usable=false` (never prescribe a retired band) | Any UI/endpoint to retire a band (the athlete flips `usable` in the DB/seed; no surface this chunk) |
| Felt-peak calibration: flip MODELED→MEASURED on **distinct sessions** (not sets) that **agree** within tolerance | The per-set EMA nudge to `peak_lb` (unchanged — works correctly) |
| pytest coverage for both | Multi-band felt-peak decomposition (still skipped, as before) |
| | Validator changes (safety clamp is a separate concern) |

Option-C two-writer boundary and `engine/` purity are preserved throughout. No `from __future__ import annotations`.

## GAP 1 — Band wear-gate

Today `ht_next_setup` (`ironlog/engine/band_composite.py`) and the assembler (`ironlog/generation/assembler.py:225`) ignore `BandPair.usable`, so a band flagged `usable=false` (retired/worn) can still be prescribed. All six bands are seeded `usable=true` today, so this is a defensive gate that becomes live the moment the athlete retires a band.

**Change (pure, self-contained in `band_composite.py`):**
- `Band` namedtuple gains a `usable` field **with a default** so existing 3-arg constructors keep working:
  `Band = namedtuple("Band", "id rest peak usable", defaults=(True,))`.
- `ht_next_setup` becomes usable-aware while staying pure:
  - `by_id` continues to index **all** bands in `inventory` — so the *current* config's peak/bottom are priced correctly even if it contains a band the athlete just retired (no `KeyError`).
  - The "raise plates within current config" shortcut (step 1) runs **only if every band in the current `config` is usable** (`all(by_id[b].usable for b in config)`). If the current config holds a retired band, skip the shortcut and go to search — force a reconfigure to a usable setup rather than pile more plates onto a retired band.
  - `_all_configs` enumerates candidates from **usable bands only** (`ids = [b.id for b in inventory if b.usable]`), so the search never proposes a config containing a retired band.
  - Fallback unchanged: if no usable config beats the current peak, return `(plates, list(config))` (hold). The degenerate "every band retired / no usable config exists" case therefore holds the current setup — noted, not built for.

**Assembler wiring (`assembler.py:225`):** build the inventory with the real flag —
`Band(bp.id, bp.bottom_lb, bp.peak_lb, bp.usable)` — so the gate holds end-to-end. This is the only production `Band(...)` construction site.

## GAP 2 — Felt-peak calibration (sessions + agreement)

Today `_count_single_band_readings` (`ironlog/persistence/ht_refine.py`) counts qualifying SetLog **rows**, so one session with 3 HT sets flips a band MODELED→MEASURED after a single session; it also never checks the observed peaks agree.

**Change (only the flip gate — the per-set EMA nudge to `peak_lb` is unchanged):**
- Replace the set-counting with **per-session observations**. For a band, gather every qualifying single-band reading (`observed = felt_peak − actual_plates`, resolving plates as the existing code does), group by `session_id`, and reduce each session to one value (the **mean** of that session's qualifying readings). Order the per-session values by `session_id` (ids increase chronologically).
- Flip MODELED→MEASURED only when **both** hold on the **most recent 3 distinct sessions**:
  1. there are **≥ 3** distinct qualifying sessions, and
  2. those 3 session-observations are **consistent**: `max − min ≤ 0.15 × mean` (guard `mean > 0`; if mean ≤ 0, not consistent).
- Rolling window (the *last* 3 sessions) so early noisy reads don't permanently block a band whose readings later settle. `CONSISTENT_READINGS_TO_MEASURE = 3` becomes the session count; add a `CONSISTENCY_TOLERANCE = 0.15`.
- Never touches `current_load`/`ht_plates`/`ht_band_config` (Option-C) — only `peak_lb`/`calibration_status`, as before.

## Data model & boundaries

No schema change. `BandPair.usable` and `BandPair.calibration_status` already exist. `engine/band_composite.py` stays pure (no DB/IO). `ht_refine.py` remains the persistence-side refiner called from the submit path after SetLogs commit. Option-C: neither change writes a generation-time field.

## Testing (pytest, run via `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q'`)

**Band wear-gate (`band_composite` pure + assembler integration):**
- Back-compat: `Band(id, rest, peak)` still constructs with `usable=True`; existing `band_composite` tests pass unchanged.
- Search skips a retired band: with e.g. Red `usable=False`, `ht_next_setup` never returns a config containing Red — it picks a usable alternative that beats the current peak.
- Current config holds a retired band: the raise-plates shortcut is skipped and the result reconfigures to a usable config (does not keep the retired band).
- No usable config beats current → holds `(plates, config)`.
- Assembler integration: a session assembled with one `BandPair.usable=False` never prescribes that band (end-to-end through `assemble` with a DB fixture).

**Felt-peak calibration (`ht_refine`):**
- 3 qualifying single-band sets in **one** session → band stays MODELED (only 1 distinct session).
- 3 distinct sessions with consistent readings (within 15%) → flips to MEASURED.
- 3 distinct sessions where one is a >15% outlier → stays MODELED.
- The per-set EMA nudge to `peak_lb` still occurs (unchanged behavior).
- Option-C guardrail: `refine_from_logged_ht` writes none of `current_load`/`ht_plates`/`ht_band_config` (assert unchanged after a call).
- Existing `ht_refine` tests still pass (adjust only those that asserted the old set-count flip, if any — treat like a coupled test: update to the new session semantics, do not weaken).

Full existing suite (baseline 359) stays green.

## Build order (SDD, server-only, 2 tasks)

1. **Band wear-gate** — `band_composite.py` (`Band.usable` default + usable-aware `ht_next_setup`) **and** the one-line `assembler.py:225` wiring, with the pure tests + the assembler integration test in one task (they form one wear-gate deliverable).
2. **Felt-peak calibration gate** — `ht_refine.py` session-count + 15% consistency flip, with tests.

## Global constraints

- Server only; NO `from __future__ import annotations`; no schema/migration; `engine/` stays pure (no DB/IO in `band_composite.py`); Option-C boundary preserved (no generation-time field written by either change); full pytest suite (359) stays green; tests run via `ssh myflix`.
