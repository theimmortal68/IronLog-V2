"""tests/test_assembler_alternating_pair.py — spec 58: real T1/T1b alternating
pair support (Tier.paired_tier_id -> GroupType.ALT_PAIR).

Two layers of coverage, matching the spec's Verification section:
  * Unit tests directly against `planned_sets_in_group_order` (the ordering
    function) and `_pair_key_for_tier` (the skeleton-time pairing resolver) —
    no DB generation machinery needed, hand-built fixtures.
  * An integration test against D1's real, live Bench/Pendlay pair
    (migration 060) proving the whole lay_skeleton -> assemble path produces
    true alternating order, and a SlotMovementOverride test proving the
    override mechanism still works unmodified inside a PAIR group.

NO from __future__ import annotations (project-wide constraint).
"""
from datetime import date
from sqlmodel import Session as DBSession, SQLModel, create_engine, select

from ironlog.generation.assembler import assemble, planned_sets_in_group_order
from ironlog.generation.context import resolve_context
from ironlog.generation.proposer import Selections, SlotSelection
from ironlog.generation.skeleton import _pair_key_for_tier, lay_skeleton
from ironlog.models.enums import GroupType, Objective, OverrideType, Scheme, SetRole
from ironlog.models.library import Movement
from ironlog.models.program import (
    Program, ProgramDay, SlotMovementOverride, Tier, TierExercise, TierKind,
)
from ironlog.models.session import ExerciseGroup, PlannedExercise, PlannedSet, Session as IronSession
import ironlog.models  # register tables


def _engine():
    e = create_engine("sqlite://")
    SQLModel.metadata.create_all(e)
    return e


# ---------------------------------------------------------------------------
# Unit coverage: planned_sets_in_group_order
# ---------------------------------------------------------------------------

def _make_group_with_two_exercises(db, a_set_count, b_set_count, a_warmups=0, b_warmups=0):
    """Build a real ALT_PAIR ExerciseGroup with two PlannedExercises (A then B
    in exercises[]), A getting a_set_count working sets (+ a_warmups warmup
    sets), B getting b_set_count (+ b_warmups). Returns (group, movement_a,
    movement_b) after commit, so `group.exercises` is relationship-populated."""
    prog = Program(name="Phase 1", phase="P1", duration_weeks=4)
    db.add(prog); db.commit(); db.refresh(prog)
    day = ProgramDay(program_id=prog.id, day_index=0, day_role="D1 Upper Push")
    db.add(day); db.commit(); db.refresh(day)
    sess = IronSession(date=date(2026, 9, 1), day_role="D1 Upper Push", phase="CUT")
    db.add(sess); db.commit(); db.refresh(sess)

    mv_a = Movement(name="Movement A [PB]", base_name="Movement A")
    mv_b = Movement(name="Movement B [PB]", base_name="Movement B")
    db.add(mv_a); db.add(mv_b); db.commit(); db.refresh(mv_a); db.refresh(mv_b)

    group = ExerciseGroup(session_id=sess.id, order_index=0, group_type=GroupType.ALT_PAIR,
                           rounds=1, rest_seconds=90, label="B/A")
    db.add(group); db.commit(); db.refresh(group)

    def _add_exercise(movement, order_index, n_working, n_warmup):
        ex = PlannedExercise(group_id=group.id, movement_id=movement.id,
                              order_index=order_index, scheme=Scheme.STRAIGHT,
                              objective=Objective.PROGRESS)
        db.add(ex); db.commit(); db.refresh(ex)
        for i in range(n_warmup):
            db.add(PlannedSet(planned_exercise_id=ex.id, set_index=-n_warmup + i,
                               set_role=SetRole.WARMUP, is_warmup=True))
        for i in range(n_working):
            db.add(PlannedSet(planned_exercise_id=ex.id, set_index=i,
                               set_role=SetRole.WORKING, is_warmup=False,
                               target_load=100.0 + i))
        db.commit()
        return ex

    _add_exercise(mv_a, 0, a_set_count, a_warmups)
    _add_exercise(mv_b, 1, b_set_count, b_warmups)
    db.refresh(group)
    return group, mv_a, mv_b


