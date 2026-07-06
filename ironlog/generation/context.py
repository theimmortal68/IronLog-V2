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
from typing import Callable, Dict, List, Set

from sqlalchemy import or_
from sqlmodel import Session, col, select

from ..engine.ledger import compute_tallies
from ..engine.stall import detect_stall
from ..engine.validator import WeeklyTallies
from ..models.enums import NoteClass, Objective, Status
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
    weak_point_hints: Dict[int, dict]
    candidate_menus: Dict[str, List[int]] = field(default_factory=dict)
    # §3A addendum (ii): movement ids with an open Note / RPE-trend flag
    note_flagged_movement_ids: Set[int] = field(default_factory=set)
    # Task 3: full Movement lookup for payload enrichment
    movements: Dict[int, "Movement"] = field(default_factory=dict)
    # Task 5: per-slot rep scheme from TierExercise (informational only)
    slot_rep_schemes: Dict[str, dict] = field(default_factory=dict)


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

def build_weak_point_hints(db: Session) -> Dict[int, dict]:
    """Per stalled movement: typed + severity + limiter record (gap D).

    Notes:
    - Always calls detect_stall even when window is empty: the failed_stalled arm
      fires on consecutive_failed_progressions >= STALL_FAILED_THRESHOLD regardless
      of E1rm history presence (a fresh movement with 2 failed progressions IS stalled).
    - detect_stall is PROGRESS-objective-gated: it always returns StallSignal(False,
      False, False) for non-PROGRESS movements, so we pass PROGRESS unconditionally
      (the stall concept only applies to progress-tracked lifts).
    - Record shape: {"stall_type": "failed"|"trend"|"both", "failed_count": int,
      "e1rm_window": {"sessions": int, "peak": float|None, "latest": float|None},
      "limiter": {"primary_muscle": str|None, "secondary_muscles": [str]}}
    """
    records: Dict[int, dict] = {}
    movements = {m.id: m for m in db.exec(select(Movement)).all()}
    states = db.exec(select(MovementState)).all()
    for st in states:
        rows = db.exec(
            select(E1rmHistory).where(E1rmHistory.movement_id == st.movement_id)
        ).all()
        window = select_progress_window(list(rows))
        sig = detect_stall(window, st.consecutive_failed_progressions, Objective.PROGRESS)
        if not sig.stalled:
            continue
        if sig.trend_stalled and sig.failed_stalled:
            stype = "both"
        elif sig.trend_stalled:
            stype = "trend"
        else:
            stype = "failed"
        m = movements.get(st.movement_id)
        records[st.movement_id] = {
            "stall_type": stype,
            "failed_count": st.consecutive_failed_progressions,
            "e1rm_window": {
                "sessions": len(window),
                "peak": max(window) if window else None,
                "latest": window[-1] if window else None,
            },
            "limiter": {
                "primary_muscle": m.primary_muscle.value if (m and m.primary_muscle) else None,
                "secondary_muscles": list(m.secondary_muscles) if m else [],
            },
        }
    return records


# ---------------------------------------------------------------------------
# §3A gate functions (addendum ii)
# ---------------------------------------------------------------------------

