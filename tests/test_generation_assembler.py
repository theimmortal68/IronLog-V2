"""
test_generation_assembler.py — Task 5: deterministic assembler + no-write gate.

Two named tests required by the spec:
  1. assembler_is_deterministic   — fixed selections → fixed, non-empty numbers
  2. assemble_does_not_write_current_load — commit-at-approve gate

NO from __future__ import annotations (project-wide constraint).
gen_db fixture auto-discovered from conftest.py.
"""
from ironlog.generation.assembler import assemble
from ironlog.generation.context import resolve_context
from ironlog.generation.proposer import Selections, SlotSelection
from ironlog.generation.skeleton import lay_skeleton
from ironlog.models.library import MovementState
from sqlmodel import select


def _canned_for(sk, ctx):
    """Deterministic selections: pick first candidate for every giant/knee slot."""
    slots = []
    for s in sk.adaptive_slots:
        if s.kind in ("giant", "knee"):
            slots.append(SlotSelection(s.slot_id, ctx.candidate_menus[s.slot_id][0]))
    return Selections(ordering=[s.slot_id for s in slots], slots=slots, rationale="t")


def test_assembler_is_deterministic(gen_db):
    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    sk = lay_skeleton("D1 Upper Push", gen_db)
    ctx = resolve_context("D1 Upper Push", sk, gen_db, wk)
    sel = _canned_for(sk, ctx)
    a = assemble(sel, sk, ctx, gen_db)
    b = assemble(sel, sk, ctx, gen_db)
    la = [ps.target_load for g in a.session.groups for e in g.exercises for ps in e.planned_sets]
    lb = [ps.target_load for g in b.session.groups for e in g.exercises for ps in e.planned_sets]
    assert la == lb and la, "fixed selections must yield fixed, non-empty numbers"


def test_assemble_does_not_write_current_load(gen_db):
    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    sk = lay_skeleton("D1 Upper Push", gen_db)
    ctx = resolve_context("D1 Upper Push", sk, gen_db, wk)
    before = {s.movement_id: s.current_load
              for s in gen_db.exec(select(MovementState)).all()}
    res = assemble(_canned_for(sk, ctx), sk, ctx, gen_db)
    after = {s.movement_id: s.current_load
             for s in gen_db.exec(select(MovementState)).all()}
    assert before == after, "assemble must NOT write current_load (commit-at-approve)"
    assert res.prospective_current_loads, "prospective loads computed in-memory"
