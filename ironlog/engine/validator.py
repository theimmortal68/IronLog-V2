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
