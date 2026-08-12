"""
test_program_seed_rotation_guard.py — rotation-path guard tests.

Pins the guard-bypass fix: any intended meso-2 rotation that doesn't resolve to
a library movement must RAISE ValueError (halt-and-flag), never silently drop.

This is the test that would have caught the original guard-bypass where d5_t1 and
d4_t3b meso-2 rotations were silently omitted instead of raising.

2026-08-12 (STAB maintenance-block redesign, Task 4): D5's own d5_t1 (RDL ->
Staggered RDL) and d5_t2b (Scout Reverse Hyper -> Reverse Hyper - Single Leg)
meso-2 rotations are BOTH removed entirely -- T1 anchor swapped to Kickstand
RDL [DB] (no meso rotation), and T2 GS fully turned over (old d5_t2a/b/c all
vacated). d2_t1 (Belt Squat -> Back Squat) is now the ONLY real meso-2
rotation left anywhere in the program -- an ANCHOR-role example. There is no
longer any real ADAPTIVE/"free"-role meso rotation program-wide (D4's
d4_t2a -> Pendlay Row was already retired in Task 3, without replacement).
Tests below repointed accordingly; see test_generation_context.py,
test_slot_override_skeleton.py, and test_generation_fallback.py for the
separate adaptive-role fallout, which now uses a synthetic test-only
MesoRotation row (no real production example survives to test against).

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
    Monkeypatches PROGRAM_TO_LIBRARY so a real meso-2 rotation target maps to
    a bogus library name — seed_phase1_program must raise when it hits the
    rotation call.

    2026-08-12 (Task 4): D5's Staggered RDL rotation no longer exists (see
    module docstring) -- repointed to "Back Squat", d2_t1's Belt Squat ->
    Back Squat rotation, the ONLY real meso-2 rotation left program-wide.
    """
    import ironlog.generation.program_seed as ps
    # Inject a bogus rotation mapping that won't resolve in the library.
    bad_map = dict(ps.PROGRAM_TO_LIBRARY)
    bad_map["Back Squat"] = "__BOGUS_NOT_IN_LIBRARY__"
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
    """d2_t1 meso-2 → Back Squat. The ONLY real meso-2 rotation left
    program-wide as of Task 4 (2026-08-12) -- see module docstring for why
    D5's own d5_t1/d5_t2b rotations no longer exist.
    """
    from ironlog.models.library import Movement
    from ironlog.models.program import MesoRotation, TierExercise

    d2_t1 = gen_db.exec(
        select(TierExercise).where(TierExercise.slot_id == "d2_t1")
    ).one()

    back_squat = gen_db.exec(
        select(Movement).where(Movement.base_name == "Back Squat")
    ).one()

    def _meso2(te_id):
        return gen_db.exec(
            select(MesoRotation).where(
                MesoRotation.tier_exercise_id == te_id,
                MesoRotation.meso_number == 2,
            )
        ).all()

    d2_mrs = _meso2(d2_t1.id)
    assert len(d2_mrs) == 1, "d2_t1 must have exactly one meso-2 rotation row"
    assert d2_mrs[0].movement_id == back_squat.id, \
        "d2_t1 meso-2 must resolve to Back Squat"


# ---------------------------------------------------------------------------
# Skeleton fires the new rotations
# ---------------------------------------------------------------------------

def test_d2_lower_a_meso2_anchor_is_back_squat(gen_db):
    """lay_skeleton D2 Lower A meso-2 fires the rotation → Back Squat as anchor.
    Confirms the MesoRotation row is wired correctly and lay_skeleton picks it
    up. 2026-08-12 (Task 4): repointed from D5's now-removed Staggered RDL
    rotation to D2's Belt Squat -> Back Squat rotation, the only one left.
    """
    from ironlog.generation.skeleton import lay_skeleton
    from ironlog.models.library import Movement

    sk = lay_skeleton("D2 Lower A", gen_db, meso_number=2)
    back_squat = gen_db.exec(
        select(Movement).where(Movement.base_name == "Back Squat")
    ).one()
    assert back_squat.id in sk.anchor_movement_ids, \
        "D2 Lower A meso-2 anchor must be Back Squat (meso rotation fired)"


def test_mesorotation_has_rep_override_fields(gen_db):
    from ironlog.models.program import MesoRotation
    cols = MesoRotation.__table__.columns.keys()
    assert "rep_low" in cols and "rep_high" in cols
