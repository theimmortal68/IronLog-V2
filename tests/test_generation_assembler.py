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
    # D1 Upper Push: T1 (anchor, STRAIGHT) + T1b (anchor, PAIR/straight;
    # 2026-07-26 Pendlay Row Narrow promotion) + T2 GS / T3 GS (giant). T4 GS
    # removed entirely 2026-08-10 (STAB maintenance-block redesign, D1
    # reconciled to already-executed Wk1 reality -- Ab Wheel Rollout moved
    # into T3 GS, Seated Cable Row and Cross-Body Rear Delt Fly dropped).
    gen_db = gen_db_calibrated
    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    sk = lay_skeleton("D1 Upper Push", gen_db)
    ctx = resolve_context("D1 Upper Push", sk, gen_db, wk)
    res = assemble(_canned_for(sk, ctx), sk, ctx, gen_db)
    groups = sorted(res.session.groups, key=lambda g: g.order_index)
    labels = [g.label for g in groups]
    assert labels == ["T1", "T1b", "T2 GS", "T3 GS"], (
        f"group labels must mirror the seeded Tier.tier_label order, got {labels}"
    )
    assert groups[0].group_type.value == "STRAIGHT" and groups[0].label == "T1"
    assert groups[2].group_type.value == "GIANT_SET" and groups[2].label == "T2 GS"


def test_d1_t2_giant_set_stays_grouped(gen_db_calibrated):
    gen_db = gen_db_calibrated
    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    sk = lay_skeleton("D1 Upper Push", gen_db)
    ctx = resolve_context("D1 Upper Push", sk, gen_db, wk)
    res = assemble(_canned_for(sk, ctx), sk, ctx, gen_db)

    # 2026-08-10 (STAB maintenance-block redesign): T2 GS's full membership
    # turned over to the movements actually executed in real Wk1 (Lying
    # Tricep Extension / Incline DB Press / Face-Up Incline Knee Raise all
    # dropped out of D1 entirely).
    group = _giant_group_by_label(res.session, "T2 GS")
    assert _names_for_group(group, gen_db) == [
        "Stryker Pad Seated OHP [DB]",
        "Matrix Machine Preacher Curl [EZ]",
        "Better Fly Standing Lateral Raise [FT]",
    ]


def test_d5_knee_modality_giant_tiers_stay_grouped(gen_db_calibrated):
    gen_db = gen_db_calibrated
    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    sk = lay_skeleton("D5 Lower B", gen_db)
    ctx = resolve_context("D5 Lower B", sk, gen_db, wk)
    res = assemble(_canned_for(sk, ctx), sk, ctx, gen_db)

    # 2026-08-12 (STAB maintenance-block redesign, Task 4): D5's T2/T3 GS
    # composition rewritten to match the FINAL doc's real D5 session.
    # 2026-08-14: Nordic Max Bulgarian Split Squat -> Matrix Machine
    # Bulgarian Split Squat (Nordic Max rig conflict with Nordic Curl Max
    # in the same giant set, athlete directive).
    t2 = _giant_group_by_label(res.session, "T2 GS")
    assert _names_for_group(t2, gen_db) == [
        "Matrix Machine Bulgarian Split Squat",
        "Nordic Curl Max [Ares]",
        "Better Fly Kickback [FT]",
    ]

    t3 = _giant_group_by_label(res.session, "T3 GS")
    assert _names_for_group(t3, gen_db) == [
        "Reverse Nordic Curl [GHR]",
        "Hybrid Board Calf Raise [D5]",
        "Hybrid Board Tib Raise [D5]",
        "Better Fly Hip Adduction [FT]",
    ]


