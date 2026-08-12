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
    """D5 T2/T3 are GIANT_SET tiers even though several slots are
    knee_modality-tagged.

    2026-08-12 (STAB maintenance-block redesign, Task 4): D5's T2 GS knee
    slot moved from the old d5_t2c (Assisted Nordic, dropped) to d5_t2e
    (Nordic Curl Max [Ares], knee_modality=NORDIC). T3 GS slots repointed
    from the dropped d5_t3a/c/d to the new d5_t3e/f/g (d5_t3b unchanged).
    """
    sk = lay_skeleton("D5 Lower B", gen_db, meso_number=1)
    slots = {s.slot_id: s for s in sk.adaptive_slots}

    assert slots["d5_t2e"].kind == "knee"
    assert slots["d5_t2e"].is_giant_tier is True
    for slot_id in ("d5_t3b", "d5_t3e", "d5_t3f", "d5_t3g"):
        assert slots[slot_id].is_giant_tier is True


def test_invalid_day_role_raises(gen_db):
    import pytest
    with pytest.raises(ValueError, match="No ProgramDay"):
        lay_skeleton("X Nonexistent", gen_db)


def test_d6_has_no_t1_anchor_and_gs1_anchor_folds_into_giant_tier(gen_db):
    """2026-08-12 (STAB maintenance-block redesign, Task 5): D6's standalone
    T1 tier (Dips) is ELIMINATED ENTIRELY -- the FINAL doc's D6 has no
    standalone T1 at all; Dips folds back into GS1 alongside the pull-up and
    the new close-grip bench. D6 now has ZERO true anchor tiers (GS1's
    Pull-up keeps tier_role="anchor" but folds into the giant tier, same as
    before -- tier_role="anchor" only becomes a real Skeleton anchor when
    tier_kind != GIANT_SET, per skeleton.py's lay_skeleton)."""
    sk = lay_skeleton("D6 Weak Points", gen_db, meso_number=1)
    slots = {s.slot_id: s for s in sk.adaptive_slots}

    assert sk.anchor_movement_ids == []
    assert sk.anchor_meta == []
    # 2026-08-12 (STAB redesign fix, post-Task-5): d6_g1a repointed to the
    # new ASSISTED "Wide-Grip Pull-up [TOWER + TUBES]" -- see
    # docs/superpowers/specs/2026-08-10-stab-maintenance-block-redesign-
    # design.md §5. Task 5 initially left this on the old "Pull-up - Neutral
    # Grip (Paused) [TOWER]" after confirming the exact repoint-target name
    # had never existed in the repo -- correct check, wrong conclusion (the
    # design doc calls for CREATING that movement, not skipping the repoint).
    assert slots["d6_g1a"].program_movement_id == _movement_id(gen_db, "Wide-Grip Pull-up [TOWER + TUBES]")
    assert slots["d6_g1a"].is_giant_tier is True
    assert slots["d6_g1a"].group_key == "GS1"
    assert slots["d6_g1a"].kind == "accessory"
    assert slots["d6_g1a"].tier_role == "anchor"


def test_d6_gs1_slots_share_giant_tier_group(gen_db):
    """All D6 GS1 slots assemble into the same GIANT_SET tier group.

    2026-08-12 (Task 5): GS1 is now Pull-up / Dips / Close-Grip Bench
    Camber-14 (d6_g1a/e/f) -- Hip Thrust (d6_g1c) and Cable Bicep Curl
    (d6_g1d) removed."""
    sk = lay_skeleton("D6 Weak Points", gen_db, meso_number=1)
    slots = {s.slot_id: s for s in sk.adaptive_slots}

    for slot_id in ("d6_g1a", "d6_g1e", "d6_g1f"):
        assert slots[slot_id].is_giant_tier is True
        assert slots[slot_id].group_key == "GS1"


def test_d6_gs1_anchor_kind_is_accessory(gen_db):
    """GS1's fixed Pull-up anchor is not candidate-menu eligible."""
    sk = lay_skeleton("D6 Weak Points", gen_db, meso_number=1)
    slots = {s.slot_id: s for s in sk.adaptive_slots}

    assert slots["d6_g1a"].kind == "accessory"


def test_d6_gs1_slot_order_preserves_pullup_first(gen_db):
    """D6 GS1 keeps Pull-up first in the shared giant set, followed by
    Dips then Close-Grip Bench Camber-14 (2026-08-12, Task 5)."""
    sk = lay_skeleton("D6 Weak Points", gen_db, meso_number=1)
    gs1_slots = [s.slot_id for s in sk.adaptive_slots if s.group_key == "GS1"]

    assert gs1_slots == ["d6_g1a", "d6_g1e", "d6_g1f"]
