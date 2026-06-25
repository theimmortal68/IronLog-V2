# WeeklyLedger (v0.3) — Design

**Date:** 2026-06-24
**Repo:** `~/projects/IronLog-V2` (this repo)
**Status:** approved design; awaiting implementation plan
**Scope:** v0.3 only — pure-logic aggregator that turns logged sets into a `WeeklyTallies` projection. Ships exactly what the v0.2 validator's deferred-binding contract requires (knee counts + pull/push volume). Items 3 and 4 of spec §5 (semi-anchor frequency, broader per-pattern volume) defer to v0.5.

---

## 1. Purpose

The v0.2 validator's cross-session rules (`KNEE_FREQUENCY`, `PULL_PUSH_RATIO`) accept a `WeeklyTallies` via `ctx.tallies` and reject when targets aren't met. v0.2 shipped the consumer half; v0.3 ships the producer.

The **WeeklyLedger** computes that `WeeklyTallies` projection from logged sets. It is per spec `docs/06_generation_algorithm_spec.md` §5: "What makes '2×/wk' and '2:1' enforceable across days rather than within one." It is the bridge between the planned-vs-logged delta (set logs) and the cross-session rules (validator).

The ledger is **not** the generation loop's "owed" computation, nor any of the broader scoring concerns from §5 items 3 + 4 (semi-anchor frequency, per-pattern volume beyond pull/push). Those are downstream of a pattern taxonomy that doesn't yet exist on `Movement` — and per the design-review framing, that taxonomy is a v0.5 decision made *with* the generation loop, not rushed into v0.3.

---

## 2. Constraints

Carried verbatim from the spec, `~/projects/IronLog-V2/CLAUDE.md`, and the v0.2 validator's API contract.

- **`engine/` is pure logic.** No DB / network / LLM / file I/O imports. The ledger receives inputs as plain arguments; the caller does the DB query and date filtering.
- **Do NOT add `from __future__ import annotations`** to any file that imports SQLModel models with `Relationship(...)`. `ledger.py` imports `Movement` and `SetLog`, both of which have Relationships. Stringified annotations break SQLAlchemy resolution.
- **Re-use the validator's `WeeklyTallies`** as the output type. Do not introduce a parallel "LedgerSnapshot" type — one shape, one contract.
- **Dynamic-vs-static principle** (locked during brainstorming):
  - Dynamic ambient state (time window, session-status filter, "now") → caller's responsibility; the ledger doesn't see it.
  - Static facts about a movement (knee modality) → modeled on `Movement` as a real column.
  - Static config (knee targets, pull:push target) → NOT the ledger's concern; lives wherever (PhasePolicy / EngineState / hardcoded constants) and gets merged onto the WeeklyTallies by whoever calls the validator.
- **`pull/push` derived from `lift_category`** as a documented coarse stand-in. v0.5's pattern taxonomy may supersede this without changing the public function signature.
- **No early exit, no silent exceptions.** Inputs that can't be aggregated (warmups, null actual_load/reps, missing movement_id) are skipped silently with explicit documentation of the under-count direction.

---

## 3. Architecture

**Single new file**, `ironlog/engine/ledger.py` (~150 lines). One public function: `compute_tallies(set_logs, movements) -> WeeklyTallies`. Module-level frozensets carry the pull/push classification; module docstring carries the unit pins (load×reps for volume, distinct sessions for knee counts).

