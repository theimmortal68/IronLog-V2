# Validator (v0.2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `ironlog/engine/validator.py` — a pure-logic, deterministic hard-rule gate that returns structured violations for every breach of the 12 rules in the approved design at `docs/superpowers/specs/2026-06-24-validator-design.md`. Pure Python + ~22 unit tests. No DB, no HTTP, no LLM.

**Architecture:** Single new module `ironlog/engine/validator.py` (~400 lines) with one private `_check_<rule>(session, ctx) -> List[Violation]` helper per rule and a public `validate(session, ctx) -> ValidationResult` orchestrator. Mirrors the pure-function style of `engine/loading.py` and `engine/progression.py`. Tests in `tests/test_validator.py` (~22 cases) build sessions in-memory via constructor kwargs (same pattern as `tests/test_loading.py`).

**Tech Stack:** Python 3.14, SQLModel domain types (read-only — validator never queries), `dataclasses`, `enum.Enum`, pytest 8. No new dependencies.

## Global Constraints

Carried verbatim from the approved spec and `~/projects/IronLog-V2/CLAUDE.md`. Every task's requirements implicitly include this section.

- **`engine/` is pure logic.** No DB / network / LLM / file I/O imports. All inputs arrive via the `ValidationContext` argument.
- **Do NOT add `from __future__ import annotations`** to any file that imports SQLModel models with `Relationship(...)`. (`validator.py` imports `Session` from `..models.session`, which has Relationships. Stringified annotations break SQLAlchemy resolution. This already bit the project once.)
- **No silent breaches.** Every rule breach surfaces as a `Violation`. No early termination on REJECTs — consumers need the full picture.
- **CLAMP vs REJECT semantics:** `corrected_value` is populated ONLY when `kind == CLAMP`. For REJECTs (including HT_BOTTOM_OVER_LIMIT and HT_BAND_NOT_REGISTERED) it is `None` and any offending observed value lives in the message string.
- **`is_structurally_valid`** (the boolean property on `ValidationResult`) is True iff no REJECT violations. CLAMPs do NOT affect it. Do not introduce an `ok` property that quietly excludes clamps — the policy of "clamps OK or not?" belongs in the caller.
- **Rule count = 12.** No more, no less. `CONDITIONING_PLACEMENT` is explicitly deferred (spec §10) and must NOT be added.
- **Test runner is on myflix.** Workstation files are NFS-mounted from myflix; the venv at `.venv/` was built on myflix. Run pytest via `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q [args]'`. Edits on the workstation are visible on myflix instantly through NFS.
- **Baseline: 18 tests pass.** After v0.2 lands, full suite is 18 + ~22 = ~40 tests, all green.

---

## File structure

Created/modified across this plan. Paths relative to `~/projects/IronLog-V2`.

```
ironlog/engine/validator.py                # NEW — Task 1 scaffolds; Tasks 2-7 fill in rules
ironlog/engine/__init__.py                 # MODIFY in Task 7 — add re-exports
tests/test_validator.py                    # NEW — Task 1 starts; Tasks 2-7 add per-rule cases
```

No model changes. No migration. No HTTP changes. No deps added.

---

### Task 1: Contract scaffolding + property tests

**Files:**
- Create: `ironlog/engine/validator.py`
- Create: `tests/test_validator.py`

**Interfaces:**
- Consumes: `ironlog.models.session.Session`, `ironlog.models.enums.{GroupType, LiftCategory, ProgressionMode}`
- Produces:
  - `ViolationKind(str, Enum)`: CLAMP, REJECT
  - `RuleCode(str, Enum)`: all 12 enum members declared
  - `@dataclass Violation`, `@dataclass MovementInfo`, `@dataclass WeeklyTallies`, `@dataclass ValidationContext`, `@dataclass ValidationResult` (with `rejects`, `clamps`, `is_structurally_valid` properties)
  - `def validate(session: Session, ctx: ValidationContext) -> ValidationResult` — returns `ValidationResult(violations=[])` for now; Tasks 2-7 fill in
  - Test-only helpers (module-level functions in `tests/test_validator.py`, NOT pytest fixtures): `make_session`, `make_group`, `make_exercise`, `make_set` — used by every later task's tests.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_validator.py` with this complete content:

```python
from datetime import date

from ironlog.models.enums import GroupType, LiftCategory, ProgressionMode, Scheme, Objective, SetRole
from ironlog.models.session import (
    ExerciseGroup, PlannedExercise, PlannedSet, Session,
)
from ironlog.engine.validator import (
    MovementInfo, RuleCode, ValidationContext, ValidationResult, Violation,
    ViolationKind, WeeklyTallies, validate,
)


# ---------------------------------------------------------------------------
# Helpers — module-level factory functions, shared by every test in the file.
# Mirror the pattern in tests/test_loading.py: plain functions, not fixtures.
# Construct domain objects via constructor kwargs (no DB).
# ---------------------------------------------------------------------------

def make_set(set_index: int = 0, *, target_load=None, target_rpe=None,
             target_plates=None, band_pair_id=None,
             set_role: SetRole = SetRole.WORKING) -> PlannedSet:
    return PlannedSet(
        planned_exercise_id=0, set_index=set_index, set_role=set_role,
        target_load=target_load, target_rpe=target_rpe,
        target_plates=target_plates, band_pair_id=band_pair_id,
    )


def make_exercise(movement_id: int, order_index: int = 0,
                  sets=None, *,
                  scheme: Scheme = Scheme.STRAIGHT,
                  objective: Objective = Objective.MAINTAIN) -> PlannedExercise:
    return PlannedExercise(
        group_id=0, movement_id=movement_id, order_index=order_index,
        scheme=scheme, objective=objective,
        planned_sets=list(sets or []),
    )


def make_group(order_index: int, group_type: GroupType, *,
               rounds: int = 1, exercises=None) -> ExerciseGroup:
    return ExerciseGroup(
        session_id=0, order_index=order_index, group_type=group_type,
        rounds=rounds, exercises=list(exercises or []),
    )


def make_session(groups=None) -> Session:
    return Session(
        date=date(2026, 1, 1), day_role="Upper A", phase="CUT",
        groups=list(groups or []),
    )


def make_movement(movement_id: int, *,
                  is_primary: bool = False,
                  load_equipment_id=None,
                  load_floor=None,
                  cap=None,
                  rpe_cap_exempt: bool = False,
                  lift_category: LiftCategory = LiftCategory.NONE,
                  progression_mode: ProgressionMode = ProgressionMode.NONE) -> MovementInfo:
    return MovementInfo(
        movement_id=movement_id,
        is_primary=is_primary,
        load_equipment_id=load_equipment_id,
        load_floor=load_floor,
        cap=cap,
        rpe_cap_exempt=rpe_cap_exempt,
        lift_category=lift_category,
        progression_mode=progression_mode,
    )


# ---------------------------------------------------------------------------
# Task 1 — Contract scaffolding tests
# ---------------------------------------------------------------------------

def test_empty_session_is_structurally_valid():
    result = validate(make_session(), ValidationContext())
    assert result.violations == []
    assert result.is_structurally_valid is True
    assert result.rejects == []
    assert result.clamps == []


def test_rejects_property_filters_to_rejects():
    result = ValidationResult(violations=[
        Violation(kind=ViolationKind.REJECT, rule=RuleCode.GIANT_SET_ROUNDS, message="r"),
        Violation(kind=ViolationKind.CLAMP,  rule=RuleCode.LOAD_OVER_CAP,    message="c", corrected_value=10.0),
    ])
    assert len(result.rejects) == 1
    assert result.rejects[0].rule == RuleCode.GIANT_SET_ROUNDS
    assert len(result.clamps) == 1
    assert result.clamps[0].rule == RuleCode.LOAD_OVER_CAP


def test_is_structurally_valid_false_with_reject():
    result = ValidationResult(violations=[
        Violation(kind=ViolationKind.REJECT, rule=RuleCode.GIANT_SET_ROUNDS, message="r"),
    ])
    assert result.is_structurally_valid is False


def test_is_structurally_valid_true_with_only_clamps():
    """Clamps do NOT affect is_structurally_valid. The caller decides whether
    clamps are acceptable for its context."""
    result = ValidationResult(violations=[
        Violation(kind=ViolationKind.CLAMP, rule=RuleCode.LOAD_OVER_CAP, message="c", corrected_value=25.0),
        Violation(kind=ViolationKind.CLAMP, rule=RuleCode.RPE_OVER_CAP,  message="c", corrected_value=8.0),
    ])
    assert result.is_structurally_valid is True
    assert len(result.clamps) == 2


def test_rulecode_enum_has_all_12_members():
    expected = {
        # Structural REJECT
        "PRIMARY_NOT_FIRST", "GIANT_SET_ROUNDS", "GIANT_SET_CONCURRENCY",
        "SINGLE_KB", "EQUIPMENT_NOT_IN_MANIFEST",
        "HT_BOTTOM_OVER_LIMIT", "HT_BAND_NOT_REGISTERED",
        # Numeric CLAMP
        "LOAD_BELOW_FLOOR", "LOAD_OVER_CAP", "RPE_OVER_CAP",
        # Cross-session REJECT
        "KNEE_FREQUENCY", "PULL_PUSH_RATIO",
    }
    actual = {r.name for r in RuleCode}
    assert actual == expected, f"missing: {expected - actual}, extra: {actual - expected}"
    # CONDITIONING_PLACEMENT is explicitly deferred per spec §10 — must not appear.
    assert "CONDITIONING_PLACEMENT" not in actual
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_validator.py 2>&1 | tail -10'`
Expected: 5 errors (collection or test failures) — `ModuleNotFoundError: No module named 'ironlog.engine.validator'`.

