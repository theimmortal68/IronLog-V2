"""context.py — RESOLVE CONTEXT (Fork 2 inject) + candidate menus (Fork 3).

Responsibilities:
- GenerationContext: the resolved snapshot injected into each session generation
- resolve_context(): aggregate phase, tallies, states, menus, hints, signals
- build_candidate_menu(): program-anchored menu (§3A addendum i)
- compute_owed_requirements(): knee owed + pull/push ratio
- build_weak_point_hints(): stall-based L1 soft hints per movement
- slot_has_deviation_signal() / should_invoke_llm(): §3A conditional gate
- build_context_payload(): the single Fork-2 payload dict

Brief reconciliations applied:
- SlotSpec/Skeleton imported from .skeleton (no daytemplate.py in this repo)
- build_candidate_menu: program-anchored — anchor first, alternatives deduped/excluded
- build_weak_point_hints: always calls detect_stall (no early-exit on empty window)
  so the failed_stalled arm fires even for movements with no E1rm history yet
- build_context_payload: uses s.tier_role (the real SlotSpec field), not s.tier
- GenerationContext gains note_flagged_movement_ids (Set[int]) per §3A addendum (ii)
- owed dict gains novelty_owed stub (empty dict; populated in Task 4 integration)
- _PATTERN_LIFT_CATEGORIES extended to cover all patterns used in program_seed.py

NO from __future__ import annotations (project-wide constraint).
"""
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Dict, List, Optional, Set

from sqlmodel import Session, select

from ..engine.ledger import compute_tallies
from ..engine.stall import detect_stall
from ..engine.validator import WeeklyTallies
from ..models.enums import Objective, Status
from ..models.library import (
    E1rmHistory, EngineState, Movement, MovementState, PhasePolicy,
)
from ..models.session import Note, SetLog
from ..persistence.run_analysis import select_progress_window
from .skeleton import Skeleton, SlotSpec

# ---------------------------------------------------------------------------
# Pattern -> lift_category mapping
# ---------------------------------------------------------------------------
# Maps slot patterns (from program_seed.py TierExercises) to the set of
# LiftCategory.value strings that qualify as alternatives.
# Patterns not in this dict (unknown/new) → no alternatives (anchor only).
# Patterns with an empty set → no LiftCategory filter matches → anchor only.
# This keeps the menu deterministic: a "wrong-pattern" slot still yields
# a non-empty menu because the program anchor is always prepended first.
_PATTERN_LIFT_CATEGORIES: Dict[str, Set[str]] = {
    # ── Upper patterns ─────────────────────────────────────────────────────
    "bench":          {"BENCH"},
    "horizontal_pull": {"ROW"},
    "vertical_push":  {"OHP", "CG_PRESS"},
    "vertical_pull":  set(),       # pull-ups; no ROW/BENCH category — anchor only
    "lateral_raise":  set(),       # no distinct LiftCategory — anchor only
    "rear_delt":      set(),       # no distinct LiftCategory — anchor only
    "lat":            set(),       # lat pullovers etc — anchor only
    "triceps":        set(),       # no distinct LiftCategory — anchor only
    # ── Lower patterns ─────────────────────────────────────────────────────
    "squat":          {"BACK_SQUAT", "FRONT_SQUAT"},
    "hip_thrust":     {"HIP_THRUST"},
    "reverse_hyper":  {"REV_HYPER"},
    "rdl":            {"RDL"},
    "hip_hinge":      {"RDL", "DEADLIFT", "HIP_THRUST"},  # broader — from v0.5 brief
    "lunge":          set(),       # BSS has NONE category — anchor only
    "calf":           set(),       # no distinct LiftCategory — anchor only
    # ── Core ───────────────────────────────────────────────────────────────
    "core":           set(),       # NONE category — anchor only
}

# Weekly knee-frequency targets (docs/06 §4).
KNEE_TARGETS: Dict[str, int] = {"NORDIC": 2, "TIB": 2, "KOT": 2, "SISSY": 1}
PULL_PUSH_TARGET: float = 2.0


# ---------------------------------------------------------------------------
# GenerationContext
# ---------------------------------------------------------------------------

@dataclass
class GenerationContext:
    """The resolved context snapshot for one generation call (Fork 2 inject)."""
    phase: str
    phase_policy: PhasePolicy
    manifest_equipment_ids: Set[int]
    movement_states: Dict[int, MovementState]
    tallies: WeeklyTallies
    owed: dict
    recent_signatures: List[dict]
    weak_point_hints: Dict[int, str]
    candidate_menus: Dict[str, List[int]] = field(default_factory=dict)
    # §3A addendum (ii): movement ids with an open Note / RPE-trend flag
    note_flagged_movement_ids: Set[int] = field(default_factory=set)


# ---------------------------------------------------------------------------
# build_candidate_menu
# ---------------------------------------------------------------------------

