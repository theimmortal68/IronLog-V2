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
from datetime import date, timedelta

from ironlog.engine.periodization_resolver import resolve_envelope
from ironlog.generation.context import (
    build_context_payload,
    build_candidate_menu,
    resolve_current_microcycle,
    resolve_context,
    should_invoke_llm,
)
from ironlog.generation.skeleton import SlotSpec, lay_skeleton
from ironlog.models.enums import NoteClass, Status
from ironlog.models.library import Movement, MovementState
from ironlog.models.periodization import (
    BodyCompState, BodyCompStateValue, DeloadState, Macrocycle, Mesocycle,
    MesocycleTemplate, Microcycle, MicrocycleLifecycleStatus, PlanStatus,
    RecoveryStatus, RecoveryStatusValue,
)
from ironlog.models.program import MesoRotation, TierExercise
from ironlog.models.session import Note
from sqlmodel import select


def _seed_active_periodization(
    db,
    *,
    planned_posture="PUSH",
    body_comp_state=BodyCompStateValue.CUT,
    recovery_status=RecoveryStatusValue.CAUTION,
    deload_active=False,
):
    today = date.today()
    macrocycle = Macrocycle(
        goal="test periodization",
        planned_start_date=today - timedelta(days=14),
        planned_end_date=today + timedelta(days=70),
        status=PlanStatus.ACTIVE,
    )
    template = MesocycleTemplate(
        name="test periodization template",
        postures=[planned_posture],
    )
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
        planned_posture=planned_posture,
    )
    db.add(microcycle)
    db.flush()
    rows = [
        BodyCompState(
            state=body_comp_state,
            effective_from=today - timedelta(days=30),
        ),
        RecoveryStatus(
            as_of_date=today,
            status=recovery_status,
        ),
    ]
    if deload_active:
        rows.append(DeloadState(
            microcycle_id=microcycle.id,
            active=True,
            triggered_at=today,
            trigger_reason="test deload",
        ))
    db.add_all(rows)
    db.commit()
    return {
        "macrocycle_id": macrocycle.id,
        "mesocycle_id": mesocycle.id,
        "microcycle_id": microcycle.id,
        "planned_posture": planned_posture,
        "body_comp_state": body_comp_state,
        "recovery_status": recovery_status,
        "deload_active": deload_active,
        "deload_trigger_reason": "test deload" if deload_active else None,
    }


def _expected_envelope(ids):
    return resolve_envelope(
        planned_posture=ids["planned_posture"],
        body_comp_state=ids["body_comp_state"],
        recovery_status=ids["recovery_status"],
        deload_active=ids["deload_active"],
        deload_trigger_reason=ids["deload_trigger_reason"],
    )


def _expected_training_posture_payload(ids, resolved):
    return {
        "microcycle_id": ids["microcycle_id"],
        "planned_posture": ids["planned_posture"],
        "body_comp_state": ids["body_comp_state"].value,
        "recovery_status": ids["recovery_status"].value,
        "deload_state": {
            "active": ids["deload_active"],
            "trigger_reason": ids["deload_trigger_reason"],
        },
        "resolved_envelope": {
            "rpe_cap": resolved.rpe_cap,
            "volume_multiplier": resolved.volume_multiplier,
            "progression_mode": resolved.progression_mode,
            "optional_work_eligible": resolved.optional_work_eligible,
            "trace": [
                {
                    "axis": step.axis,
                    "before": dict(step.before),
                    "after": dict(step.after),
                }
                for step in resolved.trace
            ],
        },
    }


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


def test_resolve_current_microcycle_tolerates_drifted_active_week(gen_db):
    today = date.today()
    template = MesocycleTemplate(name="drift lookup template", postures=["BUILD"])
    gen_db.add(template)
    gen_db.flush()
    mesocycle = Mesocycle(
        template_id=template.id,
        ordinal=1,
        planned_start_date=today - timedelta(days=21),
        planned_end_date=today - timedelta(days=1),
        status=PlanStatus.ACTIVE,
    )
    gen_db.add(mesocycle)
    gen_db.flush()
    drifted = Microcycle(
        mesocycle_id=mesocycle.id,
        ordinal=3,
        planned_start_date=today - timedelta(days=9),
        planned_end_date=today - timedelta(days=3),
        expected_sessions=5,
        lifecycle_status=MicrocycleLifecycleStatus.ACTIVE,
        planned_posture="BUILD",
    )
    completed = Microcycle(
        mesocycle_id=mesocycle.id,
        ordinal=2,
        planned_start_date=today - timedelta(days=16),
        planned_end_date=today - timedelta(days=10),
        expected_sessions=5,
        lifecycle_status=MicrocycleLifecycleStatus.COMPLETE,
        planned_posture="ESTABLISH",
    )
    gen_db.add_all([drifted, completed])
    gen_db.commit()

    assert resolve_current_microcycle(gen_db, today).id == drifted.id