- [ ] **Step 3: Create the validator module**

Create `ironlog/engine/validator.py`:

```python
"""
validator.py — deterministic hard-rule gate for proposed workout sessions.

Implements docs/06 §4 + §8 and docs/01 §4.1 (HT bottom safety). The validator
takes a proposed Session and a resolved ValidationContext, walks every rule, and
returns a ValidationResult with structured violations.

Two outcome classes per violation:
  * CLAMP — numeric, recoverable. corrected_value supplied; the consumer may
    apply it via the (locator, rule) -> corrected_value contract documented
    below.
  * REJECT — structural or safety. corrected_value is None; offending observed
    values live in the message string. The consumer cannot trivially fix.

The validator is the canonical "rules dispose" implementation: every rule is
pure deterministic Python, no LLM in the loop. It does NOT auto-apply clamps,
nor does it implement the repair loop — that's the generation loop's job
(v0.5). The validator only reports.

Clamp application contract (caller-side):
  consumer iterates ValidationResult.clamps and dispatches on rule:
    LOAD_BELOW_FLOOR | LOAD_OVER_CAP  -> write corrected_value to set.target_load
    RPE_OVER_CAP                       -> write corrected_value to set.target_rpe
  The pair (locator, rule) uniquely identifies the field. On a single set,
  LOAD_BELOW_FLOOR and LOAD_OVER_CAP cannot both fire (they require
  target_load < floor and target_load > cap respectively, which can't
  simultaneously hold given the invariant floor < cap).
"""

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
    Built by the caller from the DB; the validator never queries Movement."""
    movement_id: int
    is_primary: bool = False
    load_equipment_id: Optional[int] = None
    load_floor: Optional[float] = None
    cap: Optional[float] = None
    rpe_cap_exempt: bool = False
    lift_category: LiftCategory = LiftCategory.NONE
    progression_mode: ProgressionMode = ProgressionMode.NONE
    # NOTE: no is_kettlebell flag — derive single-KB rule from
    # load_equipment_id == ctx.kettlebell_equipment_id.


@dataclass
class WeeklyTallies:
    """Projected end-of-week state, supplied by the caller. The real
    WeeklyLedger (v0.3) will produce this; v0.2 tests supply synthetic."""
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
        the caller decides separately whether clamps are acceptable for its
        context (generation auto-applies, logging surfaces as facts)."""
        return not self.rejects


def validate(session: Session, ctx: ValidationContext) -> ValidationResult:
    """Validate a proposed session against all 12 hard rules.
    Returns ALL violations; never early-exits on a REJECT."""
    violations: List[Violation] = []
    # Tasks 2-7 will fill these in.
    return ValidationResult(violations=violations)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_validator.py 2>&1 | tail -5'`
Expected: `5 passed`. Full suite: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q 2>&1 | tail -3'` → `23 passed` (18 baseline + 5 new).

- [ ] **Step 5: Commit**

```bash
cd ~/projects/IronLog-V2
git add ironlog/engine/validator.py tests/test_validator.py
git commit -m "feat(validator): contract scaffolding — types, RuleCode, properties

12-rule enum, dataclasses, empty validate() returning ValidationResult.
Properties is_structurally_valid (no REJECTs), rejects, clamps.
Test helpers (make_session/make_group/make_exercise/make_set/make_movement)
shared by every later task's tests. 5 property tests confirm the contract.

CONDITIONING_PLACEMENT explicitly absent from RuleCode per spec §10.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: PRIMARY_NOT_FIRST (strong form)

**Files:**
- Modify: `ironlog/engine/validator.py` — add `_check_primary_not_first()` + call from `validate()`
- Modify: `tests/test_validator.py` — append cases

**Interfaces:**
- Consumes: `MovementInfo.is_primary`, `GroupType.GIANT_SET`, `GroupType.STRAIGHT`
- Produces: `_check_primary_not_first(session: Session, ctx: ValidationContext) -> List[Violation]`

**Rule recap:** All primaries must come before all non-primaries in the flat exercise sequence (groups by `order_index`, exercises by `order_index` within), AND primaries cannot appear inside a `GIANT_SET`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_validator.py`:

```python
# ---------------------------------------------------------------------------
# Task 2 — PRIMARY_NOT_FIRST
# ---------------------------------------------------------------------------

def test_primary_in_giant_set_rejects():
    ctx = ValidationContext(movements={
        1: make_movement(1, is_primary=True),
        2: make_movement(2, is_primary=False),
    })
    session = make_session([
        make_group(0, GroupType.GIANT_SET, rounds=3, exercises=[
            make_exercise(1, 0, [make_set(0)]),
            make_exercise(2, 1, [make_set(0)]),
        ]),
    ])
    result = validate(session, ctx)
    rejects = [v for v in result.rejects if v.rule == RuleCode.PRIMARY_NOT_FIRST]
    assert len(rejects) == 1
    assert rejects[0].movement_id == 1
    assert "GIANT_SET" in rejects[0].message
    assert rejects[0].corrected_value is None


def test_primary_after_non_primary_rejects():
    ctx = ValidationContext(movements={
        1: make_movement(1, is_primary=False),
        2: make_movement(2, is_primary=True),
    })
    session = make_session([
        make_group(0, GroupType.STRAIGHT, exercises=[make_exercise(1, 0, [make_set(0)])]),
        make_group(1, GroupType.STRAIGHT, exercises=[make_exercise(2, 0, [make_set(0)])]),
    ])
    result = validate(session, ctx)
    rejects = [v for v in result.rejects if v.rule == RuleCode.PRIMARY_NOT_FIRST]
    assert len(rejects) == 1
    assert rejects[0].movement_id == 2
    assert "after non-primary" in rejects[0].message


def test_primary_first_then_accessory_passes():
    ctx = ValidationContext(movements={
        1: make_movement(1, is_primary=True),
        2: make_movement(2, is_primary=False),
        3: make_movement(3, is_primary=False),
    })
    session = make_session([
        make_group(0, GroupType.STRAIGHT, exercises=[make_exercise(1, 0, [make_set(0)])]),
        make_group(1, GroupType.GIANT_SET, rounds=3, exercises=[
            make_exercise(2, 0, [make_set(0)]),
            make_exercise(3, 1, [make_set(0)]),
        ]),
    ])
    result = validate(session, ctx)
    assert [v for v in result.rejects if v.rule == RuleCode.PRIMARY_NOT_FIRST] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_validator.py::test_primary_in_giant_set_rejects tests/test_validator.py::test_primary_after_non_primary_rejects 2>&1 | tail -10'`
