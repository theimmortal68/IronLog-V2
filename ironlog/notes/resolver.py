"""resolver.py — deterministically find candidate slots matching a note's
subject movement and compute pre-validated override proposals.

NO from __future__ import annotations (project-wide constraint).
"""
import re
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from sqlalchemy import or_
from sqlmodel import Session, col, select

from ..engine.validator import ValidationContext
from ..generation.assembler import resolve_start_load
from ..generation.skeleton import _effective_exercise_order, _effective_movement_id
from ..models.enums import LiftCategory, ProgressionMode
from ..models.library import BandPair, Movement, MovementState
from ..models.program import ProgramDay, Tier, TierExercise
from ..models.session import Session as WorkoutSession


@dataclass
class ResolvedProposal:
    tier_exercise_id: int
    day_role: str                     # for display
    slot_label: str                   # e.g. "GS1" -- for display
    override_type: str                # "MOVEMENT" | "LOAD" | "REPS" | "REORDER"
    override_movement_id: Optional[int] = None
    load_delta: Optional[float] = None
    load_absolute: Optional[float] = None
    rep_low: Optional[int] = None
    rep_high: Optional[int] = None
    override_order: Optional[float] = None
    valid: bool = True
    validation_note: Optional[str] = None
    summary: str = ""                 # human-readable one-line description


def _get_note_action_type(note) -> str:
    if isinstance(note, dict):
        return note.get("action_type", "OTHER")
    if hasattr(note, "action_type") and getattr(note, "action_type") is not None:
        return getattr(note, "action_type")
    meta = getattr(note, "classification_meta", None)
    if isinstance(meta, dict):
        return meta.get("action_type", "OTHER")
    return "OTHER"


def _get_note_proposed_change(note) -> Optional[dict]:
    if isinstance(note, dict):
        return note.get("proposed_change")
    if hasattr(note, "proposed_change") and getattr(note, "proposed_change") is not None:
        return getattr(note, "proposed_change")
    meta = getattr(note, "classification_meta", None)
    if isinstance(meta, dict):
        return meta.get("proposed_change")
    return None


def _get_te_day_role(te: TierExercise, db: Session) -> str:
    tier = db.get(Tier, te.tier_id)
    if tier is not None:
        prog_day = db.get(ProgramDay, tier.program_day_id)
        if prog_day is not None:
            return prog_day.day_role
    return ""


def _get_movement_state_for_candidate(te: TierExercise, day_role: str, db: Session) -> Optional[MovementState]:
    """Mirror build_weak_point_hints exact-match-then-legacy-null-fallback pattern."""
    mid = _effective_movement_id(db, te, meso_number=1)
    rows = db.exec(
        select(MovementState).where(
            MovementState.movement_id == mid,
            or_(MovementState.day_id == day_role, col(MovementState.day_id).is_(None))
        )
    ).all()
    if not rows:
        return None
    exact = next((st for st in rows if st.day_id == day_role), None)
    if exact is not None:
        return exact
    legacy = next((st for st in rows if st.day_id is None), None)
    return legacy


def find_candidate_slots(subject_movement_name: str, db: Session) -> List[TierExercise]:
    """Resolve subject_movement_name to candidate TierExercise slots across ALL days/programs.

    Matches Movement by exact case-insensitive Movement.name or Movement.base_name.
    Does NOT reuse resolve_slot in ironlog/notes/apply.py (which resolves by ATTACHMENT).
    Mirrors skeleton._effective_movement_id's exact query pattern for effective movement lookup.
    """
    if not subject_movement_name or not isinstance(subject_movement_name, str):
        return []
    target_lower = subject_movement_name.strip().lower()
    matched_mids = set()
    for m in db.exec(select(Movement)).all():
        if m.name.lower() == target_lower or m.base_name.lower() == target_lower:
            matched_mids.add(m.id)
    if not matched_mids:
        return []
    candidates = []
    for te in db.exec(select(TierExercise)).all():
        effective_mid = _effective_movement_id(db, te, meso_number=1)
        if effective_mid in matched_mids:
            candidates.append(te)
    return candidates


