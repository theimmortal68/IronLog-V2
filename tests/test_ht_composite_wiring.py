"""test_ht_composite_wiring.py — Task 4: wire ht_next_setup into generation (Option-C).

Named tests:
  1. test_assembled_ht_carries_plates_and_config — assembler HT slot prescribes
     plates + band_config via ht_next_setup; target_felt_peak matches config_peak.
  2. test_commit_persists_ht_setup — commit_session (approval-time) is the sole
     writer of ht_plates/ht_band_config, mirroring current_load (Fork 7c).
  3. test_rule_driven_composite_ht_at_cap_is_noop — _rule_driven's at-cap branch
     must NOT hand a COMPOSITE/HIP_THRUST movement to _rep_ladder; the setup
     progression is assembler-resolved, not rep-ladder-resolved.
  4. test_rule_driven_non_composite_at_cap_still_hands_to_rep_ladder — regression
     guard: the pre-existing at-cap -> REP_LADDER handoff is untouched for a
     movement that is NOT COMPOSITE/HIP_THRUST (test_progression_special.py's
     "Hip Thrust" fixture uses plain defaults, so this is exercised there too;
     this test makes the boundary explicit).

NO from __future__ import annotations (project-wide constraint).
gen_db / gen_db_calibrated fixtures auto-discovered from conftest.py.
"""
from sqlmodel import select

from ironlog.engine.advance import advance, SessionPerf
from ironlog.generation.assembler import assemble
from ironlog.generation.context import resolve_context
from ironlog.generation.fallback import program_selections
from ironlog.generation.loop import commit_session
from ironlog.generation.skeleton import lay_skeleton
from ironlog.models.enums import LiftCategory, ProgressionMode, ProgressionRule
from ironlog.models.library import BandPair, Movement, MovementState
from ironlog.models.session import PlannedSet


def _first_ht_working_set(assembled) -> PlannedSet:
    """The first PlannedSet carrying a non-None target_plates (the HT slot)."""
    for g in assembled.session.groups:
        for ex in g.exercises:
            for ps in ex.planned_sets:
                if ps.target_plates is not None:
                    return ps
    raise AssertionError("no HT (target_plates-bearing) set found in assembled session")


def _planned_sets_for_movement(assembled, movement_id):
    """All PlannedSets belonging to the given movement_id, regardless of
    whether target_plates is populated (unlike _first_ht_working_set, which
    specifically searches for a populated HT slot)."""
    out = []
    for g in assembled.session.groups:
        for ex in g.exercises:
            if ex.movement_id == movement_id:
                out.extend(ex.planned_sets)
    return out


# ---------------------------------------------------------------------------
# 1. assembler HT slot
# ---------------------------------------------------------------------------

def test_assembled_ht_carries_plates_and_config(gen_db_calibrated):
    gen_db = gen_db_calibrated
    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    sk = lay_skeleton("D2 Lower A", gen_db)
    ctx = resolve_context("D2 Lower A", sk, gen_db, wk)
    sel = program_selections(sk)

    assembled = assemble(sel, sk, ctx, gen_db)

    ht_set = _first_ht_working_set(assembled)
    assert ht_set.target_plates is not None
    assert ht_set.band_config is not None
    assert ht_set.target_felt_peak is not None

    peak_by_id = {b.id: b.peak_lb for b in gen_db.exec(select(BandPair)).all()}
    assert ht_set.target_felt_peak == ht_set.target_plates + sum(
        peak_by_id[b] for b in ht_set.band_config
    )

    # The prospective HT setup is recorded in-memory, matching the assembled set.
    ht_mv = gen_db.exec(
        select(Movement).where(Movement.name == "Hip Thrust [HIP_THRUST]")
    ).one()
    assert assembled.prospective_ht_setups[ht_mv.id] == (
        ht_set.target_plates, ht_set.band_config,
    )


