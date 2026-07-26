"""
test_generation_skeleton.py — tests for the lay_skeleton function.

Verifies that the skeleton correctly reads the program definition, returns
anchor movement ids (with meso rotation applied for meso 2), and builds
adaptive slots with program_movement_id set on every slot.

NO from __future__ import annotations (project-wide constraint).
"""
from ironlog.generation.skeleton import lay_skeleton
from ironlog.models.library import Movement
from sqlmodel import select


def _movement_id(db, name):
    return db.exec(select(Movement.id).where(Movement.name == name)).one()


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


def test_d6_t1_anchor_preserved_and_gs1_anchor_folds_into_giant_tier(gen_db):
    """D6 keeps T1 Dips as anchor and folds GS1's Pull-up into the giant tier."""
    sk = lay_skeleton("D6 Weak Points", gen_db, meso_number=1)
    slots = {s.slot_id: s for s in sk.adaptive_slots}

    assert sk.anchor_movement_ids == [_movement_id(gen_db, "Dips [TOWER + TUBES]")]
    assert [(m.tier_label, m.rep_low, m.rep_high) for m in sk.anchor_meta] == [("T1", 6, 8)]
    # 2026-07-26: D6's Pull-up slot switched to "Wide-Grip Pull-up [TOWER]"
    # (athlete directive, same grip switch as D4) -- "Pull-up [TOWER + TUBES]"
    # is now D1-only.
    assert slots["d6_g1a"].program_movement_id == _movement_id(gen_db, "Wide-Grip Pull-up [TOWER]")
    assert slots["d6_g1a"].is_giant_tier is True
    assert slots["d6_g1a"].group_key == "GS1"
    assert slots["d6_g1a"].kind == "accessory"
    assert slots["d6_g1a"].tier_role == "anchor"


def test_d6_gs1_slots_share_giant_tier_group(gen_db):
    """All D6 GS1 slots assemble into the same GIANT_SET tier group."""
    sk = lay_skeleton("D6 Weak Points", gen_db, meso_number=1)
    slots = {s.slot_id: s for s in sk.adaptive_slots}

    for slot_id in ("d6_g1a", "d6_g1c", "d6_g1d"):
        assert slots[slot_id].is_giant_tier is True
        assert slots[slot_id].group_key == "GS1"


def test_d6_gs1_anchor_kind_is_accessory(gen_db):
    """GS1's fixed Pull-up anchor is not candidate-menu eligible."""
    sk = lay_skeleton("D6 Weak Points", gen_db, meso_number=1)
    slots = {s.slot_id: s for s in sk.adaptive_slots}

    assert slots["d6_g1a"].kind == "accessory"


def test_d6_gs1_slot_order_preserves_pullup_first(gen_db):
    """D6 GS1 keeps Pull-up first in the shared giant set."""
    sk = lay_skeleton("D6 Weak Points", gen_db, meso_number=1)
    gs1_slots = [s.slot_id for s in sk.adaptive_slots if s.group_key == "GS1"]

    assert gs1_slots == ["d6_g1a", "d6_g1c", "d6_g1d"]