def rank_candidates(candidates: List[TierExercise], note, db: Session) -> List[TierExercise]:
    """Rank candidate slots: best guess first.

    Heuristic:
    1. Prefer candidate whose day matches the note's own session_id's day_role (attachment).
    2. Otherwise prefer day-track whose MovementState row (day-scoped (movement_id, day_id)
       exact-match-then-legacy-null-fallback) has the most recent confirmed_at.
    3. Stable tie-break by TierExercise.id ascending (earlier created first).
    """
    attached_day_role = None
    session_id = getattr(note, "session_id", None)
    if isinstance(note, dict):
        session_id = note.get("session_id")
    if session_id is not None:
        ws = db.get(WorkoutSession, session_id)
        if ws is not None:
            attached_day_role = ws.day_role

    def sort_key(te: TierExercise):
        te_day_role = _get_te_day_role(te, db)
        is_attached = 1 if (attached_day_role is not None and te_day_role == attached_day_role) else 0
        st = _get_movement_state_for_candidate(te, te_day_role, db)
        confirmed_at = st.confirmed_at if (st is not None and st.confirmed_at is not None) else datetime.min
        neg_id = -te.id if te.id is not None else 0
        return (is_attached, confirmed_at, neg_id)

    return sorted(candidates, key=sort_key, reverse=True)


def _clean_swap_target(target_str: str) -> str:
    cleaned = target_str.strip()
    for prefix in ("switch to ", "swap to ", "change to ", "to ", "with "):
        if cleaned.lower().startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()
            break
    return cleaned


def _resolve_neighbor_in_tier(name: str, tier_exercises: List[TierExercise], current_te: TierExercise, db: Session) -> Optional[TierExercise]:
    if not name:
        return None
    target_lower = name.strip().lower()
    matched_mids = set()
    for m in db.exec(select(Movement)).all():
        if m.name.lower() == target_lower or m.base_name.lower() == target_lower:
            matched_mids.add(m.id)
    if not matched_mids:
        return None
    for other_te in tier_exercises:
        if other_te.id == current_te.id:
            continue
        if _effective_movement_id(db, other_te, meso_number=1) in matched_mids:
            return other_te
    return None