def test_uncalibrated_ht_does_not_fabricate_plates(gen_db):
    """A fully uncalibrated HT movement (no current_load, no ht_plates) must be
    assembled needs-calibration-style: no target_plates/band_config/target_felt_peak
    fabricated, and no entry recorded in prospective_ht_setups. Mirrors the
    non-HT needs-calibration path's "never fabricate a floor" guarantee."""
    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    sk = lay_skeleton("D2 Lower A", gen_db)
    ctx = resolve_context("D2 Lower A", sk, gen_db, wk)
    sel = program_selections(sk)

    assembled = assemble(sel, sk, ctx, gen_db)

    ht_mv = gen_db.exec(
        select(Movement).where(Movement.name == "Hip Thrust [HIP_THRUST]")
    ).one()

    ht_sets = _planned_sets_for_movement(assembled, ht_mv.id)
    assert ht_sets, "expected the HT movement to be assembled into at least one set"
    for ps in ht_sets:
        assert ps.target_plates is None
        assert not ps.band_config
        assert ps.target_felt_peak is None

    assert ht_mv.id not in assembled.prospective_ht_setups


def test_assemble_does_not_write_ht_setup(gen_db_calibrated):
    """Mirrors the existing current_load no-write gate: assemble() must NOT
    write ht_plates/ht_band_config — only commit_session may (Option-C)."""
    gen_db = gen_db_calibrated
    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    sk = lay_skeleton("D2 Lower A", gen_db)
    ctx = resolve_context("D2 Lower A", sk, gen_db, wk)
    sel = program_selections(sk)

    before = {s.movement_id: (s.ht_plates, s.ht_band_config)
              for s in gen_db.exec(select(MovementState)).all()}
    assemble(sel, sk, ctx, gen_db)
    after = {s.movement_id: (s.ht_plates, s.ht_band_config)
             for s in gen_db.exec(select(MovementState)).all()}
    assert before == after, "assemble must NOT write ht_plates/ht_band_config"


# ---------------------------------------------------------------------------
# 2. commit_session persists the HT setup (Option-C sole writer)
# ---------------------------------------------------------------------------

def test_commit_persists_ht_setup(gen_db_calibrated):
    gen_db = gen_db_calibrated
    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    sk = lay_skeleton("D2 Lower A", gen_db)
    ctx = resolve_context("D2 Lower A", sk, gen_db, wk)
    sel = program_selections(sk)
    assembled = assemble(sel, sk, ctx, gen_db)

    ht_mv = gen_db.exec(
        select(Movement).where(Movement.name == "Hip Thrust [HIP_THRUST]")
    ).one()
    expected_plates, expected_config = assembled.prospective_ht_setups[ht_mv.id]

    commit_session(
        assembled, gen_db,
        approval_mode="auto", prompt={},
        selections_dict={}, clamps=[], repairs=[], fallback_used=False,
    )

    st = gen_db.exec(
        select(MovementState).where(MovementState.movement_id == ht_mv.id)
    ).one()
    assert st.ht_plates == expected_plates
    assert st.ht_band_config == expected_config


# ---------------------------------------------------------------------------
# 3. _rule_driven at-cap guard for COMPOSITE / HIP_THRUST movements
# ---------------------------------------------------------------------------

def test_rule_driven_composite_ht_at_cap_is_noop():
    mv = Movement(
        name="Hip Thrust [HIP_THRUST]", base_name="Hip Thrust",
        lift_category=LiftCategory.HIP_THRUST,
        progression_mode=ProgressionMode.COMPOSITE,
        increment_ladder=[5], cap=220, rep_ladder=[8, 10, 12],
    )
    st = MovementState(movement_id=1, day_id="d2", current_load=220,
                       current_increment_tier=0, consecutive_advance_count=2)
    r = advance(
        ProgressionRule.RULE_DRIVEN, st,
        SessionPerf(hit_target=True, max_rpe=8.0, all_sides_cleared=True,
                   session_performed=True),
        mv, 1,
    )
    assert r.advanced is False
    assert r.active_rule == ProgressionRule.RULE_DRIVEN.value
    assert r.new_rep_target is None, "must NOT hand off to REP_LADDER"
    assert r.consecutive_advance_count == 2, "streak must be preserved, not reset"


