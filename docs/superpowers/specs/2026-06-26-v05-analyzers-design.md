# v0.5 — e1RM History + Deferred Analyzers (calibration-flip, stall detection) — Design

**Date:** 2026-06-26
**Repo:** `~/projects/IronLog-V2`
**Status:** approved design (forks locked); awaiting spec-review gate, then plan
**Scope:** A (per-session e1RM history record) + B (calibration-flip) + C (stall detection). **All deterministic.** The generation loop (D, docs/06) is **deferred to v0.6.**

---

## 1. Purpose & scope

docs/06 §9/§183 and docs/02 define two analyzers the prior versions deferred because they need data that doesn't exist yet: a **per-session e1RM history**. v0.4's hook computes a single current `e1rm`; it cannot answer "two weekly estimates within 5%?" (calibration-flip) or "flat/declining over 3 sessions?" (stall) without a history of estimates.

v0.5 delivers exactly that history record and its two first readers, **all deterministic** — keeping this version in the same provably-right texture as the validator/ledger/hook/migrations runs. The generation loop (the LLM-bearing capstone, docs/06) is its own version (v0.6) with its own rope-vs-guardrails design conversation; building it separately keeps the deterministic analyzers from sharing a mixed-texture review with the first non-deterministic engine code, and lets generation be designed against **real** calibration/stall signals.

**In scope:** A history table (migration 003 + parity test), the pure `evaluate_calibration_flip` and `detect_stall` engine functions, the applier extension that appends history rows + writes the calibration flip, and the `run_analysis` orchestrator seam.

**Out of scope (→ v0.6):** the generation loop; the HTTP/analyze *trigger* (run_analysis stays callable but unwired); `current_load` writes; the calibration-flip notification; consuming `detect_stall` for weak-point response.

---

## 2. Constraints (carried from repo CLAUDE.md + prior versions)

- **`engine/` is pure logic** — no DB/network/LLM/file-io/calendar math. `evaluate_calibration_flip` and `detect_stall` are pure.
- **`persistence/apply.py` is the single write point.** All `MovementState` writes + history-row appends + calibration flips happen there, in one resolve-all-first atomic transaction.
- **Two-writer boundary:** the applier owns `e1rm`, the history rows, `calibration_status`, and the existing progression counters/tier. `current_load` has **no writer in v0.5** (generation owns it in v0.6). `detect_stall` writes nothing.
- **No `from __future__ import annotations`** in files importing SQLModel `Relationship` models.
- **Migration authoring rule:** single-statement-atomic OR fully idempotent (`IF NOT EXISTS`). `003` is a single `CREATE TABLE IF NOT EXISTS`. Parity test must stay green (chain matches live `create_all`).
- **Tests run on myflix:** `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q'`.
- **Named constants for all thresholds** (v0.6-tunable): `CALIBRATION_AGREEMENT_PCT`, `STALL_WINDOW`, `STALL_EPSILON_PCT`, `STALL_MIN_SESSIONS`, `STALL_FAILED_THRESHOLD`.

---

## 3. Architecture overview

```
run_analysis(session_id, db, week_boundaries)        ← persistence/ orchestrator seam (no HTTP; v0.6 calls it)
  1. RESOLVE   context from db: movements, logged sets, current MovementState,
               per-session objective + phase (stamps the history rows — Pin b)
  2. ANALYZE   analyze_session(ctx)  → AnalysisResult deltas      [existing, pure]
  3. BUCKET    for each calibrating lift: bucket its e1rm_history rows into
               weekly estimates using the week_boundaries PARAMETER (Pin 1);
               evaluate_calibration_flip(weekly_estimates, status)  [pure]
  4. APPLY     persistence/apply.py, ONE atomic transaction:
                 - write MovementState deltas (e1rm, counters, tier)   [existing]
                 - append one e1rm_history row per movement (objective/phase stamped)
                 - write calibration_status = MEASURED for any lift that flipped
               (never current_load)

detect_stall(progress_anchor_e1rms, consecutive_failed, objective)   [pure; v0.6 consumes]
```

`detect_stall` is **not called by `run_analysis`** — it's built and tested ahead of its v0.6 consumer (generation's weak-point response §9), the same way the validator/ledger were built ahead of theirs.