Expected: 2 failures — the empty `validate()` returns no violations, so the assertions on `len(rejects) == 1` fail. (The 3rd test, `test_primary_first_then_accessory_passes`, will already pass against the empty stub.)

- [ ] **Step 3: Implement `_check_primary_not_first`**

In `ironlog/engine/validator.py`, add this function above `validate()`:

```python
def _check_primary_not_first(session: Session, ctx: ValidationContext) -> List[Violation]:
    """Strong form: all primary movements come before all non-primary movements
    in the flat exercise sequence, AND primaries cannot appear inside GIANT_SET."""
    violations: List[Violation] = []
    non_primary_seen = False
    for group in sorted(session.groups, key=lambda g: g.order_index):
        for ex in sorted(group.exercises, key=lambda e: e.order_index):
            info = ctx.movements.get(ex.movement_id)
            if info is None:
                continue
            if info.is_primary:
                if group.group_type == GroupType.GIANT_SET:
                    violations.append(Violation(
                        kind=ViolationKind.REJECT,
                        rule=RuleCode.PRIMARY_NOT_FIRST,
                        message="primary movement inside GIANT_SET group",
                        group_index=group.order_index,
                        movement_id=ex.movement_id,
                    ))
                elif non_primary_seen:
                    violations.append(Violation(
                        kind=ViolationKind.REJECT,
                        rule=RuleCode.PRIMARY_NOT_FIRST,
                        message="primary movement after non-primary movement (primaries must all come first)",
                        group_index=group.order_index,
                        movement_id=ex.movement_id,
                    ))
            else:
                non_primary_seen = True
    return violations
```

Update `validate()` to call it:

```python
def validate(session: Session, ctx: ValidationContext) -> ValidationResult:
    violations: List[Violation] = []
    violations.extend(_check_primary_not_first(session, ctx))
    return ValidationResult(violations=violations)
```

- [ ] **Step 4: Run all 8 validator tests**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_validator.py 2>&1 | tail -5'`
Expected: `8 passed`. Full suite: 26 passed.

- [ ] **Step 5: Commit**

```bash
git add ironlog/engine/validator.py tests/test_validator.py
git commit -m "feat(validator): PRIMARY_NOT_FIRST — strong form, no primary in GIANT_SET

Walks the flat exercise sequence tracking non_primary_seen. Two failure
modes captured: primary inside a GIANT_SET, primary appearing after any
non-primary movement. 3 tests cover both rejects and the happy path.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Group-shape & manifest REJECTs

**Files:**
- Modify: `ironlog/engine/validator.py` — add 4 check functions + 4 calls
- Modify: `tests/test_validator.py` — append cases

**Interfaces:**
- Produces:
  - `_check_giant_set_rounds(session, ctx) -> List[Violation]`
  - `_check_giant_set_concurrency(session, ctx) -> List[Violation]`
  - `_check_single_kb(session, ctx) -> List[Violation]`
  - `_check_equipment_not_in_manifest(session, ctx) -> List[Violation]`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_validator.py`:

```python
# ---------------------------------------------------------------------------
# Task 3 — group-shape & manifest REJECTs
# ---------------------------------------------------------------------------

def test_giant_set_rounds_must_be_3():
    ctx = ValidationContext(movements={
        1: make_movement(1),
        2: make_movement(2),
    })
    session = make_session([
        make_group(0, GroupType.GIANT_SET, rounds=2, exercises=[
            make_exercise(1, 0, [make_set(0)]),
            make_exercise(2, 1, [make_set(0)]),
        ]),
    ])
    rejects = [v for v in validate(session, ctx).rejects if v.rule == RuleCode.GIANT_SET_ROUNDS]
    assert len(rejects) == 1
    assert "rounds=2" in rejects[0].message
    assert rejects[0].group_index == 0


def test_giant_set_rounds_3_passes():
    ctx = ValidationContext(movements={1: make_movement(1)})
    session = make_session([
        make_group(0, GroupType.GIANT_SET, rounds=3, exercises=[make_exercise(1, 0, [make_set(0)])]),
    ])
    assert [v for v in validate(session, ctx).rejects if v.rule == RuleCode.GIANT_SET_ROUNDS] == []


def test_giant_set_concurrency_rejects_4_exercises():
    ctx = ValidationContext(movements={i: make_movement(i) for i in range(1, 5)})
    session = make_session([
        make_group(0, GroupType.GIANT_SET, rounds=3, exercises=[
            make_exercise(i, idx, [make_set(0)]) for idx, i in enumerate(range(1, 5))
        ]),
    ])
    rejects = [v for v in validate(session, ctx).rejects if v.rule == RuleCode.GIANT_SET_CONCURRENCY]
    assert len(rejects) == 1
    assert "4 exercises" in rejects[0].message


def test_giant_set_concurrency_rejects_empty():
    ctx = ValidationContext()
    session = make_session([
        make_group(0, GroupType.GIANT_SET, rounds=3, exercises=[]),
    ])
    rejects = [v for v in validate(session, ctx).rejects if v.rule == RuleCode.GIANT_SET_CONCURRENCY]
    assert len(rejects) == 1


def test_giant_set_concurrency_1_to_3_passes():
    ctx = ValidationContext(movements={i: make_movement(i) for i in range(1, 4)})
    session = make_session([
        make_group(0, GroupType.GIANT_SET, rounds=3, exercises=[
            make_exercise(i, idx, [make_set(0)]) for idx, i in enumerate(range(1, 4))
        ]),
    ])
    assert [v for v in validate(session, ctx).rejects if v.rule == RuleCode.GIANT_SET_CONCURRENCY] == []


def test_single_kb_rejects_2_kettlebells_in_giant_set():
    KB_EQUIP_ID = 12
    ctx = ValidationContext(
        kettlebell_equipment_id=KB_EQUIP_ID,
        movements={
            1: make_movement(1, load_equipment_id=KB_EQUIP_ID),
            2: make_movement(2, load_equipment_id=KB_EQUIP_ID),
        },
    )
    session = make_session([
        make_group(0, GroupType.GIANT_SET, rounds=3, exercises=[
            make_exercise(1, 0, [make_set(0)]),
            make_exercise(2, 1, [make_set(0)]),
        ]),
    ])
    rejects = [v for v in validate(session, ctx).rejects if v.rule == RuleCode.SINGLE_KB]
    assert len(rejects) == 1
    assert "2 kettlebell" in rejects[0].message
    assert rejects[0].group_index == 0


def test_single_kb_one_kb_in_giant_set_passes():
    KB_EQUIP_ID = 12
    ctx = ValidationContext(
        kettlebell_equipment_id=KB_EQUIP_ID,
        movements={
            1: make_movement(1, load_equipment_id=KB_EQUIP_ID),
            2: make_movement(2, load_equipment_id=1),  # not KB
        },
    )
    session = make_session([
        make_group(0, GroupType.GIANT_SET, rounds=3, exercises=[
            make_exercise(1, 0, [make_set(0)]),
            make_exercise(2, 1, [make_set(0)]),
        ]),
    ])
    assert [v for v in validate(session, ctx).rejects if v.rule == RuleCode.SINGLE_KB] == []


def test_single_kb_skipped_when_no_kb_equipment_id():
    """If ctx.kettlebell_equipment_id is None, the rule is a no-op even with
    multiple exercises sharing the same load_equipment_id."""
    ctx = ValidationContext(
        kettlebell_equipment_id=None,
        movements={
            1: make_movement(1, load_equipment_id=12),
            2: make_movement(2, load_equipment_id=12),
        },
    )
    session = make_session([
        make_group(0, GroupType.GIANT_SET, rounds=3, exercises=[
            make_exercise(1, 0, [make_set(0)]),
            make_exercise(2, 1, [make_set(0)]),
        ]),
    ])
    assert [v for v in validate(session, ctx).rejects if v.rule == RuleCode.SINGLE_KB] == []


def test_equipment_not_in_manifest_rejects():
    ctx = ValidationContext(
        movements={1: make_movement(1, load_equipment_id=99)},
        manifest_equipment_ids={1, 2, 3},
    )
    session = make_session([
        make_group(0, GroupType.STRAIGHT, exercises=[make_exercise(1, 0, [make_set(0)])]),
    ])
    rejects = [v for v in validate(session, ctx).rejects if v.rule == RuleCode.EQUIPMENT_NOT_IN_MANIFEST]
    assert len(rejects) == 1
    assert rejects[0].movement_id == 1
    assert "99" in rejects[0].message


def test_equipment_in_manifest_passes():
    ctx = ValidationContext(
        movements={1: make_movement(1, load_equipment_id=2)},
        manifest_equipment_ids={1, 2, 3},
    )
    session = make_session([
        make_group(0, GroupType.STRAIGHT, exercises=[make_exercise(1, 0, [make_set(0)])]),
    ])
    assert [v for v in validate(session, ctx).rejects if v.rule == RuleCode.EQUIPMENT_NOT_IN_MANIFEST] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_validator.py -k "rounds or concurrency or single_kb or manifest" 2>&1 | tail -10'`
