# Validator (v0.2) — Design

**Date:** 2026-06-24
**Repo:** `~/projects/IronLog-V2` (this repo)
**Status:** approved design; awaiting implementation plan
**Scope:** v0.2 only — the deterministic hard-rule gate from `docs/06_generation_algorithm_spec.md` §4 + §8. Pure logic, fully unit-tested, no LLM, no DB, no HTTP. Cross-session rules use deferred-binding tallies; the real `WeeklyLedger` ships in v0.3.

---

## 1. Purpose

The IronLog V2 engine separates the LLM's job ("propose accessory selection, ordering, weak-point response") from the deterministic rules' job ("never breach load floors, caps, frequencies, equipment, safety limits"). Per the design's spine invariant: **rules dispose; the model proposes.** The **validator** is what disposes.

This document specifies the v0.2 validator: a pure-logic module at `ironlog/engine/validator.py` that takes a proposed `Session` and a resolved `ValidationContext`, and returns a `ValidationResult` enumerating every rule breach. Two outcome classes per violation: **CLAMP** (numeric, recoverable — corrected_value supplied) and **REJECT** (structural or safety — caller must rethink). The validator is the prerequisite for every later piece: the generation loop (v0.5) cannot validate proposals without it; the analysis hook (v0.4) and any logging UI (post-v0.3) will use it to surface "what just happened violated which rule."

The validator is **not** the repair loop. It reports; the consumer decides whether to clamp-and-proceed, retry the LLM, fall back to a safe session, or — in the logging case — surface the violation as a fact.

---

## 2. Constraints

