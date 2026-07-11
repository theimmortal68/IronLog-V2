"""
test_generation_assembler.py — Task 5: deterministic assembler + no-write gate.

Two named tests required by the spec:
  1. assembler_is_deterministic   — fixed selections → fixed, non-empty numbers
  2. assemble_does_not_write_current_load — commit-at-approve gate

Task 6 server fix (cross-repo review): ExerciseGroup.label must mirror the
source Tier.tier_label so the client can read GroupOut.label to drive T1/T1b
RPE-adaptive rest.
  3. assembled_group_labels_match_tier_labels

NO from __future__ import annotations (project-wide constraint).
gen_db fixture auto-discovered from conftest.py.
"""
from ironlog.generation.assembler import assemble
from ironlog.generation.context import resolve_context
from ironlog.generation.proposer import Selections, SlotSelection
from ironlog.generation.skeleton import lay_skeleton
from ironlog.models.enums import GroupType
from ironlog.models.library import Movement
from ironlog.models.library import MovementState
from sqlmodel import select


def _canned_for(sk, ctx):
    """Deterministic selections: pick first candidate for every giant/knee slot."""
    slots = []
    for s in sk.adaptive_slots:
        if s.kind in ("giant", "knee"):
            slots.append(SlotSelection(s.slot_id, ctx.candidate_menus[s.slot_id][0]))
    return Selections(ordering=[s.slot_id for s in slots], slots=slots, rationale="t")


def _names_for_group(group, db):
    names = {
        m.id: m.name
        for m in db.exec(select(Movement).where(Movement.id.in_([e.movement_id for e in group.exercises]))).all()
    }
    return [names[e.movement_id] for e in sorted(group.exercises, key=lambda e: e.order_index)]


def _giant_group_by_label(session, label):
    matches = [
        g for g in session.groups
        if g.group_type == GroupType.GIANT_SET and g.label == label
    ]
    assert len(matches) == 1, f"expected exactly one GIANT_SET group for {label!r}, got {len(matches)}"
    return matches[0]


def test_assembler_is_deterministic(gen_db_calibrated):
    # Reconciled for Task 3: loads configured (gen_db_calibrated) so the assembled
    # numbers are real prescriptions, not floor fabrications.  Bodyweight movements
    # contribute target_load None (consistent across runs).
    gen_db = gen_db_calibrated
    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    sk = lay_skeleton("D1 Upper Push", gen_db)
    ctx = resolve_context("D1 Upper Push", sk, gen_db, wk)
    sel = _canned_for(sk, ctx)
    a = assemble(sel, sk, ctx, gen_db)
    b = assemble(sel, sk, ctx, gen_db)
    la = [ps.target_load for g in a.session.groups for e in g.exercises for ps in e.planned_sets]
    lb = [ps.target_load for g in b.session.groups for e in g.exercises for ps in e.planned_sets]
    assert la == lb and la, "fixed selections must yield fixed, non-empty results"
    assert any(v is not None for v in la), "configured loads must yield real numbers"


def test_assemble_does_not_write_current_load(gen_db_calibrated):
    # Reconciled for Task 3: loads configured so prospective loads are non-empty
    # (the floor that used to populate prospective for unconfigured movements is gone).
    gen_db = gen_db_calibrated
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


def test_assembled_group_labels_match_tier_labels(gen_db_calibrated):
    # Task 6 server fix: the client rest timer reads GroupOut.label to decide
    # T1/T1b RPE-adaptive rest, but the assembler never set ExerciseGroup.label —
    # it was always None. Thread Tier.tier_label through (AnchorSpec.tier_label
    # for the anchor site, SlotSpec.group_key — already the tier_label — for the
    # giant/straight adaptive sites) and assert the assembled labels are real.
    # D1 Upper Push: T1 (anchor, STRAIGHT) + T2 GS / T3 GS / T4 GS (all giant).
    gen_db = gen_db_calibrated
    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    sk = lay_skeleton("D1 Upper Push", gen_db)
    ctx = resolve_context("D1 Upper Push", sk, gen_db, wk)
    res = assemble(_canned_for(sk, ctx), sk, ctx, gen_db)
    groups = sorted(res.session.groups, key=lambda g: g.order_index)
    labels = [g.label for g in groups]
    assert labels == ["T1", "T2 GS", "T3 GS", "T4 GS"], (
        f"group labels must mirror the seeded Tier.tier_label order, got {labels}"
    )
    assert groups[0].group_type.value == "STRAIGHT" and groups[0].label == "T1"
    assert groups[1].group_type.value == "GIANT_SET" and groups[1].label == "T2 GS"


def test_d1_t2_giant_set_stays_grouped(gen_db_calibrated):
    gen_db = gen_db_calibrated
    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    sk = lay_skeleton("D1 Upper Push", gen_db)
    ctx = resolve_context("D1 Upper Push", sk, gen_db, wk)
    res = assemble(_canned_for(sk, ctx), sk, ctx, gen_db)

    group = _giant_group_by_label(res.session, "T2 GS")
    assert _names_for_group(group, gen_db) == [
        "Pendlay Row - Narrow [OB]",
        "Incline DB Press [DB + BENCH]",
        "Face-Up Incline Knee Raise",
    ]


def test_d5_knee_modality_giant_tiers_stay_grouped(gen_db_calibrated):
    gen_db = gen_db_calibrated
    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    sk = lay_skeleton("D5 Lower B", gen_db)
    ctx = resolve_context("D5 Lower B", sk, gen_db, wk)
    res = assemble(_canned_for(sk, ctx), sk, ctx, gen_db)

    t2 = _giant_group_by_label(res.session, "T2 GS")
    assert _names_for_group(t2, gen_db) == [
        "Bulgarian Split Squat [DB]",
        "Reverse Hyper [REV_HYPER]",
        "Nordic Curl [GHR]",
    ]

    t3 = _giant_group_by_label(res.session, "T3 GS")
    assert _names_for_group(t3, gen_db) == [
        "Poliquin Step-up",
        "Reverse Nordic Curl [GHR]",
        "Cable Tibialis Raise",
        "Calf Raise [GHR]",
    ]