def _compute_and_validate_proposal(te: TierExercise, note, db: Session) -> ResolvedProposal:
    tier = db.get(Tier, te.tier_id)
    slot_label = tier.tier_label if tier else ""
    prog_day = db.get(ProgramDay, tier.program_day_id) if tier else None
    day_role = prog_day.day_role if prog_day else ""

    action_type = _get_note_action_type(note)
    proposed_change = _get_note_proposed_change(note) or {}

    if action_type == "SWAP":
        target_str = str(proposed_change.get("params") or proposed_change.get("action") or "").strip()
        if not target_str:
            return ResolvedProposal(
                tier_exercise_id=te.id,
                day_role=day_role,
                slot_label=slot_label,
                override_type="MOVEMENT",
                valid=False,
                validation_note="No swap target movement specified",
                summary="Invalid movement swap",
            )
        cleaned_target = _clean_swap_target(target_str)
        target_lower = cleaned_target.lower()
        target_m = None
        for m in db.exec(select(Movement)).all():
            if m.name.lower() == target_lower or m.base_name.lower() == target_lower:
                target_m = m
                break
        if target_m is None:
            return ResolvedProposal(
                tier_exercise_id=te.id,
                day_role=day_role,
                slot_label=slot_label,
                override_type="MOVEMENT",
                valid=False,
                validation_note=f"Replacement target movement '{cleaned_target}' not found by exact match",
                summary=f"Swap to {cleaned_target}",
            )
        summary = f"Swap slot {slot_label} to {target_m.name}"
        return ResolvedProposal(
            tier_exercise_id=te.id,
            day_role=day_role,
            slot_label=slot_label,
            override_type="MOVEMENT",
            override_movement_id=target_m.id,
            valid=True,
            validation_note=None,
            summary=summary,
        )

    if action_type in ("LOAD_INCREASE", "LOAD_DECREASE"):
        params_str = str(proposed_change.get("params") or proposed_change.get("action") or "").strip()
        match = re.search(r'[-+]?(?:\d+\.?\d*|\.\d+)', params_str)
        if match is None:
            return ResolvedProposal(
                tier_exercise_id=te.id,
                day_role=day_role,
                slot_label=slot_label,
                override_type="LOAD",
                valid=False,
                validation_note=f"Could not extract numeric load delta from '{params_str}'",
                summary="Invalid load adjustment",
            )
        raw_val = float(match.group(0))
        delta = abs(raw_val) if action_type == "LOAD_INCREASE" else -abs(raw_val)
        summary = f"{'Increase' if delta >= 0 else 'Decrease'} load by {abs(delta):g} lb for {slot_label}"

        # Pre-commit safety check against validator thresholds:
        # Prefer a narrower, bespoke comparison against the SAME underlying thresholds
        # (ht_bottom_clamp, movement.load_floor, movement.cap) rather than fabricating
        # a synthetic Session/ValidationContext, which requires full ExerciseGroup and
        # PlannedSet machinery.
        mid = _effective_movement_id(db, te, meso_number=1)
        m = db.get(Movement, mid)
        if m is not None:
            is_ht = (
                m.lift_category == LiftCategory.HIP_THRUST
                or m.progression_mode == ProgressionMode.COMPOSITE
            )
            st = _get_movement_state_for_candidate(te, day_role, db)
            base_load = resolve_start_load(m, st, db)

            if is_ht:
                if st is not None and st.ht_plates is not None:
                    current_plates = st.ht_plates
                elif base_load is not None:
                    current_plates = base_load
                else:
                    return ResolvedProposal(
                        tier_exercise_id=te.id,
                        day_role=day_role,
                        slot_label=slot_label,
                        override_type="LOAD",
                        load_delta=delta,
                        valid=False,
                        validation_note=(
                            "No calibrated baseline for this HT movement - "
                            "cannot safely compute a load change"
                        ),
                        summary=summary,
                    )
                if st is not None and st.ht_band_config is not None:
                    config = list(st.ht_band_config)
                elif st is not None and st.ht_band_pair_id is not None:
                    config = [st.ht_band_pair_id]
                else:
                    config = []

                new_plates = current_plates + delta
                band_bottom_sum = 0.0
                all_bands_registered = True
                for bid in config:
                    bp = db.get(BandPair, bid)
                    if bp is None:
                        all_bands_registered = False
                        break
                    band_bottom_sum += bp.bottom_lb

                if not all_bands_registered:
                    return ResolvedProposal(
                        tier_exercise_id=te.id,
                        day_role=day_role,
                        slot_label=slot_label,
                        override_type="LOAD",
                        load_delta=delta,
                        valid=False,
                        validation_note="HT band_pair_id not registered — cannot evaluate bottom-clamp safety",
                        summary=summary,
                    )

                bottom_total = new_plates + band_bottom_sum
                ht_bottom_clamp = ValidationContext().ht_bottom_clamp
                if bottom_total > ht_bottom_clamp:
                    return ResolvedProposal(
                        tier_exercise_id=te.id,
                        day_role=day_role,
                        slot_label=slot_label,
                        override_type="LOAD",
                        load_delta=delta,
                        valid=False,
                        validation_note=(
                            f"HT bottom total {bottom_total:.1f} lb exceeds clamp "
                            f"{ht_bottom_clamp:.1f} lb (plates+band at bottom position)"
                        ),
                        summary=summary,
                    )
                new_load = new_plates
            else:
                current_load = (
                    st.current_load
                    if (st is not None and st.current_load is not None)
                    else (base_load if base_load is not None else 0.0)
                )
                new_load = current_load + delta

            if m.load_floor is not None and new_load < m.load_floor:
                return ResolvedProposal(
                    tier_exercise_id=te.id,
                    day_role=day_role,
                    slot_label=slot_label,
                    override_type="LOAD",
                    load_delta=delta,
                    valid=False,
                    validation_note=f"Load {new_load:g} below floor {m.load_floor:g}",
                    summary=summary,
                )
            if m.cap is not None and new_load > m.cap:
                return ResolvedProposal(
                    tier_exercise_id=te.id,
                    day_role=day_role,
                    slot_label=slot_label,
                    override_type="LOAD",
                    load_delta=delta,
                    valid=False,
                    validation_note=f"Load {new_load:g} over cap {m.cap:g}",
                    summary=summary,
                )

        return ResolvedProposal(
            tier_exercise_id=te.id,
            day_role=day_role,
            slot_label=slot_label,
            override_type="LOAD",
            load_delta=delta,
            valid=True,
            validation_note=None,
            summary=summary,
        )

    if action_type == "REP_CHANGE":
        params_str = str(proposed_change.get("params") or proposed_change.get("action") or "").strip()
        range_match = re.search(r'\b(\d+)\s*(?:-|to)\s*(\d+)\b', params_str)
        if range_match:
            rep_low = int(range_match.group(1))
            rep_high = int(range_match.group(2))
            if rep_low > rep_high:
                rep_low, rep_high = rep_high, rep_low
        else:
            single_match = re.search(r'\b(\d+)\s*[xX]\s*(\d+)\b', params_str)
            if single_match:
                rep_low = rep_high = int(single_match.group(1))
            else:
                single_match = re.search(r'\b(\d+)\b', params_str)
                if single_match:
                    rep_low = rep_high = int(single_match.group(1))
                else:
                    return ResolvedProposal(
                        tier_exercise_id=te.id,
                        day_role=day_role,
                        slot_label=slot_label,
                        override_type="REPS",
                        valid=False,
                        validation_note=f"Could not extract rep targets from '{params_str}'",
                        summary="Invalid rep adjustment",
                    )

        if rep_low <= 0 or rep_high <= 0:
            return ResolvedProposal(
                tier_exercise_id=te.id,
                day_role=day_role,
                slot_label=slot_label,
                override_type="REPS",
                rep_low=rep_low,
                rep_high=rep_high,
                valid=False,
                validation_note="Rep targets must be positive integers",
                summary="Invalid rep adjustment",
            )

        summary = (
            f"Change reps to {rep_low}-{rep_high} for {slot_label}"
            if rep_low != rep_high
            else f"Change reps to {rep_low} for {slot_label}"
        )
        return ResolvedProposal(
            tier_exercise_id=te.id,
            day_role=day_role,
            slot_label=slot_label,
            override_type="REPS",
            rep_low=rep_low,
            rep_high=rep_high,
            valid=True,
            validation_note=None,
            summary=summary,
        )

    if action_type == "REORDER":
        before_name = str(proposed_change.get("before_movement") or "").strip()
        after_name = str(proposed_change.get("after_movement") or "").strip()
        if not before_name and not after_name:
            return ResolvedProposal(
                tier_exercise_id=te.id,
                day_role=day_role,
                slot_label=slot_label,
                override_type="REORDER",
                valid=False,
                validation_note="REORDER note did not specify before_movement or after_movement",
                summary="Invalid reorder",
            )

        tier_exercises = db.exec(select(TierExercise).where(TierExercise.tier_id == te.tier_id)).all()
        before_te = _resolve_neighbor_in_tier(before_name, tier_exercises, te, db) if before_name else None
        after_te = _resolve_neighbor_in_tier(after_name, tier_exercises, te, db) if after_name else None

        if before_name and after_name:
            if before_te is None or after_te is None:
                return ResolvedProposal(
                    tier_exercise_id=te.id,
                    day_role=day_role,
                    slot_label=slot_label,
                    override_type="REORDER",
                    valid=False,
                    validation_note=(
                        f"Could not resolve neighboring movements '{before_name}' "
                        f"and/or '{after_name}' in tier {slot_label}"
                    ),
                    summary="Invalid reorder",
                )
            before_order = _effective_exercise_order(db, before_te)
            after_order = _effective_exercise_order(db, after_te)
            override_order = (before_order + after_order) / 2.0
            summary = f"Move {slot_label} between {after_name} and {before_name}"
            return ResolvedProposal(
                tier_exercise_id=te.id,
                day_role=day_role,
                slot_label=slot_label,
                override_type="REORDER",
                override_order=override_order,
                valid=True,
                validation_note=None,
                summary=summary,
            )
        elif before_name:
            if before_te is None:
                return ResolvedProposal(
                    tier_exercise_id=te.id,
                    day_role=day_role,
                    slot_label=slot_label,
                    override_type="REORDER",
                    valid=False,
                    validation_note=f"Could not resolve neighboring movement '{before_name}' in tier {slot_label}",
                    summary="Invalid reorder",
                )
            before_order = _effective_exercise_order(db, before_te)
            # Reasonable offset: place 0.5 before the neighbor order
            override_order = before_order - 0.5
            summary = f"Move {slot_label} before {before_name}"
            return ResolvedProposal(
                tier_exercise_id=te.id,
                day_role=day_role,
                slot_label=slot_label,
                override_type="REORDER",
                override_order=override_order,
                valid=True,
                validation_note=None,
                summary=summary,
            )
        else:
            if after_te is None:
                return ResolvedProposal(
                    tier_exercise_id=te.id,
                    day_role=day_role,
                    slot_label=slot_label,
                    override_type="REORDER",
                    valid=False,
                    validation_note=f"Could not resolve neighboring movement '{after_name}' in tier {slot_label}",
                    summary="Invalid reorder",
                )
            after_order = _effective_exercise_order(db, after_te)
            # Reasonable offset: place 0.5 after the neighbor order
            override_order = after_order + 0.5
            summary = f"Move {slot_label} after {after_name}"
            return ResolvedProposal(
                tier_exercise_id=te.id,
                day_role=day_role,
                slot_label=slot_label,
                override_type="REORDER",
                override_order=override_order,
                valid=True,
                validation_note=None,
                summary=summary,
            )

    return ResolvedProposal(
        tier_exercise_id=te.id,
        day_role=day_role,
        slot_label=slot_label,
        override_type="UNKNOWN",
        valid=False,
        validation_note=f"Unsupported action_type '{action_type}'",
        summary="Unsupported note action",
    )


def resolve_note(note, db: Session) -> List[ResolvedProposal]:
    """Resolve a classified note into pre-validated proposals across candidate slots.

    Only meaningful for action_type in {SWAP, LOAD_INCREASE, LOAD_DECREASE, REP_CHANGE, REORDER}.
    Returns empty list for OTHER or if proposed_change is None or no candidates match.
    Does NOT mutate anything (read-only).
    """
    action_type = _get_note_action_type(note)
    if action_type not in {"SWAP", "LOAD_INCREASE", "LOAD_DECREASE", "REP_CHANGE", "REORDER"}:
        return []
    proposed_change = _get_note_proposed_change(note)
    if not proposed_change or not isinstance(proposed_change, dict):
        return []
    subject_movement_name = proposed_change.get("movement")
    if not subject_movement_name or not isinstance(subject_movement_name, str):
        return []
    candidates = find_candidate_slots(subject_movement_name, db)
    if not candidates:
        return []
    ranked = rank_candidates(candidates, note, db)
    return [_compute_and_validate_proposal(te, note, db) for te in ranked]