Carried verbatim from `~/projects/IronLog-V2/CLAUDE.md` (the project's architecture invariants) and `docs/06` §4 + §8:

- **`engine/` is pure logic.** No DB, no network, no LLM, no file I/O imports. All inputs arrive via the `ValidationContext` argument.
- **Do not add `from __future__ import annotations`** to any file with SQLModel `Relationship(...)` — it stringifies types and SQLAlchemy can't resolve them. (The validator file itself has no Relationship, but its `Session` import does.)
- **DTO field names already established on the wire (`MovementDto`, `BandPairDto`, etc.) are stable contracts** with the Android client (see CLAUDE.md "Client contract"). The validator doesn't expose new wire shapes in v0.2, but if any later endpoint surfaces `ValidationResult` to the client, the field names defined here become the contract.
- **No silent breaches.** Every rule breach surfaces as a `Violation`. The validator never auto-applies clamps; that's the consumer's choice.
- **Two outcome classes:** CLAMP (corrected_value supplied; consumer may apply it) and REJECT (structural or safety; consumer cannot trivially fix). HT bottom-clamp safety is deliberately REJECT, not CLAMP — see §4.7.
- **No early exit.** The validator runs every rule against every applicable element and returns all violations. Consumers need the full picture.

---

## 3. Architecture

**Single file**, `ironlog/engine/validator.py` (~400 lines including the rule catalog). One private helper function per rule (`_check_<rule_lowercase>(session, ctx) -> List[Violation]`); a public `validate()` calls all of them and concatenates results.

**Rejected alternatives:**
- **Rule-registry pattern** (each rule a class registered in a list) — premature abstraction for 12 rules; adds ~200 lines of indirection that doesn't pay back until the rule count >> 20.
- **Single inline `validate()` with no helpers** — loses individual-rule testability and forces re-walking the session tree for each conceptual rule.

**Style mirrors** `ironlog/engine/loading.py` and `ironlog/engine/progression.py`: pure functions, full type hints, module docstring explaining intent, no I/O imports.

**Wired into:** `ironlog/engine/__init__.py` re-exports the public surface (`validate`, the dataclasses, the enums).

**Tested by:** `tests/test_validator.py` (~22 cases) using in-memory Session/ExerciseGroup/PlannedExercise/PlannedSet construction. No DB.

---

## 4. Public API

```python
# ironlog/engine/validator.py
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set
from ..models.enums import GroupType, LiftCategory, ProgressionMode
from ..models.session import Session


class ViolationKind(str, Enum):
    CLAMP = "CLAMP"
    REJECT = "REJECT"


class RuleCode(str, Enum):
    # Structural (REJECT) — within-session
    PRIMARY_NOT_FIRST = "PRIMARY_NOT_FIRST"
    GIANT_SET_ROUNDS = "GIANT_SET_ROUNDS"
    GIANT_SET_CONCURRENCY = "GIANT_SET_CONCURRENCY"
    SINGLE_KB = "SINGLE_KB"
    EQUIPMENT_NOT_IN_MANIFEST = "EQUIPMENT_NOT_IN_MANIFEST"
    HT_BOTTOM_OVER_LIMIT = "HT_BOTTOM_OVER_LIMIT"
    HT_BAND_NOT_REGISTERED = "HT_BAND_NOT_REGISTERED"
    # Numeric (CLAMP) — within-session; corrected_value supplied
    LOAD_BELOW_FLOOR = "LOAD_BELOW_FLOOR"
    LOAD_OVER_CAP = "LOAD_OVER_CAP"
    RPE_OVER_CAP = "RPE_OVER_CAP"
    # Cross-session (REJECT) — checked iff ctx.tallies is not None
    KNEE_FREQUENCY = "KNEE_FREQUENCY"
    PULL_PUSH_RATIO = "PULL_PUSH_RATIO"


@dataclass
class Violation:
    kind: ViolationKind
    rule: RuleCode
    message: str
    group_index: Optional[int] = None
    movement_id: Optional[int] = None
    set_index: Optional[int] = None
    corrected_value: Optional[float] = None  # populated ONLY when kind == CLAMP


@dataclass
class MovementInfo:
    """Projection of a Movement onto only the fields the validator needs.
    Built by the caller from the DB; the validator never queries Movement directly."""
    movement_id: int
    is_primary: bool = False
    load_equipment_id: Optional[int] = None
    load_floor: Optional[float] = None
    cap: Optional[float] = None
    rpe_cap_exempt: bool = False
    lift_category: LiftCategory = LiftCategory.NONE
    progression_mode: ProgressionMode = ProgressionMode.NONE
    # NOTE: no is_kettlebell flag — derived from `load_equipment_id == ctx.kettlebell_equipment_id`.


@dataclass
class WeeklyTallies:
    """Projected end-of-week state, supplied by the caller. The real `WeeklyLedger`
    (v0.3) will produce this; for v0.2, tests supply synthetic tallies."""
    knee_counts: Dict[str, int] = field(default_factory=dict)
    knee_targets: Dict[str, int] = field(default_factory=dict)
    pull_volume: float = 0.0
    push_volume: float = 0.0
    pull_push_target: float = 2.0


@dataclass
class ValidationContext:
    movements: Dict[int, MovementInfo] = field(default_factory=dict)
    manifest_equipment_ids: Set[int] = field(default_factory=set)
    phase_hard_cap: float = 8.0
    band_bottom_lb: Dict[int, float] = field(default_factory=dict)
    ht_bottom_clamp: float = 220.0
    kettlebell_equipment_id: Optional[int] = None
    tallies: Optional[WeeklyTallies] = None


@dataclass
class ValidationResult:
    violations: List[Violation] = field(default_factory=list)

    @property
    def rejects(self) -> List[Violation]:
        return [v for v in self.violations if v.kind == ViolationKind.REJECT]

    @property
    def clamps(self) -> List[Violation]:
        return [v for v in self.violations if v.kind == ViolationKind.CLAMP]

    @property
    def is_structurally_valid(self) -> bool:
        """True iff there are no REJECT violations. CLAMPs do NOT affect this —
        callers decide separately whether clamps are acceptable for their context
        (auto-apply during generation vs. surface as fact during logging)."""
        return not self.rejects


def validate(session: Session, ctx: ValidationContext) -> ValidationResult: ...
```

### 4.1 Clamp application contract (caller's responsibility)

When a consumer wants to apply a clamp, the pair `(locator, rule) → corrected_value` is unambiguous:

| Rule | Locator | Field on located element |
|---|---|---|
| `LOAD_BELOW_FLOOR` | `(group_index, movement_id, set_index)` | `set.target_load` |
| `LOAD_OVER_CAP`    | `(group_index, movement_id, set_index)` | `set.target_load` |
| `RPE_OVER_CAP`     | `(group_index, movement_id, set_index)` | `set.target_rpe`  |

On a single set, `LOAD_BELOW_FLOOR` and `LOAD_OVER_CAP` cannot both fire (they require `target_load < floor` and `target_load > cap`, which can't simultaneously hold given the invariant `floor < cap`). `RPE_OVER_CAP` is orthogonal. If a new clamp rule is added later that targets `target_load`, it gets its own `RuleCode` and the consumer dispatches on `rule`. `corrected_value` is **only** meaningful when `kind == CLAMP`; for REJECTs it is `None` and any offending observed value lives in the message string.

---

## 5. Traversal & lookup invariants

- Walk `session.groups` sorted by `order_index`.
- Within each group, walk `group.exercises` sorted by `order_index`.
- Within each exercise, iterate `exercise.planned_sets` in their order.
- Resolve movement via `info = ctx.movements.get(exercise.movement_id)`.
  - If `info is None`: **skip movement-dependent checks for that exercise** (do not crash). Non-movement-dependent rules (GIANT_SET_ROUNDS, GIANT_SET_CONCURRENCY at the group level; KNEE_FREQUENCY / PULL_PUSH_RATIO at the session level) still run.
- Each rule helper emits 0+ Violations; `validate()` concatenates all twelve results into one `ValidationResult.violations` list, structural-then-numeric-then-cross-session order for human readability.
- **No early termination on REJECTs.** Consumers need the full picture (e.g., "3 rejects + 2 clamps" is more useful than "first reject; bail").

---

## 6. Rule catalog

12 rules. Structural REJECTs first, then CLAMPs, then cross-session REJECTs.

### 6.1 PRIMARY_NOT_FIRST (REJECT) — strong form

All primary movements must come before all non-primary movements in the flat exercise sequence, AND primaries cannot appear inside a GIANT_SET.

Implementation walks the flat sequence (groups by order_index, exercises by order_index within) tracking `non_primary_seen: bool`. For each exercise:
- If `info.is_primary` and `group.group_type == GroupType.GIANT_SET` → reject `"primary movement inside GIANT_SET group"`. Locator: `group_index, movement_id`.
- Else if `info.is_primary` and `non_primary_seen` → reject `"primary movement after non-primary movement (primaries must all come first)"`. Locator: `group_index, movement_id`.
- Else if not `info.is_primary` → set `non_primary_seen = True`.

### 6.2 GIANT_SET_ROUNDS (REJECT)

For each group with `group_type == GIANT_SET` where `group.rounds != 3` → reject. Locator: `group_index`. Message: `"GIANT_SET rounds={n}, expected 3"`. This is the "wrong rep scheme" diagnostic; the repair fix is to change `group.rounds`.

### 6.3 GIANT_SET_CONCURRENCY (REJECT)

For each group with `group_type == GIANT_SET` where `len(exercises) not in 1..=3` → reject. Locator: `group_index`. Message: `"GIANT_SET has {n} exercises, expected 1-3 (room geometry)"`. This is the "physically can't set up" diagnostic; the repair fix is to drop or split exercises. 0 exercises rejects too (empty giant set is malformed).

### 6.4 SINGLE_KB (REJECT)

For each `GIANT_SET` group: count exercises whose movement satisfies `info.load_equipment_id == ctx.kettlebell_equipment_id`. If `ctx.kettlebell_equipment_id is None`, skip the rule entirely (no KB equipment registered means no possible violation). If count `>= 2` → reject. Locator: `group_index`. Message: `"GIANT_SET has {n} kettlebell movements; only 1 KB station available"`.

### 6.5 EQUIPMENT_NOT_IN_MANIFEST (REJECT)

For each exercise: if `info.load_equipment_id is not None` and `info.load_equipment_id not in ctx.manifest_equipment_ids` → reject. Locator: `group_index, movement_id`. Message: `"Equipment id {id} not in active manifest"`. One violation per offending exercise; the manifest is resolved by the caller from the current `Phase` + `Equipment.available_phase`.

### 6.6 HT_BOTTOM_OVER_LIMIT (REJECT — safety)

Per `docs/01_ht_composite_spec.md` §4.1: the GMWD hip-thrust lap-bar latch flexes at bottom totals above 220 lb. This is hardware-safety, not a soft "round it down" — hence REJECT, not CLAMP.

For each exercise whose movement satisfies `lift_category == LiftCategory.HIP_THRUST OR progression_mode == ProgressionMode.COMPOSITE`, for each planned set where both `target_plates is not None` AND `band_pair_id is not None`:
- If `band_pair_id in ctx.band_bottom_lb`:
  - `bottom_total = target_plates + ctx.band_bottom_lb[band_pair_id]`
  - If `bottom_total > ctx.ht_bottom_clamp` → reject. Locator: `group_index, movement_id, set_index`. Message: `"HT bottom total {bottom_total:.1f} lb exceeds clamp {ctx.ht_bottom_clamp:.1f} lb (plates+band at bottom position)"`. `corrected_value = None` (REJECT semantics; the offending value is in the message).
- If `band_pair_id NOT in ctx.band_bottom_lb` → emit `HT_BAND_NOT_REGISTERED` (§6.7) instead. Do not compute `bottom_total` from a missing band entry.
- If either `target_plates` or `band_pair_id` is None → skip that set (incomplete prescription; can't compute bottom total).

### 6.7 HT_BAND_NOT_REGISTERED (REJECT — safety)

Companion to §6.6. For an HT set with `target_plates` and `band_pair_id` both set, if `band_pair_id not in ctx.band_bottom_lb` → reject. Locator: `group_index, movement_id, set_index`. Message: `"HT band_pair_id {id} not registered in ctx.band_bottom_lb — cannot evaluate bottom-clamp safety"`. Fail-loud rather than treat the missing entry as 0; the bottom clamp is a hardware safety rule and silent substitution would let an unevaluable prescription pass.

### 6.8 LOAD_BELOW_FLOOR (CLAMP)

For each set with `target_load is not None`: if `info.load_floor is not None` and `target_load < info.load_floor` → CLAMP. Locator: `group_index, movement_id, set_index`. `corrected_value = info.load_floor`. Message: `"Load {target_load} below floor {load_floor}"`.

### 6.9 LOAD_OVER_CAP (CLAMP)

For each set with `target_load is not None`: if `info.cap is not None` and `target_load > info.cap` → CLAMP. Locator: `group_index, movement_id, set_index`. `corrected_value = info.cap`. Message: `"Load {target_load} over cap {cap}"`.

### 6.10 RPE_OVER_CAP (CLAMP)

For each set with `target_rpe is not None`: if `info.rpe_cap_exempt is False` and `target_rpe > ctx.phase_hard_cap` → CLAMP. Locator: `group_index, movement_id, set_index`. `corrected_value = ctx.phase_hard_cap`. Message: `"RPE {target_rpe} over phase cap {phase_hard_cap}"`. `rpe_cap_exempt == True` skips entirely (HT is always-progress and exempt per docs/01 §5).

### 6.11 KNEE_FREQUENCY (REJECT — cross-session)

Skipped entirely if `ctx.tallies is None`. Otherwise, for each `(modality, target)` in `ctx.tallies.knee_targets`:
- `count = ctx.tallies.knee_counts.get(modality, 0)`
- If `count < target` → reject. No locator (session-level). Message: `"{modality} frequency unmet: {count}/{target} (owed {target - count})"`.

### 6.12 PULL_PUSH_RATIO (REJECT — cross-session)

Skipped entirely if `ctx.tallies is None`. Otherwise: if `ctx.tallies.push_volume > 0` and `ctx.tallies.pull_volume / ctx.tallies.push_volume < ctx.tallies.pull_push_target` → reject. No locator. Message: `"Pull:push ratio {ratio:.2f} below target {pull_push_target:.1f}"`. `push_volume == 0` → skip (avoid div-by-zero; insufficient data to compute).

---

## 7. Testing strategy

`tests/test_validator.py`, pytest, ~22 cases. Style matches `tests/test_loading.py`.

**Per-rule** (24 cases — one happy + one sad per rule × 12 rules): for each rule, one "clean session, this rule does not fire" case + one "minimal session that triggers exactly this rule" case. Each sad case asserts the specific `RuleCode` and (for CLAMPs) the `corrected_value`.

**Cross-cutting** (~6 cases):
- Empty `session.groups` → `is_structurally_valid == True`, `len(violations) == 0`.
- Movement not in `ctx.movements` → movement-dependent rules silently skip; group-level rules (GIANT_SET_ROUNDS, GIANT_SET_CONCURRENCY) still fire if their conditions hold.
- HT set with `target_plates=None` OR `band_pair_id=None` → both HT rules skip (incomplete prescription).
- Clamps don't fail `is_structurally_valid`: a session with multiple CLAMPs but no REJECTs → `is_structurally_valid == True`, `len(clamps) > 0`.
- Apply-loop integration: build a session triggering `LOAD_BELOW_FLOOR` + `RPE_OVER_CAP` on the same set, apply both clamps via `(rule)`-dispatch, re-run `validate()`, expect zero clamps and `is_structurally_valid == True`.
- `tallies=None` → cross-session rules emit zero violations regardless of session content.

**Sessions built in-memory** (no DB). A small helper fixture builds Session/ExerciseGroup/PlannedExercise/PlannedSet via constructor kwargs (the same pattern as `tests/test_loading.py`'s domain construction).

**Test rigor invariants:**
- Every test asserts a specific `RuleCode` (no "violations is non-empty" without saying which).
- CLAMP tests verify `corrected_value` is the expected number.
- REJECT tests assert `corrected_value is None`.
- HT REJECT tests verify the message includes the computed bottom_total and the clamp value.

---

## 8. Build & verify

```
cd ~/projects/IronLog-V2
.venv/bin/python -m ironlog.seed         # idempotent
.venv/bin/pytest -q                       # baseline: 18 existing tests
# after implementation:
.venv/bin/pytest -q tests/test_validator.py    # ~22 new tests
.venv/bin/pytest -q                       # full suite: ~40 tests, all green
```

No build step (Python source). No migrations (validator adds no columns). No HTTP changes (pure logic). The API server (`uvicorn ironlog.api.app:app`) restart is unnecessary in v0.2.

---

## 9. Wire impact: none

v0.2 adds no HTTP routes, no JSON shapes, no DTO changes. The Android client (`com.jauschua.ironlogv2`) continues to consume the four existing endpoints unchanged. The validator types (`ValidationResult`, `Violation`, etc.) are internal Python; they don't cross the wire until a later version surfaces them.

---

## 10. Out of scope (explicit YAGNI)

These are deliberately deferred. Listed so they don't sneak in.

- **HTTP endpoint** (e.g., `POST /sessions/validate`). Pure logic only per Question 3 of the brainstorming. Generation loop (v0.5) and any logging UI (v0.3+) are the first real callers.
- **CONDITIONING_PLACEMENT rule.** Z2 end-block / Dreadmill is not modeled in the current schema (`Dreadmill` not in Equipment seed, `GroupType` enum lacks a CONDITIONING variant, spec describes Z2 as "separate room, lightweight logging"). Add when conditioning gets first-class modeling (likely a `ConditioningLog` table or a `group_type=CONDITIONING` variant) — likely v0.4 or later.
- **Time-budget rule** (45–80 min cap, `docs/06` §4 feasibility). No per-movement setup/transition cost data exists in the schema. Adding a coarse heuristic now would invent numbers and violate the "locked reference data" invariant. Defer until the spec's "spend the transition budget well" concept lands real data.
- **Novelty / signature distance** (`docs/06` §7). Soft constraint scored across multiple sessions, not a per-session `validate()` concern. Lives in the generation loop's scoring (v0.5), not the validator.
- **Repair loop itself.** Validator only reports violations. The generation loop (v0.5) consumes the result and decides: clamp-and-proceed, retry the LLM with structured reasons, or fall back to the deterministic safe session.
- **`Equipment.available_phase` → manifest mapping.** The validator takes `manifest_equipment_ids` as input; resolving `P1/P2/P3/P4` phase strings to a concrete set of equipment IDs is the *caller's* job.
- **WeeklyLedger.** v0.3. The validator accepts a projected `WeeklyTallies` via `ctx.tallies`; when the real ledger ships, it just produces that projection. No validator API change at the v0.2 → v0.3 boundary.
- **Wire-formatting `ValidationResult` as JSON for the client.** No client UI consumes validator output in v0.2.

---

## 11. Architecture invariants honored

Cross-checked against `~/projects/IronLog-V2/CLAUDE.md`:

| Invariant | How v0.2 honors it |
|---|---|
| **1. Rules dispose; the model proposes.** | The validator is the canonical "rules dispose" implementation. Every rule is deterministic Python; no LLM in the loop. |
| **2. Definition vs State.** | `MovementInfo` projects only static facts (the validator never receives mutable `MovementState`). Cross-session counts come via `WeeklyTallies` (state), passed separately from the per-session check. |
| **3. Planned vs Logged.** | Validator operates on `Session`/`PlannedSet` (the prescribed shape). Logging-side `SetLog` validation in a future version uses the same rule set with `tallies` reflecting actuals. |
| **4. The capture fix.** | Not directly exercised by v0.2 (no log validation yet), but `MovementInfo.rpe_cap_exempt` is the per-movement signal that the validator respects. |
| **5. Objective gating.** | Out of scope for v0.2 (stall logic lives in the analysis hook, v0.4). |
| **6. Locked reference data.** | Validator reads `ctx.ht_bottom_clamp` (default 220), `ctx.phase_hard_cap`, the band table via `ctx.band_bottom_lb`. None of these are invented by the validator — they're caller-supplied from the seeded values. |

---

## 12. Approvals

| Step | Status | Date |
|---|---|---|
| v0.2 scope: validator only | approved | 2026-06-24 |
| Cross-session via deferred-binding tallies | approved | 2026-06-24 |
| Pure logic only (no HTTP endpoint) | approved | 2026-06-24 |
| Architecture: Option A (one fn per rule, single file) | approved | 2026-06-24 |
| Contract revisions (ok → is_structurally_valid; KB derived; ROUNDS/CONCURRENCY split; HT band fail-loud) | approved | 2026-06-24 |
| Rule catalog (12 rules) | approved | 2026-06-24 |
| Testing strategy + out-of-scope | approved | 2026-06-24 |
| Spec written | this commit | 2026-06-24 |
| User review of spec | pending | — |
| Implementation plan (`writing-plans` skill) | not yet started | — |