def build_candidate_menu(
    slot: SlotSpec,
    db: Session,
    manifest_ids: Set[int],
) -> List[int]:
    """Program-anchored candidate menu for one adaptive slot (§3A addendum i).

    Returns [slot.program_movement_id] + filtered alternatives (anchor excluded,
    deduped). The program movement is always offered first — the LLM may pick
    an alternative only when feedback justifies a deviation.

    Hard filters on alternatives (Fork 3):
    - Movement.status == ACTIVE
    - Equipment feasible: load_equipment_id in manifest_ids (or None — BW/protocol)
    - is_primary == False (anchors placed deterministically; not in adaptive menus)
    - Kind-specific:
        knee slot  → knee_modality.value == slot.knee_modality
        other slot → lift_category.value in _PATTERN_LIFT_CATEGORIES[slot.pattern]
                     (unknown / empty pattern → empty set → no alternatives)

    The model cannot pick off-menu; menu-membership is re-checked by validate.
    """
    rows = db.exec(select(Movement).where(Movement.status == Status.ACTIVE)).all()
    alternatives: List[int] = []
    for m in rows:
        if m.is_primary:
            continue  # anchors placed deterministically; never in adaptive menus
        if m.load_equipment_id is not None and m.load_equipment_id not in manifest_ids:
            continue
        if slot.kind == "knee":
            if m.knee_modality is None or m.knee_modality.value != slot.knee_modality:
                continue
        elif slot.pattern is not None:
            cats = _PATTERN_LIFT_CATEGORIES.get(slot.pattern, set())
            if m.lift_category.value not in cats:
                continue
        alternatives.append(m.id)

    # Program-anchored: anchor first, alternatives deduped with anchor excluded
    anchor = slot.program_movement_id
    alts = [mid for mid in sorted(alternatives) if mid != anchor]
    if anchor is not None:
        return [anchor] + alts
    return alts


# ---------------------------------------------------------------------------
# compute_owed_requirements
# ---------------------------------------------------------------------------

def compute_owed_requirements(tallies: WeeklyTallies) -> dict:
    """Compute owed requirements from tallies.

    Returns:
      knee_owed: Dict[modality, remaining_sessions_owed_this_week]
      pull_push_ratio: current pull/push volume ratio (None if push==0)
      pull_push_target: target ratio (from tallies)
      novelty_owed: stub {} until Task 4 signature module is integrated
    """
    owed_knee = {
        k: max(0, tallies.knee_targets.get(k, 0) - tallies.knee_counts.get(k, 0))
        for k in tallies.knee_targets
    }
    push = tallies.push_volume or 0.0
    ratio = (tallies.pull_volume / push) if push else None
    return {
        "knee_owed": owed_knee,
        "pull_push_ratio": ratio,
        "pull_push_target": tallies.pull_push_target,
        # §3A addendum (ii): novelty_owed populated in Task 4 integration;
        # stub here so slot_has_deviation_signal's owed.get("novelty_owed") works.
        "novelty_owed": {},
    }


# ---------------------------------------------------------------------------
# build_weak_point_hints
# ---------------------------------------------------------------------------

def build_weak_point_hints(db: Session) -> Dict[int, str]:
    """L1 soft hint (Fork 3 / §9): for each movement with a MovementState, run
    detect_stall; if stalled, flag the limiter hint (soft).

    Notes:
    - Always calls detect_stall even when window is empty: the failed_stalled arm
      fires on consecutive_failed_progressions >= STALL_FAILED_THRESHOLD regardless
      of E1rm history presence (a fresh movement with 2 failed progressions IS stalled).
    - detect_stall is PROGRESS-objective-gated: it always returns StallSignal(False,
      False, False) for non-PROGRESS movements, so we pass PROGRESS unconditionally
      (the stall concept only applies to progress-tracked lifts).
    """
    hints: Dict[int, str] = {}
    states = db.exec(select(MovementState)).all()
    for st in states:
        rows = db.exec(
            select(E1rmHistory).where(E1rmHistory.movement_id == st.movement_id)
        ).all()
        window = select_progress_window(list(rows))
        sig = detect_stall(window, st.consecutive_failed_progressions, Objective.PROGRESS)
        if sig.stalled:
            hints[st.movement_id] = (
                "stalled: bias accessory volume toward the limiter (L1)"
            )
    return hints


# ---------------------------------------------------------------------------
# §3A gate functions (addendum ii)
# ---------------------------------------------------------------------------

def slot_has_deviation_signal(slot: SlotSpec, ctx: GenerationContext) -> bool:
    """A slot justifies a possible deviation iff (§3A):
    - its program movement is stalled / has a weak-point limiter, OR
    - novelty_owed is True for this slot (signature would repeat — §7), OR
    - its program movement has an open Note / RPE-trend flag.
    """
    mid = slot.program_movement_id
    if mid in ctx.weak_point_hints:               # stall fired / limiter present
        return True
    if ctx.owed.get("novelty_owed", {}).get(slot.slot_id):  # signature repeat (§7)
        return True
    if mid in ctx.note_flagged_movement_ids:      # open Note / RPE-trend flag
        return True
    return False