def test_equal_set_counts_alternate_a1_b1_a2_b2():
    db = DBSession(_engine())
    group, mv_a, mv_b = _make_group_with_two_exercises(db, a_set_count=3, b_set_count=3)

    ordered = planned_sets_in_group_order(group)
    movement_sequence = [pe.movement_id for pe, ps in ordered]

    assert movement_sequence == [mv_a.id, mv_b.id, mv_a.id, mv_b.id, mv_a.id, mv_b.id], (
        f"equal set counts must alternate A1,B1,A2,B2,A3,B3 -- got {movement_sequence}"
    )
    # And each pair's set_index increments together (A1/B1 both index 0, etc.)
    set_indices = [ps.set_index for _, ps in ordered]
    assert set_indices == [0, 0, 1, 1, 2, 2]


def test_warmups_stay_first_then_alternate():
    db = DBSession(_engine())
    group, mv_a, mv_b = _make_group_with_two_exercises(
        db, a_set_count=3, b_set_count=3, a_warmups=1, b_warmups=1,
    )
    ordered = planned_sets_in_group_order(group)
    warmup_flags = [ps.is_warmup for _, ps in ordered]
    # Two warmups first (one per exercise), then 6 alternating working sets.
    assert warmup_flags == [True, True, False, False, False, False, False, False]
    movement_sequence_after_warmups = [pe.movement_id for pe, ps in ordered[2:]]
    assert movement_sequence_after_warmups == [mv_a.id, mv_b.id] * 3


def test_mismatched_set_counts_fall_back_to_straight_remainder():
    """A has 4 working sets, B has 2. Per the spec's documented fallback:
    alternate through min(N_a, N_b) rounds, then run the remainder of the
    longer exercise straight -- no sets silently dropped."""
    db = DBSession(_engine())
    group, mv_a, mv_b = _make_group_with_two_exercises(db, a_set_count=4, b_set_count=2)

    ordered = planned_sets_in_group_order(group)
    movement_sequence = [pe.movement_id for pe, ps in ordered]

    assert movement_sequence == [mv_a.id, mv_b.id, mv_a.id, mv_b.id, mv_a.id, mv_a.id], (
        f"mismatched counts: 2 alternating rounds then A's remaining 2 sets straight, "
        f"got {movement_sequence}"
    )
    # No set dropped: total ordered sets == 4 + 2.
    assert len(ordered) == 6


def test_all_sets_present_no_silent_drop_reversed_mismatch():
    """Same as above with B the longer side, to guard against an
    implementation that hardcodes 'A is always longer'."""
    db = DBSession(_engine())
    group, mv_a, mv_b = _make_group_with_two_exercises(db, a_set_count=2, b_set_count=5)

    ordered = planned_sets_in_group_order(group)
    assert len(ordered) == 7
    movement_sequence = [pe.movement_id for pe, ps in ordered]
    assert movement_sequence[:4] == [mv_a.id, mv_b.id, mv_a.id, mv_b.id]
    assert movement_sequence[4:] == [mv_b.id, mv_b.id, mv_b.id]