def test_d1_group_order_is_unaffected_by_tier_order_fix(gen_db_calibrated):
    """No-regression check for the tier_order fix (2026-08-14).

    D1 Upper Push's anchor tiers (T1, T1b) are genuinely first in
    Tier.tier_order -- sorting groups by TRUE tier_order instead of the old
    anchors-always-first assumption must produce the EXACT SAME group
    ordering as before: T1, T1b, T2 GS, T3 GS. This is the critical
    no-regression case: every day whose anchors are actually first in the
    program (D1, D4, D6 in production) must see zero behavior change.
    """
    gen_db = gen_db_calibrated
    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    sk = lay_skeleton("D1 Upper Push", gen_db)
    ctx = resolve_context("D1 Upper Push", sk, gen_db, wk)
    res = assemble(_canned_for(sk, ctx), sk, ctx, gen_db)
    groups = sorted(res.session.groups, key=lambda g: g.order_index)
    layout = [(g.label, g.group_type.value) for g in groups]
    assert layout == [
        ("T1", "STRAIGHT"),
        ("T1b", "STRAIGHT"),
        ("T2 GS", "GIANT_SET"),
        ("T3 GS", "GIANT_SET"),
    ], f"D1's group order must be unchanged by the tier_order fix, got {layout}"


def test_d2_trailing_anchor_tier_sorts_last_by_true_tier_order(gen_db_calibrated):
    """Reproduces the real production bug (2026-08-14 fix).

    D2 Lower A has a NEW trailing anchor tier, "T4" (tier_role="anchor",
    TierKind.T1_STRAIGHT, Ab Trainer Decline Sit-up), seeded AFTER the two
    GIANT_SET tiers in Tier.tier_order (T1=1, T2 GS=2, T3 GS=3, T4=4). The
    old assembler put ALL anchors first regardless of true tier position, so
    T4's exercise rendered as the session's SECOND exercise (right after T1)
    instead of last. Confirmed live on production D2/D5 generation before
    this fix; this test is the D2 half of that reproduction (D5 mirrors it
    exactly with Ab Trainer Russian Twist -- see the twin case below).

    Correct order per Tier.tier_order: T1, T2 GS, T3 GS, T4 (trailing).
    """
    gen_db = gen_db_calibrated
    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    sk = lay_skeleton("D2 Lower A", gen_db)
    ctx = resolve_context("D2 Lower A", sk, gen_db, wk)
    res = assemble(_canned_for(sk, ctx), sk, ctx, gen_db)
    groups = sorted(res.session.groups, key=lambda g: g.order_index)
    layout = [(g.label, g.group_type.value) for g in groups]
    assert layout == [
        ("T1", "STRAIGHT"),
        ("T2 GS", "GIANT_SET"),
        ("T3 GS", "GIANT_SET"),
        ("T4", "STRAIGHT"),
    ], f"D2's trailing anchor tier (T4) must sort LAST by true tier_order, got {layout}"
    # The T4 group itself must contain the trailing anchor's exercise.
    t4 = groups[-1]
    assert _names_for_group(t4, gen_db) == ["Ab Trainer Decline Sit-up"]


def test_d5_trailing_anchor_tier_sorts_last_by_true_tier_order(gen_db_calibrated):
    """D5 Lower B twin of the D2 trailing-anchor-tier reproduction above.

    D5's T4 (Ab Trainer Russian Twist) mirrors D2's T4 shape exactly --
    same bug, same fix, different movement and program day.
    """
    gen_db = gen_db_calibrated
    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    sk = lay_skeleton("D5 Lower B", gen_db)
    ctx = resolve_context("D5 Lower B", sk, gen_db, wk)
    res = assemble(_canned_for(sk, ctx), sk, ctx, gen_db)
    groups = sorted(res.session.groups, key=lambda g: g.order_index)
    layout = [(g.label, g.group_type.value) for g in groups]
    assert layout == [
        ("T1", "STRAIGHT"),
        ("T2 GS", "GIANT_SET"),
        ("T3 GS", "GIANT_SET"),
        ("T4", "STRAIGHT"),
    ], f"D5's trailing anchor tier (T4) must sort LAST by true tier_order, got {layout}"
    t4 = groups[-1]
    assert _names_for_group(t4, gen_db) == ["Ab Trainer Russian Twist"]
