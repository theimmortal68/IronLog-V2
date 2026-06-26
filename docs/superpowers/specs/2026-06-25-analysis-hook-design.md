# Analysis Hook (v0.4) — Design

**Date:** 2026-06-25
**Repo:** `~/projects/IronLog-V2` (this repo)
**Status:** approved design; awaiting implementation plan
**Scope:** v0.4 only — the post-session, between-session state-mutation step. Single-session updates that are computable from the just-logged session + current state. Defers anything needing an e1RM history series (calibration flip, multi-session stall) to v0.5.

---

## 1. Purpose

Per `docs/06_generation_algorithm_spec.md` §0 step 7: `PERFORM → LOG → ANALYZE → update state + ledger → feeds next generation`. The **analysis hook** is the ANALYZE step. After a session is logged, it reads what happened and updates the per-movement and global state the next generation will read.

This is the first task in the project that **mutates** state (`MovementState`, `EngineState`), so unlike the validator (v0.2) and ledger (v0.3) — both pure read-only producers — it needs a write seam. The design keeps the engine pure anyway: a pure core computes a plan of proposed deltas (`AnalysisResult`), and a thin applier in a separate `persistence/` package performs the writes. This is the validator's "compute violations, caller applies" contract, applied to writes.

v0.4 ships the single-session updates: per-movement e1RM, the ceiling/failed-progression counters, tier step-down, and phase-gate *evaluation* (report-only). It defers the two responsibilities that need an e1RM history series — calibration flip (INHERITED→CALIBRATING→MEASURED, needs "two weekly estimates within ~5%") and multi-session stall detection ("e1RM flat over 3 sessions") — to v0.5, where the generation loop is their consumer. The e1RM write path carries a one-line seam so v0.5 can append a history point without retrofitting.

---

## 2. Constraints

From `~/projects/IronLog-V2/CLAUDE.md` and the specs:

