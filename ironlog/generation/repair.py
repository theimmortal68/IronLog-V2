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
    validate,
)
from ..models.library import BandPair, Movement
from .assembler import AssembledSession, assemble
from .context import GenerationContext
from .gemini import ProposerError
from .proposer import Proposer, Selections
from .skeleton import Skeleton


# ---------------------------------------------------------------------------
# build_validation_context
# ---------------------------------------------------------------------------

def build_validation_context(ctx: GenerationContext, db: DBSession) -> ValidationContext:
    """Project every Movement in the DB into a MovementInfo and build the
    ValidationContext for this generation call.

    band_bottom_lb is populated from every BandPair in the DB (keyed by
    BandPair.id, matching the ids stored in ht_band_config / band_config) so
    _check_ht_safety can evaluate bottom-clamp safety for banded HT sets
    instead of rejecting them as unregistered. ht_bottom_clamp is set to 225.0
    (raised from the 220.0 dataclass default — user decision 2026-07-06, to
    allow D5's accepted 223 lb bottom). kettlebell_equipment_id is left at its
    dataclass default.
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
    band_bottom_lb: Dict[int, float] = {
        bp.id: bp.bottom_lb for bp in db.exec(select(BandPair)).all()
    }
    # tallies=None: per-session generation validate is STRUCTURAL-ONLY.
    # Cross-session frequency rules (KNEE_FREQUENCY, PULL_PUSH_RATIO) must not
    # hard-reject a single generated session — no one session can retroactively
    # satisfy a weekly target.  Weekly frequency is guaranteed at program-design
    # time (test_knee_frequencies_are_satisfiable) and soft-biased via owed
    # requirements (Fork 3); it is not a per-session structural hard reject.
    phase_hard_cap = (
        ctx.resolved_envelope.rpe_cap
        if ctx.resolved_envelope is not None
        else ctx.phase_policy.hard_cap
    )
    return ValidationContext(
        movements=infos,
        manifest_equipment_ids=set(ctx.manifest_equipment_ids),
        phase_hard_cap=phase_hard_cap,
        band_bottom_lb=band_bottom_lb,
        ht_bottom_clamp=225.0,
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
# check_menu_membership  (Fork 1 post-check)
# ---------------------------------------------------------------------------

def check_menu_membership(selections: Selections, ctx: GenerationContext) -> List[str]:
    """Return outcome-only reasons for any SlotSelection whose movement_id is
    not in its slot's candidate menu.

    Only checks slots that have an entry in ctx.candidate_menus (menu-governed
    adaptive slots: "giant" and "knee").  Anchor/conditioning slots absent from
    candidate_menus are skipped.

    Reasons are OUTCOME-ONLY: they state the unmet fact and its locus — NEVER
    a remedy, fix, or suggestion ("add X", "swap Y", "use Z").
    """
    reasons: List[str] = []
    for ss in selections.slots:
        menu = ctx.candidate_menus.get(ss.slot_id)
        if menu is None:
            continue  # not a menu-governed slot; skip
        if ss.movement_id not in menu:
            reasons.append(
                f"MENU_MEMBERSHIP: slot {ss.slot_id} movement {ss.movement_id}"
                f" not in candidate menu"
            )
    return reasons


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
# Provenance helpers
# ---------------------------------------------------------------------------

def _selections_to_dict(sel: Selections) -> dict:
    """Serialize a Selections object to a JSON-serialisable dict (§10 provenance)."""
    return {
        "ordering": list(sel.ordering),
        "slots": [
            {
                "slot_id": s.slot_id,
                "movement_id": s.movement_id,
                "variant": s.variant,
                "technique_tags": list(s.technique_tags),
            }
            for s in sel.slots
        ],
        "rationale": sel.rationale,
    }


def _clamps_to_list(result: ValidationResult) -> list:
    """Serialize CLAMP violations to a JSON-serialisable list (§10 provenance)."""
    return [
        {
            "rule": v.rule.value,
            "group_index": v.group_index,
            "movement_id": v.movement_id,
            "set_index": v.set_index,
            "corrected_value": v.corrected_value,
        }
        for v in result.clamps
    ]


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
    # §10 provenance fields — threaded through to commit_session / GenerationLog
    prompt: Optional[dict] = None           # the injected context payload (build_context_payload)
    selections_dict: Optional[dict] = None  # winning Selections serialised as dict
    clamps: Optional[list] = None           # applied clamp details (rule+locator+corrected_value)


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
      2. check_menu_membership: if any selection is off-menu, reject without
         assembling and feed back outcome-only membership reasons.
      3. Assemble the returned selections into a Session.
      4. Run validate; apply all CLAMPs (corrected_value written in place).
      5. Re-validate after clamping.
      6. If structurally valid (no REJECTs) → return success.
      7. Otherwise build outcome-only rejection_reasons and retry.

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

        # THE ROBUSTNESS GATE: a live proposer (GeminiProposer) can fail on network
        # transport, timeout, HTTP error, or a non-conforming response (ProposerError).
        # Such a failure must be treated as a FAILED ATTEMPT — record an outcome-only
        # reason and retry — so that after max_retries the exhausted path fires and
        # generate_session degrades to the deterministic fallback_session.  A live
        # Gemini outage must NEVER surface as an unhandled 500.
        try:
            sel = proposer.propose(payload_with_feedback)
        except ProposerError as e:
            reasons = [f"PROPOSER_ERROR: {type(e).__name__}"]  # outcome-only, no remedy
            continue
        except Exception as e:  # noqa: BLE001
            # Live-Gemini network/transport failures must degrade, not 500.  Narrow
            # to httpx transport errors when httpx is importable; anything else
            # re-raises so genuine programming bugs still surface.
            try:
                import httpx  # noqa: PLC0415
            except ImportError:
                raise
            if isinstance(e, httpx.HTTPError):
                reasons = [f"PROPOSER_ERROR: {type(e).__name__}"]  # outcome-only
                continue
            raise

        # Menu-membership pre-check (Fork 1 post-check): hard-enforce that every
        # selection is in its slot's candidate menu.  Off-menu → structural
        # rejection without assembling; outcome-only reasons feed back to the
        # proposer next round.  A knee slot's menu contains only knee-modality
        # movements, so this guarantees the knee slot is filled per-session by
        # construction.
        membership_reasons = check_menu_membership(sel, ctx)
        if membership_reasons:
            reasons = membership_reasons
            continue

        assembled = assemble(sel, skeleton, ctx, db)

        result_pre = validate(assembled.session, vc)
        n = apply_clamps(assembled.session, result_pre)
        total_clamps += n
        result_post = validate(assembled.session, vc)  # re-validate after clamps applied

        if result_post.is_structurally_valid:
            return RepairOutcome(
                assembled=assembled,
                attempts=attempt,
                clamps_applied=total_clamps,
                rejections=[],
                exhausted=False,
                prompt=payload,
                selections_dict=_selections_to_dict(sel),
                clamps=_clamps_to_list(result_pre),  # what was corrected
            )
        reasons = rejection_reasons(result_post)

    return RepairOutcome(
        assembled=None,
        attempts=max_retries,
        clamps_applied=total_clamps,
        rejections=reasons,
        exhausted=True,
        prompt=payload,
        selections_dict=None,
        clamps=[],
    )
