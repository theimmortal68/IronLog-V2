"""tests/test_generation_context.py — Task 3: context resolver + menus + gate.

Tests written FIRST (TDD). run red → implement → run green.

Brief reconciliations applied here:
- SlotSpec imported from skeleton (no daytemplate.py exists)
- SlotSpec requires all positional fields (no optional defaults in the dataclass)
- program_movement_id=None used for the bare filter test (no anchor to prepend)
- test_menu_is_program_anchored: exercises the §3A addendum (i) contract
- test_should_invoke_llm_*: exercises the §3A addendum (ii) gate contract

NO from __future__ import annotations (project-wide constraint).
"""
from ironlog.generation.context import (
    build_candidate_menu,
    resolve_context,
    should_invoke_llm,
)
from ironlog.generation.skeleton import SlotSpec, lay_skeleton
from ironlog.models.enums import NoteClass, Status
from ironlog.models.library import Movement, MovementState
from ironlog.models.session import Note
from sqlmodel import select


def test_menu_hard_filters_inactive_and_wrong_pattern(gen_db):
    """Knee menu must include only ACTIVE movements with the matching knee_modality."""
    manifest = {e for e in gen_db.exec(select(Movement.load_equipment_id)).all() if e}
    # program_movement_id=None: no anchor to prepend; tests the filter only
    knee_slot = SlotSpec(
        slot_id="k", kind="knee", pattern=None,
        tier_role="free", knee_modality="NORDIC", program_movement_id=None,
        is_giant_tier=True,
    )
    menu = build_candidate_menu(knee_slot, gen_db, manifest)
    movers = {m.id: m for m in gen_db.exec(select(Movement)).all()}
    assert menu, "knee menu must be non-empty (NORDIC frequency is satisfiable)"
    for mid in menu:
        assert movers[mid].status == Status.ACTIVE
        assert movers[mid].knee_modality is not None
        assert movers[mid].knee_modality.value == "NORDIC"


def test_resolve_context_builds_per_slot_menus_and_tallies(gen_db):
    """resolve_context must populate candidate_menus for all giant/knee slots."""
    week_keyer = lambda d: (d.year, d.isocalendar()[1])
    sk = lay_skeleton("D1 Upper Push", gen_db)
    ctx = resolve_context("D1 Upper Push", sk, gen_db, week_keyer)
    assert ctx.tallies is not None
    for slot in sk.adaptive_slots:
        if slot.kind in ("giant", "knee"):
            assert slot.slot_id in ctx.candidate_menus, (
                f"slot {slot.slot_id!r} (kind={slot.kind!r}) missing from candidate_menus")


def test_menu_is_program_anchored(gen_db):
    """§3A addendum (i): program_movement_id must appear first in the candidate menu."""
    sk = lay_skeleton("D1 Upper Push", gen_db)
    manifest = {m.load_equipment_id for m in gen_db.exec(select(Movement)).all()
                if m.load_equipment_id}
    tested = False
    for slot in sk.adaptive_slots:
        if slot.kind == "giant" and slot.program_movement_id is not None:
            menu = build_candidate_menu(slot, gen_db, manifest)
            assert menu, f"menu for {slot.slot_id!r} must be non-empty"
            assert menu[0] == slot.program_movement_id, (
                f"slot {slot.slot_id!r}: first item must be program_movement_id="
                f"{slot.program_movement_id}, got {menu[0]}"
            )
            tested = True
            break
    assert tested, "D1 Upper Push must have at least one giant slot with program_movement_id"


def test_should_invoke_llm_quiet_db_returns_false(gen_db):
    """Quiet seeded DB (no stalls, no open notes) → LLM must not be invoked."""
    week_keyer = lambda d: (d.year, d.isocalendar()[1])
    sk = lay_skeleton("D1 Upper Push", gen_db)
    ctx = resolve_context("D1 Upper Push", sk, gen_db, week_keyer)
    assert not should_invoke_llm(sk, ctx), (
        "quiet seeded DB has no feedback signals; LLM call must be suppressed"
    )