Expected: 5 failures (the 5 *_rejects cases); the 5 happy-path tests pass against the stub.

- [ ] **Step 3: Implement the 4 check functions**

Add to `ironlog/engine/validator.py` above `validate()`:

```python
def _check_giant_set_rounds(session: Session, ctx: ValidationContext) -> List[Violation]:
    """GIANT_SET groups must have rounds == 3."""
    violations: List[Violation] = []
    for group in session.groups:
        if group.group_type == GroupType.GIANT_SET and group.rounds != 3:
            violations.append(Violation(
                kind=ViolationKind.REJECT,
                rule=RuleCode.GIANT_SET_ROUNDS,
                message=f"GIANT_SET rounds={group.rounds}, expected 3",
                group_index=group.order_index,
            ))
    return violations


def _check_giant_set_concurrency(session: Session, ctx: ValidationContext) -> List[Violation]:
    """GIANT_SET groups must have 1..=3 exercises (room geometry)."""
    violations: List[Violation] = []
    for group in session.groups:
        if group.group_type == GroupType.GIANT_SET:
            n = len(group.exercises)
            if not 1 <= n <= 3:
                violations.append(Violation(
                    kind=ViolationKind.REJECT,
                    rule=RuleCode.GIANT_SET_CONCURRENCY,
                    message=f"GIANT_SET has {n} exercises, expected 1-3 (room geometry)",
                    group_index=group.order_index,
                ))
    return violations


def _check_single_kb(session: Session, ctx: ValidationContext) -> List[Violation]:
    """At most one kettlebell-loaded exercise per GIANT_SET. Skipped if
    ctx.kettlebell_equipment_id is None (no KB equipment registered)."""
    if ctx.kettlebell_equipment_id is None:
        return []
    violations: List[Violation] = []
    for group in session.groups:
        if group.group_type != GroupType.GIANT_SET:
            continue
        count = 0
        for ex in group.exercises:
            info = ctx.movements.get(ex.movement_id)
            if info and info.load_equipment_id == ctx.kettlebell_equipment_id:
                count += 1
        if count >= 2:
            violations.append(Violation(
                kind=ViolationKind.REJECT,
                rule=RuleCode.SINGLE_KB,
                message=f"GIANT_SET has {count} kettlebell movements; only 1 KB station available",
                group_index=group.order_index,
            ))
    return violations


def _check_equipment_not_in_manifest(session: Session, ctx: ValidationContext) -> List[Violation]:
    """Every loaded movement's equipment must be in the active-phase manifest."""
    violations: List[Violation] = []
    for group in session.groups:
        for ex in group.exercises:
            info = ctx.movements.get(ex.movement_id)
            if info is None or info.load_equipment_id is None:
                continue
            if info.load_equipment_id not in ctx.manifest_equipment_ids:
                violations.append(Violation(
                    kind=ViolationKind.REJECT,
                    rule=RuleCode.EQUIPMENT_NOT_IN_MANIFEST,
                    message=f"Equipment id {info.load_equipment_id} not in active manifest",
                    group_index=group.order_index,
                    movement_id=ex.movement_id,
                ))
    return violations
```

Update `validate()`:

```python
def validate(session: Session, ctx: ValidationContext) -> ValidationResult:
    violations: List[Violation] = []
    violations.extend(_check_primary_not_first(session, ctx))
    violations.extend(_check_giant_set_rounds(session, ctx))
    violations.extend(_check_giant_set_concurrency(session, ctx))
    violations.extend(_check_single_kb(session, ctx))
    violations.extend(_check_equipment_not_in_manifest(session, ctx))
    return ValidationResult(violations=violations)
```

- [ ] **Step 4: Run all validator tests**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_validator.py 2>&1 | tail -5'`
Expected: `18 passed`. Full suite: 36 passed.

- [ ] **Step 5: Commit**

```bash
git add ironlog/engine/validator.py tests/test_validator.py
git commit -m "feat(validator): group-shape and manifest REJECTs

GIANT_SET_ROUNDS (must equal 3), GIANT_SET_CONCURRENCY (1-3 exercises;
empty rejects too), SINGLE_KB (derived from load_equipment_id == 
ctx.kettlebell_equipment_id; skipped if no KB equipment registered),
EQUIPMENT_NOT_IN_MANIFEST. 10 tests cover happy + sad paths for each.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: HT safety pair

**Files:**
- Modify: `ironlog/engine/validator.py` — add `_check_ht_safety()` + call
- Modify: `tests/test_validator.py` — append cases

**Interfaces:**
- Produces: `_check_ht_safety(session, ctx) -> List[Violation]` — emits both `HT_BOTTOM_OVER_LIMIT` and `HT_BAND_NOT_REGISTERED` violations as appropriate

**Why one function for two rules:** they share the same traversal (HT sets with both `target_plates` and `band_pair_id`) and one fires when the other can't compute. Splitting them across two functions would double-walk the same sets and risk drift.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_validator.py`:

