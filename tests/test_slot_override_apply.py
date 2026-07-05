"""tests/test_slot_override_apply.py — Task 1 (note-apply REDESIGN): the
assembler applies an active LOAD/REPS SlotMovementOverride at prescription
time (Option C: prescription-only, never MovementState/current_load).

MOVEMENT overrides remain lay_skeleton's responsibility (see
test_slot_override_skeleton.py); this file covers the new override_type
axis (LOAD / REPS) and the assembler application point.

NO from __future__ import annotations (project-wide constraint).
gen_db / gen_db_calibrated fixtures auto-discovered from conftest.py.
"""
from sqlmodel import select

from ironlog.generation.assembler import assemble
from ironlog.generation.context import resolve_context
from ironlog.generation.proposer import Selections, SlotSelection
from ironlog.generation.skeleton import lay_skeleton
from ironlog.models.enums import OverrideType
from ironlog.models.library import Movement, MovementState
from ironlog.models.program import SlotMovementOverride, TierExercise
from ironlog.models.session import Note


def _canned_for(sk, ctx):
    """Deterministic selections: pick first candidate for every giant/knee slot."""
    slots = []
    for s in sk.adaptive_slots:
        if s.kind in ("giant", "knee"):
            slots.append(SlotSelection(s.slot_id, ctx.candidate_menus[s.slot_id][0]))
    return Selections(ordering=[s.slot_id for s in slots], slots=slots, rationale="t")


def _assemble(gen_db):
    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    sk = lay_skeleton("D1 Upper Push", gen_db)
    ctx = resolve_context("D1 Upper Push", sk, gen_db, wk)
    return assemble(_canned_for(sk, ctx), sk, ctx, gen_db)


def _t1_planned_set(res):
    """The T1 anchor group's single planned set (Bench Press [PB], STRAIGHT scheme)."""
    t1_group = next(g for g in res.session.groups if g.label == "T1")
    ex = t1_group.exercises[0]
    return ex.planned_sets[0]


def _other_slot_loads(res):
    return [
        ps.target_load
        for g in res.session.groups if g.label != "T1"
        for e in g.exercises for ps in e.planned_sets
    ]


def test_load_and_reps_override_apply_at_prescription(gen_db_calibrated):
    gen_db = gen_db_calibrated

    te = gen_db.exec(
        select(TierExercise).where(TierExercise.slot_id == "d1_t1")
    ).one()
    movement = gen_db.get(Movement, te.movement_id)
    note = Note(text="test")
    gen_db.add(note)
    gen_db.commit()
    gen_db.refresh(note)

    # Baseline — no active override.
    baseline = _assemble(gen_db)
    baseline_set = _t1_planned_set(baseline)
    engine_load = baseline_set.target_load
    baseline_reps_low = baseline_set.target_reps_low
    baseline_reps_high = baseline_set.target_reps_high
    assert engine_load is not None, "T1 (Bench Press) must have a calibrated load"

    state = gen_db.exec(
        select(MovementState).where(MovementState.movement_id == movement.id)
    ).one()
    current_load_before = state.current_load

    # --- LOAD override, load_delta=10 — only the T1 slot's target_load changes.
    ov = SlotMovementOverride(
        tier_exercise_id=te.id, override_movement_id=te.movement_id,
        source_note_id=note.id, active=True,
        override_type=OverrideType.LOAD, load_delta=10,
    )
    gen_db.add(ov)
    gen_db.commit()
    gen_db.refresh(ov)

    with_delta = _assemble(gen_db)
    assert _t1_planned_set(with_delta).target_load == engine_load + 10
    assert _other_slot_loads(with_delta) == _other_slot_loads(baseline), \
        "a LOAD override on d1_t1 must not affect any other slot"

    # Option-C guardrail: the override must NEVER write current_load/MovementState.
    gen_db.refresh(state)
    assert state.current_load == current_load_before, \
        "LOAD override must adjust only the prescribed value, never MovementState.current_load"

    # --- load_absolute=225 takes precedence over load_delta.
    ov.load_delta = None
    ov.load_absolute = 225
    gen_db.add(ov)
    gen_db.commit()
    with_absolute = _assemble(gen_db)
    assert _t1_planned_set(with_absolute).target_load == 225

    gen_db.refresh(state)
    assert state.current_load == current_load_before, \
        "load_absolute override must also never write current_load"

    # --- REPS override — rep_low/rep_high applied to the slot's PlannedSets; load untouched.
    ov.override_type = OverrideType.REPS
    ov.load_absolute = None
    ov.rep_low = 5
    ov.rep_high = 8
    gen_db.add(ov)
    gen_db.commit()
    with_reps = _assemble(gen_db)
    reps_set = _t1_planned_set(with_reps)
    assert reps_set.target_reps_low == 5 and reps_set.target_reps_high == 8
    assert reps_set.target_load == engine_load, "REPS override must not touch load"

    # --- active=False reverts fully to the baseline prescription.
    ov.active = False
    gen_db.add(ov)
    gen_db.commit()
    reverted = _assemble(gen_db)
    reverted_set = _t1_planned_set(reverted)
    assert reverted_set.target_load == engine_load
    assert reverted_set.target_reps_low == baseline_reps_low
    assert reverted_set.target_reps_high == baseline_reps_high


def test_load_override_applies_even_when_a_movement_override_coexists(gen_db_calibrated):
    """Fix 1 guard: a slot may carry BOTH a MOVEMENT and a LOAD override at once
    (the generalized table enforces no per-slot uniqueness). The assembler's
    override lookup must filter to LOAD/REPS so a bare .first() cannot return the
    MOVEMENT row and silently drop the load adjustment.

    The MOVEMENT override is inserted FIRST (lower id) so an unfiltered .first()
    would return it — this case fails without the symmetric override_type filter.
    It swaps bench -> bench (same movement) to keep the engine load stable, so the
    only expected change to the T1 prescription is the LOAD override's +10.
    """
    gen_db = gen_db_calibrated
    te = gen_db.exec(
        select(TierExercise).where(TierExercise.slot_id == "d1_t1")
    ).one()
    note = Note(text="test")
    gen_db.add(note)
    gen_db.commit()
    gen_db.refresh(note)

    engine_load = _t1_planned_set(_assemble(gen_db)).target_load
    assert engine_load is not None

    # MOVEMENT override first (lower id) — bench->bench, a no-op swap.
    mv_ov = SlotMovementOverride(
        tier_exercise_id=te.id, override_movement_id=te.movement_id,
        source_note_id=note.id, active=True,
        override_type=OverrideType.MOVEMENT,
    )
    gen_db.add(mv_ov)
    gen_db.commit()
    # LOAD override second (higher id).
    load_ov = SlotMovementOverride(
        tier_exercise_id=te.id, override_movement_id=te.movement_id,
        source_note_id=note.id, active=True,
        override_type=OverrideType.LOAD, load_delta=10,
    )
    gen_db.add(load_ov)
    gen_db.commit()

    both = _t1_planned_set(_assemble(gen_db))
    assert both.target_load == engine_load + 10, \
        "LOAD override must apply even when a MOVEMENT override coexists on the slot"
