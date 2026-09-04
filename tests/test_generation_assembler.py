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
from datetime import date, timedelta

from ironlog.engine.periodization_resolver import resolve_envelope
from ironlog.generation.assembler import assemble
from ironlog.generation.context import resolve_context
from ironlog.generation.proposer import Selections, SlotSelection
from ironlog.generation.repair import build_validation_context
from ironlog.generation.skeleton import lay_skeleton
from ironlog.models.enums import GroupType, Objective, SetRole
from ironlog.models.library import Movement
from ironlog.models.library import MovementState
from ironlog.models.periodization import (
    BodyCompState, BodyCompStateValue, DeloadState, Macrocycle, Mesocycle,
    MesocycleTemplate, Microcycle, MicrocycleLifecycleStatus, PlanStatus,
    RecoveryStatus, RecoveryStatusValue,
)
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


def _seed_active_deload_periodization(db):
    today = date.today()
    macrocycle = Macrocycle(
        goal="test deload",
        planned_start_date=today - timedelta(days=14),
        planned_end_date=today + timedelta(days=70),
        status=PlanStatus.ACTIVE,
    )
    template = MesocycleTemplate(name="test deload template", postures=["PUSH"])
    db.add_all([macrocycle, template])
    db.flush()
    mesocycle = Mesocycle(
        template_id=template.id,
        macrocycle_id=macrocycle.id,
        ordinal=1,
        planned_start_date=today - timedelta(days=7),
        planned_end_date=today + timedelta(days=21),
        status=PlanStatus.ACTIVE,
    )
    db.add(mesocycle)
    db.flush()
    microcycle = Microcycle(
        mesocycle_id=mesocycle.id,
        ordinal=1,
        planned_start_date=today - timedelta(days=2),
        planned_end_date=today + timedelta(days=4),
        expected_sessions=5,
        lifecycle_status=MicrocycleLifecycleStatus.ACTIVE,
        planned_posture="PUSH",
    )
    db.add(microcycle)
    db.flush()
    db.add_all([
        BodyCompState(
            state=BodyCompStateValue.CUT,
            effective_from=today - timedelta(days=30),
        ),
        RecoveryStatus(
            as_of_date=today,
            status=RecoveryStatusValue.POOR,
        ),
        DeloadState(
            microcycle_id=microcycle.id,
            active=True,
            triggered_at=today,
            trigger_reason="test deload",
        ),
    ])
    db.commit()
    return {
        "macrocycle_id": macrocycle.id,
        "mesocycle_id": mesocycle.id,
        "microcycle_id": microcycle.id,
        "planned_posture": "PUSH",
        "body_comp_state": BodyCompStateValue.CUT,
        "recovery_status": RecoveryStatusValue.POOR,
        "deload_active": True,
        "deload_trigger_reason": "test deload",
    }


def _expected_envelope(ids):
    return resolve_envelope(
        planned_posture=ids["planned_posture"],
        body_comp_state=ids["body_comp_state"],
        recovery_status=ids["recovery_status"],
        deload_active=ids["deload_active"],
        deload_trigger_reason=ids["deload_trigger_reason"],
    )


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


def test_active_periodization_snapshot_and_knobs_drive_generated_session(gen_db_calibrated):
    gen_db = gen_db_calibrated
    ids = _seed_active_deload_periodization(gen_db)
    bench = gen_db.exec(
        select(Movement).where(Movement.name == "Bench Press [PB]")
    ).one()
    progress_pullup = gen_db.exec(
        select(Movement).where(Movement.name == "Pull-up [TOWER + TUBES]")
    ).one()
    assert progress_pullup.objective_override == Objective.PROGRESS
    bench_state = gen_db.exec(
        select(MovementState).where(MovementState.movement_id == bench.id)
    ).first()
    bench_state.pending_load_delta = 5.0
    gen_db.add(bench_state)
    gen_db.commit()

    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    sk = lay_skeleton("D1 Upper Push", gen_db)
    ctx = resolve_context("D1 Upper Push", sk, gen_db, wk)
    expected = _expected_envelope(ids)
    assert ctx.resolved_envelope == expected
    validation_context = build_validation_context(ctx, gen_db)
    assert validation_context.phase_hard_cap == expected.rpe_cap
    selections = _canned_for(sk, ctx)
    for slot in selections.slots:
        if slot.slot_id == "d1_t3a":
            slot.movement_id = progress_pullup.id
            break
    assembled = assemble(selections, sk, ctx, gen_db)

    snapshot = assembled.session.prescription_snapshot
    assert snapshot == {
        "macrocycle_id": ids["macrocycle_id"],
        "mesocycle_id": ids["mesocycle_id"],
        "microcycle_id": ids["microcycle_id"],
        "planned_posture": ids["planned_posture"],
        "body_comp_state": ids["body_comp_state"].value,
        "recovery_status": ids["recovery_status"].value,
        "deload_state": {
            "active": ids["deload_active"],
            "trigger_reason": ids["deload_trigger_reason"],
        },
        "resolved_envelope": {
            "rpe_cap": expected.rpe_cap,
            "volume_multiplier": expected.volume_multiplier,
            "progression_mode": expected.progression_mode,
            "optional_work_eligible": expected.optional_work_eligible,
        },
        "resolver_policy_version": "periodization_resolver.v1",
    }

    exercises = [
        exercise
        for group in assembled.session.groups
        for exercise in group.exercises
    ]
    bench_exercise = next(ex for ex in exercises if ex.movement_id == bench.id)
    bench_work = [ps for ps in bench_exercise.planned_sets if not ps.is_warmup]
    bench_meta = next(meta for meta in sk.anchor_meta if meta.tier_label == "T1")
    assert expected.rpe_cap < ctx.phase_policy.rpe_band_high
    assert expected.rpe_cap < bench_meta.rpe_cap
    assert len([ps for ps in bench_work if ps.set_role == SetRole.WORKING]) == max(
        1,
        round(3 * expected.volume_multiplier),
    )
    assert {ps.target_rpe for ps in bench_work} == {expected.rpe_cap}
    assert {ps.target_load for ps in bench_work} == {100.0}
    assert assembled.prospective_current_loads[bench.id] == 100.0

    pullup_exercise = next(ex for ex in exercises if ex.movement_id == progress_pullup.id)
    assert pullup_exercise.objective == Objective.MAINTAIN