def test_rule_driven_composite_ht_below_cap_is_noop():
    # Below cap, COMPOSITE/HIP_THRUST movements must still no-op — the assembler
    # (ht_next_setup), not this rule's tier-advance path, drives HT progression
    # regardless of cap. One progression path, not two.
    mv = Movement(
        name="Hip Thrust [HIP_THRUST]", base_name="Hip Thrust",
        lift_category=LiftCategory.HIP_THRUST,
        progression_mode=ProgressionMode.COMPOSITE,
        increment_ladder=[5], cap=220, rep_ladder=[8, 10, 12],
    )
    st = MovementState(movement_id=1, day_id="d2", current_load=100,
                       current_increment_tier=0, consecutive_advance_count=2)
    r = advance(
        ProgressionRule.RULE_DRIVEN, st,
        SessionPerf(hit_target=True, max_rpe=8.0, all_sides_cleared=True,
                   session_performed=True),
        mv, 1,
    )
    assert r.advanced is False
    assert r.active_rule == ProgressionRule.RULE_DRIVEN.value
    assert r.new_tier is None, "must NOT bump the tier"
    assert r.consecutive_advance_count == st.consecutive_advance_count == 2, \
        "streak must be preserved, not reset"


def test_rule_driven_non_composite_at_cap_still_hands_to_rep_ladder():
    # Explicit boundary check: a movement that is neither COMPOSITE nor
    # HIP_THRUST keeps the pre-existing at-cap -> REP_LADDER handoff.
    mv = Movement(
        name="Some Ladder Movement", base_name="Some Ladder Movement",
        lift_category=LiftCategory.NONE, progression_mode=ProgressionMode.LADDER,
        increment_ladder=[5], cap=220, rep_ladder=[8, 10, 12],
    )
    st = MovementState(movement_id=2, day_id="d2", current_load=220,
                       current_increment_tier=0)
    r = advance(
        ProgressionRule.RULE_DRIVEN, st,
        SessionPerf(hit_target=True, max_rpe=8.0, all_sides_cleared=True,
                   session_performed=True),
        mv, 1,
    )
    assert r.active_rule == ProgressionRule.REP_LADDER.value


# ---------------------------------------------------------------------------
# 4. wear-gate: a retired BandPair must never be prescribed (Task 1)
# ---------------------------------------------------------------------------

def test_assembler_does_not_prescribe_a_retired_band(gen_db_calibrated):
    # NOTE: the vanilla gen_db_calibrated fixture leaves the HT movement with
    # no ht_plates/ht_band_config (only current_load), so the assembler's
    # raise-plates shortcut never even engages a band (band_config == []).
    # To exercise the wear-gate we force an at-cap band setup first -- 202
    # plates + Orange (bottom 220, peak 247), mirroring
    # test_add_band_when_plates_capped in test_ht_next_setup.py -- so a
    # reconfigure is guaranteed, then retire Orange and confirm it's excluded.
    gen_db = gen_db_calibrated
    ht_mv = gen_db.exec(
        select(Movement).where(Movement.name == "Hip Thrust [HIP_THRUST]")
    ).one()
    orange = gen_db.exec(
        select(BandPair).where(BandPair.label == "#0 Orange")
    ).one()

    st = gen_db.exec(
        select(MovementState).where(MovementState.movement_id == ht_mv.id)
    ).one()
    st.ht_plates = 202.0
    st.ht_band_config = [orange.id]
    gen_db.add(st)
    gen_db.commit()

    # Retire Orange -- the band the HT movement is currently using.
    orange.usable = False
    gen_db.add(orange)
    gen_db.commit()

    wk = lambda d: (d.year, d.isocalendar()[1])  # noqa: E731
    sk = lay_skeleton("D2 Lower A", gen_db)
    ctx = resolve_context("D2 Lower A", sk, gen_db, wk)
    sel = program_selections(sk)
    assembled = assemble(sel, sk, ctx, gen_db)

    ht_set = _first_ht_working_set(assembled)
    assert ht_set.band_config is not None
    assert orange.id not in ht_set.band_config          # retired Orange is never prescribed