```python
# ---------------------------------------------------------------------------
# Task 4 — HT safety pair (HT_BOTTOM_OVER_LIMIT + HT_BAND_NOT_REGISTERED)
# ---------------------------------------------------------------------------

def test_ht_bottom_over_limit_rejects():
    ctx = ValidationContext(
        movements={1: make_movement(1, lift_category=LiftCategory.HIP_THRUST)},
        band_bottom_lb={1: 14.0},  # #0 Orange
        ht_bottom_clamp=220.0,
    )
    session = make_session([
        make_group(0, GroupType.STRAIGHT, exercises=[
            make_exercise(1, 0, [make_set(0, target_plates=220.0, band_pair_id=1)]),
            # 220 + 14 = 234 bottom > 220 clamp
        ]),
    ])
    rejects = [v for v in validate(session, ctx).rejects if v.rule == RuleCode.HT_BOTTOM_OVER_LIMIT]
    assert len(rejects) == 1
    assert rejects[0].set_index == 0
    assert rejects[0].movement_id == 1
    assert "234" in rejects[0].message
    assert "220" in rejects[0].message
    assert "plates+band at bottom position" in rejects[0].message
    assert rejects[0].corrected_value is None


def test_ht_bottom_under_limit_passes():
    ctx = ValidationContext(
        movements={1: make_movement(1, lift_category=LiftCategory.HIP_THRUST)},
        band_bottom_lb={1: 14.0},
    )
    session = make_session([
        make_group(0, GroupType.STRAIGHT, exercises=[
            make_exercise(1, 0, [make_set(0, target_plates=180.0, band_pair_id=1)]),
        ]),
    ])
    assert [v for v in validate(session, ctx).rejects if v.rule == RuleCode.HT_BOTTOM_OVER_LIMIT] == []


def test_ht_composite_progression_also_triggers():
    """Lift_category fallback: if not HIP_THRUST, progression_mode==COMPOSITE
    still triggers HT-safety checks."""
    ctx = ValidationContext(
        movements={1: make_movement(1, progression_mode=ProgressionMode.COMPOSITE)},
        band_bottom_lb={1: 14.0},
    )
    session = make_session([
        make_group(0, GroupType.STRAIGHT, exercises=[
            make_exercise(1, 0, [make_set(0, target_plates=220.0, band_pair_id=1)]),
        ]),
    ])
    rejects = [v for v in validate(session, ctx).rejects if v.rule == RuleCode.HT_BOTTOM_OVER_LIMIT]
    assert len(rejects) == 1


def test_ht_unregistered_band_rejects():
    """Fail loud rather than substituting 0 — bottom safety check cannot be
    silently bypassed."""
    ctx = ValidationContext(
        movements={1: make_movement(1, lift_category=LiftCategory.HIP_THRUST)},
        band_bottom_lb={1: 14.0},  # only band 1 registered
    )
    session = make_session([
        make_group(0, GroupType.STRAIGHT, exercises=[
            make_exercise(1, 0, [make_set(0, target_plates=100.0, band_pair_id=99)]),
        ]),
    ])
    rejects = [v for v in validate(session, ctx).rejects if v.rule == RuleCode.HT_BAND_NOT_REGISTERED]
    assert len(rejects) == 1
    assert "99" in rejects[0].message
    assert rejects[0].set_index == 0
    # And the bottom-over-limit check must NOT have run with a silent 0:
    assert [v for v in validate(session, ctx).rejects if v.rule == RuleCode.HT_BOTTOM_OVER_LIMIT] == []


def test_ht_incomplete_prescription_skipped():
    """If either target_plates or band_pair_id is None, both HT rules skip."""
    ctx = ValidationContext(
        movements={1: make_movement(1, lift_category=LiftCategory.HIP_THRUST)},
        band_bottom_lb={1: 14.0},
    )
    session = make_session([
        make_group(0, GroupType.STRAIGHT, exercises=[
            make_exercise(1, 0, [
                make_set(0, target_plates=220.0, band_pair_id=None),  # missing band
                make_set(1, target_plates=None, band_pair_id=1),       # missing plates
            ]),
        ]),
    ])
    result = validate(session, ctx)
    assert [v for v in result.rejects if v.rule in (RuleCode.HT_BOTTOM_OVER_LIMIT, RuleCode.HT_BAND_NOT_REGISTERED)] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_validator.py -k "ht_" 2>&1 | tail -10'`
Expected: 3 failures (over-limit, composite-triggers, unregistered-band); 2 pass against stub (under-limit, incomplete).

- [ ] **Step 3: Implement `_check_ht_safety`**

Add to `ironlog/engine/validator.py` above `validate()`:

```python
def _check_ht_safety(session: Session, ctx: ValidationContext) -> List[Violation]:
    """HT bottom-clamp safety (REJECT, hardware-safety per docs/01 §4.1).
    Emits HT_BOTTOM_OVER_LIMIT when bottom_total exceeds ctx.ht_bottom_clamp.
    Emits HT_BAND_NOT_REGISTERED when the prescribed band_pair_id is not in
    ctx.band_bottom_lb — fail loud rather than substitute 0."""
    violations: List[Violation] = []
    for group in session.groups:
        for ex in group.exercises:
            info = ctx.movements.get(ex.movement_id)
            if info is None:
                continue
            is_ht = (info.lift_category == LiftCategory.HIP_THRUST
                     or info.progression_mode == ProgressionMode.COMPOSITE)
            if not is_ht:
                continue
            for ps in ex.planned_sets:
                if ps.target_plates is None or ps.band_pair_id is None:
                    continue  # incomplete prescription; can't evaluate
                if ps.band_pair_id not in ctx.band_bottom_lb:
                    violations.append(Violation(
                        kind=ViolationKind.REJECT,
                        rule=RuleCode.HT_BAND_NOT_REGISTERED,
                        message=(f"HT band_pair_id {ps.band_pair_id} not registered in "
                                 f"ctx.band_bottom_lb — cannot evaluate bottom-clamp safety"),
                        group_index=group.order_index,
                        movement_id=ex.movement_id,
                        set_index=ps.set_index,
                    ))
                    continue  # do NOT compute bottom_total from a missing entry
                bottom_total = ps.target_plates + ctx.band_bottom_lb[ps.band_pair_id]
                if bottom_total > ctx.ht_bottom_clamp:
                    violations.append(Violation(
                        kind=ViolationKind.REJECT,
                        rule=RuleCode.HT_BOTTOM_OVER_LIMIT,
                        message=(f"HT bottom total {bottom_total:.1f} lb exceeds clamp "
                                 f"{ctx.ht_bottom_clamp:.1f} lb (plates+band at bottom position)"),
                        group_index=group.order_index,
                        movement_id=ex.movement_id,
                        set_index=ps.set_index,
                    ))
    return violations
```

Update `validate()` to call it:

```python
def validate(session: Session, ctx: ValidationContext) -> ValidationResult:
    violations: List[Violation] = []
    violations.extend(_check_primary_not_first(session, ctx))
    violations.extend(_check_giant_set_rounds(session, ctx))
    violations.extend(_check_giant_set_concurrency(session, ctx))
    violations.extend(_check_single_kb(session, ctx))
    violations.extend(_check_equipment_not_in_manifest(session, ctx))
    violations.extend(_check_ht_safety(session, ctx))
    return ValidationResult(violations=violations)
```

- [ ] **Step 4: Run all validator tests**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_validator.py 2>&1 | tail -5'`
Expected: `23 passed`. Full suite: 41 passed.

- [ ] **Step 5: Commit**

```bash
git add ironlog/engine/validator.py tests/test_validator.py
git commit -m "feat(validator): HT safety pair — bottom over limit + band not registered

HT_BOTTOM_OVER_LIMIT and HT_BAND_NOT_REGISTERED share one walk:
plates+band exceeds ctx.ht_bottom_clamp -> bottom-over; band_pair_id
absent from ctx.band_bottom_lb -> not-registered (fail loud, NEVER
silently treat as 0). HT triggers on lift_category==HIP_THRUST OR
progression_mode==COMPOSITE. Incomplete prescription (missing plates
or band) is skipped without violation. 5 tests.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: CLAMP triad

**Files:**
- Modify: `ironlog/engine/validator.py` — add 3 check functions + 3 calls
- Modify: `tests/test_validator.py` — append cases

**Interfaces:**
- Produces:
  - `_check_load_below_floor(session, ctx) -> List[Violation]`
  - `_check_load_over_cap(session, ctx) -> List[Violation]`
  - `_check_rpe_over_cap(session, ctx) -> List[Violation]`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_validator.py`:

