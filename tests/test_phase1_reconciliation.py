"""Tests for the Phase-1 seed reconciliation (Task 2 of the in-gym-logging chunk).

Covers: literal rep targets on selected TierExercises, Tier.rest_seconds across all
9 tier_label buckets, Movement.scheme flips (Belt Squat + RDL -> STRAIGHT;
Bench Press -> STRAIGHT via the Task 2 review fix), the matching
TierExercise.scheme sync for d1_t1/d2_t1/d5_t1, Movement.unilateral flags on
8 movements, and rpe_cap=6.0 on the D6 Reverse-Hyper-Recovery TierExercise
(slot_id="d6_g2a" post-YAML-reconciliation; moved GS3 -> GS2).

Uses the real `gen_db` fixture from tests/conftest.py (in-memory DB seeded via
seed.seed() + seed_phase1_program()). NO from __future__ import annotations
(project-wide constraint).
"""
from sqlmodel import select

from ironlog.models.enums import Scheme
from ironlog.models.library import Movement
from ironlog.models.program import ProgramDay, Tier, TierExercise

# slot_id -> (rep_low, rep_high) — post-YAML-reconciliation final values.
# 2026-08-10 (STAB maintenance-block redesign): D1's block below reflects
# the reconciled-to-already-executed-Wk1-reality structure -- T1/T1b rep
# range dropped 6-8 -> 4-6; T2 GS fully turned over to
# d1_t2f/d1_t2g/d1_t2e (Stryker Pad Seated OHP / Matrix Machine Preacher
# Curl / Better Fly Standing Lateral Raise), so d1_t2b/d1_t2c no longer
# exist; d1_t3a (Wide-Grip Pull-up) drops to 4-6 reps; d1_t3b (Cross-Body
# Lateral Raise) is gone; T4 GS tier removed entirely, so d1_t4a/d1_t4c are
# gone and Ab Wheel Rollout (was d1_t4b, 8-8) relocated into T3 GS as
# d1_t3d at 8-12.
CHANGED_REP_TARGETS = {
    "d1_t1": (4, 6),
    "d1_t2a": (4, 6),
    "d1_t2f": (8, 12),
    "d1_t2g": (8, 12),
    "d1_t2e": (10, 15),
    "d1_t3a": (4, 6),
    # 2026-08-13: d1_t3c (Lat Prayer) vacated -- replaced by fresh d1_t3e
    # (Better Fly Sagittal Lat Pulldown [FT], athlete directive), same
    # rep target (8-12).
    "d1_t3e": (8, 12),
    "d1_t3d": (8, 12),
    # 2026-08-11 (STAB maintenance-block redesign, Task 2): d2_t1 (Belt Squat
    # anchor) moves from UNCHANGED_REP_TARGETS to here -- T1's rep range
    # dropped 6-8 -> 4-6, matching every other T1 primary in this redesign.
    "d2_t1": (4, 6),
    "d2_t3a": (8, 12),
    # 2026-08-12 (Task 4/D5 plan-owner addendum): d2_t3b (Cable Tib Raise,
    # 10-15) REMOVED -- slot_id vacated, replaced by fresh d2_t3e (Hybrid
    # Board Tib Raise [D2], never-reassign-slot_id).
    "d2_t3e": (10, 15),
    # 2026-08-11 (STAB maintenance-block redesign, Task 3): D4 reconciled to
    # the FINAL doc's real D4 session. d4_t1_btn_ohp (new T1 anchor, Seated
    # BTN OHP [PB]) replaces the old d4_t1_ohp -- 4-6 reps. d4_t1 (Better
    # Fly Lat Pulldown, reused slot_id) stays 6-8, coincidentally unchanged
    # from Wide-Grip Pull-up's own rep range. T2 GS fully turned over:
    # d4_t2a/d4_t2b (Meadows Row / Single-Arm DB Row) no longer exist,
    # replaced by d4_t2d/d4_t2e/d4_t2f (Stryker Pad CSR Barbell / Ab Trainer
    # Hanging Leg Raise / Better Fly Cable Pullover). d4_t3a (DB Rear Delt
    # Fly) widens 8-12 -> 10-15. d4_t3b (Andreoni Cable Pullover) no longer
    # exists, replaced by d4_t3e (Lying Tricep Extension [SB], reused
    # movement, fresh slot) at 8-12.
    "d4_t1_btn_ohp": (4, 6),
    "d4_t1": (6, 8),
    "d4_t2d": (8, 12),
    "d4_t2e": (8, 12),
    "d4_t2f": (10, 15),
    "d4_t3a": (10, 15),
    "d4_t3e": (8, 12),
    # 2026-08-12 (STAB maintenance-block redesign, Task 4): D5 reconciled to
    # the FINAL doc's real D5 session. T1 RDL [PB] (was UNCHANGED_REP_TARGETS
    # "d5_t1") -> Kickstand RDL [DB] (fresh slot "d5_t1_kickstand_rdl"),
    # rep range 6-8 -> 4-6. T1b (Hip Thrust) tier removed entirely. T2 GS
    # fully turned over: old d5_t2a/b/c (Bulgarian Split Squat, Scout
    # Reverse Hyper, Assisted Nordic) all vacated, replaced by fresh
    # d5_t2d/e/f. T3 GS: d5_t3b (Reverse Nordic Curl) unchanged, not listed
    # here; old d5_t3a/c/d (Poliquin Step-up, Cable Tib Raise, Hyper Pro
    # Calf Raise) all vacated, replaced by fresh d5_t3e/f/g (Hybrid Board
    # Calf Raise [D5] / Hybrid Board Tib Raise [D5] / Better Fly Hip
    # Adduction, the TIB slot a plan-owner addendum resolving this task's
    # own NEEDS_CONTEXT round-trip). New T4 straight tier (d5_t4a, Ab
    # Trainer Russian Twist).
    "d5_t1_kickstand_rdl": (4, 6),
    # 2026-08-14: d5_t2d (Nordic Max Bulgarian Split Squat) vacated --
    # replaced by fresh d5_t2h (Matrix Machine Bulgarian Split Squat, Nordic
    # Max rig conflict with Nordic Curl Max in the same giant set), same
    # rep target (8-12).
    "d5_t2h": (8, 12),
    "d5_t2e": (6, 8),
    "d5_t2f": (10, 15),
    "d5_t3e": (10, 15),
    "d5_t3f": (10, 15),
    "d5_t3g": (10, 15),
    "d5_t4a": (10, 15),
    # 2026-08-12 (STAB maintenance-block redesign, Task 5): D6 reconciled to
    # the FINAL doc's real D6 session. D6's standalone T1 tier (Dips)
    # eliminated entirely -- "d6_t1" no longer exists, Dips folds into GS1
    # at a fresh slot "d6_g1e" (8-12, was 6-8 at d6_t1). GS1 gains a 3rd
    # member, "d6_g1f" (Close-Grip Bench Camber-14, reused "Swiss Bar CG
    # Press [SB]"), 4-6 reps. GS1's d6_g1a (Pull-up) UNCHANGED (5-8, not
    # listed here). Hip Thrust (d6_g1c) and Cable Bicep Curl (d6_g1d) both
    # removed -- no longer listed. GS2 fully turned over: old d6_g2a/b/c
    # (Reverse Hyper Recovery / DB Seal Row / Lateral Raise) all vacated,
    # replaced by fresh d6_g2d/e/f (Better Fly Cable Bicep Curl / Stryker
    # Pad CSR Cables / Better Fly Rear Delt Extension). GS3: d6_g3a (Face
    # Pull) rep range corrected 15-20 -> 10-15 (FINAL doc); old d6_g3b/c
    # (Cable V-Bar Pushdown / T-Bar Row Wide) vacated, replaced by fresh
    # d6_g3d/e (Better Fly OH Tricep Extension / AbMat Ab Bench Pad Cable
    # Crunch).
    "d6_g1e": (8, 12),
    "d6_g1f": (4, 6),
    # 2026-08-16: d6_g2d (Better Fly Cable Bicep Curl) vacated -- replaced by
    # fresh d6_g2g (D-Handle Cable Bicep Curl, athlete directive), same rep
    # target (10-15).
    "d6_g2g": (10, 15),
    "d6_g2e": (8, 12),
    "d6_g2f": (10, 15),
    "d6_g3a": (10, 15),
    "d6_g3d": (8, 12),
    "d6_g3e": (10, 15),
}