def slot_has_deviation_signal(slot: SlotSpec, ctx: GenerationContext) -> bool:
    """A slot justifies a possible deviation iff (§3A):
    - its program movement is stalled / has a weak-point limiter, OR
    - novelty_owed is True for this slot (signature would repeat — §7), OR
    - its program movement has an open Note / RPE-trend flag.

    Guardrail completeness: only menu-governed slots (giant / knee — those with an
    entry in ctx.candidate_menus) are deviation-eligible.  Accessory (semi/free
    non-giant, non-knee) slots have no guardrailed candidate menu, so the LLM
    cannot be constrained to on-menu selections for them.  A menu-less slot is
    therefore treated as non-deviable — the program movement stands regardless of
    any feedback signal.  This ensures every LLM deviation is backed by a menu that
    check_menu_membership can enforce.
    """
    if slot.slot_id not in ctx.candidate_menus:
        # No guardrailed menu → not deviation-eligible; program movement stands.
        return False
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
    # Task 5: day-scope the state read. Movements shared across days (Hip
    # Thrust D2/D5/D6, Reverse Hyper, Nordic, Cable Tib) now have per-day
    # (movement_id, day_id=day_role) rows (Task 4's seed_movement_baselines).
    # A plain movement_id-keyed read across ALL rows collapses those to
    # whichever row the dict comprehension iterates last (undefined vs. the
    # day being generated) — e.g. D2 Lower A picking up D6 Weak Points' HT
    # plates. Scope to (day_id == day_role) OR legacy day_id IS NULL rows,
    # and order NULL rows first so a day-scoped row always overwrites a
    # legacy NULL row for the same movement_id (day-scoped wins last-write).
    states: Dict[int, MovementState] = {}
    for s in db.exec(
        select(MovementState)
        .where(
            or_(MovementState.day_id == day_role,
                col(MovementState.day_id).is_(None))
        )
        .order_by(col(MovementState.day_id).is_(None).desc())
    ).all():
        states[s.movement_id] = s

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

    # Open Notes: movement_id is set, note has not been applied, AND the note is
    # actionable (CONFIG_CHANGE / PROGRAMMING_REQUEST) — the same set the
    # /notes/review inbox shows. JOURNAL and TRANSIENT_FLAG notes never enter the
    # review inbox and are never touched by a terminal action, so they must not
    # flag a movement for the proposer (see fix/note-flag-actionable-only).
    note_rows = db.exec(
        select(Note).where(
            Note.movement_id.is_not(None),
            Note.applied == False,  # noqa: E712 — SQLAlchemy == False is correct here
            col(Note.classification).in_(
                [NoteClass.CONFIG_CHANGE, NoteClass.PROGRAMMING_REQUEST]
            ),
        )
    ).all()
    note_flagged: Set[int] = {
        n.movement_id for n in note_rows if n.movement_id is not None
    }

    owed = compute_owed_requirements(tallies)

    # Task 5: build per-slot rep schemes from TierExercise records (informational).
    # Key on the slot's stable identity (slot_id), NOT the movement_id: an active
    # SlotMovementOverride or a meso-2 MesoRotation makes a slot's effective
    # program_movement_id differ from its base TierExercise.movement_id, so a
    # movement-keyed lookup would miss (rep_scheme None) or hit the wrong TE.
    from ..models.program import TierExercise
    te_by_slot: Dict[str, TierExercise] = {
        te.slot_id: te
        for te in db.exec(select(TierExercise)).all()
    }
    slot_rep_schemes: Dict[str, dict] = {}
    for slot in skeleton.adaptive_slots:
        te = te_by_slot.get(slot.slot_id)
        if te is not None:
            slot_rep_schemes[slot.slot_id] = {
                "rep_low": te.rep_low,
                "rep_high": te.rep_high,
                "scheme": te.scheme,
            }

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
        movements=movements,
        slot_rep_schemes=slot_rep_schemes,
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
# _candidate_descriptor (Task 3)
# ---------------------------------------------------------------------------

def _candidate_descriptor(mid: int, slot: SlotSpec, ctx: "GenerationContext") -> dict:
    m = ctx.movements.get(mid)
    return {
        "id": mid,
        "name": m.name if m else str(mid),
        "primary_muscle": m.primary_muscle.value if (m and m.primary_muscle) else None,
        "secondary_muscles": list(m.secondary_muscles) if m else [],
        "lift_category": m.lift_category.value if m else None,
        "pattern": slot.pattern,
        "equipment_tags": list(m.equipment_tags) if m else [],
        "is_program_anchor": mid == slot.program_movement_id,
    }


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
        "phase_intent": {
            "objective": ctx.phase_policy.default_objective.value,
            "rpe_band": [ctx.phase_policy.rpe_band_low, ctx.phase_policy.rpe_band_high],
            "volume_posture": ctx.phase_policy.volume_posture,
        },
        "anchors": skeleton.anchor_movement_ids,
        "slots": [
            {
                "slot_id": s.slot_id,
                "kind": s.kind,
                "pattern": s.pattern,
                "tier_role": s.tier_role,       # brief used s.tier — real field is tier_role
                "knee_modality": s.knee_modality,
                "rep_scheme": ctx.slot_rep_schemes.get(s.slot_id),
                "candidates": [
                    _candidate_descriptor(mid, s, ctx)
                    for mid in ctx.candidate_menus.get(s.slot_id, [])
                ],
            }
            for s in skeleton.adaptive_slots
        ],
        "owed": ctx.owed,
        "recent_signatures": ctx.recent_signatures,
        "weak_point_hints": ctx.weak_point_hints,
    }