def test_assemble_prescription_snapshot_none_without_current_periodization(gen_db_calibrated):
    gen_db = gen_db_calibrated
    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    sk = lay_skeleton("D1 Upper Push", gen_db)
    ctx = resolve_context("D1 Upper Push", sk, gen_db, wk)

    assert ctx.current_microcycle is None
    assert ctx.current_body_comp_state is None
    assert ctx.current_recovery_status is None
    assert ctx.resolved_envelope is None

    res = assemble(_canned_for(sk, ctx), sk, ctx, gen_db)

    assert res.session.prescription_snapshot is None


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
    # 2026-09-01: T1/T1b now form a real alternating pair (migration 060,
    # spec 58) -- they merge into ONE group labeled "T1b/T1" (Pendlay-first,
    # matching tier_order after the pairing migration) instead of two
    # separate STRAIGHT groups. See test_assembler_alternating_pair.py for
    # the label-format assertion (pair_key's lower-tier_order side first).
    gen_db = gen_db_calibrated
    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    sk = lay_skeleton("D1 Upper Push", gen_db)
    ctx = resolve_context("D1 Upper Push", sk, gen_db, wk)
    res = assemble(_canned_for(sk, ctx), sk, ctx, gen_db)
    groups = sorted(res.session.groups, key=lambda g: g.order_index)
    labels = [g.label for g in groups]
    assert labels == ["T1b/T1", "T2 GS", "T3 GS"], (
        f"group labels must mirror the seeded Tier.tier_label order, got {labels}"
    )
    assert groups[0].group_type.value == "ALT_PAIR" and groups[0].label == "T1b/T1"
    assert groups[1].group_type.value == "GIANT_SET" and groups[1].label == "T2 GS"


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
    # 2026-09-03 (athlete directive, C1): Sagittal Lat Pulldown traded in
    # from T3 GS (fresh slot "d1_t2h"); Lateral Raise traded out to T3 GS.
    group = _giant_group_by_label(res.session, "T2 GS")
    assert _names_for_group(group, gen_db) == [
        "Stryker Pad Seated OHP [DB]",
        "Better Fly Sagittal Lat Pulldown [FT]",
        "Matrix Machine Preacher Curl [EZ]",
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
    # 2026-08-20: Nordic Curl Max [Ares] (d5_t2e) VACATED -- D5 no longer
    # has a Nordic slot at all, replaced by Lying Leg Curl [GHR + Ares]
    # (fresh slot d5_t2i). D2's Nordic slot now carries the program's sole
    # weekly Nordic exposure (rotating A/B via WeekParityRotation).
    # 2026-08-22 (athlete directive): full T2 GS/T3 GS/T4 restructure into
    # two 4-member giant sets GS1/GS2 -- T4 (Ab Trainer Russian Twist)
    # folds into GS1. All members keep their prior slot_id/config, just
    # relocated (see program_seed.py's _seed_d5 for the full history).
    gs1 = _giant_group_by_label(res.session, "GS1")
    assert _names_for_group(gs1, gen_db) == [
        "Lying Leg Curl [GHR + Ares]",
        "Ab Trainer Russian Twist",
        "Hybrid Board Tib Raise [D5]",
        "Better Fly Hip Adduction [FT]",
    ]

    gs2 = _giant_group_by_label(res.session, "GS2")
    assert _names_for_group(gs2, gen_db) == [
        "Matrix Machine Bulgarian Split Squat",
        "Reverse Nordic Curl [GHR]",
        "Hybrid Board Calf Raise [D5]",
        "Better Fly Kickback [FT]",
    ]


def test_d1_group_order_is_unaffected_by_tier_order_fix(gen_db_calibrated):
    """No-regression check for the tier_order fix (2026-08-14).

    D1 Upper Push's anchor tiers (T1, T1b) are genuinely first in
    Tier.tier_order -- sorting groups by TRUE tier_order instead of the old
    anchors-always-first assumption must produce the EXACT SAME group
    ordering as before: T1, T1b, T2 GS, T3 GS. This is the critical
    no-regression case: every day whose anchors are actually first in the
    program (D1, D4, D6 in production) must see zero behavior change.

    2026-09-01: T1/T1b now form a real alternating pair (migration 060,
    spec 58) -- merged into one ALT_PAIR group ("T1b/T1"), so the group
    COUNT changes (4 -> 3), but the underlying tier_order (Pendlay=1,
    Bench=2) still governs the merged group's position and label-side
    ordering -- this is still a no-regression case for the tier_order fix
    itself, just re-expressed for the new merged shape.
    """
    gen_db = gen_db_calibrated
    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    sk = lay_skeleton("D1 Upper Push", gen_db)
    ctx = resolve_context("D1 Upper Push", sk, gen_db, wk)
    res = assemble(_canned_for(sk, ctx), sk, ctx, gen_db)
    groups = sorted(res.session.groups, key=lambda g: g.order_index)
    layout = [(g.label, g.group_type.value) for g in groups]
    assert layout == [
        ("T1b/T1", "ALT_PAIR"),
        ("T2 GS", "GIANT_SET"),
        ("T3 GS", "GIANT_SET"),
    ], f"D1's group order must be unchanged by the tier_order fix, got {layout}"


def test_d2_t2_giant_set_has_three_members_no_trailing_t4(gen_db_calibrated):
    """D2's former trailing-anchor-tier reproduction case, updated 2026-08-19.

    D2 Lower A used to have a NEW trailing anchor tier, "T4" (tier_role=
    "anchor", TierKind.T1_STRAIGHT, Ab Trainer Decline Sit-up), seeded AFTER
    the two GIANT_SET tiers -- see test_d5_trailing_anchor_tier_sorts_last_
    by_true_tier_order below for the still-live twin of that original
    tier_order bug reproduction. As of 2026-08-19 (athlete directive), D2's
    T4 was merged into T2 GS as a 3rd giant-set member and no longer exists
    as its own tier -- this test now asserts that merged shape instead.

    Same day, later: Ab Trainer Decline Sit-up direct-traded into T3 GS to
    deconflict bench-attachment contention. First attempt paired this with
    Hybrid Board Tib Raise [D2] moving T3->T2, but that reintroduced a shoe
    conflict (Tib Raise needs T3's flat shoe); revised same day to trade
    Hybrid Board Calf Raise [D2] into T2 instead (less shoe-sensitive) and
    keep Tib Raise in T3 -- T2 GS's 3rd-slot member updated below.

    2026-08-20: d2_t2e (Nordic Curl Max) now rotates A/B via
    WeekParityRotation -- pin as_of to a fixed "A"-week date (the epoch
    Monday itself) so this test's 2nd-slot expectation is deterministic
    instead of depending on which real-world week it happens to run in.
    """
    gen_db = gen_db_calibrated
    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    sk = lay_skeleton("D2 Lower A", gen_db, as_of=date(2026, 1, 5))
    ctx = resolve_context("D2 Lower A", sk, gen_db, wk)
    res = assemble(_canned_for(sk, ctx), sk, ctx, gen_db)
    groups = sorted(res.session.groups, key=lambda g: g.order_index)
    layout = [(g.label, g.group_type.value) for g in groups]
    assert layout == [
        ("T1", "STRAIGHT"),
        ("T2 GS", "GIANT_SET"),
        ("T3 GS", "GIANT_SET"),
    ], f"D2 must have no standalone T4 tier after the T4->T2 GS merge, got {layout}"
    t2 = groups[1]
    assert _names_for_group(t2, gen_db) == [
        "Matrix Machine Sissy Squat",
        "Nordic Curl Max [Apex]",
        "Hybrid Board Calf Raise [D2]",
    ]


def test_d5_giant_sets_have_no_trailing_anchor_tier(gen_db_calibrated):
    """D5 Lower B twin of the D2 trailing-anchor-tier reproduction above,
    updated 2026-08-22.

    D5 used to have a trailing T4 anchor tier (Ab Trainer Russian Twist)
    that reproduced the real tier_order sort bug this test file guards
    against (same class as D2's twin case above). As of 2026-08-22
    (athlete directive), D5's T4 was folded into GS1 as a 3rd-slot member
    and no longer exists as its own tier -- this test now asserts that
    merged shape instead (mirrors D2's equivalent update).
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
        ("GS1", "GIANT_SET"),
        ("GS2", "GIANT_SET"),
    ], f"D5 must have no standalone T4 tier after the T4->GS1 merge, got {layout}"
    gs1 = groups[1]
    assert "Ab Trainer Russian Twist" in _names_for_group(gs1, gen_db)