# slot_id -> (rep_low, rep_high) — anchor slots reconciled to the YAML.
# 2026-08-12 (Task 4): "d5_t1" (RDL anchor, 6-8) moved to CHANGED_REP_TARGETS
# above as "d5_t1_kickstand_rdl" (4-6) -- the T1 anchor's content and reps
# both changed (Kickstand RDL replaces RDL [PB]), so this dict is now empty.
UNCHANGED_REP_TARGETS = {
}

# (day_role, tier_label) -> rest_seconds. Per-day because rests are non-uniform
# per label after the YAML reconciliation.
TIER_REST_MAP = {
    ("D1 Upper Push", "T1"): 120,
    ("D1 Upper Push", "T2 GS"): 90,
    ("D1 Upper Push", "T3 GS"): 75,
    # ("D1 Upper Push", "T4 GS") removed 2026-08-10 (STAB maintenance-block
    # redesign) -- D1's T4 GS tier no longer exists.
    ("D2 Lower A", "T1"): 150,
    # ("D2 Lower A", "T1b") removed 2026-08-11 (STAB maintenance-block
    # redesign, Task 2) -- D2's Hip Thrust T1b tier no longer exists.
    ("D2 Lower A", "T2 GS"): 90,
    ("D2 Lower A", "T3 GS"): 60,
    # ("D2 Lower A", "T4") added 2026-08-11, new straight tier
    # (Ab Trainer Decline Sit-up); tier_label "T4" not "T4 GS" (not a
    # GIANT_SET), so it's a distinct key from the "*_GS" convention below.
    ("D2 Lower A", "T4"): 90,
    ("D4 Upper Pull", "T1"): 120,
    ("D4 Upper Pull", "T1b"): 180,
    ("D4 Upper Pull", "T2 GS"): 90,
    ("D4 Upper Pull", "T3 GS"): 75,
    ("D5 Lower B", "T1"): 180,
    # ("D5 Lower B", "T1b") removed 2026-08-12 (STAB maintenance-block
    # redesign, Task 4) -- D5's Hip Thrust T1b tier no longer exists (2nd
    # of 3 Hip Thrust removals across this redesign).
    ("D5 Lower B", "T2 GS"): 90,
    ("D5 Lower B", "T3 GS"): 60,
    # ("D5 Lower B", "T4") added 2026-08-12, new straight tier (Ab Trainer
    # Russian Twist); tier_label "T4" not "T4 GS" (not a GIANT_SET).
    ("D5 Lower B", "T4"): 90,
    # ("D6 Weak Points", "T1") removed 2026-08-12 (STAB maintenance-block
    # redesign, Task 5) -- D6's standalone T1 tier (Dips) no longer exists;
    # Dips folds back into GS1 (3rd and final Hip Thrust removal + T1
    # elimination across this redesign).
    ("D6 Weak Points", "GS1"): 90,
    ("D6 Weak Points", "GS2"): 90,
    ("D6 Weak Points", "GS3"): 60,
}

