"""tests/test_slot_override_apply.py — Task 1 (note-apply REDESIGN): the
assembler applies an active LOAD/REPS SlotMovementOverride at prescription
time (Option C: prescription-only, never MovementState/current_load).

MOVEMENT overrides remain lay_skeleton's responsibility (see
test_slot_override_skeleton.py); this file covers the new override_type
axis (LOAD / REPS) and the assembler application point.

NO from __future__ import annotations (project-wide constraint).
gen_db / gen_db_calibrated fixtures auto-discovered from conftest.py.
"""
import importlib

from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from ironlog.generation.assembler import assemble
from ironlog.generation.context import resolve_context
from ironlog.generation.proposer import Selections, SlotSelection
from ironlog.generation.skeleton import lay_skeleton
from ironlog.models.enums import OverrideType, SetRole
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
    """The T1 anchor group's first working set (Bench Press [PB], STRAIGHT scheme)."""
    t1_group = next(g for g in res.session.groups if g.label == "T1")
    ex = t1_group.exercises[0]
    return next(ps for ps in ex.planned_sets if ps.set_role == SetRole.WORKING)


def _other_slot_loads(res):
    return [
        ps.target_load
        for g in res.session.groups if g.label != "T1"
        for e in g.exercises for ps in e.planned_sets
    ]


def _calibrated_staticpool_engine():
    """A fully seeded (103-movement library + Phase 1 program) + calibrated DB on
    a StaticPool engine so an in-memory SQLite DB is shared across threads — the
    TestClient runs the ASGI app in a worker thread, which a default-pool
    in-memory engine would not share. Mirrors the conftest gen_db + calibration
    setup, but thread-safe for HTTP tests."""
    from datetime import datetime

    from ironlog.generation.load_trust import load_field_for_mode
    from ironlog.generation.program_seed import seed_phase1_program

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    import ironlog.db as db
    db.engine = eng
    import ironlog.seed as seed
    importlib.reload(seed)
    seed.engine = eng
    seed.seed()
    now = datetime.utcnow()
    with Session(eng) as s:
        seed_phase1_program(s)
        states = {st.movement_id: st for st in s.exec(select(MovementState)).all()}
        for m in s.exec(select(Movement)).all():
            field = load_field_for_mode(m.progression_mode)
            if field is None:
                continue
            st = states.get(m.id) or MovementState(movement_id=m.id)
            if getattr(st, field) is None:
                setattr(st, field, 100.0 if field == "current_load" else 0.0)
            st.confirmed_at = now
            s.add(st)
        s.commit()
    return eng


def _http_client(eng):
    from ironlog.api.app import app, get_session

    def _override():
        with Session(eng) as s:
            yield s
    app.dependency_overrides[get_session] = _override
    return TestClient(app), app


def test_same_slot_same_type_apply_supersedes_prior_override(gen_db_calibrated):
    """Latest apply wins: applying LOAD +10 then LOAD +15 on the SAME slot must
    leave exactly ONE active override (the +15); the +10 is deactivated. Without
    the deactivate-prior fix both rows stay active and the assembler's unordered
    .first() silently prescribes the older +10. Covers apply_override directly +
    the /overrides list (one row) + the assembler outcome (engine_load + 15)."""
    from ironlog.notes.apply import apply_override

    gen_db = gen_db_calibrated
    te = gen_db.exec(select(TierExercise).where(TierExercise.slot_id == "d1_t1")).one()
    note = Note(text="bump bench")
    gen_db.add(note); gen_db.commit(); gen_db.refresh(note)

    engine_load = _t1_planned_set(_assemble(gen_db)).target_load
    assert engine_load is not None

    first = apply_override(note, te.id, "LOAD", gen_db, load_delta=10)
    second = apply_override(note, te.id, "LOAD", gen_db, load_delta=15)

    # Exactly one active override on the slot — the +15 — and the +10 is off.
    active = gen_db.exec(select(SlotMovementOverride).where(
        SlotMovementOverride.tier_exercise_id == te.id,
        SlotMovementOverride.override_type == OverrideType.LOAD,
        SlotMovementOverride.active == True)).all()  # noqa: E712
    assert len(active) == 1
    assert active[0].id == second.id
    assert active[0].load_delta == 15
    gen_db.refresh(first)
    assert first.active is False

    # The assembler prescribes the LATEST override (+15), not the superseded +10.
    assert _t1_planned_set(_assemble(gen_db)).target_load == engine_load + 15


def test_supersede_reflected_in_overrides_list_http():
    """The /overrides HTTP list shows just the one active (latest) override after
    a same-slot same-type re-apply — the superseded row is filtered out."""
    eng = _calibrated_staticpool_engine()
    client, app = _http_client(eng)
    try:
        with Session(eng) as s:
            te = s.exec(select(TierExercise).where(TierExercise.slot_id == "d1_t1")).one()
            note = Note(text="bump bench")
            s.add(note); s.commit(); s.refresh(note)
            te_id, note_id = te.id, note.id

        assert client.post(f"/notes/{note_id}/apply", json={
            "tier_exercise_id": te_id, "override_type": "LOAD", "load_delta": 10}).status_code == 200
        assert client.post(f"/notes/{note_id}/apply", json={
            "tier_exercise_id": te_id, "override_type": "LOAD", "load_delta": 15}).status_code == 200

        body = client.get("/overrides").json()
        assert len(body) == 1
        assert body[0]["override_type"] == "LOAD"
        assert body[0]["load_delta"] == 15
    finally:
        app.dependency_overrides.clear()


def test_http_load_apply_then_generate_seam():
    """Chained HTTP LOAD apply -> generate seam (LOAD equivalent of the MOVEMENT
    test_apply_then_generate_slot_emits_target_movement): POST a LOAD +10 apply
    via the endpoint, then run the real lay_skeleton -> resolve_context ->
    assemble path and assert the applied slot's prescribed target_load ==
    engine_load + 10, with every other slot unchanged."""
    eng = _calibrated_staticpool_engine()

    # Baseline engine load for the d1_t1 slot (no override yet).
    with Session(eng) as s:
        baseline = _assemble(s)
        engine_load = _t1_planned_set(baseline).target_load
        baseline_others = _other_slot_loads(baseline)
        assert engine_load is not None

        te = s.exec(select(TierExercise).where(TierExercise.slot_id == "d1_t1")).one()
        note = Note(text="bump bench +10")
        s.add(note); s.commit(); s.refresh(note)
        te_id, note_id = te.id, note.id

    client, app = _http_client(eng)
    try:
        resp = client.post(f"/notes/{note_id}/apply", json={
            "tier_exercise_id": te_id, "override_type": "LOAD", "load_delta": 10})
        assert resp.status_code == 200
    finally:
        app.dependency_overrides.clear()

    with Session(eng) as s:
        after = _assemble(s)
        assert _t1_planned_set(after).target_load == engine_load + 10, \
            "the HTTP-applied LOAD override must flow through generate to the prescription"
        assert _other_slot_loads(after) == baseline_others, \
            "a LOAD override on d1_t1 must not affect any other slot"


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
