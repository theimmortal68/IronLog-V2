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
from ironlog.models.enums import Status
from ironlog.models.library import Movement, MovementState
from sqlmodel import select


def test_menu_hard_filters_inactive_and_wrong_pattern(gen_db):
    """Knee menu must include only ACTIVE movements with the matching knee_modality."""
    manifest = {e for e in gen_db.exec(select(Movement.load_equipment_id)).all() if e}
    # program_movement_id=None: no anchor to prepend; tests the filter only
    knee_slot = SlotSpec(
        slot_id="k", kind="knee", pattern=None,
        tier_role="free", knee_modality="NORDIC", program_movement_id=None,
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


def test_should_invoke_llm_stall_signal_returns_true(gen_db):
    """Plant a failed-progression stall on a semi/free slot → LLM must be invoked."""
    sk = lay_skeleton("D1 Upper Push", gen_db)
    # Pick the first semi/free adaptive slot with a program_movement_id
    target_slot = next(
        s for s in sk.adaptive_slots
        if s.tier_role in ("semi", "free") and s.program_movement_id is not None
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
        "stall on a semi/free slot → should_invoke_llm must return True"
    )