def test_straight_group_unaffected_by_alternating_logic():
    """A non-ALT_PAIR group (e.g. a giant set) must keep the old nested
    exercise/set order -- this spec adds a sibling group type, it does not
    touch GIANT_SET/STRAIGHT behavior."""
    db = DBSession(_engine())
    prog = Program(name="Phase 1", phase="P1", duration_weeks=4)
    db.add(prog); db.commit(); db.refresh(prog)
    day = ProgramDay(program_id=prog.id, day_index=0, day_role="D1 Upper Push")
    db.add(day); db.commit(); db.refresh(day)
    sess = IronSession(date=date(2026, 9, 1), day_role="D1 Upper Push", phase="CUT")
    db.add(sess); db.commit(); db.refresh(sess)
    mv = Movement(name="Solo [PB]", base_name="Solo")
    db.add(mv); db.commit(); db.refresh(mv)
    group = ExerciseGroup(session_id=sess.id, order_index=0, group_type=GroupType.STRAIGHT,
                           rounds=1, rest_seconds=120, label="T1")
    db.add(group); db.commit(); db.refresh(group)
    ex = PlannedExercise(group_id=group.id, movement_id=mv.id, order_index=0,
                          scheme=Scheme.STRAIGHT, objective=Objective.PROGRESS)
    db.add(ex); db.commit(); db.refresh(ex)
    for i in range(3):
        db.add(PlannedSet(planned_exercise_id=ex.id, set_index=i, set_role=SetRole.WORKING,
                           is_warmup=False, target_load=100.0))
    db.commit(); db.refresh(group)

    ordered = planned_sets_in_group_order(group)
    assert [ps.set_index for _, ps in ordered] == [0, 1, 2]


# ---------------------------------------------------------------------------
# Unit coverage: _pair_key_for_tier (skeleton-time pairing resolution)
# ---------------------------------------------------------------------------

def _make_program_day(db):
    prog = Program(name="Phase 1", phase="P1", duration_weeks=4)
    db.add(prog); db.commit(); db.refresh(prog)
    day = ProgramDay(program_id=prog.id, day_index=0, day_role="D1 Upper Push")
    db.add(day); db.commit(); db.refresh(day)
    return day


def test_missing_partner_tier_degrades_to_straight_no_crash():
    """paired_tier_id points at a tier id that doesn't exist -- must not
    crash lay_skeleton/_pair_key_for_tier; must degrade to an empty pair_key
    (equivalent to T1_STRAIGHT)."""
    db = DBSession(_engine())
    day = _make_program_day(db)
    mv = Movement(name="Bench Press [PB]", base_name="Bench Press")
    db.add(mv); db.commit(); db.refresh(mv)
    tier = Tier(program_day_id=day.id, tier_label="T1", tier_order=1,
                tier_kind=TierKind.PAIR, paired_tier_id=999999)
    db.add(tier); db.commit(); db.refresh(tier)
    te = TierExercise(tier_id=tier.id, slot_id="d1_t1", movement_id=mv.id,
                       exercise_order=1, tier_role="anchor")
    db.add(te); db.commit(); db.refresh(te)

    pair_key = _pair_key_for_tier(db, tier, [te])
    assert pair_key == ""


def test_partner_pointing_outside_program_day_degrades_to_straight():
    db = DBSession(_engine())
    day1 = _make_program_day(db)
    prog2 = Program(name="Phase 1b", phase="P1", duration_weeks=4)
    db.add(prog2); db.commit(); db.refresh(prog2)
    day2 = ProgramDay(program_id=prog2.id, day_index=1, day_role="D2 Lower A")
    db.add(day2); db.commit(); db.refresh(day2)

    mv = Movement(name="Bench Press [PB]", base_name="Bench Press")
    db.add(mv); db.commit(); db.refresh(mv)
    other_tier = Tier(program_day_id=day2.id, tier_label="T1", tier_order=1,
                       tier_kind=TierKind.PAIR)
    db.add(other_tier); db.commit(); db.refresh(other_tier)
    tier = Tier(program_day_id=day1.id, tier_label="T1", tier_order=1,
                tier_kind=TierKind.PAIR, paired_tier_id=other_tier.id)
    db.add(tier); db.commit(); db.refresh(tier)
    te = TierExercise(tier_id=tier.id, slot_id="d1_t1", movement_id=mv.id,
                       exercise_order=1, tier_role="anchor")
    db.add(te); db.commit(); db.refresh(te)

    assert _pair_key_for_tier(db, tier, [te]) == ""


