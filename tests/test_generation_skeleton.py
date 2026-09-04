"""
test_generation_skeleton.py — tests for the lay_skeleton function.

Verifies that the skeleton correctly reads the program definition, returns
anchor movement ids (with meso rotation applied for meso 2), and builds
adaptive slots with program_movement_id set on every slot.

NO from __future__ import annotations (project-wide constraint).
"""
from ironlog.generation.skeleton import lay_skeleton, week_parity
from ironlog.models.library import Movement
from sqlmodel import select

from ironlog.models.program import MicrocycleParityRotation, MesoRotation, TierExercise, Tier, ProgramDay
from datetime import date


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

    2026-08-20 (athlete directive): d5_t2e VACATED -- D5 no longer has any
    Nordic/knee-tagged slot in T2 GS at all, replaced by fresh d5_t2i
    (Lying Leg Curl [GHR + Ares], no knee_modality -- hamstring curl isn't
    part of the knee taxonomy). d5_t2i is still a GIANT_SET member (kind
    "giant", not "knee"), which is what this test actually verifies now --
    T3's knee-tagged slots (d5_t3b/f) are unaffected and still covered.
    """
    sk = lay_skeleton("D5 Lower B", gen_db, meso_number=1)
    slots = {s.slot_id: s for s in sk.adaptive_slots}

    assert slots["d5_t2i"].kind == "giant"
    assert slots["d5_t2i"].is_giant_tier is True
    for slot_id in ("d5_t3b", "d5_t3e", "d5_t3f", "d5_t3g"):
        assert slots[slot_id].is_giant_tier is True


def test_invalid_day_role_raises(gen_db):
    import pytest
    with pytest.raises(ValueError, match="No ProgramDay"):
        lay_skeleton("X Nonexistent", gen_db)


def test_d6_has_t1_anchor_and_gs1_pullup_folds_into_giant_tier(gen_db):
    """2026-08-12 (STAB maintenance-block redesign, Task 5): D6's standalone
    T1 tier (Dips) is ELIMINATED ENTIRELY -- the FINAL doc's D6 has no
    standalone T1 at all; Dips folds back into GS1 alongside the pull-up and
    the new close-grip bench.

    2026-09-03: code/yaml catch-up to already-live migrations 048-057 --
    D6's T1 tier is BACK, but as a real T1_STRAIGHT anchor (Standing OHP
    [PB], low-fatigue specificity/lockout work), not the old GS1-folded
    Dips. sk.anchor_movement_ids now includes Standing OHP's movement id
    (a real Skeleton anchor, since tier_kind=T1_STRAIGHT != GIANT_SET).
    GS1's Pull-up still keeps tier_role="anchor" but still folds into the
    giant tier (tier_role="anchor" only becomes a real Skeleton anchor when
    tier_kind != GIANT_SET, per skeleton.py's lay_skeleton)."""
    sk = lay_skeleton("D6 Weak Points", gen_db, meso_number=1)
    slots = {s.slot_id: s for s in sk.adaptive_slots}

    assert sk.anchor_movement_ids == [_movement_id(gen_db, "Standing OHP [PB]")]
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
    (d6_g1d) removed.

    2026-08-16 (athlete directive, revised): Dips (d6_g1e) and Close-Grip
    Bench Camber-14 (d6_g1f) directly traded GS placement -- Dips + CG
    Press are both heavy compound presses, rotating them together causes
    interference. GS1 is now Pull-up / CG Press / Rear Delt Extension
    (d6_g1a/d6_g1f/d6_g2f); Dips moved to GS2.

    2026-09-03: code/yaml catch-up to already-live migrations 048-057 --
    CG Press drops out of D6 entirely (T1 tier restored as Standing OHP,
    not CG Press). GS1's 3rd member is now Cable Serratus Punch/Reach [FT]
    (fresh slot "d6_g1h", migration 057) -- "d6_g1f" is VACATED. GS1 is now
    Pull-up / Cable Serratus Punch-Reach / Rear Delt Extension
    (d6_g1a/d6_g1h/d6_g2f)."""
    sk = lay_skeleton("D6 Weak Points", gen_db, meso_number=1)
    slots = {s.slot_id: s for s in sk.adaptive_slots}

    for slot_id in ("d6_g1a", "d6_g1h", "d6_g2f"):
        assert slots[slot_id].is_giant_tier is True
        assert slots[slot_id].group_key == "GS1"


def test_d6_gs1_anchor_kind_is_accessory(gen_db):
    """GS1's fixed Pull-up anchor is not candidate-menu eligible."""
    sk = lay_skeleton("D6 Weak Points", gen_db, meso_number=1)
    slots = {s.slot_id: s for s in sk.adaptive_slots}

    assert slots["d6_g1a"].kind == "accessory"


def test_d6_gs1_slot_order_preserves_pullup_first(gen_db):
    """D6 GS1 keeps Pull-up first in the shared giant set, followed by
    Dips then Close-Grip Bench Camber-14 (2026-08-12, Task 5).

    2026-08-16 (revised): Dips (d6_g1e) and Close-Grip Bench Camber-14
    (d6_g1f) directly traded GS placement -- see test_d6_gs1_slots_share_
    giant_tier_group's updated docstring for why. GS1 order is now
    Pull-up / CG Press / Rear Delt Extension.

    2026-09-03: code/yaml catch-up to already-live migrations 048-057 --
    CG Press (d6_g1f) drops out of D6 entirely; GS1 order is now
    Pull-up / Cable Serratus Punch-Reach / Rear Delt Extension."""
    sk = lay_skeleton("D6 Weak Points", gen_db, meso_number=1)
    gs1_slots = [s.slot_id for s in sk.adaptive_slots if s.group_key == "GS1"]

    assert gs1_slots == ["d6_g1a", "d6_g1h", "d6_g2f"]


def test_lay_skeleton_microcycle_ordinal_parity_resolution(gen_db):
    """
    Assert lay_skeleton(..., microcycle_ordinal=<ordinal>) resolves the correct A/B movement,
    and two dates in different calendar weeks resolve to the same movement if microcycle_ordinal is the same.
    """
    # Find a TierExercise to use
    prog_day = gen_db.exec(select(ProgramDay).where(ProgramDay.day_role == "D1 Upper Push")).first()
    tier = gen_db.exec(select(Tier).where(Tier.program_day_id == prog_day.id)).first()
    te = gen_db.exec(select(TierExercise).where(TierExercise.tier_id == tier.id)).first()

    movement_a = 9991
    movement_b = 9992

    # Insert MicrocycleParityRotation
    gen_db.add(MicrocycleParityRotation(
        tier_exercise_id=te.id,
        week_parity="A",
        movement_id=movement_a
    ))
    gen_db.add(MicrocycleParityRotation(
        tier_exercise_id=te.id,
        week_parity="B",
        movement_id=movement_b
    ))
    gen_db.commit()

    # Even ordinal -> A
    sk_even = lay_skeleton("D1 Upper Push", gen_db, microcycle_ordinal=2)
    sk_even_slots = {s.slot_id: s for s in sk_even.adaptive_slots}
    # T1 might be anchor, check anchor or adaptive
    resolved_a = False
    if movement_a in sk_even.anchor_movement_ids:
        resolved_a = True
    elif te.slot_id in sk_even_slots and sk_even_slots[te.slot_id].program_movement_id == movement_a:
        resolved_a = True
    assert resolved_a, "Even ordinal should resolve to A"

    # Odd ordinal -> B
    sk_odd = lay_skeleton("D1 Upper Push", gen_db, microcycle_ordinal=3)
    sk_odd_slots = {s.slot_id: s for s in sk_odd.adaptive_slots}
    resolved_b = False
    if movement_b in sk_odd.anchor_movement_ids:
        resolved_b = True
    elif te.slot_id in sk_odd_slots and sk_odd_slots[te.slot_id].program_movement_id == movement_b:
        resolved_b = True
    assert resolved_b, "Odd ordinal should resolve to B"

    # Assert different dates but same ordinal resolve identically. Pick dates
    # that genuinely differ under the OLD calendar-based week_parity (proven
    # by the assert below) -- otherwise this test would pass even if
    # _resolve_slot regressed back to calendar keying, silently losing
    # coverage for the exact bug this spec fixes (Fable review, High finding).
    date1 = date(2026, 1, 5)   # week_parity == "A" (the epoch Monday itself)
    date2 = date(2026, 1, 12)  # week_parity == "B" (one week later)
    assert week_parity(date1) != week_parity(date2), (
        "test setup invalid: these dates must differ under the OLD "
        "calendar-based parity for this to be a meaningful regression guard"
    )
    sk_date1 = lay_skeleton("D1 Upper Push", gen_db, microcycle_ordinal=4, as_of=date1)
    sk_date2 = lay_skeleton("D1 Upper Push", gen_db, microcycle_ordinal=4, as_of=date2)
    assert sk_date1.anchor_movement_ids == sk_date2.anchor_movement_ids
    assert [s.program_movement_id for s in sk_date1.adaptive_slots] == [s.program_movement_id for s in sk_date2.adaptive_slots]


def test_meso_rotation_mesocycle_id_lookup(gen_db):
    """
    Assert MesoRotation resolution via mesocycle_id returns the same result
    as the equivalent legacy meso_number lookup.
    """
    prog_day = gen_db.exec(select(ProgramDay).where(ProgramDay.day_role == "D1 Upper Push")).first()
    tier = gen_db.exec(select(Tier).where(Tier.program_day_id == prog_day.id)).first()
    te = gen_db.exec(select(TierExercise).where(TierExercise.tier_id == tier.id)).first()
    
    mesocycle_id = 99
    movement_override = 9993
    
    # We will insert a MesoRotation with BOTH mesocycle_id and meso_number
    gen_db.add(MesoRotation(
        tier_exercise_id=te.id,
        meso_number=7,
        mesocycle_id=mesocycle_id,
        movement_id=movement_override
    ))
    gen_db.commit()

    sk_legacy = lay_skeleton("D1 Upper Push", gen_db, meso_number=7)
    sk_new = lay_skeleton("D1 Upper Push", gen_db, mesocycle_id=mesocycle_id)

    assert sk_legacy.anchor_movement_ids == sk_new.anchor_movement_ids
    assert [s.program_movement_id for s in sk_legacy.adaptive_slots] == [s.program_movement_id for s in sk_new.adaptive_slots]
    
    # And check the movement actually overrode
    resolved_override = False
    sk_new_slots = {s.slot_id: s for s in sk_new.adaptive_slots}
    if movement_override in sk_new.anchor_movement_ids:
        resolved_override = True
    elif te.slot_id in sk_new_slots and sk_new_slots[te.slot_id].program_movement_id == movement_override:
        resolved_override = True
    assert resolved_override, "MesoRotation should override the movement"