def should_invoke_llm(skeleton: Skeleton, ctx: GenerationContext) -> bool:
    """Call the LLM iff >=1 adaptive slot carries a deviation signal (§3A).

    Only semi/free tier_role slots and knee slots are considered — anchor slots
    are placed deterministically and are not subject to LLM-driven deviation.
    Quiet week (meso 1, no signals) → deterministic program-emit → no LLM call.
    """
    return any(
        slot_has_deviation_signal(s, ctx)
        for s in skeleton.adaptive_slots
        if s.tier_role in ("semi", "free") or s.knee_modality
    )


# ---------------------------------------------------------------------------
# resolve_context
# ---------------------------------------------------------------------------

def resolve_context(
    day_role: str,
    skeleton: Skeleton,
    db: Session,
    week_keyer: Callable[[date], object],
) -> GenerationContext:
    """Resolve all context for one generation call (Fork 2 inject).

    Reads: EngineState (phase), PhasePolicy, all Movements, MovementStates,
    SetLogs (for tallies), open Notes (for note_flagged_movement_ids), and
    recent completed sessions for the same day_role.
    Builds per-slot candidate menus and weak-point hints.
    Does NOT write anything.
    """
    engine_state = db.exec(select(EngineState)).one()
    phase = engine_state.current_phase
    policy = db.exec(
        select(PhasePolicy).where(PhasePolicy.phase == phase)
    ).one()

    movements = {m.id: m for m in db.exec(select(Movement)).all()}
    manifest_ids: Set[int] = {
        m.load_equipment_id
        for m in movements.values()
        if m.load_equipment_id is not None
    }
    states: Dict[int, MovementState] = {
        s.movement_id: s for s in db.exec(select(MovementState)).all()
    }

    # Weekly tallies (ledger)
    set_logs = db.exec(select(SetLog)).all()
    tallies = compute_tallies(set_logs, movements)
    tallies.knee_targets = dict(KNEE_TARGETS)
    tallies.pull_push_target = PULL_PUSH_TARGET

    # Candidate menus: giant + knee adaptive slots only
    menus: Dict[str, List[int]] = {}
    for slot in skeleton.adaptive_slots:
        if slot.kind in ("giant", "knee"):
            menus[slot.slot_id] = build_candidate_menu(slot, db, manifest_ids)

    # Recent same-role sessions (for novelty signature comparison, §7)
    recent = [
        s.signature
        for s in _recent_same_role_sessions(db, day_role)
    ]

    # Weak-point hints (stall detection, L1 soft)
    weak_hints = build_weak_point_hints(db)

    # Open Notes: movement_id is set and note has not been applied
    note_rows = db.exec(
        select(Note).where(
            Note.movement_id.is_not(None),
            Note.applied == False,  # noqa: E712 — SQLAlchemy == False is correct here
        )
    ).all()
    note_flagged: Set[int] = {
        n.movement_id for n in note_rows if n.movement_id is not None
    }

    owed = compute_owed_requirements(tallies)

    return GenerationContext(
        phase=phase,
        phase_policy=policy,
        manifest_equipment_ids=manifest_ids,
        movement_states=states,
        tallies=tallies,
        owed=owed,
        recent_signatures=recent,
        weak_point_hints=weak_hints,
        candidate_menus=menus,
        note_flagged_movement_ids=note_flagged,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _recent_same_role_sessions(db: Session, day_role: str, n: int = 2) -> list:
    """Last n completed sessions for this day_role, most-recent first."""
    from ..models.enums import SessionStatus
    from ..models.session import Session as WorkoutSession

    rows = db.exec(
        select(WorkoutSession)
        .where(
            WorkoutSession.day_role == day_role,
            WorkoutSession.status == SessionStatus.COMPLETED,
        )
        .order_by(WorkoutSession.date.desc())
    ).all()
    return rows[:n]


# ---------------------------------------------------------------------------
# build_context_payload
# ---------------------------------------------------------------------------

def build_context_payload(ctx: GenerationContext, skeleton: Skeleton) -> dict:
    """The single injected prompt payload (Fork 2).

    Pure data — the Gemini adapter (Task 11) serializes this.
    Includes per-slot menus, owed reqs, recent signatures, weak-point hints.
    """
    return {
        "day_role": skeleton.day_role,
        "phase": ctx.phase,
        "anchors": skeleton.anchor_movement_ids,
        "slots": [
            {
                "slot_id": s.slot_id,
                "kind": s.kind,
                "pattern": s.pattern,
                "tier_role": s.tier_role,       # brief used s.tier — real field is tier_role
                "knee_modality": s.knee_modality,
                "candidates": ctx.candidate_menus.get(s.slot_id, []),
            }
            for s in skeleton.adaptive_slots
        ],
        "owed": ctx.owed,
        "recent_signatures": ctx.recent_signatures,
        "weak_point_hints": ctx.weak_point_hints,
    }