def test_symmetric_pair_produces_stable_shared_key():
    db = DBSession(_engine())
    day = _make_program_day(db)
    mv_a = Movement(name="Bench Press [PB]", base_name="Bench Press")
    mv_b = Movement(name="Pendlay Row [OB]", base_name="Pendlay Row")
    db.add(mv_a); db.add(mv_b); db.commit(); db.refresh(mv_a); db.refresh(mv_b)

    t_bench = Tier(program_day_id=day.id, tier_label="T1", tier_order=2, tier_kind=TierKind.T1_STRAIGHT)
    db.add(t_bench); db.commit(); db.refresh(t_bench)
    t_row = Tier(program_day_id=day.id, tier_label="T1b", tier_order=1, tier_kind=TierKind.PAIR,
                 paired_tier_id=t_bench.id)
    db.add(t_row); db.commit(); db.refresh(t_row)
    t_bench.paired_tier_id = t_row.id
    db.add(t_bench); db.commit(); db.refresh(t_bench)

    te_bench = TierExercise(tier_id=t_bench.id, slot_id="d1_t1", movement_id=mv_a.id,
                             exercise_order=1, tier_role="anchor")
    te_row = TierExercise(tier_id=t_row.id, slot_id="d1_t1b", movement_id=mv_b.id,
                           exercise_order=1, tier_role="anchor")
    db.add(te_bench); db.add(te_row); db.commit()
    db.refresh(te_bench); db.refresh(te_row)

    key_from_bench = _pair_key_for_tier(db, t_bench, [te_bench])
    key_from_row = _pair_key_for_tier(db, t_row, [te_row])
    assert key_from_bench == key_from_row
    assert key_from_bench != ""


def test_pair_tier_with_multiple_exercises_raises():
    """A PAIR tier is a data error if it somehow carries more than one
    TierExercise -- must surface loudly, not silently pick one. Needs a
    real, valid partner (else _pair_key_for_tier returns "" before ever
    reaching the exercise-count check)."""
    db = DBSession(_engine())
    day = _make_program_day(db)
    mv_a = Movement(name="Bench Press [PB]", base_name="Bench Press")
    mv_b = Movement(name="Incline Bench [PB]", base_name="Incline Bench")
    mv_row = Movement(name="Pendlay Row [OB]", base_name="Pendlay Row")
    db.add(mv_a); db.add(mv_b); db.add(mv_row); db.commit()
    db.refresh(mv_a); db.refresh(mv_b); db.refresh(mv_row)

    tier = Tier(program_day_id=day.id, tier_label="T1b", tier_order=1, tier_kind=TierKind.PAIR)
    db.add(tier); db.commit(); db.refresh(tier)
    partner = Tier(program_day_id=day.id, tier_label="T1", tier_order=2,
                    tier_kind=TierKind.T1_STRAIGHT, paired_tier_id=tier.id)
    db.add(partner); db.commit(); db.refresh(partner)
    tier.paired_tier_id = partner.id
    db.add(tier); db.commit(); db.refresh(tier)
    te_partner = TierExercise(tier_id=partner.id, slot_id="d1_t1", movement_id=mv_row.id,
                               exercise_order=1, tier_role="anchor")
    db.add(te_partner); db.commit()

    te1 = TierExercise(tier_id=tier.id, slot_id="d1_t1b_a", movement_id=mv_a.id,
                        exercise_order=1, tier_role="anchor")
    te2 = TierExercise(tier_id=tier.id, slot_id="d1_t1b_b", movement_id=mv_b.id,
                        exercise_order=2, tier_role="anchor")
    db.add(te1); db.add(te2); db.commit()

    import pytest
    with pytest.raises(ValueError, match="must have exactly"):
        _pair_key_for_tier(db, tier, [te1, te2])


# ---------------------------------------------------------------------------
# Integration coverage: D1's real live pair (migration 060)
# ---------------------------------------------------------------------------

def _canned(sk, ctx):
    slots = []
    for s in sk.adaptive_slots:
        if s.kind in ("giant", "knee"):
            slots.append(SlotSelection(s.slot_id, ctx.candidate_menus[s.slot_id][0]))
    return Selections(ordering=[s.slot_id for s in slots], slots=slots, rationale="t")


