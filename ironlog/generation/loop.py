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

from sqlmodel import Session as DBSession

from ..models.enums import SessionStatus
from ..models.library import GenerationLog
from ..models.session import Session
from ..persistence.run_analysis import _resolve_movement_state
from .assembler import AssembledSession, assemble
from .context import GenerationContext, build_context_payload, resolve_context, should_invoke_llm
from .fallback import fallback_session, program_selections
from .proposer import Proposer
from .repair import (
    RepairOutcome, apply_clamps, build_validation_context, propose_validate_repair,
    rejection_reasons, _selections_to_dict, _clamps_to_list,
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
    """The SOLE writer of current_load / ht_plates / ht_band_config (Fork 7c, Option-C).

    1. Persists the Session graph (session + groups + exercises + sets) via
       SQLAlchemy cascade on db.add(session).
    2. Writes current_load from assembled.prospective_current_loads, and
       ht_plates/ht_band_config from assembled.prospective_ht_setups, to each
       MovementState, creating the row if it doesn't exist yet. Also clears
       pending_load_delta (K2) for every movement whose current_load it writes —
       the earned step is now baked in, so the bump lands exactly once.
    3. Writes a GenerationLog provenance row (Fork 7d).
    4. Sets approved_at.

    This is the ONLY place generation writes current_load, ht_plates, or
    ht_band_config.
    """
    session = assembled.session
    session.status = SessionStatus.PLANNED
    session.approved_at = datetime.utcnow()
    # Cascade via save-update default: adds groups → exercises → sets
    db.add(session)
    db.commit()
    db.refresh(session)

    # Write prospective_current_loads and prospective_ht_setups — the sole
    # generation write of current_load / ht_plates / ht_band_config. Merged into
    # one get-or-create loop (over the union of touched movement ids) so a
    # movement appearing in both maps (every HT movement does) gets a single
    # MovementState row instead of two independent inserts racing the unique
    # constraint on (movement_id, day_id).
    #
    # Day-scoped by the committing session's own day_role (mirrors
    # run_analysis.py's `day_id = workout.day_role` / the Task 5 read-path
    # fix): movements shared across days (Hip Thrust D2/D5/D6, Reverse Hyper,
    # Nordic, Cable Tib) have one MovementState row PER (movement_id, day_id).
    # A day-blind `.first()` here would silently write this session's
    # advancement into whichever day's row happened to be created first,
    # corrupting a sibling day's state the moment the athlete logs.
    #
    # _resolve_movement_state (run_analysis.py, Task 5) is reused verbatim
    # rather than re-implemented: exact (movement_id, day_id) match first;
    # else adopt a legacy (day_id IS NULL) row for this movement_id by
    # stamping its day_id — every pre-Task-1 row / test fixture seeded before
    # the progression engine has exactly one such row per movement_id, and
    # naively creating a second (day-scoped) row alongside it would leave
    # movement_id-only `.one()` lookups elsewhere broken; else create fresh.
    day_id = assembled.session.day_role
    touched_mids = set(assembled.prospective_current_loads) | set(assembled.prospective_ht_setups)
    for mid in touched_mids:
        st = _resolve_movement_state(db, mid, day_id)
        if mid in assembled.prospective_current_loads:
            st.current_load = assembled.prospective_current_loads[mid]   # THE ONLY PLACE generation writes current_load
            # K2 advance->load bridge: the earned step is now baked into
            # current_load — clear the marker so the bump applies exactly once
            # (a regenerate without a new clean session cannot double-bump).
            st.pending_load_delta = None
        if mid in assembled.prospective_ht_setups:
            plates, config = assembled.prospective_ht_setups[mid]
            st.ht_plates = plates             # THE ONLY PLACE generation writes ht_plates
            st.ht_band_config = list(config)  # THE ONLY PLACE generation writes ht_band_config
            st.pending_ht_plates = None
            st.pending_ht_band_config = None
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
        prog_sel = program_selections(sk)
        assembled = assemble(prog_sel, sk, ctx, db)
        vc = build_validation_context(ctx, db)
        result_pre = validate(assembled.session, vc)
        n_clamps = apply_clamps(assembled.session, result_pre)  # writes in-place
        result_post = validate(assembled.session, vc)           # re-validate after clamps
        # §10 provenance: build context payload for the quiet path (no LLM call, but
        # captures the resolved state that drove this generation for replayability).
        payload = build_context_payload(ctx, sk)
        if not result_post.is_structurally_valid:
            # The program prior is structurally invalid (should not occur on a
            # well-seeded program, but guard against it).  Fall back to last-valid-
            # refreshed rather than silently emitting an invalid session.
            assembled = fallback_session(sk, ctx, db)
            fb_pre = validate(assembled.session, vc)
            n_fb_clamps = apply_clamps(assembled.session, fb_pre)
            fb_post = validate(assembled.session, vc)
            if not fb_post.is_structurally_valid:
                # Both program prior and fallback are invalid — surface the error.
                raise ValueError(
                    f"Quiet-path structural REJECT: program prior invalid and "
                    f"fallback also invalid. Rejects: {rejection_reasons(fb_post)}"
                )
            return RepairOutcome(
                assembled=assembled,
                attempts=0,
                clamps_applied=n_fb_clamps,
                rejections=rejection_reasons(result_post),
                exhausted=False,
                prompt=payload,
                selections_dict=_selections_to_dict(prog_sel),
                clamps=_clamps_to_list(fb_pre),
            )
        return RepairOutcome(
            assembled=assembled,
            attempts=0,
            clamps_applied=n_clamps,
            rejections=[],
            exhausted=False,
            prompt=payload,
            selections_dict=_selections_to_dict(prog_sel),
            clamps=_clamps_to_list(result_pre),
        )

    # Signal present → LLM proposes (whole-session, Fork 4b); validate / repair loop.
    payload = build_context_payload(ctx, sk)
    outcome = propose_validate_repair(proposer, payload, sk, ctx, db)
    if outcome.exhausted:
        # Repair exhausted → deterministic fallback (Fork 4c / §3A).
        fallback = fallback_session(sk, ctx, db)
        outcome.assembled = fallback
        # Update provenance to reflect the fallback selections used.
        outcome.selections_dict = _selections_to_dict(program_selections(sk))
        outcome.clamps = []
    return outcome
