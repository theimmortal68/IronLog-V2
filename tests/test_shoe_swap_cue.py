"""
test_shoe_swap_cue.py — S1/S2/S3: per-tier Tier.shoe propagates through the
assembler (AnchorSpec.shoe / SlotSpec.shoe) onto ExerciseGroup.shoe, and
surfaces in GroupOut.shoe (the /sessions/{id} serialization) — display-only
session-graph metadata for the client's mid-session shoe-swap cue. No
engine/current_load involvement (assembler tests already gate that
separately in test_generation_assembler.py / test_assembler_fidelity.py).

Anchors on the seeded D5 Lower B program day, which is the one day with a
mid-session swap: T1/T1b carry "Metcon 9"; T2 GS/T3 GS carry "Adipower II"
(swap moved from before-T3 to before-T2 on 2026-07-17 so it lands before
Bulgarian Split Squat, which needs the Adipower II heel).

NO from __future__ import annotations (project-wide constraint).
gen_db fixture auto-discovered from conftest.py.
"""
from ironlog.api.app import _make_proposer, _serialize_session, _week_keyer
from ironlog.generation.loop import generate_session
from ironlog.generation.skeleton import lay_skeleton


def _proposer_for(day_role, db):
    sk = lay_skeleton(day_role, db)
    return _make_proposer(sk)


def _shoes_by_label(gen_db, day_role):
    """Generate + commit + serialize a session for day_role; return
    {tier_label: {shoe values seen}} across all assembled ExerciseGroups."""
    out = generate_session(day_role, gen_db, _proposer_for(day_role, gen_db), _week_keyer)
    assert out.exhausted is False
    sess = out.assembled.session
    gen_db.add(sess)
    gen_db.commit()
    gen_db.refresh(sess)

    detail = _serialize_session(sess, gen_db)
    by_label = {}
    for g in detail.groups:
        by_label.setdefault(g.label, set()).add(g.shoe)
    return by_label


def test_d5_shoe_swap_metcon_to_adipower(gen_db):
    """D5 Lower B: T1 / T1b carry 'Metcon 9'; T2 GS (the mid-session swap
    target, moved before Bulgarian Split Squat) / T3 GS carry 'Adipower II'."""
    by_label = _shoes_by_label(gen_db, "D5 Lower B")
    assert by_label["T1"] == {"Metcon 9"}
    assert by_label["T1b"] == {"Metcon 9"}
    assert by_label["T2 GS"] == {"Adipower II"}
    assert by_label["T3 GS"] == {"Adipower II"}


def test_d1_all_tiers_metcon9_no_swap(gen_db):
    """D1 Upper Push has no mid-session swap: every tier is 'Metcon 9'."""
    by_label = _shoes_by_label(gen_db, "D1 Upper Push")
    for label, shoes in by_label.items():
        assert shoes == {"Metcon 9"}, f"tier {label!r} expected only Metcon 9, got {shoes}"