def test_d1_real_pair_generates_alternating_order(gen_db_calibrated):
    """End-to-end: lay_skeleton -> resolve_context -> assemble on the real,
    live D1 program produces one ALT_PAIR group ('T1b/T1', Pendlay-first)
    whose planned_sets_in_group_order alternates Pendlay/Bench sets."""
    gen_db = gen_db_calibrated
    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    sk = lay_skeleton("D1 Upper Push", gen_db)
    ctx = resolve_context("D1 Upper Push", sk, gen_db, wk)
    res = assemble(_canned(sk, ctx), sk, ctx, gen_db)

    pair_group = next(g for g in res.session.groups if g.label == "T1b/T1")
    assert pair_group.group_type == GroupType.ALT_PAIR
    assert pair_group.rest_seconds == 90

    pendlay = gen_db.exec(select(Movement).where(Movement.name == "Pendlay Row - Narrow [OB]")).one()
    bench = gen_db.exec(select(Movement).where(Movement.name == "Bench Press [PB]")).one()

    ordered = planned_sets_in_group_order(pair_group)
    working = [(pe.movement_id, ps) for pe, ps in ordered if not ps.is_warmup]
    movement_sequence = [mid for mid, _ in working]
    assert movement_sequence[0] == pendlay.id, "Pendlay goes first per athlete preference"
    assert movement_sequence[1] == bench.id
    # Strictly alternating (no two same-movement sets adjacent) across all working sets.
    for i in range(len(movement_sequence) - 1):
        assert movement_sequence[i] != movement_sequence[i + 1], (
            f"working sets must strictly alternate, got {movement_sequence}"
        )


def test_slot_movement_override_inside_pair_group_still_resolves_and_alternates(gen_db_calibrated):
    """A SlotMovementOverride on the d1_t1 (Bench) slot inside the pair must
    still resolve the overridden movement (existing override mechanism,
    unmodified) AND the resulting group must still be a real alternating
    pair, not silently degrade to straight."""
    gen_db = gen_db_calibrated

    te = gen_db.exec(select(TierExercise).where(TierExercise.slot_id == "d1_t1")).one()
    overhead_press = Movement(name="Standing OHP Override Test [PB]", base_name="Standing OHP Override Test",
                               region="UPPER")
    gen_db.add(overhead_press); gen_db.commit(); gen_db.refresh(overhead_press)

    from ironlog.models.session import Note
    note = Note(text="swap bench for override test")
    gen_db.add(note); gen_db.commit(); gen_db.refresh(note)
    ov = SlotMovementOverride(
        tier_exercise_id=te.id, override_movement_id=overhead_press.id,
        source_note_id=note.id, active=True, override_type=OverrideType.MOVEMENT,
    )
    gen_db.add(ov); gen_db.commit()

    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    sk = lay_skeleton("D1 Upper Push", gen_db)
    ctx = resolve_context("D1 Upper Push", sk, gen_db, wk)
    res = assemble(_canned(sk, ctx), sk, ctx, gen_db)

    pair_group = next(g for g in res.session.groups if g.label == "T1b/T1")
    assert pair_group.group_type == GroupType.ALT_PAIR, (
        "a MOVEMENT override on one side of the pair must not degrade the group to STRAIGHT"
    )
    movement_ids_in_group = {pe.movement_id for pe in pair_group.exercises}
    assert overhead_press.id in movement_ids_in_group, (
        "the overridden movement must still appear inside the pair group"
    )

    ordered = planned_sets_in_group_order(pair_group)
    working_movement_sequence = [pe.movement_id for pe, ps in ordered if not ps.is_warmup]
    for i in range(len(working_movement_sequence) - 1):
        assert working_movement_sequence[i] != working_movement_sequence[i + 1], (
            "sets must still strictly alternate with an active override in the pair"
        )
