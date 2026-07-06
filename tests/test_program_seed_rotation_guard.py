"""
test_program_seed_rotation_guard.py — rotation-path guard tests.

Pins the guard-bypass fix: any intended meso-2 rotation that doesn't resolve to
a library movement must RAISE ValueError (halt-and-flag), never silently drop.

This is the test that would have caught the original guard-bypass where d5_t1 and
d4_t3b meso-2 rotations were silently omitted instead of raising.

NO from __future__ import annotations (project-wide constraint).
"""
import importlib

import pytest
from sqlmodel import Session, create_engine, select

import ironlog.db as db


# ---------------------------------------------------------------------------
# Helper: fresh seeded DB
# ---------------------------------------------------------------------------

def _make_seeded_session(eng):
    """Seed library into eng, return an open Session (caller must close)."""
    db.engine = eng
    import ironlog.seed as seed
    importlib.reload(seed)
    seed.engine = eng
    seed.seed()
    return Session(eng)


# ---------------------------------------------------------------------------
# GUARD: unresolved meso-2 ROTATION must raise, not silently drop
# ---------------------------------------------------------------------------

def test_unresolved_meso_rotation_raises(monkeypatch):
    """ROTATION PATH GUARD: an unresolved meso-2 rotation must raise ValueError
    (halt-and-flag), not silently drop.

    This is the test that would have caught the guard-bypass where d5_t1 meso-2
    (Staggered RDL) was silently omitted rather than going through _resolve.
    Monkeypatches PROGRAM_TO_LIBRARY so Staggered RDL maps to a bogus library
    name — seed_phase1_program must raise when it hits the rotation call.
    """
    import ironlog.generation.program_seed as ps
    # Inject a bogus rotation mapping that won't resolve in the library.
    bad_map = dict(ps.PROGRAM_TO_LIBRARY)
    bad_map["Staggered RDL"] = "__BOGUS_NOT_IN_LIBRARY__"
    monkeypatch.setattr(ps, "PROGRAM_TO_LIBRARY", bad_map)

    eng = create_engine("sqlite://")
    s = _make_seeded_session(eng)
    with s:
        with pytest.raises(ValueError, match="HALT-AND-FLAG"):
            ps.seed_phase1_program(s)


# ---------------------------------------------------------------------------
# New MesoRotation rows: present and resolved correctly
# ---------------------------------------------------------------------------

def test_new_meso_rotations_exist_and_resolve(gen_db):
    """d5_t1 meso-2 → Staggered RDL; d5_t2b meso-2 → Reverse Hyper - Single Leg
    (with a 12–15 rep override). Both rows must exist and reference the correct
    library movement.

    (Post-YAML-reconciliation: the old d4_t3b → Single-Arm DB Row meso rotation
    was removed — Single-Arm DB Row is now a standalone T2 slot (d4_t2b), and the
    single-leg Reverse Hyper became a real distinct-movement meso-2 rotation.)
    """
    from ironlog.models.library import Movement
    from ironlog.models.program import MesoRotation, TierExercise

    d5_t1 = gen_db.exec(
        select(TierExercise).where(TierExercise.slot_id == "d5_t1")
    ).one()
    d5_t2b = gen_db.exec(
        select(TierExercise).where(TierExercise.slot_id == "d5_t2b")
    ).one()

    staggered = gen_db.exec(
        select(Movement).where(Movement.base_name == "Staggered RDL")
    ).one()
    single_leg = gen_db.exec(
        select(Movement).where(Movement.base_name == "Reverse Hyper - Single Leg")
    ).one()

    def _meso2(te_id):
        return gen_db.exec(
            select(MesoRotation).where(
                MesoRotation.tier_exercise_id == te_id,
                MesoRotation.meso_number == 2,
            )
        ).all()

    d5_mrs = _meso2(d5_t1.id)
    assert len(d5_mrs) == 1, "d5_t1 must have exactly one meso-2 rotation row"
    assert d5_mrs[0].movement_id == staggered.id, \
        "d5_t1 meso-2 must resolve to Staggered RDL"

    d5_t2b_mrs = _meso2(d5_t2b.id)
    assert len(d5_t2b_mrs) == 1, "d5_t2b must have exactly one meso-2 rotation row"
    assert d5_t2b_mrs[0].movement_id == single_leg.id, \
        "d5_t2b meso-2 must resolve to Reverse Hyper - Single Leg"
    assert (d5_t2b_mrs[0].rep_low, d5_t2b_mrs[0].rep_high) == (12, 15), \
        "d5_t2b meso-2 must carry the 12–15 rep override"


# ---------------------------------------------------------------------------
# Skeleton fires the new rotations
# ---------------------------------------------------------------------------

def test_d5_lower_b_meso2_anchor_is_staggered_rdl(gen_db):
    """lay_skeleton D5 Lower B meso-2 fires the rotation → Staggered RDL as anchor.
    Confirms the MesoRotation row is wired correctly and lay_skeleton picks it up.
    """
    from ironlog.generation.skeleton import lay_skeleton
    from ironlog.models.library import Movement

    sk = lay_skeleton("D5 Lower B", gen_db, meso_number=2)
    staggered = gen_db.exec(
        select(Movement).where(Movement.base_name == "Staggered RDL")
    ).one()
    assert staggered.id in sk.anchor_movement_ids, \
        "D5 Lower B meso-2 anchor must be Staggered RDL (meso rotation fired)"


def test_mesorotation_has_rep_override_fields(gen_db):
    from ironlog.models.program import MesoRotation
    cols = MesoRotation.__table__.columns.keys()
    assert "rep_low" in cols and "rep_high" in cols
