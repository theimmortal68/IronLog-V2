"""loop.py — approval gate + commit-at-approve (Fork 7) [orchestrator in Task 10].

commit_session is the SOLE writer of current_load (Fork 7c, two-writer boundary):
  - run_analysis writes e1rm / tier / ceiling fields (never current_load)
  - commit_session writes current_load from prospective_current_loads

A discarded regenerate leaves state untouched by construction: assemble() only
computes prospective values in memory; only a committed approval persists them.

generate_session wires the §3A conditional gate (Task 10):
  - Quiet week (no deviation signal on any adaptive slot) → emit the program
    deterministically via program_selections; the proposer is NEVER called.
  - Signal present → propose_validate_repair → fallback if exhausted.

NO from __future__ import annotations (project-wide constraint).
"""
from datetime import datetime
from typing import Callable, List

from sqlmodel import Session as DBSession, select

from ..models.enums import SessionStatus
from ..models.library import GenerationLog, MovementState
from ..models.session import Session
from .assembler import AssembledSession, assemble
from .context import GenerationContext, build_context_payload, resolve_context, should_invoke_llm
from .fallback import fallback_session, program_selections
from .proposer import Proposer
from .repair import (
    RepairOutcome, apply_clamps, build_validation_context, propose_validate_repair,
)
from .skeleton import Skeleton, lay_skeleton
from ..engine.validator import validate


def is_clean(outcome: RepairOutcome) -> bool:
    """Fork 7a/7b: clean iff zero repairs AND zero clamps.

    A fallback (assembled is None OR exhausted is True) is NEVER clean.
    A quiet-week deterministic emission (attempts=0, clamps_applied=0) IS clean —
    it is the most pristine outcome possible (no LLM call, no repairs, no clamps).
    The check uses attempts <= 1 to cover both the quiet path (0) and the
    first-try-clean LLM path (1).
    """
    if outcome.assembled is None or outcome.exhausted:
        return False
    return outcome.attempts <= 1 and outcome.clamps_applied == 0


def commit_session(
    assembled: AssembledSession,
    db: DBSession,
    *,
    approval_mode: str,
    prompt: dict,
    selections_dict: dict,
    clamps: List,
    repairs: List,
    fallback_used: bool,
) -> Session:
    """The SOLE writer of current_load (Fork 7c).

    1. Persists the Session graph (session + groups + exercises + sets) via
       SQLAlchemy cascade on db.add(session).
    2. Writes current_load from assembled.prospective_current_loads to each
       MovementState, creating the row if it doesn't exist yet.
    3. Writes a GenerationLog provenance row (Fork 7d).
    4. Sets approved_at.

    This is the ONLY place generation writes current_load.
    """
    session = assembled.session
    session.status = SessionStatus.PLANNED
    session.approved_at = datetime.utcnow()
    # Cascade via save-update default: adds groups → exercises → sets
    db.add(session)
    db.commit()
    db.refresh(session)

    # Write prospective_current_loads — the sole generation write of current_load
    for mid, load in assembled.prospective_current_loads.items():
        st = db.exec(
            select(MovementState).where(MovementState.movement_id == mid)
        ).first()
        if st is None:
            st = MovementState(movement_id=mid)
        st.current_load = load          # THE ONLY PLACE generation writes current_load
        db.add(st)

    # Provenance row (Fork 7d)
    db.add(GenerationLog(
        session_id=session.id,
        prompt_json=prompt,
        selections_json=selections_dict,
        clamps_json=clamps,
        repairs_json=repairs,
        approval_mode=approval_mode,
        fallback_used=fallback_used,
    ))
    db.commit()
    db.refresh(session)
    return session


# ---------------------------------------------------------------------------
# generate_session — the §3A orchestrator (Task 10)
# ---------------------------------------------------------------------------

def generate_session(
    day_role: str,
    db: DBSession,
    proposer: Proposer,
    week_keyer: Callable,
) -> RepairOutcome:
    """Resolve context → lay skeleton → §3A gate → assemble / propose / fallback.

    §3A conditional gate:
      - If no adaptive slot carries a deviation signal (stall / weak-point hint /
        novelty owed / open note), emit the program deterministically via
        program_selections.  The proposer is NEVER called (quiet week / meso-1).
      - If >=1 slot justifies a deviation, call propose_validate_repair (the LLM
        proposes the whole session).  If that exhausts without a valid result,
        fall back to the deterministic fallback_session.

    Returns a RepairOutcome with the assembled candidate (not committed).  The
    caller (approve endpoint / commit_session) writes it to the DB on approval.
    Regenerate = call generate_session again; no DB state is written until approve.
    """
    sk = lay_skeleton(day_role, db)
    ctx = resolve_context(day_role, sk, db, week_keyer)

    # §3A conditional gate: quiet week → deterministic program emission; no LLM call.
    if not should_invoke_llm(sk, ctx):
        assembled = assemble(program_selections(sk), sk, ctx, db)
        # The program prior is valid by construction; clamps applied for safety.
        n_clamps = apply_clamps(
            assembled.session,
            validate(assembled.session, build_validation_context(ctx, db)),
        )
        return RepairOutcome(
            assembled=assembled,
            attempts=0,
            clamps_applied=n_clamps,
            rejections=[],
            exhausted=False,
        )

    # Signal present → LLM proposes (whole-session, Fork 4b); validate / repair loop.
    payload = build_context_payload(ctx, sk)
    outcome = propose_validate_repair(proposer, payload, sk, ctx, db)
    if outcome.exhausted:
        # Repair exhausted → deterministic fallback (Fork 4c / §3A).
        outcome.assembled = fallback_session(sk, ctx, db)
    return outcome