---

## 4. A — the e1RM history record

**New table** `e1rmhistory` (SQLModel class `E1rmHistory`, table name `e1rmhistory`). One row **per analyzed session per movement**, appended by the applier in the same event that updates `MovementState.e1rm`.

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `movement_id` | int FK→movement.id, indexed | |
| `session_id` | int FK→session.id, indexed | the analyzed session |
| `e1rm` | float | the anchor e1RM (best tapped working set, from the hook's `_best_e1rm_set`) |
| `objective` | Objective (str enum) | **load-bearing**: the per-session objective, for the PROGRESS-window selection (C, Pin 1) |
| `phase` | Phase (str enum) | context / reconstructability |
| `anchor_load` | float | reconstructability of the e1RM |
| `anchor_reps` | int | reconstructability |
| `anchor_rpe` | float | reconstructability (target_rpe of the anchor set) |
| `computed_at` | datetime | when analysis ran |

Week bucketing keys off the row's **`Session.date`** (joined via `session_id`), not `computed_at` — `date` is the calendar anchor; `computed_at` is provenance.

`MovementState.e1rm` (+ `e1rm_updated_at`) **stays** as the denormalized cached head of this log — fast read, now backed by the append series (not redundant).

**Migration `003_add_e1rmhistory_table.sql`** — a single `CREATE TABLE IF NOT EXISTS e1rmhistory (...)`, idempotent per the authoring rule. The `E1rmHistory` model is added so `create_all` builds the same schema; the parity test (`test_chain_matches_create_all`) must stay green — its DDL is aligned to `create_all`'s emitted column types exactly as `001`/`002` were. **`calibration_status` is NOT added — it pre-exists on `MovementState`** (verified: `CalibrationStatus = INHERITED|CALIBRATING|MEASURED`).

---

## 5. B — calibration-flip

**Pure engine function** (`engine/calibration.py`):
```
evaluate_calibration_flip(weekly_estimates: list[float], current_status: CalibrationStatus) -> bool
```
- Returns `True` (flip to `MEASURED`) iff: `current_status == CALIBRATING` **and** there are ≥2 weekly estimates **and** the **last two** weekly estimates agree within `CALIBRATION_AGREEMENT_PCT` (5%): `abs(a-b) / max(a,b) <= 0.05`.
- One-way: only fires from `CALIBRATING`. `INHERITED` and `MEASURED` are untouched (entry into `CALIBRATING` is out of scope — set by seed/the calibration block).
- Pure: receives **pre-bucketed weekly estimates**; no rows, no dates, no calendar math.

**Bucketing (in `run_analysis`, not the engine):** for a calibrating lift, group its `e1rmhistory` rows into weeks using the **`week_boundaries` parameter** (§7, Pin 1), then aggregate each week's session anchor e1RMs into one estimate. The applier writes `calibration_status = MEASURED` for any lift that flips, in the atomic transaction.

**Reconstructability (pinned):** a flip is fully reproducible from the per-session anchor rows + the `week_boundaries` that defined the aggregation — never an unexplained authority grant.

**Constant:** `CALIBRATION_AGREEMENT_PCT = 0.05`.

> **Gate decision 2 (weekly aggregator):** how a week's multiple session anchor e1RMs collapse to one weekly estimate is a real judgment call, **surfaced deliberately, not defaulted**:
> - **mean** — smooths within-week noise for a stable estimate, but can mask a real mid-week climb (averages a 200→210 week to 205).
> - **max** — consistent with the session-anchor "best set" choice and represents demonstrated capability; more reactive, less smoothing.
> Default written below is a placeholder pending the gate: **`max`** (consistency with the anchor-is-best principle), to be confirmed or overridden at spec-review.

---

## 6. C — stall detection

**Pure engine function** (`engine/stall.py`):
```
@dataclass
class StallSignal:
    trend_stalled: bool
    failed_stalled: bool
    stalled: bool        # convenience: trend_stalled or failed_stalled

detect_stall(progress_anchor_e1rms: list[float], consecutive_failed: int, objective: Objective) -> StallSignal
```
- **PROGRESS-gated:** if `objective != PROGRESS`, returns all-`False` (a maintained/measuring lift is never "stalled"). The caller passes the e1RMs from the lift's **last `STALL_WINDOW` PROGRESS-objective sessions** — *not* the last 3 calendar sessions (so a lift entering REBUILD isn't instantly flagged off its intentionally-flat maintenance history; window-selection applies maintained-flat-is-success).
- **`trend_stalled`** (whole-window definition — catches plateau & decline, does **not** false-flag dip-and-recover): requires ≥`STALL_MIN_SESSIONS` (3) in-window e1RMs; `trend_stalled = True` iff **no e1RM in the window exceeds the window's first (oldest) value by more than `STALL_EPSILON_PCT`** — i.e. `max(window) <= window[0] * (1 + STALL_EPSILON_PCT)`. Fewer than 3 → `False` (not enough data). (Endpoint comparison rejected: it false-flags a 100→95→102 recovery; whole-window does not.)
- **`failed_stalled`** = `consecutive_failed >= STALL_FAILED_THRESHOLD` (2) — reuses v0.4's existing counter (the §183 "2 failed prescriptions" arm).
- **`stalled`** = `trend_stalled or failed_stalled`. The **union/weighting is v0.6 generation's trigger policy** — v0.5 honestly reports both computed sub-signals; it does not decide what to do about them.
- Pure, no writes, no stored flag (ledger precedent: recompute-don't-store; stall is a current-condition read, not a latch).

**Constants:** `STALL_WINDOW = 3`, `STALL_MIN_SESSIONS = 3`, `STALL_EPSILON_PCT = 0.01`, `STALL_FAILED_THRESHOLD = 2`.

---

## 7. The `run_analysis` seam (persistence orchestrator)

```
run_analysis(session_id: int, db: Session, week_boundaries: <param, §gate-1>) -> AnalysisResult
```
- The **deterministic analyze→apply boundary**, drawn now so v0.6 *calls* it rather than reassembling the flow inside LLM-bearing code. No HTTP, no client scope.
- Resolves context (incl. per-session objective + phase to stamp rows — **Pin b: `run_analysis` owns this stamping**), runs `analyze_session`, buckets weekly estimates via `week_boundaries`, evaluates flips, and calls the applier once. Writes nothing itself — the applier executes all DB writes (single write point, one transaction, resolve-all-first per v0.4).
- **Cold-start is expected, not broken:** until ~3 PROGRESS sessions log, the analyzers are data-starved (calibration needs 2 weekly estimates; stall needs 3 PROGRESS sessions). Recorded in the v0.6 memory note.

> **Gate decision 1 (week-boundary parameter):** `week_boundaries` **must be a parameter**, not baked into `run_analysis` — otherwise the no-calendar-in-the-engine guarantee is merely relocated into the orchestrator. The date concept lives at `run_analysis`'s caller (v0.6 / a future endpoint / tests). Proposed concrete shape (confirm/override at gate): a **sorted `list[date]` of week-start cutoffs**; a session buckets into the latest cutoff `<= Session.date`. Alternatives: a `Callable[[date], WeekKey]`, or explicit `list[(start, end)]` ranges.

---

## 8. Two-writer boundary (confirmation)

| Field | Writer in v0.5 |
|---|---|
| `MovementState.e1rm` / `e1rm_updated_at` | applier (existing) |
| `e1rmhistory` rows | applier (new — appended in the same transaction) |
| `calibration_status` (→MEASURED) | applier (new — on flip) |
| `consecutive_ceiling` / `consecutive_failed` / tier | applier (existing) |
| `current_load` | **no writer** (generation, v0.6) |
| stall signal | **not written** — `detect_stall` is pure, recomputed on read |

One writer per field; `current_load`'s writer simply doesn't exist yet. The drift hazard is not reintroduced.

---

## 9. Testing

- **Pure core — calibration (`tests/test_calibration.py`):** flip when last-2 weekly within 5%; no flip outside 5%; no flip with <2 estimates; one-way (no flip from `INHERITED` or `MEASURED`); flip uses the **last two** estimates (not any two).
- **Pure core — stall (`tests/test_stall.py`):** plateau → `trend_stalled`; decline → `trend_stalled`; **dip-and-recover (100→95→102) → NOT `trend_stalled`** (the load-bearing case); <3 sessions → `False`; monotonic climb → `False`; `failed_stalled` at threshold; `stalled` = union; `objective != PROGRESS` → all-`False`.
- **Persistence (`tests/test_run_analysis.py` / extend `test_apply_analysis.py`):** history row appended per movement with objective/phase stamped; `calibration_status` written on flip; **`current_load` untouched**; atomicity (a mid-write failure leaves no partial state); bucketing uses the `week_boundaries` parameter (PROGRESS-window + weekly aggregation exercised end-to-end).
- **Migration parity (`tests/test_migrations.py`):** `003` in the chain; `test_chain_matches_create_all` green (the `E1rmHistory` model and `003`'s DDL agree on every column's type/notnull/default/pk).

Baseline 112 → target ~+18–24 (analyzers + persistence + parity).

---

## 10. Deferred to v0.6 (carry-forwards)

- The **generation loop** (docs/06) — its own rope-vs-guardrails design conversation.
- The **analyze trigger** — `run_analysis`'s caller (generation runs analyze as part of its loop; or a logging endpoint). One-liner against the seam.
- **Calibration-flip notification** — when `MEASURED` grants real load-push authority, add visibility (not a gate) so a behavior-changing flip isn't silent. Not built now.
- **Cold-start** — analyzers data-starved until ~3 PROGRESS sessions log; expected, not broken.
- **`detect_stall` consumer** — generation's weak-point response (§9): graduated L1→L2→L3 bias on the union signal.
- **N+1 → `IN(...)`** — as the applier's write/read surface grows, batch point-queries.
- **`current_load` writer** — generation becomes its sole writer.

---

## 11. Architecture invariants honored

| Invariant | How |
|---|---|
| Rules dispose / model proposes | v0.5 is all rules (no model yet); deterministic analyzers + applier. |
| `engine/` pure | calibration + stall functions are pure; no DB/date/LLM. Bucketing + writes live in persistence. |
| Single write point | applier owns every write incl. history + flip, one atomic transaction. |
| Definition vs state | history rows are state (events); `MovementState.e1rm` is denormalized head. |
| Recompute-don't-store | stall is a pure recompute (no stored flag); only the one-way latch (`calibration_status`) is stored. |
| Mandatory `feedback_tap` | the anchor (and thus every history e1RM) still requires a tapped working set; not relaxed. |
| Objective gating | calibration flip only from `CALIBRATING`; stall only for `PROGRESS`. |
| Migration safety | `003` single-statement `CREATE TABLE IF NOT EXISTS`; parity test enforces model↔chain agreement. |

---

## 12. Spec-review-gate decisions (elevated — confirm before plan)

1. **Week-boundary parameter shape** (§7) — `list[date]` cutoffs (proposed) vs callable vs ranges. The parameter itself is non-negotiable (Pin 1); only its shape is open.
2. **Weekly aggregator** (§5) — `max` (proposed, anchor-is-best consistency) vs `mean` (smoothing). Deliberate choice, not a default.

Mechanical locks (no gate needed): history columns (objective/phase load-bearing + reconstructability fields); named constants; `003` adds only the table (`calibration_status` pre-exists, verified).

---

## 13. Approvals

| Item | Status | Date |
|---|---|---|
| Scope: A+B+C deterministic now, D (generation) → v0.6 | approved | 2026-06-26 |
| A: per-session anchor history row; `e1rm` stays denormalized head | approved | 2026-06-26 |
| B: applier-written flip (measurement-confidence fact); one-way; reconstructable | approved | 2026-06-26 |
| C: pure recompute (no stored flag); whole-window trend def; PROGRESS-window selection; two sub-signals | approved | 2026-06-26 |
| D-seam: `run_analysis` orchestrator (no HTTP); cold-start expected; owns objective stamping | approved | 2026-06-26 |
| Two-writer boundary: applier single write point; `current_load` unwritten | approved | 2026-06-26 |
| Spec written | this commit | 2026-06-26 |
| Gate decisions (week-boundary shape; weekly aggregator) | pending | — |
| User spec review | pending | — |
| Implementation plan (`writing-plans`) | not started | — |