def test_slot_rep_scheme_resolves_at_meso2_with_adaptive_rotation(gen_db):
    """The per-slot rep_scheme lookup must key on slot identity, NOT the effective
    movement. Once adaptive-slot meso rotations went live (lay_skeleton now resolves
    them via _effective_movement_id), an adaptive slot's program_movement_id can differ
    from its base TierExercise.movement_id. A movement-keyed lookup would miss (None) or
    return the wrong TE's scheme. D4's d4_t2a has a seeded meso-2 rotation (Meadows Row
    -> Pendlay Row), so at meso 2 its rep_scheme must still resolve to d4_t2a's own
    TierExercise rep_low/rep_high/scheme.
    """
    from ironlog.models.program import MesoRotation, TierExercise
    week_keyer = lambda d: (d.year, d.isocalendar()[1])

    te = gen_db.exec(
        select(TierExercise).where(TierExercise.slot_id == "d4_t2a")
    ).one()
    mr = gen_db.exec(
        select(MesoRotation).where(
            MesoRotation.tier_exercise_id == te.id,
            MesoRotation.meso_number == 2,
        )
    ).one()
    assert mr.movement_id != te.movement_id, "the meso-2 rotation must be a real swap"

    sk = lay_skeleton("D4 Upper Pull", gen_db, meso_number=2)
    slot = next(s for s in sk.adaptive_slots if s.slot_id == "d4_t2a")
    assert slot.program_movement_id == mr.movement_id, \
        "effective movement at meso 2 is the rotated one (differs from base)"

    ctx = resolve_context("D4 Upper Pull", sk, gen_db, week_keyer)
    rs = ctx.slot_rep_schemes.get("d4_t2a")
    assert rs is not None, \
        "rep_scheme must resolve even when the slot's effective movement != its base"
    assert rs["rep_low"] == te.rep_low
    assert rs["rep_high"] == te.rep_high
    assert rs["scheme"] == te.scheme


def test_should_invoke_llm_stall_signal_returns_true(gen_db):
    """Plant a failed-progression stall on a semi/free slot → LLM must be invoked."""
    sk = lay_skeleton("D1 Upper Push", gen_db)
    # Pick the first semi/free adaptive slot with a program_movement_id that has a menu
    # (giant slot: kind="giant") so that slot_has_deviation_signal can fire.
    target_slot = next(
        s for s in sk.adaptive_slots
        if s.tier_role in ("semi", "free")
        and s.kind == "giant"
        and s.program_movement_id is not None
    )
    # Plant a failed-progression stall signal (STALL_FAILED_THRESHOLD = 2)
    gen_db.add(MovementState(
        movement_id=target_slot.program_movement_id,
        consecutive_failed_progressions=2,
    ))
    gen_db.commit()

    week_keyer = lambda d: (d.year, d.isocalendar()[1])
    ctx = resolve_context("D1 Upper Push", sk, gen_db, week_keyer)
    assert should_invoke_llm(sk, ctx), (
        "stall on a semi/free giant slot → should_invoke_llm must return True"
    )


