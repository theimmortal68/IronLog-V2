"""repair.py — validate + bounded repair (Fork 4 a/b).

Wraps the validator into a propose → assemble → clamp → re-validate loop,
bounded at max_retries (default 3).  On each retry the proposer receives
outcome-only rejection reasons (what requirement was unmet + its locus) and
re-emits a whole-session proposal.  The caller never receives remedy advice
from this layer.

Public API
----------
build_validation_context(ctx, db) -> ValidationContext
rejection_reasons(result)         -> List[str]   (outcome-only, no remedies)
apply_clamps(session, result)     -> int          (count applied)
RepairOutcome                     dataclass
propose_validate_repair(...)      -> RepairOutcome

NO from __future__ import annotations (project-wide constraint).
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from sqlmodel import Session as DBSession, select

from ..engine.validator import (
    MovementInfo, RuleCode, ValidationContext, ValidationResult,
    ViolationKind, validate,
)
from ..models.library import Movement
from .assembler import AssembledSession, assemble
from .context import GenerationContext
from .proposer import Proposer, selections_from_dict
from .skeleton import Skeleton


# ---------------------------------------------------------------------------
# build_validation_context
# ---------------------------------------------------------------------------

def build_validation_context(ctx: GenerationContext, db: DBSession) -> ValidationContext:
    """Project every Movement in the DB into a MovementInfo and build the
    ValidationContext for this generation call.

    band_bottom_lb, ht_bottom_clamp, and kettlebell_equipment_id are left at
    their dataclass defaults (HT-safety evaluation is handled separately).
    """
    infos: Dict[int, MovementInfo] = {}
    for m in db.exec(select(Movement)).all():
        infos[m.id] = MovementInfo(
            movement_id=m.id,
            is_primary=m.is_primary,
            load_equipment_id=m.load_equipment_id,
            load_floor=m.load_floor,
            cap=m.cap,
            rpe_cap_exempt=m.rpe_cap_exempt,
            lift_category=m.lift_category,
            progression_mode=m.progression_mode,
        )
    # tallies=None: per-session generation validate is STRUCTURAL-ONLY.
    # Cross-session frequency rules (KNEE_FREQUENCY, PULL_PUSH_RATIO) must not
    # hard-reject a single generated session — no one session can retroactively
    # satisfy a weekly target.  Weekly frequency is guaranteed at program-design
    # time (test_knee_frequencies_are_satisfiable) and soft-biased via owed
    # requirements (Fork 3); it is not a per-session structural hard reject.
    return ValidationContext(
        movements=infos,
        manifest_equipment_ids=set(ctx.manifest_equipment_ids),
        phase_hard_cap=ctx.phase_policy.hard_cap,
        tallies=None,
    )


# ---------------------------------------------------------------------------
# rejection_reasons  (Fork 4a — outcome-only)
# ---------------------------------------------------------------------------

def rejection_reasons(result: ValidationResult) -> List[str]:
    """Return outcome-only rejection strings from the validator's REJECT violations.

    Each string carries:
      <rule_code>: <validator message>[ [group N[, movement M]]]

    The validator's messages already state requirement + observed status
    (e.g. "NORDIC frequency unmet: 0/2 (owed 2)").  We append a locus
    (group/movement index) where available and pass the rest through verbatim.

    NEVER append a remedy, fix, or suggestion ("add X", "swap Y", "use Z").
    The proposer must infer the corrective action from the outcome alone.
    """
    out = []
    for v in result.rejects:
        locus = ""
        if v.group_index is not None:
            locus = f" [group {v.group_index}" + (
                f", movement {v.movement_id}]"
                if v.movement_id is not None
                else "]"
            )
        out.append(f"{v.rule.value}: {v.message}{locus}")
    return out


# ---------------------------------------------------------------------------
# apply_clamps  (Fork 4b)
# ---------------------------------------------------------------------------

def apply_clamps(session, result: ValidationResult) -> int:
    """Apply each CLAMP violation's corrected_value to the target field.

    Clamp application contract (mirrors validator.py module docstring):
      LOAD_BELOW_FLOOR | LOAD_OVER_CAP  -> write corrected_value to set.target_load
      RPE_OVER_CAP                       -> write corrected_value to set.target_rpe

    Locates each PlannedSet by (group_index, movement_id, set_index).
    Skips any clamp whose locator resolves to None or whose corrected_value is None.

    Returns the count of clamps successfully applied.
    """
    index: Dict[tuple, object] = {}
    for g in session.groups:
        for e in g.exercises:
            for ps in e.planned_sets:
                index[(g.order_index, e.movement_id, ps.set_index)] = ps
    n = 0
    for c in result.clamps:
        ps = index.get((c.group_index, c.movement_id, c.set_index))
        if ps is None or c.corrected_value is None:
            continue
        if c.rule in (RuleCode.LOAD_BELOW_FLOOR, RuleCode.LOAD_OVER_CAP):
            ps.target_load = c.corrected_value
        elif c.rule == RuleCode.RPE_OVER_CAP:
            ps.target_rpe = c.corrected_value
        n += 1
    return n


# ---------------------------------------------------------------------------
# RepairOutcome
# ---------------------------------------------------------------------------

@dataclass
class RepairOutcome:
    """Result of propose_validate_repair."""
    assembled: Optional[AssembledSession]    # None when exhausted
    attempts: int                            # how many propose-validate cycles ran
    clamps_applied: int                      # cumulative across all attempts
    rejections: List[str] = field(default_factory=list)  # outcome-only, last round
    exhausted: bool = False                  # True iff max_retries reached without success


# ---------------------------------------------------------------------------
# propose_validate_repair
# ---------------------------------------------------------------------------

def propose_validate_repair(
    proposer: Proposer,
    payload: dict,
    skeleton: Skeleton,
    ctx: GenerationContext,
    db: DBSession,
    max_retries: int = 3,
) -> RepairOutcome:
    """Propose → assemble → clamp → re-validate loop, bounded at max_retries.

    On each attempt:
      1. Call proposer.propose(payload + rejection feedback from previous round).
      2. Assemble the returned selections into a Session.
      3. Run validate; apply all CLAMPs (corrected_value written in place).
      4. Re-validate after clamping.
      5. If structurally valid (no REJECTs) → return success.
      6. Otherwise build outcome-only rejection_reasons and retry.

    After max_retries exhausted → return RepairOutcome(assembled=None, exhausted=True)
    with the final round's rejection reasons so the caller (Task 7 fallback) has
    the full set of unmet requirements.

    Feedback to the proposer is OUTCOME-ONLY: we pass what requirement was
    violated and where, never a remedy.  The proposer must re-propose the
    entire session each round.
    """
    vc = build_validation_context(ctx, db)
    reasons: List[str] = []
    total_clamps = 0

    for attempt in range(1, max_retries + 1):
        payload_with_feedback = dict(payload)
        if reasons:
            payload_with_feedback["rejections"] = reasons  # outcome-only feedback

        sel = proposer.propose(payload_with_feedback)
        assembled = assemble(sel, skeleton, ctx, db)

        result = validate(assembled.session, vc)
        total_clamps += apply_clamps(assembled.session, result)
        result = validate(assembled.session, vc)  # re-validate after clamps applied

        if result.is_structurally_valid:
            return RepairOutcome(
                assembled=assembled,
                attempts=attempt,
                clamps_applied=total_clamps,
                rejections=[],
                exhausted=False,
            )
        reasons = rejection_reasons(result)

    return RepairOutcome(
        assembled=None,
        attempts=max_retries,
        clamps_applied=total_clamps,
        rejections=reasons,
        exhausted=True,
    )