UNILATERAL_MOVEMENTS = [
    "Meadows Row [OB + LM]",
    "Bulgarian Split Squat [DB]",
    "ATG Split Squat",
    "Cross-Body Cable Rear Delt Fly [FT]",
    "Cross-Body Cable Lateral Raise [FT]",
    "Single-Arm DB Row [DB]",
    "Poliquin Step-up",
    "Staggered RDL [PB]",
]


def test_rep_targets_reconciled(gen_db):
    tes = {te.slot_id: te for te in gen_db.exec(select(TierExercise)).all()}
    for slot_id, (rep_low, rep_high) in CHANGED_REP_TARGETS.items():
        te = tes[slot_id]
        assert (te.rep_low, te.rep_high) == (rep_low, rep_high), (
            f"{slot_id}: expected ({rep_low}, {rep_high}), "
            f"got ({te.rep_low}, {te.rep_high})"
        )


def test_rep_targets_unchanged_controls(gen_db):
    tes = {te.slot_id: te for te in gen_db.exec(select(TierExercise)).all()}
    for slot_id, (rep_low, rep_high) in UNCHANGED_REP_TARGETS.items():
        te = tes[slot_id]
        assert (te.rep_low, te.rep_high) == (rep_low, rep_high), (
            f"{slot_id}: expected UNCHANGED ({rep_low}, {rep_high}), "
            f"got ({te.rep_low}, {te.rep_high})"
        )


def test_tier_rests_seeded(gen_db):
    day_role_by_id = {
        pd.id: pd.day_role for pd in gen_db.exec(select(ProgramDay)).all()
    }
    by_key = {}
    for t in gen_db.exec(select(Tier)).all():
        role = day_role_by_id[t.program_day_id]
        by_key[(role, t.tier_label)] = t.rest_seconds

    for key, expected_rest in TIER_REST_MAP.items():
        assert key in by_key, f"no Tier row found for {key}"
        assert by_key[key] == expected_rest, (
            f"{key}: expected rest_seconds == {expected_rest}, got {by_key[key]}"
        )