def test_payload_shape_is_legacy_when_no_active_microcycle(gen_db):
    week_keyer = lambda d: (d.year, d.isocalendar()[1])
    sk = lay_skeleton("D1 Upper Push", gen_db)
    ctx = resolve_context("D1 Upper Push", sk, gen_db, week_keyer)
    payload = build_context_payload(ctx, sk)

    assert ctx.current_microcycle is None
    assert ctx.current_body_comp_state is None
    assert ctx.current_recovery_status is None
    assert ctx.resolved_envelope is None
    assert "training_posture" not in payload
    assert set(payload) == {
        "day_role",
        "phase",
        "phase_intent",
        "anchors",
        "slots",
        "owed",
        "recent_signatures",
        "weak_point_hints",
    }


def test_resolve_context_populates_current_periodization_envelope(gen_db):
    ids = _seed_active_periodization(gen_db)
    week_keyer = lambda d: (d.year, d.isocalendar()[1])
    sk = lay_skeleton("D1 Upper Push", gen_db)
    ctx = resolve_context("D1 Upper Push", sk, gen_db, week_keyer)

    expected = _expected_envelope(ids)

    assert ctx.current_microcycle.id == ids["microcycle_id"]
    assert ctx.current_body_comp_state.state == ids["body_comp_state"]
    assert ctx.current_recovery_status.status == ids["recovery_status"]
    assert ctx.current_deload_state is None
    assert ctx.resolved_envelope == expected


def test_context_payload_includes_training_posture_trace_when_microcycle_active(gen_db):
    ids = _seed_active_periodization(gen_db)
    week_keyer = lambda d: (d.year, d.isocalendar()[1])
    sk = lay_skeleton("D1 Upper Push", gen_db)
    ctx = resolve_context("D1 Upper Push", sk, gen_db, week_keyer)
    payload = build_context_payload(ctx, sk)

    expected = _expected_envelope(ids)

    assert payload["training_posture"] == _expected_training_posture_payload(
        ids,
        expected,
    )


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
    return the wrong TE's scheme.

    (2026-08-11, STAB maintenance-block redesign, Task 3: this test previously used
    D4's d4_t2a, which carried a meso-2 rotation to Pendlay Row. D4's T2 GS was fully
    turned over per the FINAL doc and no longer carries any meso rotation -- repointed
    to D5's d5_t2b, the program's other adaptive-slot ("free" role) meso-rotation
    example, unaffected by that task.

    2026-08-12, Task 4: D5's own T2 GS is now ALSO fully turned over (d5_t2b no
    longer exists) -- there is no real adaptive-role meso rotation left
    anywhere in the program. This test now inserts a synthetic, test-only
    MesoRotation row directly on D5's real d5_t2h slot (Matrix Machine Bulgarian
    Split Squat, "free" role), pointing at an already-seeded-but-otherwise-
    unwired movement ("Reverse Hyper - Single Leg [REV_HYPER]", preserving
    continuity with the old test's target), rather than reading a real
    production rotation. Not written to program_seed.py -- the FINAL doc
    calls for no meso rotation on this slot, this is purely a test fixture
    proving the resolution MECHANISM still works.
    """
    week_keyer = lambda d: (d.year, d.isocalendar()[1])

    te = gen_db.exec(
        select(TierExercise).where(TierExercise.slot_id == "d5_t2h")
    ).one()
    single_leg = gen_db.exec(
        select(Movement).where(Movement.base_name == "Reverse Hyper - Single Leg")
    ).one()
    assert single_leg.id != te.movement_id, "the meso-2 rotation must be a real swap"
    gen_db.add(MesoRotation(tier_exercise_id=te.id, meso_number=2, movement_id=single_leg.id))
    gen_db.commit()
    mr = gen_db.exec(
        select(MesoRotation).where(
            MesoRotation.tier_exercise_id == te.id,
            MesoRotation.meso_number == 2,
        )
    ).one()

    sk = lay_skeleton("D5 Lower B", gen_db, meso_number=2)
    slot = next(s for s in sk.adaptive_slots if s.slot_id == "d5_t2h")
    assert slot.program_movement_id == mr.movement_id, \
        "effective movement at meso 2 is the rotated one (differs from base)"

    ctx = resolve_context("D5 Lower B", sk, gen_db, week_keyer)
    rs = ctx.slot_rep_schemes.get("d5_t2h")
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