- **`engine/` is pure logic.** `engine/analysis.py` imports only `dataclasses`, `enum`, `typing`, the model enums, and the existing engine helpers (`e1rm`, `progression`). No DB, no network, no LLM, no file I/O. The write happens in `persistence/apply.py`, never in `engine/`.
- **Do NOT add `from __future__ import annotations`** to any file importing SQLModel models with `Relationship(...)`. `persistence/apply.py` imports `MovementState`; `engine/analysis.py` imports only enums/dataclasses (no Relationship models) but the rule stands repo-wide.
- **Definition vs State.** `Movement` is the static definition; `MovementState`/`EngineState` are the mutable state. The hook reads `Movement`-derived facts via the caller's projection and writes only `MovementState` fields (plus reporting a possible `EngineState.current_phase` transition — never writing it).
- **Planned vs Logged.** The hook compares the prescribed `PlannedSet` against the performed `SetLog` — that delta IS the signal that drives the ceiling/miss determination. The caller pairs each logged set with its prescription before calling.
- **Objective gating** (the load-bearing invariant): stall / progression machinery fires **only** when the resolved objective is `PROGRESS`. In `MAINTAIN` (the primaries' objective in CUT/STAB), flat is success — the hook updates e1RM and evaluates phase gates and does nothing else.
- **Locked reference data.** The hook reads `ht_bottom_clamp`, RPE caps, bodyweight targets, etc. via the caller's input; it never invents them. The one schema addition (a counter field) introduces no reference values.

---

## 3. Architecture

**Pure core + thin applier**, mirroring the validator's compute/apply split — but for writes.

```
ironlog/engine/analysis.py          NEW — pure: analyze_session(ctx: AnalysisContext) -> AnalysisResult
ironlog/persistence/__init__.py     NEW — package marker
ironlog/persistence/apply.py        NEW — apply_analysis(result, db) -> None  (THE single write point)
ironlog/models/library.py           MODIFY — add MovementState.consecutive_failed_progressions: int = 0
ironlog/engine/__init__.py          MODIFY — re-export analyze_session + the result/context dataclasses
tests/test_analysis.py              NEW — ~17 pure-core cases (no DB)
tests/test_apply_analysis.py        NEW — ~6 applier cases (in-memory SQLite)
```

**Rejected alternatives** (settled during brainstorming):
- *Hook does its own DB reads + writes.* Converts the engine-is-pure invariant from hard to soft (engine imports the DB for the first time), forces DB fixtures into engine tests, and smears orchestration across callers. Rejected.
- *Pure per-field functions, no aggregate result type.* No single "what changed this session" object for audit/logging; orchestration leaks to every caller. Rejected.
- *Auto-flip the phase when a gate is satisfied.* A single noisy reading (a transient 213 weigh-in) would silently rewrite the whole loading regime. Report-only instead (§6.4). Rejected.

`persistence/` is a new top-level package (not `api/`, which is HTTP-specific and unused in v0.4; not `engine/`, which must stay pure). It is the single write point — the v0.5 e1RM-history append hooks in there in one line.

---

## 4. Data shapes

### 4.1 Input (caller assembles from the DB; the hook never queries)

```python
@dataclass
class LoggedSet:
    """One logged working set paired with its prescription.
    target_rpe feeds e1RM; target_reps_low/high feed the outcome classification
    (§6.2); feedback_tap feeds both."""
    actual_load: Optional[float]
    actual_reps: Optional[int]
    feedback_tap: Optional[FeedbackTap]
    is_warmup: bool
    target_rpe: Optional[float]
    target_reps_low: Optional[int]
    target_reps_high: Optional[int]


@dataclass
class MovementAnalysisInput:
    movement_id: int
    objective: Objective                  # already resolved (override or phase default) by caller
    current_tier: int
    increment_ladder_len: int             # tier step-down bound
    consecutive_ceiling_sessions: int     # current counter (read)
    consecutive_failed_progressions: int  # current counter (read) — new field, §5
    logged_sets: List[LoggedSet]


@dataclass
class EngineStateInput:
    current_phase: Phase
    bodyweight: Optional[float]
    cut_to_stab_target: float             # e.g. 213
    cut_to_stab_tolerance: float          # e.g. 2 → gate met when bodyweight <= target + tol
    rhr_down: bool
    sleep_ok: bool
    no_rpe_creep: bool
    bw_stable_2wk: bool
    strength_bounce: bool
    subjective_ok: bool


@dataclass
class AnalysisContext:
    movements: List[MovementAnalysisInput]
    engine_state: EngineStateInput
```

### 4.2 Output (proposed deltas; `None` = "leave this field untouched")

```python
@dataclass
class MovementStateDelta:
    movement_id: int
    new_e1rm: Optional[float] = None               # None = untouched (no qualifying sets)
    new_tier: Optional[int] = None                 # None = unchanged
    new_consecutive_ceiling: Optional[int] = None  # None = unchanged
    new_consecutive_failed: Optional[int] = None   # None = unchanged
    # NOTE: no current_load field — the hook NEVER writes current_load (generation's sole job).


@dataclass
class AnalysisResult:
    movement_deltas: List[MovementStateDelta]
    phase_transition_available: Optional[Phase] = None  # report-only; applier never writes current_phase
```

`None`-means-untouched is unambiguous because the hook never legitimately writes a null e1rm — the "no qualifying sets" guard maps exactly to `new_e1rm = None`.

---

## 5. Schema change (one field)

`MovementState.consecutive_failed_progressions: int = 0` — mirrors the existing `consecutive_ceiling_sessions`. It is *current state* (a counter), not history, so it fits the "computable from this session + current state" scope. Default 0; no existing seed data needs it set. This is the only schema change in v0.4.

Deploy note (same shape as v0.3 §12): the production DB on myflix gets a one-shot non-destructive `ALTER TABLE movementstate ADD COLUMN consecutive_failed_progressions INTEGER NOT NULL DEFAULT 0` at deploy time. The `rm + reseed` shortcut is only acceptable while the DB has zero logged sessions (gate the check the same way v0.3's §12 does).

---

## 6. Rule semantics

### 6.1 The single shared anchor set

For each movement, both the e1RM update and the outcome classification key off **one anchor: the best working set by `estimate_e1rm`**.

- A "working set" = `not is_warmup AND actual_load is not None AND actual_reps is not None`.
- "Qualifying for e1RM" additionally requires `feedback_tap is not None AND target_rpe is not None` (the estimate needs both).
- The anchor = the e1RM-qualifying working set with the **maximum** `estimate_e1rm(load, reps, target_rpe, tap)`.

Using the top-effort set (not "all sets") is the load-bearing choice for the **top-set + back-off** scheme (the primaries' scheme): back-off sets sit below the ceiling by design, so an "all sets reps ≥ high" rule would never detect a ceiling. Judging the intended top effort mirrors the e1RM `max()` and is correct across schemes (STRAIGHT, giant-set, top-set+backoff all have a definable top-effort set).

If **no** set qualifies for e1RM, there is no anchor: `new_e1rm = None` (untouched, never null/zero) AND the outcome is unclassifiable → all counters untouched (`None` in the delta). The no-qualifying-sets guard extends to outcomes.

### 6.2 Per-session outcome — one coherent determination

Judged on the anchor set, mutually exclusive by construction (`if / elif / else`):

| Outcome | Condition on the anchor set | Counter effect (PROGRESS only) |
|---|---|---|
| **MISS** | `feedback_tap == TOO_HARD` (needs no rep range) | `failed += 1`, `ceiling → 0` |
| **MISS** | has `target_reps_low/high` AND `actual_reps < target_reps_low` | `failed += 1`, `ceiling → 0` |
| **CEILING** | has `target_reps_low/high` AND `actual_reps >= target_reps_high` AND `tap != TOO_HARD` | `ceiling += 1`, `failed → 0` |
| **NEITHER** | in-range, not over-effort | `ceiling → 0`, `failed → 0` |
| **NEITHER** | anchor lacks `target_reps_low/high` and tap != TOO_HARD (can't confirm hit/miss) | `ceiling → 0`, `failed → 0` |

A single session can never tick both counters — CEILING and MISS are exclusive branches. `target_reps_low/high` on `LoggedSet` are consumed **only** by this classification (they are not used by e1RM); their presence is what enables the reps-based branches.

### 6.3 Counters, tier, and objective gating

**All of §6.2 + tier step-down is PROGRESS-gated.** If `objective != PROGRESS`, the hook leaves `new_consecutive_ceiling`, `new_consecutive_failed`, and `new_tier` all `None` (untouched). In MAINTAIN, only e1RM (§6.5) and phase-gate eval (§6.4) run.

When `objective == PROGRESS`, the outcome from §6.2 sets `new_consecutive_ceiling` and `new_consecutive_failed` to their post-session values (the increments/resets in the table).

**Tier step-down** (PROGRESS only): after computing `new_consecutive_failed`, call
`step_down_tier(current_tier, increment_ladder_len, consecutive_fails=new_consecutive_failed, threshold=2)`.
If it returns a tier different from `current_tier`, set `new_tier` to it AND reset `new_consecutive_failed = 0` (the streak is consumed by the drop). Otherwise `new_tier = None`. `step_down_tier` already respects the ladder bound (won't drop past the finest rung).

### 6.4 Phase-gate evaluation (read-only, always-on)

- **CUT→STAB:** `current_phase == CUT` AND `bodyweight is not None` AND `bodyweight <= cut_to_stab_target + cut_to_stab_tolerance` → `phase_transition_available = STAB`.
  - `bodyweight is None` → gate **not** satisfied (missing data is "unavailable," never "met").
- **STAB→REBUILD:** `current_phase == STAB` AND all six readiness flags True (`rhr_down, sleep_ok, no_rpe_creep, bw_stable_2wk, strength_bounce, subjective_ok`) → `phase_transition_available = REBUILD`.
- Otherwise `None`.

Report-only: the applier never writes `current_phase`. A single session's reading must never silently rewrite the training phase — especially STAB→REBUILD, which rewrites RPE bands, caps, the HT `+5` wake, and flips every primary maintain→progress. The flip stays a deliberate human/explicit action (a future endpoint or confirmation).

### 6.5 e1RM update (always-on, objective-independent)

`new_e1rm = max(estimate_e1rm(load, reps, target_rpe, tap))` over the e1RM-qualifying working sets. No qualifying sets → `new_e1rm = None` (untouched). e1RM is a *measurement*, not a prescription — it updates regardless of objective; the MAINTAIN "don't add load" rule gates the load/tier *response*, never the measurement write. Best-set `max()` keeps a fatigued set-3 from dragging the estimate down (docs/02 explicitly warns about this).

---

## 7. The applier (write seam)

`persistence/apply.py`:

```python
from datetime import datetime, timezone
from sqlmodel import select
from ..models.library import MovementState


def apply_analysis(result, db) -> None:
    """Apply an AnalysisResult to the DB. The single write point.

    Atomic by construction: every MovementState row is resolved FIRST, so a
    missing row raises before any mutation — no partial write. Never writes
    current_phase (phase_transition_available is report-only)."""
    states = {
        d.movement_id: db.exec(
            select(MovementState).where(MovementState.movement_id == d.movement_id)
        ).one()
        for d in result.movement_deltas
    }
    now = datetime.now(timezone.utc)
    for d in result.movement_deltas:
        state = states[d.movement_id]
        if d.new_e1rm is not None:
            state.e1rm = d.new_e1rm
            state.e1rm_updated_at = now
            # v0.5 SEAM: append (movement_id, d.new_e1rm, now) to the e1RM history here.
        if d.new_tier is not None:
            state.current_increment_tier = d.new_tier
        if d.new_consecutive_ceiling is not None:
            state.consecutive_ceiling_sessions = d.new_consecutive_ceiling
        if d.new_consecutive_failed is not None:
            state.consecutive_failed_progressions = d.new_consecutive_failed
        db.add(state)
    db.commit()
```

- **Atomicity by construction:** the dict-comprehension resolves all rows before the mutation loop. A missing `MovementState` makes `.one()` raise during resolution, before any field is touched and before `commit()` — so nothing is written. (Stronger than relying on transaction rollback.)
- **Single `now()`** for all e1RM timestamps in the batch — one consistent instant, and the one impure call isolated to the applier.
- **`phase_transition_available` is deliberately not consumed.** The applier may log it; it never writes `current_phase`.
- **v0.5 history seam:** the single commented line inside the `new_e1rm` branch.

---

## 8. Testing

### 8.1 `tests/test_analysis.py` (~17 pure-core cases, no DB)

**e1RM (always-on):**
1. Best-set max across working sets — the heaviest qualifying set wins.
2. Fatigued set-3 (lower estimate) does not drag the e1RM down below the top set.
3. No qualifying sets (all warmups / null load / null reps / null tap) → `new_e1rm = None`.
4. Warmup excluded from the estimate; null-load excluded; null-tap excluded; null-target_rpe excluded.

**Outcome determination (PROGRESS movement):**
5. CEILING — anchor `reps >= high`, tap `ON_TARGET` → `new_consecutive_ceiling = prev+1`, `new_consecutive_failed = 0`.
6. MISS via `reps < low` → `new_consecutive_failed = prev+1`, `new_consecutive_ceiling = 0`.
7. MISS via `tap == TOO_HARD` (reps in range) → `new_consecutive_failed = prev+1`, `new_consecutive_ceiling = 0`.
8. NEITHER (in-range, not over-effort) → both counters → 0.
9. NEITHER via anchor lacking `target_reps_low/high` and tap != TOO_HARD → both → 0.
10. Mutual exclusivity — assert no session produces both a ceiling increment and a failed increment.

**Objective gating:**
11. MAINTAIN movement → `new_consecutive_ceiling`, `new_consecutive_failed`, `new_tier` all `None`; `new_e1rm` still set.
12. PROGRESS movement with the same logged data → counters move.

**Tier step-down (PROGRESS):**
13. 2nd consecutive MISS (prev failed = 1 → 2) → `new_tier` drops one rung AND `new_consecutive_failed = 0`.
14. 1st MISS (prev failed = 0 → 1) → `new_tier = None` (no drop yet).
15. Step-down respects the ladder bound — already at the finest rung → `new_tier = None` even at threshold.

**Combined decoupling (the measurement/prescription proof):**
16. Anchor set `tap == TOO_HARD` → `new_e1rm` IS written (measurement records from the same anchor) AND outcome = MISS (`new_consecutive_failed` ticks). Proves e1RM and the miss-counter are decoupled in a single case.

**Phase gates:**
17. CUT→STAB met / not-met (bodyweight above target+tol) / `bodyweight = None` → not-met; STAB→REBUILD all-six-true → REBUILD / one-false → None; wrong starting phase → None. (Several asserts under one test, or split — implementer's call.)

### 8.2 `tests/test_apply_analysis.py` (~6 applier cases, in-memory SQLite)

The first DB-touching test file. Fixture: `create_engine("sqlite://")` + `SQLModel.metadata.create_all` + a `Session`, seeded with the `MovementState`/`EngineState` rows under test. Isolated to this file; the engine tests stay pure.

1. Each non-None delta field writes its `MovementState` column; `None` fields leave the column untouched.
2. `new_e1rm` set → both `e1rm` AND `e1rm_updated_at` written.
3. `phase_transition_available = REBUILD` → `EngineState.current_phase` is **unchanged** after apply (the report-only guarantee, asserted).
4. Multi-movement result writes each row independently.
5. `new_e1rm = None` on a movement that already has an e1rm → the existing `e1rm` is preserved (not nulled).
6. **Atomicity:** a result with `[valid_movement_delta, missing_movement_delta]` → `apply_analysis` raises (the missing row's `.one()`), and the valid movement's `MovementState`, re-queried, is **unchanged** — no partial write.

---

## 9. Build & verify

```
ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q'     # baseline 72; expect ~95 after
```

Test runner is myflix via SSH (the venv lives there; workstation pytest imports fail). Files NFS-sync workstation→myflix instantly. No HTTP changes. One nullable-default column add (§5). No client impact (§10).

---

## 10. Wire impact: none

v0.4 adds no HTTP routes, no JSON shapes, no DTO changes. The Android client continues to consume the four existing endpoints unchanged. The new `consecutive_failed_progressions` column is internal; it does not cross the wire. (Per the CLAUDE.md client contract, only `Movement` field renames/removals/retypes are breaking; this adds a `MovementState` column the client never reads.)

---

## 11. Out of scope (explicit YAGNI)

- **e1RM history table / multi-session stall ("flat over 3 sessions") / calibration flip (INHERITED→CALIBRATING→MEASURED)** — deferred to v0.5; they need an e1RM history series, and the generation loop is their consumer. The applier carries the one-line seam.
- **Auto phase-flip** — report-only per §6.4; the actual `current_phase` write is a deliberate future action (endpoint or human confirmation).
- **`current_load` writes** — generation's sole responsibility; the hook treats it as read-only input (never an output).
- **HTTP endpoint** — pure core + applier only; no route until a caller needs it.
- **Weak-point response (L1→L2→L3)** — the hook *detects* the outcome (MISS / CEILING); the graduated response is generation-loop judgment (v0.5).
- **`bw_stable_2wk` computation** — consumed as a pre-set flag on `EngineState`; computing it from a bodyweight series is deferred with the history work.
- **Reading/writing the WeeklyLedger** — the §0-step-7 phrase is "update state + ledger," but the ledger (v0.3) is a pure recompute-on-demand function with no stored state to update. The analysis hook does not touch it; whoever needs tallies recomputes them via `compute_tallies`. No coupling.

---

## 12. Architecture invariants honored

| Invariant | How v0.4 honors it |
|---|---|
| **1. Rules dispose; the model proposes.** | The hook is deterministic Python; no LLM. It computes state deltas the rules dictate. |
| **2. Definition vs State.** | Reads `Movement`-derived facts via the caller's projection; writes only `MovementState` counters/e1rm. The new `consecutive_failed_progressions` is state, parallel to the existing ceiling counter. |
| **3. Planned vs Logged.** | The ceiling/miss determination IS the planned-vs-logged delta (prescribed reps/RPE vs performed reps/tap). |
| **4. The capture fix.** | Honors `is_warmup` (excluded) and `feedback_tap` (the per-set signal driving both e1RM and the outcome). Does not re-derive warmup from names. |
| **5. Objective gating.** | Ceiling/failed/tier fire only when `objective == PROGRESS`; e1RM + gate-eval are objective-independent measurements. This is the spec's "stall logic only on PROGRESS" made concrete. |
| **6. Locked reference data.** | Bodyweight target, RPE cap, ladder length all arrive via the caller's input; the hook invents none. The schema add is a counter, not a reference value. |

---

## 13. Approvals

| Step | Status | Date |
|---|---|---|
| v0.4 scope: single-session updates; defer history-dependent rules to v0.5 | approved | 2026-06-25 |
| Architecture: pure plan → thin applier (new persistence/ package) | approved | 2026-06-25 |
| e1RM rule: best tapped set via max(); objective-independent; no-sets→untouched | approved | 2026-06-25 |
| Gating: measurements always-on; prescription machinery PROGRESS-gated; current_load never written | approved | 2026-06-25 |
| Phase gate: evaluate + report; never auto-flip | approved | 2026-06-25 |
| One shared anchor (best-by-e1RM) for both e1RM and outcome; three-way exclusive determination | approved | 2026-06-25 |
| One new counter field (consecutive_failed_progressions) | approved | 2026-06-25 |
| Applier atomicity (resolve-all-first) + combined-decoupling + atomicity tests | approved | 2026-06-25 |
| Spec written | this commit | 2026-06-25 |
| User review of spec | pending | — |
| Implementation plan (`writing-plans` skill) | not yet started | — |