def test_schemes_straight(gen_db):
    names = {m.name: m for m in gen_db.exec(select(Movement)).all()}
    assert names["Belt Squat [GHR + FT]"].scheme == Scheme.STRAIGHT
    assert names["RDL [PB]"].scheme == Scheme.STRAIGHT
    # Task 2 review fix: Bench Press seed-source parity with the live-only
    # fix (the 148.5-class bug — Bench must not regress to a 2-set
    # top+backoff on a from-scratch reseed).
    assert names["Bench Press [PB]"].scheme == Scheme.STRAIGHT


def test_te_schemes_synced_to_straight(gen_db):
    # Task 2 review fix: TierExercise.scheme (the string field that flows
    # into generation/context.py's slot_rep_schemes -> the injected LLM
    # payload) must match the reconciled Movement.scheme for the three
    # flipped T1 anchors, not just the deterministic-assembler-authoritative
    # Movement.scheme.
    # 2026-08-12 (Task 4): "d5_t1" -> "d5_t1_kickstand_rdl" (T1 anchor swap,
    # RDL [PB] -> Kickstand RDL [DB]; TierExercise.scheme stays "STRAIGHT",
    # same convention as every other T1 anchor regardless of movement).
    tes = {te.slot_id: te for te in gen_db.exec(select(TierExercise)).all()}
    for slot_id in ("d1_t1", "d2_t1", "d5_t1_kickstand_rdl"):
        assert tes[slot_id].scheme == "STRAIGHT", (
            f"{slot_id}: expected TierExercise.scheme == 'STRAIGHT', "
            f"got {tes[slot_id].scheme!r}"
        )


def test_unilateral_flags(gen_db):
    names = {m.name: m for m in gen_db.exec(select(Movement)).all()}
    for name in UNILATERAL_MOVEMENTS:
        assert names[name].unilateral is True, f"{name}: expected unilateral=True"
    assert names["Bench Press [PB]"].unilateral is False


def test_reverse_hyper_recovery_rpe_cap(gen_db):
    # Reverse Hyper Recovery moved GS3 -> GS2 in the YAML reconciliation; its
    # rpe_cap=6.0 lived on d6_g2a.
    #
    # 2026-08-12 (STAB maintenance-block redesign, Task 5): d6_g2a no longer
    # exists -- GS2 fully turned over (Reverse Hyper Recovery drops out of
    # D6's wiring entirely, replaced by Better Fly Cable Bicep Curl /
    # Stryker Pad CSR Cables / Better Fly Rear Delt Extension, none of which
    # carry a non-default rpe_cap). This was the ONLY rpe_cap != 8.0 example
    # anywhere in the real program. Attaches a synthetic TierExercise (own
    # Tier, tier_order=99, matching the established synthetic-slot pattern
    # used throughout the HT test files) reusing the same still-ACTIVE-but-
    # unwired Reverse Hyper Recovery movement and its original rpe_cap=6.0,
    # to keep this wiring-level check (distinct from test_assembler_
    # fidelity.py's end-to-end generation check of the same scenario).
    from ironlog.models.program import TierKind

    rhr = gen_db.exec(
        select(Movement).where(Movement.name == "Reverse Hyper Recovery [REV_HYPER]")
    ).one()
    pd = gen_db.exec(select(ProgramDay).where(ProgramDay.day_role == "D6 Weak Points")).one()
    tier = Tier(program_day_id=pd.id, tier_label="TEST-RPE", tier_order=99,
                tier_kind=TierKind.GIANT_SET, rounds=3, rest_seconds=90, shoe="Metcon 9")
    gen_db.add(tier)
    gen_db.flush()
    gen_db.add(TierExercise(
        tier_id=tier.id, slot_id="test_rhr_rpe_cap", movement_id=rhr.id,
        exercise_order=1, tier_role="free", pattern="reverse_hyper",
        rep_low=15, rep_high=20, scheme="FIXED", rpe_cap=6.0,
    ))
    gen_db.commit()

    tes = {te.slot_id: te for te in gen_db.exec(select(TierExercise)).all()}
    assert tes["test_rhr_rpe_cap"].rpe_cap == 6.0
