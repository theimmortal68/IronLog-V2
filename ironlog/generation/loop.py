"""loop.py — approval gate + commit-at-approve (Fork 7) [orchestrator in Task 10].

commit_session is the SOLE writer of current_load (Fork 7c, two-writer boundary):
  - run_analysis writes e1rm / tier / ceiling fields (never current_load)
  - commit_session writes current_load from prospective_current_loads

A discarded regenerate leaves state untouched by construction: assemble() only
computes prospective values in memory; only a committed approval persists them.

NO from __future__ import annotations (project-wide constraint).
"""
from datetime import datetime
from typing import List

from sqlmodel import Session as DBSession, select

from ..models.enums import SessionStatus
from ..models.library import GenerationLog, MovementState
from ..models.session import Session
from .assembler import AssembledSession
from .repair import RepairOutcome


def is_clean(outcome: RepairOutcome) -> bool:
    """Fork 7a/7b: clean iff zero repairs AND zero clamps.

    A fallback (assembled is None OR exhausted is True) is NEVER clean.
    """
    if outcome.assembled is None or outcome.exhausted:
        return False
    return outcome.attempts == 1 and outcome.clamps_applied == 0


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