Mirrors `ironlog/engine/validator.py` style: pure functions, full type hints, module docstring explaining intent, no I/O imports. Tested via `tests/test_ledger.py` (~12 cases) with in-memory SetLog/Movement construction (same pattern as the validator's tests).

**Rejected alternatives** (settled during brainstorming):
- Persistent `WeeklyLedger` table with post-session-hook writes. Risk: derived state stored separately drifts from its source — exactly V1's RPE failure mode. Recompute is trivial at one-user, ~50-working-sets-per-session, ~5-sessions-per-week scale.
- Caller-supplied classifier callable. Classification is a near-static property of a movement; "is this a Nordic" doesn't change with runtime state. Hiding the answer behind a callable obscures where the truth lives.
- Separate `LedgerSnapshot` output type then helper to convert. Two types for one shape; the WeeklyTallies dataclass already has empty defaults for the config fields the ledger doesn't produce.

**Wired into** `ironlog/engine/__init__.py` re-exports: `compute_tallies` (function) and the new `KneeModality` enum (re-exported through the engine layer since callers will need it to set `Movement.knee_modality` correctly).

---

## 4. Public API

### 4.1 New enum in `models/enums.py`

```python
class KneeModality(str, Enum):
    NORDIC = "NORDIC"   # Nordic hamstring curls
    TIB = "TIB"         # tibialis anterior raises
    KOT = "KOT"         # knees-over-toes (ATG split squat, sissy progressions)
    SISSY = "SISSY"     # sissy squats
```

### 4.2 New nullable column on `Movement`

```python
# in ironlog/models/library.py, Movement class:
knee_modality: Optional[KneeModality] = None
```

None of the 5 currently-seeded movements (Back Squat, Hip Thrust, Lateral Raise, Front Squat, Pull-up) is a knee-modality lift. The seed is updated to add the column with default `None`; no existing seed data needs the field set. Knee-modality movements get seeded when they're actually added to the user's program (likely as part of v0.4 or v0.5 when knee work enters the cataloged library).

### 4.3 New module `ironlog/engine/ledger.py`

```python
"""
ledger.py — pure-logic aggregator that turns logged sets into a WeeklyTallies.

Implements docs/06 §5 for the two items the v0.2 validator consumes: knee
modality counts and horizontal pull/push volume. Items 3 (semi-anchor
frequency) and 4 (broader per-pattern volume) are deferred to v0.5 along
with the pattern taxonomy that's their prerequisite.

The ledger is a producer counterpart to the validator (which is a consumer
of WeeklyTallies). It re-uses validator.WeeklyTallies as its output type:
one shape, one contract. The validator's cross-session checks pair with
the ledger's projection without any glue.

Pinned units (canonical, in docstrings + tests):
  * Pull/push volume metric = load * reps per set, summed across qualifying
    sets in the window. The validator's PULL_PUSH_RATIO rule expects this
    "volume-load / tonnage" semantic. NOT sets-only, NOT reps-only.
  * Knee counts unit = distinct sessions ("frequency"), NOT sets. A session
    with three working Nordic sets contributes 1 to knee_counts["NORDIC"],
    not 3. This is the standard "Nx/wk" reading from spec §4.

Classification (the v0.3 choices):
  * Knee modality: read directly from Movement.knee_modality (the new
    nullable enum column added in v0.3).
  * Pull/push: derived from Movement.lift_category via two module-level
    frozensets. Coarse stand-in; v0.5's pattern taxonomy may supersede
    this without changing the public function signature.

Silent-skip directions (graceful degradation):
  * Warmups (is_warmup=True): never count.
  * Incomplete log (actual_load=None or actual_reps=None): no volume can
    be computed; skip.
  * Movement absent from movements dict: skip the set log entirely.
    UNDERCOUNT DIRECTION: for frequency rules (KNEE_FREQUENCY), missing
    movement -> undercount -> over-prescription, which is the safer error
    direction (more knee work than required is safe; less than required is
    not). For ratio rules (PULL_PUSH_RATIO), the direction is symmetric
    and depends on which side is missing — callers should ensure the
    movements dict is complete for the set_logs they pass.

Not in scope (see docs/superpowers/specs/2026-06-24-weekly-ledger-design.md §10):
  * HTTP endpoint, persistent ledger table, semi-anchor frequency, broader
    pattern volume, targets sourcing, date math, apply-clamps helper.
"""

from typing import Dict, Iterable

from ..models.enums import LiftCategory
from ..models.library import Movement
from ..models.session import SetLog
from .validator import WeeklyTallies


# Coarse pull/push stand-in derived from existing lift_category.
# v0.5's pattern taxonomy may supersede this — when it does, swap the
# derivation here without changing compute_tallies's signature.
_HORIZONTAL_PULL: frozenset[LiftCategory] = frozenset({LiftCategory.ROW})
_HORIZONTAL_PUSH: frozenset[LiftCategory] = frozenset({
    LiftCategory.BENCH, LiftCategory.CG_PRESS,
})


def compute_tallies(
    set_logs: Iterable[SetLog],
    movements: Dict[int, Movement],
) -> WeeklyTallies: ...
```

The returned `WeeklyTallies` has:
- `knee_counts: Dict[str, int]` — keys are `KneeModality` string values; modalities with zero count are **absent** (not present-with-0).
- `pull_volume: float` — sum of `actual_load × actual_reps` across qualifying ROW sets.
- `push_volume: float` — sum of `actual_load × actual_reps` across qualifying BENCH + CG_PRESS sets.
- `knee_targets: Dict[str, int] = {}` (the dataclass default — the ledger does NOT supply targets).
- `pull_push_target: float = 2.0` (the dataclass default — same).

---

## 5. Semantics

### 5.1 Pinned unit: pull/push volume = `load × reps`

For each qualifying SetLog (pull or push, after gates):

```python
volume_contribution = set_log.actual_load * set_log.actual_reps
```

This is "volume-load" / "tonnage" — the canonical strength-program work metric. Reasons it's the right unit:

- **Sets-only is too coarse.** A 3×5 vs 3×15 carry very different work; counting only sets would treat them as equivalent for the 2:1 ratio purpose.
- **Reps-only ignores intensity.** 10 reps at 45 lb and 10 reps at 225 lb are not equivalent work.
- **Load×reps captures both.** The 2:1 horizontal pull:push rule is a *work-balance* metric; work has units of (force × distance) which, at the gym, maps cleanly to load × reps.

Pinned by `tests/test_ledger.py::test_pull_volume_is_load_times_reps` and `test_push_volume_is_load_times_reps`.

**Zero or null `actual_load` (intentional design choice — silent under-count):**

- `actual_load == 0` and `actual_reps == n`: contributes `0 × n == 0` to volume. The set still qualifies as a working set per §5.3 (load is non-null, reps are non-null, not a warmup) — so a knee-modality set with `actual_load=0` *does* count toward its modality's session-frequency, but adds zero to pull/push volume.
- `actual_load is None`: skipped entirely by §5.3's gate; contributes nothing to volume *and* nothing to knee counts.

For bodyweight or pure-band pull/push movements (e.g., a future bodyweight ROW variant) where load reads as 0 by design, the pull/push volume silently under-counts. **This is acceptable in v0.3** — bodyweight-load tracking convention is out of scope. None of the currently-seeded movements in `_HORIZONTAL_PULL` (ROW) or `_HORIZONTAL_PUSH` (BENCH, CG_PRESS) are bodyweight movements, so the under-count surfaces only if a future library entry uses these `lift_category` values for an unloaded variant. v0.5 may revisit if a bodyweight-load convention (e.g., a `bodyweight: bool` flag plus an `EngineState.bodyweight` lookup) lands on `Movement`. Pinned by `tests/test_ledger.py::test_zero_load_contributes_zero_volume`.

### 5.2 Pinned unit: knee counts = distinct sessions ("frequency")

For each `KneeModality`, the count is `len({set_log.session_id for set_log in qualifying_logs_of_that_modality})`.

**Three sets of Nordics in one session → count of 1.** Two sets of Nordics in session A + one set in session B → count of 2.

This is the standard "Nx/wk" reading from spec §4 ("Nordic 2×/wk" = trained on 2 days that week). If the user wants set-count instead (e.g., for a volume-based knee progression rule later), that's a separate metric and would live alongside, not replace this. Pinned by `tests/test_ledger.py::test_knee_counts_are_distinct_sessions`.

### 5.3 Working-set definition

A `SetLog` qualifies as a "working set" iff:

- `is_warmup is False` — warmups never count toward weekly work.
- `actual_load is not None` AND `actual_reps is not None` — incomplete log; no volume can be computed.

The mandatory-`feedback_tap`-on-working-sets rule is enforced at the **API layer** (the capture fix). The ledger does NOT re-check `feedback_tap` is non-null — a logged set passing the load+reps+warmup gates is treated as legitimately working work. If the API ever lets a working set land without feedback_tap, that's an upstream contract violation, not a ledger concern.

### 5.4 Classification

**Pull/push** — derived from `Movement.lift_category` via two module-level frozensets:

```python
_HORIZONTAL_PULL: frozenset[LiftCategory] = frozenset({LiftCategory.ROW})
_HORIZONTAL_PUSH: frozenset[LiftCategory] = frozenset({LiftCategory.BENCH, LiftCategory.CG_PRESS})
```

A SetLog contributes to `pull_volume` iff its movement's `lift_category in _HORIZONTAL_PULL`; to `push_volume` iff `in _HORIZONTAL_PUSH`; **to neither otherwise.** Back Squat (BACK_SQUAT), Hip Thrust (HIP_THRUST), Lateral Raise (NONE), Pull-up (NONE), OHP (OHP), RDL (RDL), Deadlift (DEADLIFT), Front Squat (FRONT_SQUAT), Rev Hyper (REV_HYPER) all contribute to neither — they're not in the horizontal pull/push contest.

Pinned by `tests/test_ledger.py::test_squat_contributes_to_neither_volume` (and implicitly by the pull and push pin tests, which use specific lift_category values).

**Knee modality** — direct: `movement.knee_modality` (the new nullable enum column added in v0.3). A SetLog from a movement with non-None `knee_modality` contributes its `session_id` to the per-modality set. Movements with `knee_modality is None` (which today is *all* seeded movements) contribute nothing to `knee_counts`.

---

## 6. Edge cases

### 6.1 Silent-skip table

| Case | Behavior | Direction |
|---|---|---|
| `is_warmup is True` | Skipped entirely | N/A (warmups are not "work") |
| `actual_load is None` OR `actual_reps is None` | Skipped entirely | Volume undercounted by the missing entries |
| `movement_id not in movements` dict | Skipped entirely | Counts and volumes both undercount |
| Movement with `knee_modality is None` and `lift_category` not in pull/push sets | Skipped (contributes to nothing) | Correct — neither knee nor pull/push |

### 6.2 Under-count is the safer direction (for frequency)

When the movements dict is incomplete and SetLogs reference unknown `movement_id`s, the ledger silently skips them. This **undercounts**.

- **For frequency rules** (`KNEE_FREQUENCY`): undercount → validator says "knee work owed" when it's actually been done → caller is prompted to prescribe more knee work. **Over-prescription is the safer error direction** (more knee work than required is safe; less than required is not).
- **For ratio rules** (`PULL_PUSH_RATIO`): the direction is *symmetric* and depends on which side is missing. Missing pull movements → ratio reads artificially low → validator may falsely reject; missing push movements → ratio reads artificially high → validator may falsely accept. Callers should ensure the movements dict is complete for the SetLogs they pass.

Both effects are documented in the module docstring per the v0.3 final-review framing.

### 6.3 What the ledger does NOT do

- Does not consult `Session` records (only `SetLog`; SetLog carries its own `session_id` and `movement_id`).
- Does not consult `PlannedSet` (operates on the logged side only; planned-vs-logged delta is a different analysis, downstream of this).
- Does not consult `ExerciseSurvey`, `Note`, `MovementState`, or `EngineState`.
- Does not perform date filtering (caller pre-filters SetLogs to the window).
- Does not filter by session status (caller pre-filters to COMPLETED if they want to).
- Does not produce `knee_targets` or `pull_push_target` on the returned WeeklyTallies (those are static config; left at WeeklyTallies dataclass defaults).

---

## 7. Testing

`tests/test_ledger.py`, pytest, ~13 cases. Style matches `tests/test_validator.py` (in-memory construction via small factory helpers; explicit `RuleCode`-equivalent assertions on returned fields).

**Core (7 cases — including all four pin tests):**

1. `test_empty_input_returns_default_tallies` — `compute_tallies([], {}) == WeeklyTallies()` (default-valued dataclass; `knee_counts == {}`, both volumes 0.0, defaults preserved).
2. `test_pull_volume_is_load_times_reps` *(pin)* — one ROW SetLog with `actual_load=100, actual_reps=10` → `pull_volume == 1000.0`. Documents the canonical metric.
3. `test_push_volume_is_load_times_reps` *(pin)* — one BENCH SetLog 100×10 → `push_volume == 1000.0`. Add a CG_PRESS SetLog 50×8 → `push_volume == 1400.0` (cumulative, both lift_categories in `_HORIZONTAL_PUSH` count).
4. `test_knee_counts_are_distinct_sessions` *(pin)* — three Nordic SetLogs sharing `session_id=1` → `knee_counts["NORDIC"] == 1`. Add Nordic SetLog with `session_id=2` → `knee_counts["NORDIC"] == 2`.
5. `test_squat_contributes_to_neither_volume` *(pin)* — Back Squat SetLog 225×5 → `pull_volume == 0.0` AND `push_volume == 0.0`. Hip Thrust SetLog similarly contributes to neither.
6. `test_zero_load_contributes_zero_volume` *(pin)* — ROW SetLog with `actual_load=0, actual_reps=10, is_warmup=False` → `pull_volume == 0.0` (the set qualifies as working per §5.3 but `0 × 10 == 0`). Documents the bodyweight/banded under-count case from §5.1.
7. `test_multi_modality_knee_mix` — one Nordic session, one TIB session, two KOT sessions, no SISSY sessions → `knee_counts == {"NORDIC": 1, "TIB": 1, "KOT": 2}` (no SISSY key — zero-count modalities are absent, not present-with-0).

**Edge cases (6 cases):**

7. `test_warmup_skipped` — working ROW set 100×10 + warmup ROW set 45×10 → `pull_volume == 1000.0`.
8. `test_null_actual_load_skipped` — ROW SetLog with `actual_load=None, actual_reps=10` → `pull_volume == 0.0`.
9. `test_null_actual_reps_skipped` — ROW SetLog with `actual_load=100, actual_reps=None` → `pull_volume == 0.0`.
10. `test_missing_movement_silently_skipped` — SetLog references `movement_id=99` not in the movements dict → no exception, no entry in counts, no volume credited; existing valid SetLog still aggregated.
11. `test_targets_left_at_defaults` — returned `WeeklyTallies.knee_targets == {}` AND `pull_push_target == 2.0` regardless of input. The ledger does NOT produce targets.
12. `test_mixed_multi_session_aggregation` — 3 sessions with assorted ROW + BENCH + Nordic + Back Squat + Lateral Raise sets → all tallies sum correctly across sessions; counts use the right `session_id`s; volumes use load×reps; squat and lateral-raise sets correctly contribute to nothing.

**Test rigor invariants:**

- Every test asserts specific fields on the returned `WeeklyTallies` (not "result is not None").
- Volume tests use distinguishable load and rep numbers so the assertion can't pass under wrong-metric implementations (e.g., 100×10=1000, not 100=100 or 10=10).
- Knee tests use explicit `session_id`s — the distinct-sessions semantic is asserted by the test data, not just the count number.

---

## 8. Build & verify

```
cd ~/projects/IronLog-V2
.venv/bin/python -m ironlog.seed         # idempotent; adds the new knee_modality column with NULL defaults
.venv/bin/pytest -q                       # baseline: 57 tests (post-cleanup)
# after implementation:
.venv/bin/pytest -q tests/test_ledger.py  # 13 new tests
.venv/bin/pytest -q                       # full suite: ~70 tests, all green
```

The seed needs a one-line edit only to acknowledge the new column (no existing movement is a knee-modality lift). No HTTP changes. No client impact. No migration script — SQLite + SQLModel `create_db_and_tables()` handles the column add on a fresh DB; existing DBs need a `DROP TABLE movement` or a manual ALTER (the v0.2 deploy DB is the only one to consider; one-shot fix at deploy time, captured in §11 deploy notes).

---

## 9. Wire impact: none

v0.3 adds no HTTP routes, no JSON shapes, no DTO changes. The Android client (`com.jauschua.ironlogv2`) continues to consume the four existing endpoints unchanged. The `KneeModality` enum is internal Python; it doesn't cross the wire until a later version surfaces it.

The systemd service on myflix (`ironlogv2.service`) restarts automatically on the next deploy when uvicorn picks up the new model. No service-config change.

---

## 10. Out of scope (explicit YAGNI)

These are deliberately deferred. Listed so they don't sneak in.

- **HTTP endpoint** (e.g., `GET /tallies?week=YYYY-WW`). Pure logic only, per the same call we made for the validator. The first real caller is the v0.5 generation loop, which can consume the function directly.
- **Persistent `WeeklyLedger` table** + post-session-hook updates. Derived state stored separately invites V1's RPE-drift failure mode (tallies and their source DB go out of sync; truth becomes ambiguous). Recompute is trivial at our scale (~50 working sets per session × ~5 sessions per week = ~250 rows aggregated in microseconds).
- **Per-pattern volume beyond pull/push** (squat-pattern, hinge-pattern, vertical-pull, vertical-push, etc.). Requires a pattern taxonomy that doesn't exist on `Movement` today. Per the v0.3 framing, that taxonomy is a v0.5 decision made *with* the generation loop, not rushed in here.
- **Semi-anchor frequency tracking** (spec §5 item 3). Tracks "main row 2×, triceps 2×" across the meso. Generation loop's read; no current consumer.
- **Knee targets / pull:push target sourcing.** Where these config values live (PhasePolicy? EngineState? hardcoded constants?) is the *caller's* question. The ledger returns the `WeeklyTallies` defaults (`{}` and `2.0`); whoever calls `validate()` merges in the targets from wherever they're configured.
- **Date math / week computation.** Caller pre-filters SetLogs to the window. The ledger has no `date` concept. Calendar-week vs rolling-7d vs custom-range semantics vary by consumer; baking one in is premature.
- **Session-status filtering.** Caller decides whether to include `PLANNED` / `IN_PROGRESS` / `COMPLETED` / `SKIPPED` sessions. Most callers will pass COMPLETED only.
- **Apply-clamps utility** (the reviewer-flagged pre-v0.5 helper from v0.2's final review). Different concern, different module; lives in `validator.py` when generation needs it.
- **Classifier callable injection.** Settled during brainstorming: classification is a near-static property of a movement and belongs in the data model (for knee modality) or derived from existing model fields (for pull/push). A callable would hide where the classification really lives.

---

## 11. Architecture invariants honored

Cross-checked against `~/projects/IronLog-V2/CLAUDE.md`:

| Invariant | How v0.3 honors it |
|---|---|
| **1. Rules dispose; the model proposes.** | The ledger is pure deterministic Python (no LLM). It produces facts the validator uses to enforce rules. |
| **2. Definition vs State.** | `Movement.knee_modality` is a static *definition* (a Nordic is a Nordic, doesn't change). The aggregated counts/volumes are computed from logged *state* at call time, not stored. |
| **3. Planned vs Logged.** | Ledger reads `SetLog` (the logged side) exclusively. The planned-vs-logged delta is a separate concern; the ledger reports actuals only. |
| **4. The capture fix.** | Honors `is_warmup` as a real column (excludes warmups). Trusts API-layer `feedback_tap` enforcement; does not re-check. |
| **5. Objective gating.** | Out of scope for v0.3 (lives in v0.4 analysis hook). |
| **6. Locked reference data.** | Adds `KneeModality` as a new enum and a nullable `knee_modality` column; does NOT touch the existing locked values (equipment floors, HT band table, phase policies, caps). |

---

## 12. Deploy notes (v0.3 schema change)

The new `Movement.knee_modality` column is the only schema change.

- **Fresh DB** (created from scratch): `python -m ironlog.seed` creates the table with the new column. No action needed.
- **Existing DB on myflix** (the v0.2 production DB): one-shot fix at deploy time. **Preferred — non-destructive ALTER (safe regardless of logging state):**
  ```
  ssh myflix 'sqlite3 ~/projects/IronLog-V2/ironlog.db "ALTER TABLE movement ADD COLUMN knee_modality TEXT NULL"'
  ssh myflix 'sudo systemctl restart ironlogv2.service'
  ```
  Preserves all existing rows including any logged sessions and set logs. **This should be the default path** once logging starts in v0.4+. Defaulting to ALTER makes it impossible to accidentally wipe user data in a future deploy.
- **Acceptable shortcut — only while the DB has zero logged sessions:** verify first, then nuke-and-reseed:
  ```
  ssh myflix 'cd ~/projects/IronLog-V2 && \
    [ "$(sqlite3 ironlog.db "SELECT COUNT(*) FROM setlog")" = "0" ] && \
    [ "$(sqlite3 ironlog.db "SELECT COUNT(*) FROM session")" = "0" ] && \
    sudo systemctl stop ironlogv2.service && rm ironlog.db && \
    .venv/bin/python -m ironlog.seed && \
    sudo systemctl start ironlogv2.service || \
    echo "ABORT: db has logged data — use the ALTER path"'
  ```
  Faster for the v0.3 deploy specifically (current DB has only reference data + the 5 example movements), but the gated guard above prevents this path from ever wiping real data in a later deploy where someone forgets the DB now has sessions.

The systemd unit (`ironlogv2.service`) needs no edit; it restarts uvicorn which picks up the new model on next start. Confirm via `ssh myflix 'systemctl is-active ironlogv2.service'` and `curl -sf http://myflix.media:8000/movements >/dev/null && echo UP`.

---

## 13. Approvals

| Step | Status | Date |
|---|---|---|
| v0.3 scope: knee + pull/push only (defer items 3 + 4 to v0.5 with the pattern taxonomy) | approved | 2026-06-24 |
| Classification: knee_modality as Movement field; pull/push derived from lift_category | approved | 2026-06-24 |
| Time bucketing: caller pre-filters (dynamic state defers) | approved | 2026-06-24 |
| Persistence: pure function, no second copy | approved | 2026-06-24 |
| Pinned units: load×reps for volume, distinct sessions for knee counts | approved | 2026-06-24 |
| Architecture: single file, one public function, mirrors validator style | approved | 2026-06-24 |
| Testing strategy + out-of-scope | approved | 2026-06-24 |
| Spec written | this commit | 2026-06-24 |
| User review of spec | pending | — |
| Implementation plan (`writing-plans` skill) | not yet started | — |
