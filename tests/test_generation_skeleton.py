"""
test_generation_skeleton.py — tests for the lay_skeleton function.

Verifies that the skeleton correctly reads the program definition, returns
anchor movement ids (with meso rotation applied for meso 2), and builds
adaptive slots with program_movement_id set on every slot.

NO from __future__ import annotations (project-wide constraint).
"""
from ironlog.generation.skeleton import lay_skeleton


def test_lay_skeleton_reads_program_anchor_and_slots(gen_db):
    sk = lay_skeleton("D1 Upper Push", gen_db, meso_number=1)
    assert sk.anchor_movement_ids, "T1 anchor placed from the program"
    assert sk.adaptive_slots, "adaptive slots read from the program"
    # every adaptive slot carries its program-prescribed movement (the prior)
    assert all(s.program_movement_id is not None for s in sk.adaptive_slots)


def test_meso_rotation_swaps_anchor_variant(gen_db):
    m1 = lay_skeleton("D2 Lower A", gen_db, meso_number=1)
    m2 = lay_skeleton("D2 Lower A", gen_db, meso_number=2)
    # D2-T1 is the Belt Squat <-> Back Squat rotation slot
    assert m1.anchor_movement_ids != m2.anchor_movement_ids


def test_skeleton_day_role_stored(gen_db):
    sk = lay_skeleton("D4 Upper Pull", gen_db, meso_number=1)
    assert sk.day_role == "D4 Upper Pull"


def test_d2_has_knee_slots_in_adaptive(gen_db):
    """D2 adaptive slots include knee slots for Nordic (TIB+NORDIC+KOT)."""
    sk = lay_skeleton("D2 Lower A", gen_db, meso_number=1)
    knee_slots = [s for s in sk.adaptive_slots if s.kind == "knee"]
    assert len(knee_slots) >= 3, "D2 must have at least 3 knee slots (NORDIC, KOT, TIB)"


def test_knee_slots_in_giant_tiers_remember_giant_tier(gen_db):
    """D5 T3 is a GIANT_SET tier even though most slots are knee_modality-tagged."""
    sk = lay_skeleton("D5 Lower B", gen_db, meso_number=1)
    slots = {s.slot_id: s for s in sk.adaptive_slots}

    assert slots["d5_t2c"].kind == "knee"
    assert slots["d5_t2c"].is_giant_tier is True
    for slot_id in ("d5_t3a", "d5_t3b", "d5_t3c", "d5_t3d"):
        assert slots[slot_id].is_giant_tier is True


def test_invalid_day_role_raises(gen_db):
    import pytest
    with pytest.raises(ValueError, match="No ProgramDay"):
        lay_skeleton("X Nonexistent", gen_db)


def test_d6_has_anchor(gen_db):
    """D6 Weak Points has a Pull-up anchor in GS1."""
    sk = lay_skeleton("D6 Weak Points", gen_db, meso_number=1)
    assert sk.anchor_movement_ids, "D6 must have a Pull-up anchor"
