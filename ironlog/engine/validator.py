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


def validate(session: Session, ctx: ValidationContext) -> ValidationResult:
    """Validate a proposed session against all 12 hard rules.
    Returns ALL violations; never early-exits on a REJECT."""
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