```python
# ---------------------------------------------------------------------------
# Task 5 — CLAMP triad
# ---------------------------------------------------------------------------

def test_load_below_floor_clamps():
    ctx = ValidationContext(movements={1: make_movement(1, load_floor=45.0)})
    session = make_session([
        make_group(0, GroupType.STRAIGHT, exercises=[
            make_exercise(1, 0, [make_set(0, target_load=35.0)]),
        ]),
    ])
    clamps = [v for v in validate(session, ctx).clamps if v.rule == RuleCode.LOAD_BELOW_FLOOR]
    assert len(clamps) == 1
    assert clamps[0].corrected_value == 45.0
    assert clamps[0].set_index == 0
    assert "35" in clamps[0].message and "45" in clamps[0].message


def test_load_at_floor_does_not_clamp():
    ctx = ValidationContext(movements={1: make_movement(1, load_floor=45.0)})
    session = make_session([
        make_group(0, GroupType.STRAIGHT, exercises=[
            make_exercise(1, 0, [make_set(0, target_load=45.0)]),
        ]),
    ])
    assert [v for v in validate(session, ctx).clamps if v.rule == RuleCode.LOAD_BELOW_FLOOR] == []


def test_load_over_cap_clamps():
    ctx = ValidationContext(movements={1: make_movement(1, cap=25.0)})
    session = make_session([
        make_group(0, GroupType.STRAIGHT, exercises=[
            make_exercise(1, 0, [make_set(0, target_load=30.0)]),
        ]),
    ])
    clamps = [v for v in validate(session, ctx).clamps if v.rule == RuleCode.LOAD_OVER_CAP]
    assert len(clamps) == 1
    assert clamps[0].corrected_value == 25.0


def test_rpe_over_cap_clamps():
    ctx = ValidationContext(
        movements={1: make_movement(1)},
        phase_hard_cap=8.0,
    )
    session = make_session([
        make_group(0, GroupType.STRAIGHT, exercises=[
            make_exercise(1, 0, [make_set(0, target_rpe=9.0)]),
        ]),
    ])
    clamps = [v for v in validate(session, ctx).clamps if v.rule == RuleCode.RPE_OVER_CAP]
    assert len(clamps) == 1
    assert clamps[0].corrected_value == 8.0


def test_rpe_cap_exempt_movement_does_not_clamp():
    """HT and similar always-progress movements are exempt from the RPE cap."""
    ctx = ValidationContext(
        movements={1: make_movement(1, rpe_cap_exempt=True)},
        phase_hard_cap=8.0,
    )
    session = make_session([
        make_group(0, GroupType.STRAIGHT, exercises=[
            make_exercise(1, 0, [make_set(0, target_rpe=9.5)]),
        ]),
    ])
    assert [v for v in validate(session, ctx).clamps if v.rule == RuleCode.RPE_OVER_CAP] == []


def test_no_target_load_skips_load_clamps():
    ctx = ValidationContext(movements={1: make_movement(1, load_floor=45.0, cap=25.0)})
    session = make_session([
        make_group(0, GroupType.STRAIGHT, exercises=[
            make_exercise(1, 0, [make_set(0, target_load=None)]),
        ]),
    ])
    assert validate(session, ctx).clamps == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_validator.py -k "clamp or floor or cap or rpe" 2>&1 | tail -10'`
Expected: 3 failures (the 3 *_clamps cases); the 3 negative cases pass against stub.

- [ ] **Step 3: Implement the 3 clamp checks**

Add to `ironlog/engine/validator.py` above `validate()`:

```python
def _check_load_below_floor(session: Session, ctx: ValidationContext) -> List[Violation]:
    """target_load < movement.load_floor -> CLAMP up to load_floor."""
    violations: List[Violation] = []
    for group in session.groups:
        for ex in group.exercises:
            info = ctx.movements.get(ex.movement_id)
            if info is None or info.load_floor is None:
                continue
            for ps in ex.planned_sets:
                if ps.target_load is not None and ps.target_load < info.load_floor:
                    violations.append(Violation(
                        kind=ViolationKind.CLAMP,
                        rule=RuleCode.LOAD_BELOW_FLOOR,
                        message=f"Load {ps.target_load} below floor {info.load_floor}",
                        group_index=group.order_index,
                        movement_id=ex.movement_id,
                        set_index=ps.set_index,
                        corrected_value=info.load_floor,
                    ))
    return violations


def _check_load_over_cap(session: Session, ctx: ValidationContext) -> List[Violation]:
    """target_load > movement.cap -> CLAMP down to cap."""
    violations: List[Violation] = []
    for group in session.groups:
        for ex in group.exercises:
            info = ctx.movements.get(ex.movement_id)
            if info is None or info.cap is None:
                continue
            for ps in ex.planned_sets:
                if ps.target_load is not None and ps.target_load > info.cap:
                    violations.append(Violation(
                        kind=ViolationKind.CLAMP,
                        rule=RuleCode.LOAD_OVER_CAP,
                        message=f"Load {ps.target_load} over cap {info.cap}",
                        group_index=group.order_index,
                        movement_id=ex.movement_id,
                        set_index=ps.set_index,
                        corrected_value=info.cap,
                    ))
    return violations


def _check_rpe_over_cap(session: Session, ctx: ValidationContext) -> List[Violation]:
    """target_rpe > ctx.phase_hard_cap (and not rpe_cap_exempt) -> CLAMP down."""
    violations: List[Violation] = []
    for group in session.groups:
        for ex in group.exercises:
            info = ctx.movements.get(ex.movement_id)
            if info is None or info.rpe_cap_exempt:
                continue
            for ps in ex.planned_sets:
                if ps.target_rpe is not None and ps.target_rpe > ctx.phase_hard_cap:
                    violations.append(Violation(
                        kind=ViolationKind.CLAMP,
                        rule=RuleCode.RPE_OVER_CAP,
                        message=f"RPE {ps.target_rpe} over phase cap {ctx.phase_hard_cap}",
                        group_index=group.order_index,
                        movement_id=ex.movement_id,
                        set_index=ps.set_index,
                        corrected_value=ctx.phase_hard_cap,
                    ))
    return violations
```

Update `validate()`:

```python
def validate(session: Session, ctx: ValidationContext) -> ValidationResult:
    violations: List[Violation] = []
    violations.extend(_check_primary_not_first(session, ctx))
    violations.extend(_check_giant_set_rounds(session, ctx))
    violations.extend(_check_giant_set_concurrency(session, ctx))
    violations.extend(_check_single_kb(session, ctx))
    violations.extend(_check_equipment_not_in_manifest(session, ctx))
    violations.extend(_check_ht_safety(session, ctx))
    violations.extend(_check_load_below_floor(session, ctx))
    violations.extend(_check_load_over_cap(session, ctx))
    violations.extend(_check_rpe_over_cap(session, ctx))
    return ValidationResult(violations=violations)
```

- [ ] **Step 4: Run all validator tests**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_validator.py 2>&1 | tail -5'`
Expected: `29 passed`. Full suite: 47 passed.

- [ ] **Step 5: Commit**

```bash
git add ironlog/engine/validator.py tests/test_validator.py
git commit -m "feat(validator): CLAMP triad — LOAD_BELOW_FLOOR, LOAD_OVER_CAP, RPE_OVER_CAP

target_load below movement.load_floor clamps up; over movement.cap
clamps down; target_rpe over ctx.phase_hard_cap clamps to cap unless
rpe_cap_exempt (HT). corrected_value populated on each. 6 tests.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Cross-session REJECTs (deferred-binding tallies)

**Files:**
- Modify: `ironlog/engine/validator.py` — add 2 check functions + 2 conditional calls
- Modify: `tests/test_validator.py` — append cases

**Interfaces:**
- Produces:
  - `_check_knee_frequency(ctx) -> List[Violation]` — takes only ctx; session is not consulted
  - `_check_pull_push_ratio(ctx) -> List[Violation]` — same

**Note:** these cross-session rules read from `ctx.tallies`, NOT from the session itself. The session is consulted only by within-session rules. When `ctx.tallies is None`, both functions return `[]` (cross-session checks skipped entirely).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_validator.py`:

```python
# ---------------------------------------------------------------------------
# Task 6 — cross-session REJECTs (deferred-binding tallies)
# ---------------------------------------------------------------------------

def test_knee_frequency_unmet_rejects():
    ctx = ValidationContext(tallies=WeeklyTallies(
        knee_counts={"NORDIC": 1, "TIB": 1, "KOT": 2, "SISSY": 0},
        knee_targets={"NORDIC": 2, "TIB": 2, "KOT": 2, "SISSY": 1},
    ))
    rejects = [v for v in validate(make_session(), ctx).rejects if v.rule == RuleCode.KNEE_FREQUENCY]
    # NORDIC owed 1, TIB owed 1, SISSY owed 1 → 3 rejects; KOT is met
    assert len(rejects) == 3
    msgs = " | ".join(v.message for v in rejects)
    assert "NORDIC" in msgs
    assert "TIB" in msgs
    assert "SISSY" in msgs
    assert "KOT" not in msgs


def test_knee_frequency_met_passes():
    ctx = ValidationContext(tallies=WeeklyTallies(
        knee_counts={"NORDIC": 2, "TIB": 2},
        knee_targets={"NORDIC": 2, "TIB": 2},
    ))
    assert [v for v in validate(make_session(), ctx).rejects if v.rule == RuleCode.KNEE_FREQUENCY] == []


def test_pull_push_ratio_below_target_rejects():
    ctx = ValidationContext(tallies=WeeklyTallies(
        pull_volume=1300.0, push_volume=1000.0, pull_push_target=2.0,
    ))
    rejects = [v for v in validate(make_session(), ctx).rejects if v.rule == RuleCode.PULL_PUSH_RATIO]
    assert len(rejects) == 1
    assert "1.30" in rejects[0].message
    assert "2" in rejects[0].message


def test_pull_push_ratio_at_target_passes():
    ctx = ValidationContext(tallies=WeeklyTallies(
        pull_volume=2000.0, push_volume=1000.0, pull_push_target=2.0,
    ))
    assert [v for v in validate(make_session(), ctx).rejects if v.rule == RuleCode.PULL_PUSH_RATIO] == []


def test_pull_push_zero_push_skipped():
    """push_volume == 0 → skip (avoid div-by-zero)."""
    ctx = ValidationContext(tallies=WeeklyTallies(
        pull_volume=500.0, push_volume=0.0, pull_push_target=2.0,
    ))
    assert [v for v in validate(make_session(), ctx).rejects if v.rule == RuleCode.PULL_PUSH_RATIO] == []


def test_tallies_none_skips_cross_session_entirely():
    """ctx.tallies is None → KNEE_FREQUENCY and PULL_PUSH_RATIO emit nothing
    regardless of session content."""
    ctx = ValidationContext(tallies=None)
    result = validate(make_session(), ctx)
    cross_session = [v for v in result.violations
                     if v.rule in (RuleCode.KNEE_FREQUENCY, RuleCode.PULL_PUSH_RATIO)]
    assert cross_session == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_validator.py -k "knee or pull_push or tallies" 2>&1 | tail -10'`
Expected: 2 failures (knee-unmet, ratio-below); 4 pass against stub.

- [ ] **Step 3: Implement the cross-session checks**

Add to `ironlog/engine/validator.py` above `validate()`:

```python
def _check_knee_frequency(ctx: ValidationContext) -> List[Violation]:
    """Each knee modality must hit its weekly target. Skipped if ctx.tallies is None."""
    if ctx.tallies is None:
        return []
    violations: List[Violation] = []
    for modality, target in ctx.tallies.knee_targets.items():
        count = ctx.tallies.knee_counts.get(modality, 0)
        if count < target:
            violations.append(Violation(
                kind=ViolationKind.REJECT,
                rule=RuleCode.KNEE_FREQUENCY,
                message=f"{modality} frequency unmet: {count}/{target} (owed {target - count})",
            ))
    return violations


def _check_pull_push_ratio(ctx: ValidationContext) -> List[Violation]:
    """Pull:push volume ratio must meet target. Skipped if push_volume == 0
    (avoid div-by-zero) or if ctx.tallies is None."""
    if ctx.tallies is None or ctx.tallies.push_volume == 0:
        return []
    ratio = ctx.tallies.pull_volume / ctx.tallies.push_volume
    if ratio < ctx.tallies.pull_push_target:
        return [Violation(
            kind=ViolationKind.REJECT,
            rule=RuleCode.PULL_PUSH_RATIO,
            message=f"Pull:push ratio {ratio:.2f} below target {ctx.tallies.pull_push_target:.1f}",
        )]
    return []
```

Update `validate()`:

```python
def validate(session: Session, ctx: ValidationContext) -> ValidationResult:
    violations: List[Violation] = []
    violations.extend(_check_primary_not_first(session, ctx))
    violations.extend(_check_giant_set_rounds(session, ctx))
    violations.extend(_check_giant_set_concurrency(session, ctx))
    violations.extend(_check_single_kb(session, ctx))
    violations.extend(_check_equipment_not_in_manifest(session, ctx))
    violations.extend(_check_ht_safety(session, ctx))
    violations.extend(_check_load_below_floor(session, ctx))
    violations.extend(_check_load_over_cap(session, ctx))
    violations.extend(_check_rpe_over_cap(session, ctx))
    violations.extend(_check_knee_frequency(ctx))
    violations.extend(_check_pull_push_ratio(ctx))
    return ValidationResult(violations=violations)
```

- [ ] **Step 4: Run all validator tests**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_validator.py 2>&1 | tail -5'`
Expected: `35 passed`. Full suite: 53 passed.

- [ ] **Step 5: Commit**

```bash
git add ironlog/engine/validator.py tests/test_validator.py
git commit -m "feat(validator): cross-session REJECTs — knee frequency + pull:push ratio

Both read from ctx.tallies (WeeklyTallies); both no-op if ctx.tallies
is None (deferred-binding for the WeeklyLedger ship in v0.3).
PULL_PUSH_RATIO also skipped if push_volume == 0 (div-by-zero guard).
6 tests.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Cross-cutting tests + engine/__init__ exports + final verification

**Files:**
- Modify: `ironlog/engine/__init__.py` — add validator re-exports
- Modify: `tests/test_validator.py` — append cross-cutting tests

**Interfaces:**
- Consumes: every public name from validator.py
- Produces: validator's public names re-exported from `ironlog.engine` (matches the existing pattern for `loading`, `progression`, `e1rm`, `autoregulate`)

- [ ] **Step 1: Write the cross-cutting tests**

Append to `tests/test_validator.py`:

```python
# ---------------------------------------------------------------------------
# Task 7 — cross-cutting tests
# ---------------------------------------------------------------------------

def test_missing_movement_skips_movement_dependent_rules():
    """Exercise references a movement not in ctx.movements. Movement-dependent
    rules silently skip that exercise; group-level rules still fire."""
    ctx = ValidationContext()  # movements is empty
    session = make_session([
        make_group(0, GroupType.GIANT_SET, rounds=2, exercises=[  # GIANT_SET_ROUNDS will fire
            make_exercise(99, 0, [make_set(0, target_load=10.0)]),  # movement 99 unknown
        ]),
    ])
    result = validate(session, ctx)
    # Movement-dependent rules (PRIMARY_NOT_FIRST, EQUIPMENT_*, HT_*, LOAD_*, RPE_*, SINGLE_KB)
    # should NOT fire — they all key off the missing MovementInfo.
    movement_dependent_rules = {
        RuleCode.PRIMARY_NOT_FIRST, RuleCode.SINGLE_KB,
        RuleCode.EQUIPMENT_NOT_IN_MANIFEST,
        RuleCode.HT_BOTTOM_OVER_LIMIT, RuleCode.HT_BAND_NOT_REGISTERED,
        RuleCode.LOAD_BELOW_FLOOR, RuleCode.LOAD_OVER_CAP, RuleCode.RPE_OVER_CAP,
    }
    for v in result.violations:
        assert v.rule not in movement_dependent_rules, f"unexpected: {v}"
    # GIANT_SET_ROUNDS (group-level, no movement lookup) SHOULD fire:
    assert [v for v in result.rejects if v.rule == RuleCode.GIANT_SET_ROUNDS] != []