def test_menu_less_slot_is_not_deviation_eligible(gen_db):
    """FIX 3 (guardrail completeness): a slot absent from candidate_menus must NOT
    make should_invoke_llm return True, even when all other signals are present.

    Accessory (semi/free non-giant, non-knee) slots have no guardrailed candidate
    menu, so the LLM cannot deviate safely into them.  slot_has_deviation_signal
    must return False for any slot with no entry in ctx.candidate_menus.
    """
    from ironlog.generation.context import GenerationContext, slot_has_deviation_signal
    from ironlog.generation.skeleton import SlotSpec
    from ironlog.models.library import PhasePolicy

    wk = lambda d: (d.year, d.isocalendar()[1])
    sk = lay_skeleton("D1 Upper Push", gen_db)
    ctx = resolve_context("D1 Upper Push", sk, gen_db, wk)

    # Manufacture a slot that is absent from ctx.candidate_menus (menu-less accessory)
    # with a fake movement_id that also appears in weak_point_hints and note_flagged,
    # to confirm none of those signals can trigger deviation on a menu-less slot.
    dummy_mid = 99999
    menu_less_slot = SlotSpec(
        slot_id="menuless_test_slot",
        kind="semi",  # not "giant" or "knee" → no menu built by resolve_context
        pattern="lateral_raise",
        tier_role="free",
        knee_modality=None,
        program_movement_id=dummy_mid,
    )
    # Inject all possible signals into the context
    ctx.weak_point_hints[dummy_mid] = {
        "stall_type": "failed", "failed_count": 2,
        "e1rm_window": {"sessions": 0, "peak": None, "latest": None},
        "limiter": {"primary_muscle": None, "secondary_muscles": []},
    }
    ctx.note_flagged_movement_ids.add(dummy_mid)
    ctx.owed["novelty_owed"][menu_less_slot.slot_id] = True

    # The slot has no entry in candidate_menus → must not be deviation-eligible
    assert menu_less_slot.slot_id not in ctx.candidate_menus, (
        "precondition: menu_less_slot must be absent from candidate_menus"
    )
    assert slot_has_deviation_signal(menu_less_slot, ctx) is False, (
        "a slot absent from candidate_menus must return False from "
        "slot_has_deviation_signal even when all other signals are present"
    )


def test_note_flag_only_unresolved_actionable_notes(gen_db):
    """note_flagged_movement_ids must fire ONLY for unresolved CONFIG_CHANGE /
    PROGRAMMING_REQUEST notes — the exact set the /notes/review inbox shows.

    A JOURNAL or TRANSIENT_FLAG note never enters the review inbox and is never
    touched by a terminal action (apply/confirm/dismiss), so it would stay
    applied=False forever. If the note-flag query is classification-blind, a
    stray "felt strong" (JOURNAL) or "shoulder sore" (TRANSIENT_FLAG) note would
    nudge the proposer to reconsider that movement forever. An applied
    CONFIG_CHANGE (resolved) must also not flag.
    """
    movements = gen_db.exec(select(Movement)).all()
    assert len(movements) >= 5, "seeded library must have at least 5 movements"
    mid_config_change = movements[0].id
    mid_programming_request = movements[1].id
    mid_journal = movements[2].id
    mid_transient_flag = movements[3].id
    mid_applied_config_change = movements[4].id

    gen_db.add_all([
        Note(
            movement_id=mid_config_change, text="swap to dumbbells",
            classification=NoteClass.CONFIG_CHANGE, applied=False,
        ),
        Note(
            movement_id=mid_programming_request, text="want more volume",
            classification=NoteClass.PROGRAMMING_REQUEST, applied=False,
        ),
        Note(
            movement_id=mid_journal, text="felt strong today",
            classification=NoteClass.JOURNAL, applied=False,
        ),
        Note(
            movement_id=mid_transient_flag, text="shoulder sore",
            classification=NoteClass.TRANSIENT_FLAG, applied=False,
        ),
        Note(
            movement_id=mid_applied_config_change, text="already applied swap",
            classification=NoteClass.CONFIG_CHANGE, applied=True,
        ),
    ])
    gen_db.commit()

    week_keyer = lambda d: (d.year, d.isocalendar()[1])
    sk = lay_skeleton("D1 Upper Push", gen_db)
    ctx = resolve_context("D1 Upper Push", sk, gen_db, week_keyer)

    assert mid_config_change in ctx.note_flagged_movement_ids
    assert mid_programming_request in ctx.note_flagged_movement_ids
    assert mid_journal not in ctx.note_flagged_movement_ids, (
        "JOURNAL notes must never flag a movement for the proposer"
    )
    assert mid_transient_flag not in ctx.note_flagged_movement_ids, (
        "TRANSIENT_FLAG notes must never flag a movement for the proposer"
    )
    assert mid_applied_config_change not in ctx.note_flagged_movement_ids, (
        "an applied (resolved) CONFIG_CHANGE note must not flag the movement"
    )
    assert ctx.note_flagged_movement_ids == {mid_config_change, mid_programming_request}
