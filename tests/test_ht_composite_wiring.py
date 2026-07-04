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