def test_apply_loop_round_trip():
    """Build a session triggering LOAD_BELOW_FLOOR + RPE_OVER_CAP on the same
    set, apply both clamps via (rule)-dispatch, re-run validate(), expect
    is_structurally_valid==True and zero clamps."""
    ctx = ValidationContext(
        movements={1: make_movement(1, load_floor=45.0)},
        phase_hard_cap=8.0,
    )
    bad_set = make_set(0, target_load=35.0, target_rpe=9.0)
    session = make_session([
        make_group(0, GroupType.STRAIGHT, exercises=[make_exercise(1, 0, [bad_set])]),
    ])
    result = validate(session, ctx)
    assert len(result.clamps) == 2  # below floor + rpe over cap
    assert result.is_structurally_valid is True  # no REJECTs

    # Apply each clamp by (rule) dispatch
    for v in result.clamps:
        target_set = session.groups[v.group_index].exercises[0].planned_sets[v.set_index]
        if v.rule in (RuleCode.LOAD_BELOW_FLOOR, RuleCode.LOAD_OVER_CAP):
            target_set.target_load = v.corrected_value
        elif v.rule == RuleCode.RPE_OVER_CAP:
            target_set.target_rpe = v.corrected_value

    # Re-validate — clean
    result2 = validate(session, ctx)
    assert result2.clamps == []
    assert result2.is_structurally_valid is True


def test_validator_imports_via_engine_package():
    """The public names are re-exported from ironlog.engine (matches the
    pattern for loading, progression, etc.)."""
    from ironlog.engine import (
        validate as eng_validate,
        ValidationContext as eng_ctx,
        ValidationResult as eng_result,
        Violation as eng_violation,
        ViolationKind as eng_kind,
        RuleCode as eng_rule,
        MovementInfo as eng_minfo,
        WeeklyTallies as eng_tallies,
    )
    assert eng_validate is validate
    assert eng_ctx is ValidationContext
    assert eng_result is ValidationResult
    assert eng_violation is Violation
    assert eng_kind is ViolationKind
    assert eng_rule is RuleCode
    assert eng_minfo is MovementInfo
    assert eng_tallies is WeeklyTallies
```

- [ ] **Step 2: Run the new tests to see them fail**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_validator.py::test_validator_imports_via_engine_package 2>&1 | tail -10'`
Expected: 1 failure — `ImportError: cannot import name 'validate' from 'ironlog.engine'`.

(The other two cross-cutting tests — `missing_movement` and `apply_loop` — should already pass at this point against the full validator from Tasks 1-6; if they don't, that's a real bug to fix in those tasks before touching `__init__.py`.)

- [ ] **Step 3: Wire `engine/__init__.py`**

Replace `ironlog/engine/__init__.py` with:

```python
from .e1rm import estimate_e1rm, epley_e1rm, implied_rir           # noqa: F401
from .loading import round_to_achievable, clamp_to_cap, current_increment  # noqa: F401
from .autoregulate import next_set_load                            # noqa: F401
from .progression import (                                          # noqa: F401
    resolve_objective, should_attempt_progression, step_down_tier,
    reset_tier_on_rebuild, maybe_reset_tier_on_breakthrough,
)
from .validator import (                                            # noqa: F401
    MovementInfo, RuleCode, ValidationContext, ValidationResult,
    Violation, ViolationKind, WeeklyTallies, validate,
)
```

- [ ] **Step 4: Run the full test suite (final verification)**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q 2>&1 | tail -5'`
Expected: `56 passed in <1s` (18 baseline + 38 validator tests — count rounds to ~38 with all the cross-cutting cases; the precise number depends on how Task 1-6's tests sum). The exact number doesn't matter — what matters is **zero failures**.

Also run a sanity check that the seed still works (the validator changes don't touch DB or models, but verifying once that the seed runs end-to-end catches surprise breakage):

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && rm -f /tmp/ironlog-seedcheck.db && IRONLOG_DB=/tmp/ironlog-seedcheck.db .venv/bin/python -m ironlog.seed 2>&1 | tail -3'`
Expected: `Seeded ironlog.db` (or no output if the seed log changed). No traceback.

Actually skip the seed check if `IRONLOG_DB` env override isn't supported by `seed.py` — read the file first:
`grep -n "IRONLOG_DB\|ironlog.db" ironlog/db.py ironlog/seed.py | head` — if the seed always writes to `./ironlog.db`, just rely on the pytest run as the green-baseline check.

- [ ] **Step 5: Commit**

```bash
git add ironlog/engine/__init__.py tests/test_validator.py
git commit -m "feat(validator): engine package re-exports + cross-cutting tests

Adds validator's public surface to ironlog.engine (matches the pattern
for loading, progression, etc.). Three cross-cutting tests: missing
movement silently skips movement-dependent rules while group-level
rules still fire; round-trip apply-clamp test verifies the
(locator, rule) -> corrected_value contract end-to-end; package-level
import test pins the re-export surface.

Full suite green: 18 baseline tests + ~38 validator tests, all passing.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-review (against the spec)

**Spec coverage** — every rule in the spec's §6 maps to a task:
- §6.1 PRIMARY_NOT_FIRST → Task 2
- §6.2 GIANT_SET_ROUNDS → Task 3
- §6.3 GIANT_SET_CONCURRENCY → Task 3
- §6.4 SINGLE_KB → Task 3
- §6.5 EQUIPMENT_NOT_IN_MANIFEST → Task 3
- §6.6 HT_BOTTOM_OVER_LIMIT → Task 4
- §6.7 HT_BAND_NOT_REGISTERED → Task 4
- §6.8 LOAD_BELOW_FLOOR → Task 5
- §6.9 LOAD_OVER_CAP → Task 5
- §6.10 RPE_OVER_CAP → Task 5
- §6.11 KNEE_FREQUENCY → Task 6
- §6.12 PULL_PUSH_RATIO → Task 6

Public API (§4) → Task 1. Traversal & lookup invariants (§5) → covered across Tasks 2-7. Testing strategy (§7) → tests are written per-task, cross-cutting tests in Task 7 cover the "missing movement," apply-loop, and `tallies=None` cases. Build & verify (§8) → final pytest run in Task 7 Step 4. Wire impact (§9) → no API/wire changes; confirmed by no modification to `ironlog/api/` or `ironlog/models/`. Out of scope (§10) → enforced via the "no CONDITIONING_PLACEMENT" check in Task 1 Step 1.

**Placeholder scan** — no TBDs, no "TODO", no "implement appropriate," no "fill in," no "similar to Task N" without code. Each code-changing step has a complete code block.

**Type consistency** — `make_session`/`make_group`/`make_exercise`/`make_set`/`make_movement` signatures defined in Task 1 are used verbatim by Tasks 2-7. `ValidationContext` field order matches across §4 of the spec and Task 1's class body. `RuleCode` enum members defined in Task 1 are referenced by exact name in every later test. `_check_*` function names are consistent (each is `_check_<rule_lowercase>` with the exception of `_check_ht_safety` which intentionally covers two rules in one walk — flagged in Task 4's "Why one function for two rules" note). No drift.
