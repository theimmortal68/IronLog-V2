"""
program_seed.py — seed the Phase 1 program definition (the evolving-seed prior).

Seeded: main-work tiers only (D1-D6), TierExercises, MesoRotations.
NOT seeded: warmups, finishers (EMOM), Z2 (deferred to v0.7).

Movement resolution rule (the library-import lesson — halt-and-flag):
- Every program movement name is looked up in PROGRAM_TO_LIBRARY to get the
  canonical library name, then matched by exact name against the seeded library.
- Any name that resolves to NEITHER raises ValueError immediately.
  NEVER invent a movement, NEVER silently skip.

NO from __future__ import annotations (project-wide constraint).
"""
from typing import Dict, Optional

from sqlmodel import Session, select

from ironlog.models.enums import KneeModality
from ironlog.models.library import Movement
from ironlog.models.program import (
    MesoRotation, Program, ProgramDay, Tier, TierExercise, TierKind,
)

# ---------------------------------------------------------------------------
# MOVEMENT NAME RESOLUTION MAP
# ---------------------------------------------------------------------------
# Maps program-document movement names to canonical library Movement.name.
# After map lookup, resolution checks for exact name match in the library.
# If neither succeeds, seed raises ValueError (halt-and-flag, never invent).

PROGRAM_TO_LIBRARY: Dict[str, str] = {
    # ── D1 Upper Push ────────────────────────────────────────────────────────
    "Pendlay Row Narrow":                           "Pendlay Row - Narrow [OB]",
    "Incline DB Press":                             "Incline DB Press [DB + BENCH]",
    "Pull-up (2-phase)":                            "Pull-up [TOWER + TUBES]",
    "Cross-Body Lateral Raise":                     "Cross-Body Cable Lateral Raise [FT]",
    "Cross-Body Rear Delt Fly":                     "Cross-Body Cable Rear Delt Fly [FT]",
    "Seated Cable Row":                             "Seated Cable Row [FT]",
    "Ab Wheel Rollout":                             "Ab Wheel [WHEEL]",
    "Lat Prayer":                                   "Lat Prayer [ANDREONI + FT]",
    # ── D2 Lower A ───────────────────────────────────────────────────────────
    "Belt Squat":                                   "Belt Squat [GHR + FT]",
    "Barbell Hip Thrust (220 cap)":                 "Hip Thrust [HIP_THRUST]",
    "Assisted Nordic":                              "Nordic Curl [GHR]",
    "Scout Reverse Hyper (180 cap)":               "Reverse Hyper [REV_HYPER]",
    "Cable Tib Raise":                              "Cable Tibialis Raise",
    # ── D4 Upper Pull ────────────────────────────────────────────────────────
    "Assisted Pull-up (2-phase)":                   "Pull-up [TOWER + TUBES]",
    "Meadows Row":                                  "Meadows Row [OB + LM]",
    "Meadows SA Row":                               "Meadows Row [OB + LM]",
    # ── D5 Lower B ───────────────────────────────────────────────────────────
    "RDL":                                          "RDL [PB]",
    "Barbell Hip Thrust (220 cap, independent track)": "Hip Thrust [HIP_THRUST]",
    "Bulgarian Split Squat":                        "Bulgarian Split Squat [DB]",
    "Scout Reverse Hyper":                          "Reverse Hyper [REV_HYPER]",
    "Assisted Nordic (eccentric)":                  "Nordic Curl [GHR]",
    "Hyper Pro Calf Raise":                         "Calf Raise [GHR]",
    # ── D6 Weak Points ───────────────────────────────────────────────────────
    "Pull-up (Set 1 unassisted max test)":          "Pull-up [TOWER + TUBES]",
    "Dips":                                         "Dips [ANDREONI + FT]",
    "Hip Thrust (D5 × 0.80, FIXED)":               "Hip Thrust [HIP_THRUST]",
    "T-Bar Row Wide":                               "T-Bar Row - Wide [OB + KLEVA + LM]",
    "DB Seal Row":                                  "DB Seal Row [DB + UTIL_SEAT]",
    "Lateral Raise":                                "Lateral Raise [FT]",
    "Cable V-Bar Pushdown":                         "Cable V-Bar Pushdown [FT]",
    "Reverse Hyper Recovery":                       "Light Reverse Hyper [REV_HYPER]",
    # ── Meso-2 rotation variants ─────────────────────────────────────────────
    "Back Squat":                                   "Back Squat [PB]",
    "Pendlay Row":                                  "Pendlay Row - Medium [OB]",
    "Staggered RDL":                                "Staggered RDL [PB]",
    "Single-Arm DB Row":                            "Single-Arm DB Row [DB]",
}


def _build_lib_map(db: Session) -> Dict[str, int]:
    """Return {movement.name: movement.id} for all seeded library movements."""
    return {m.name: m.id for m in db.exec(select(Movement)).all()}


def _resolve(prog_name: str, lib: Dict[str, int]) -> int:
    """Resolve a program movement name to a library Movement.id.

    Resolution steps:
      1. Look up in PROGRAM_TO_LIBRARY → get canonical library name.
      2. Exact match against the seeded library by canonical name.
    Raises ValueError (halt-and-flag) on any failure.
    """
    canonical = PROGRAM_TO_LIBRARY.get(prog_name, prog_name)
    mid = lib.get(canonical)
    if mid is None:
        raise ValueError(
            f"HALT-AND-FLAG: program movement {prog_name!r} "
            f"(canonical: {canonical!r}) not found in seeded library. "
            "Add to library MOVEMENTS or PROGRAM_TO_LIBRARY before seeding. "
            "NEVER invent or silently skip."
        )
    return mid


def seed_phase1_program(db: Session) -> None:
    """Seed the Phase 1 Post-HGC program definition into the given session.

    Seeded: Program, 7 ProgramDays (5 training + 2 rest), Tiers, TierExercises,
    and all MesoRotations:
      - d2_t1: Belt Squat → Back Squat (meso-2)
      - d4_t2a: Meadows Row → Pendlay Row - Medium (meso-2)
      - d5_t1: RDL → Staggered RDL (meso-2)
      - d4_t3b: Meadows SA Row → Single-Arm DB Row (meso-2)

    Intentionally excluded (same library movement, no distinct rotation):
      - d1_t1 meso-2: BMF 21" bench variant is an equipment note, not a library change
      - d5_t2b meso-2: single-leg Reverse Hyper is a technique note, not a library change

    The guard contract: _resolve() is called on EVERY rotation movement name; any
    unresolved name raises ValueError immediately (halt-and-flag, never invent/skip).

    Warmups, finishers, Z2 are NOT seeded (deferred to v0.7).
    The session is committed at the end.
    NEVER call this on the production DB without a reseed/migration plan.
    """
    lib = _build_lib_map(db)

    # ── Program ──────────────────────────────────────────────────────────────
    prog = Program(
        name="Post-HGC Phase 1 (Pre-APEX Bridge)",
        phase="P1_CUT",
        duration_weeks=4,
    )
    db.add(prog)
    db.flush()

    # ── ProgramDays (7 days: 5 training + 2 rest) ────────────────────────────
    days_spec = [
        (1, "D1 Upper Push",  False),
        (2, "D2 Lower A",     False),
        (3, "",               True),   # Wed REST
        (4, "D4 Upper Pull",  False),
        (5, "D5 Lower B",     False),
        (6, "D6 Weak Points", False),
        (7, "",               True),   # Sun REST
    ]
    for idx, role, is_rest in days_spec:
        db.add(ProgramDay(program_id=prog.id, day_index=idx,
                          day_role=role, is_rest=is_rest))
    db.flush()

    day_objs: Dict[str, ProgramDay] = {
        pd.day_role: pd
        for pd in db.exec(
            select(ProgramDay).where(ProgramDay.program_id == prog.id)
        ).all()
    }

    # ── Seed each training day ────────────────────────────────────────────────
    _seed_d1(db, day_objs["D1 Upper Push"],  lib)
    _seed_d2(db, day_objs["D2 Lower A"],     lib)
    _seed_d4(db, day_objs["D4 Upper Pull"],  lib)
    _seed_d5(db, day_objs["D5 Lower B"],     lib)
    _seed_d6(db, day_objs["D6 Weak Points"], lib)

    db.commit()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _add_tier(db: Session, program_day_id: int, label: str, order: int,
              kind: TierKind, rounds: int = 1,
              rest_seconds: Optional[int] = None) -> Tier:
    t = Tier(program_day_id=program_day_id, tier_label=label, tier_order=order,
             tier_kind=kind, rounds=rounds, rest_seconds=rest_seconds)
    db.add(t)
    db.flush()
    return t


def _add_te(db: Session, tier_id: int, slot_id: str, prog_name: str,
            lib: Dict[str, int], order: int, tier_role: str,
            pattern: Optional[str] = None,
            knee_modality: Optional[KneeModality] = None,
            rep_low: Optional[int] = None,
            rep_high: Optional[int] = None,
            rpe_cap: Optional[float] = None,
            scheme: Optional[str] = None) -> TierExercise:
    te = TierExercise(
        tier_id=tier_id,
        slot_id=slot_id,
        movement_id=_resolve(prog_name, lib),
        exercise_order=order,
        tier_role=tier_role,
        pattern=pattern,
        knee_modality=knee_modality,
        rep_low=rep_low,
        rep_high=rep_high,
        rpe_cap=rpe_cap,
        scheme=scheme,
    )
    db.add(te)
    db.flush()
    return te


def _add_mr(db: Session, te: TierExercise, meso_number: int,
            prog_name: str, lib: Dict[str, int]) -> MesoRotation:
    mr = MesoRotation(
        tier_exercise_id=te.id,
        meso_number=meso_number,
        movement_id=_resolve(prog_name, lib),
    )
    db.add(mr)
    return mr


# ---------------------------------------------------------------------------
# D1 — Upper Push
# ---------------------------------------------------------------------------

def _seed_d1(db: Session, pd: ProgramDay, lib: Dict[str, int]) -> None:
    # T1 — Bench Press [PB] (anchor; meso-2 bar swap is equipment-level, not a
    #      library movement change, so no MesoRotation row)
    t1 = _add_tier(db, pd.id, "T1", 1, TierKind.T1_STRAIGHT, rounds=1, rest_seconds=120)
    _add_te(db, t1.id, "d1_t1", "Bench Press [PB]", lib, 1, "anchor",
            pattern="bench", rep_low=8, rep_high=8, rpe_cap=8.0,
            scheme="STRAIGHT")

    # T2 GS — Pendlay Row Narrow / Incline DB Press / Face-Up Incline Knee Raise
    t2 = _add_tier(db, pd.id, "T2 GS", 2, TierKind.GIANT_SET, rounds=3, rest_seconds=90)
    _add_te(db, t2.id, "d1_t2a", "Pendlay Row Narrow", lib, 1, "semi",
            pattern="horizontal_pull", rep_low=8, rep_high=8,
            scheme="DOUBLE_PROGRESSION")
    _add_te(db, t2.id, "d1_t2b", "Incline DB Press", lib, 2, "free",
            pattern="vertical_push", rep_low=10, rep_high=10,
            scheme="DOUBLE_PROGRESSION")
    _add_te(db, t2.id, "d1_t2c", "Face-Up Incline Knee Raise", lib, 3, "free",
            pattern="core", rep_low=15, rep_high=15)

    # T3 GS — Pull-up / Cross-Body Lateral Raise / Cross-Body Rear Delt Fly
    t3 = _add_tier(db, pd.id, "T3 GS", 3, TierKind.GIANT_SET, rounds=3, rest_seconds=60)
    _add_te(db, t3.id, "d1_t3a", "Pull-up (2-phase)", lib, 1, "free",
            pattern="vertical_pull", rep_low=8, rep_high=8, scheme="REP_RATIO")
    _add_te(db, t3.id, "d1_t3b", "Cross-Body Lateral Raise", lib, 2, "free",
            pattern="lateral_raise", rep_low=12, rep_high=12,
            scheme="DOUBLE_PROGRESSION")
    _add_te(db, t3.id, "d1_t3c", "Cross-Body Rear Delt Fly", lib, 3, "free",
            pattern="rear_delt", rep_low=12, rep_high=12,
            scheme="DOUBLE_PROGRESSION")

    # T4 GS — Seated Cable Row / Ab Wheel Rollout / Lat Prayer
    t4 = _add_tier(db, pd.id, "T4 GS", 4, TierKind.GIANT_SET, rounds=3, rest_seconds=60)
    _add_te(db, t4.id, "d1_t4a", "Seated Cable Row", lib, 1, "semi",
            pattern="horizontal_pull", rep_low=12, rep_high=12,
            scheme="DOUBLE_PROGRESSION")
    _add_te(db, t4.id, "d1_t4b", "Ab Wheel Rollout", lib, 2, "free",
            pattern="core", rep_low=8, rep_high=8)
    _add_te(db, t4.id, "d1_t4c", "Lat Prayer", lib, 3, "free",
            pattern="lat", rep_low=12, rep_high=12, scheme="DOUBLE_PROGRESSION")


# ---------------------------------------------------------------------------
# D2 — Lower A
# ---------------------------------------------------------------------------

def _seed_d2(db: Session, pd: ProgramDay, lib: Dict[str, int]) -> None:
    # T1 — Belt Squat (anchor; meso-2 rotation → Back Squat)
    t1 = _add_tier(db, pd.id, "T1", 1, TierKind.T1_STRAIGHT, rounds=1, rest_seconds=120)
    d2_t1 = _add_te(db, t1.id, "d2_t1", "Belt Squat", lib, 1, "anchor",
                    pattern="squat", rep_low=5, rep_high=8, rpe_cap=8.0,
                    scheme="STRAIGHT")
    _add_mr(db, d2_t1, 2, "Back Squat", lib)

    # T1b — Barbell Hip Thrust (semi)
    t1b = _add_tier(db, pd.id, "T1b", 2, TierKind.PAIR, rounds=1, rest_seconds=120)
    _add_te(db, t1b.id, "d2_t1b", "Barbell Hip Thrust (220 cap)", lib, 1, "semi",
            pattern="hip_thrust", rep_low=8, rep_high=8, rpe_cap=8.0,
            scheme="COMPOSITE")

    # T2 GS — Assisted Nordic / Scout Reverse Hyper
    t2 = _add_tier(db, pd.id, "T2 GS", 3, TierKind.GIANT_SET, rounds=3, rest_seconds=90)
    _add_te(db, t2.id, "d2_t2a", "Assisted Nordic", lib, 1, "free",
            knee_modality=KneeModality.NORDIC, rep_low=6, rep_high=10,
            scheme="ASSISTED")
    _add_te(db, t2.id, "d2_t2b", "Scout Reverse Hyper (180 cap)", lib, 2, "free",
            pattern="reverse_hyper", rep_low=15, rep_high=25, scheme="REP_AT_CAP")

    # T3 — ATG Split Squat / Cable Tib Raise (knee pair)
    t3 = _add_tier(db, pd.id, "T3", 4, TierKind.PAIR, rounds=1, rest_seconds=60)
    _add_te(db, t3.id, "d2_t3a", "ATG Split Squat", lib, 1, "free",
            knee_modality=KneeModality.KOT, rep_low=8, rep_high=10,
            scheme="DOUBLE_PROGRESSION")
    _add_te(db, t3.id, "d2_t3b", "Cable Tib Raise", lib, 2, "free",
            knee_modality=KneeModality.TIB, rep_low=12, rep_high=15,
            scheme="DOUBLE_PROGRESSION")


# ---------------------------------------------------------------------------
# D4 — Upper Pull
# ---------------------------------------------------------------------------

def _seed_d4(db: Session, pd: ProgramDay, lib: Dict[str, int]) -> None:
    # T1 — Assisted Pull-up (anchor)
    t1 = _add_tier(db, pd.id, "T1", 1, TierKind.T1_STRAIGHT, rounds=1, rest_seconds=120)
    _add_te(db, t1.id, "d4_t1", "Assisted Pull-up (2-phase)", lib, 1, "anchor",
            pattern="vertical_pull", rep_low=5, rep_high=8, scheme="REP_RATIO")

    # T2 GS — Meadows Row / Andreoni Cable Pullover / Face-Up Incline Knee Raise
    t2 = _add_tier(db, pd.id, "T2 GS", 2, TierKind.GIANT_SET, rounds=3, rest_seconds=90)
    d4_t2a = _add_te(db, t2.id, "d4_t2a", "Meadows Row", lib, 1, "semi",
                     pattern="horizontal_pull", rep_low=8, rep_high=10,
                     scheme="DOUBLE_PROGRESSION")
    _add_mr(db, d4_t2a, 2, "Pendlay Row", lib)
    _add_te(db, t2.id, "d4_t2b", "Andreoni Cable Pullover", lib, 2, "free",
            pattern="lat", rep_low=10, rep_high=12, scheme="DOUBLE_PROGRESSION")
    _add_te(db, t2.id, "d4_t2c", "Face-Up Incline Knee Raise", lib, 3, "free",
            pattern="core", rep_low=8, rep_high=12)

    # T3 GS — Cross-Body Rear Delt Fly / Meadows SA Row / Dragon Flag
    t3 = _add_tier(db, pd.id, "T3 GS", 3, TierKind.GIANT_SET, rounds=3, rest_seconds=60)
    _add_te(db, t3.id, "d4_t3a", "Cross-Body Rear Delt Fly", lib, 1, "free",
            pattern="rear_delt", rep_low=10, rep_high=12,
            scheme="DOUBLE_PROGRESSION")
    d4_t3b = _add_te(db, t3.id, "d4_t3b", "Meadows SA Row", lib, 2, "free",
                     pattern="horizontal_pull", rep_low=8, rep_high=10,
                     scheme="DOUBLE_PROGRESSION")
    # meso-2: Meadows SA Row → Single-Arm DB Row (distinct movement → resolve-or-raise)
    _add_mr(db, d4_t3b, 2, "Single-Arm DB Row", lib)
    _add_te(db, t3.id, "d4_t3c", "Dragon Flag", lib, 3, "free",
            pattern="core", rep_low=3, rep_high=6)


# ---------------------------------------------------------------------------
# D5 — Lower B
# ---------------------------------------------------------------------------

def _seed_d5(db: Session, pd: ProgramDay, lib: Dict[str, int]) -> None:
    # T1 — RDL (anchor; meso-2 rotation → Staggered RDL)
    t1 = _add_tier(db, pd.id, "T1", 1, TierKind.T1_STRAIGHT, rounds=1, rest_seconds=120)
    d5_t1 = _add_te(db, t1.id, "d5_t1", "RDL", lib, 1, "anchor",
                    pattern="rdl", rep_low=4, rep_high=6, rpe_cap=8.0,
                    scheme="STRAIGHT")
    # meso-2: RDL → Staggered RDL (distinct movement → resolve-or-raise, MesoRotation row)
    _add_mr(db, d5_t1, 2, "Staggered RDL", lib)

    # T1b — Barbell Hip Thrust (semi, independent track)
    t1b = _add_tier(db, pd.id, "T1b", 2, TierKind.PAIR, rounds=1, rest_seconds=120)
    _add_te(db, t1b.id, "d5_t1b",
            "Barbell Hip Thrust (220 cap, independent track)", lib, 1, "semi",
            pattern="hip_thrust", rep_low=8, rep_high=8, rpe_cap=8.0,
            scheme="COMPOSITE")

    # T2 GS — Bulgarian Split Squat / Scout Reverse Hyper / Assisted Nordic
    t2 = _add_tier(db, pd.id, "T2 GS", 3, TierKind.GIANT_SET, rounds=3, rest_seconds=90)
    _add_te(db, t2.id, "d5_t2a", "Bulgarian Split Squat", lib, 1, "free",
            pattern="lunge", rep_low=8, rep_high=10, scheme="DOUBLE_PROGRESSION")
    _add_te(db, t2.id, "d5_t2b", "Scout Reverse Hyper", lib, 2, "free",
            pattern="reverse_hyper", rep_low=12, rep_high=15,
            scheme="DOUBLE_PROGRESSION")
    # d5_t2b meso-2: single-leg Reverse Hyper is a TECHNIQUE note on the same library
    # movement — intentionally NO MesoRotation row (not a guard-bypass; the same
    # movement id is used for both meso-1 and meso-2, distinction handled by the coach).
    _add_te(db, t2.id, "d5_t2c", "Assisted Nordic (eccentric)", lib, 3, "free",
            knee_modality=KneeModality.NORDIC, rep_low=5, rep_high=8,
            scheme="ASSISTED")

    # T3 GS — Poliquin / Sissy / Cable Tib / Hyper Pro Calf
    t3 = _add_tier(db, pd.id, "T3 GS", 4, TierKind.GIANT_SET, rounds=3, rest_seconds=60)
    _add_te(db, t3.id, "d5_t3a", "Poliquin Step-up", lib, 1, "free",
            knee_modality=KneeModality.KOT, rep_low=8, rep_high=10,
            scheme="DOUBLE_PROGRESSION")
    _add_te(db, t3.id, "d5_t3b", "Sissy Squat", lib, 2, "free",
            knee_modality=KneeModality.SISSY, rep_low=8, rep_high=12,
            scheme="DOUBLE_PROGRESSION")
    _add_te(db, t3.id, "d5_t3c", "Cable Tib Raise", lib, 3, "free",
            knee_modality=KneeModality.TIB, rep_low=12, rep_high=15,
            scheme="DOUBLE_PROGRESSION")
    _add_te(db, t3.id, "d5_t3d", "Hyper Pro Calf Raise", lib, 4, "free",
            pattern="calf", rep_low=10, rep_high=12, scheme="DOUBLE_PROGRESSION")


# ---------------------------------------------------------------------------
# D6 — Weak Points
# ---------------------------------------------------------------------------

def _seed_d6(db: Session, pd: ProgramDay, lib: Dict[str, int]) -> None:
    # GS1 — Pull-up / Dips / Hip Thrust
    gs1 = _add_tier(db, pd.id, "GS1", 1, TierKind.GIANT_SET, rounds=3, rest_seconds=90)
    _add_te(db, gs1.id, "d6_g1a", "Pull-up (Set 1 unassisted max test)", lib,
            1, "anchor", pattern="vertical_pull", rep_low=5, rep_high=8,
            scheme="REP_RATIO")
    _add_te(db, gs1.id, "d6_g1b", "Dips", lib, 2, "free",
            pattern="vertical_push", rep_low=5, rep_high=8,
            scheme="DOUBLE_PROGRESSION")
    _add_te(db, gs1.id, "d6_g1c", "Hip Thrust (D5 × 0.80, FIXED)", lib, 3, "free",
            pattern="hip_thrust", rep_low=12, rep_high=12, scheme="FIXED")

    # GS2 — T-Bar Row Wide / DB Seal Row / Lateral Raise
    gs2 = _add_tier(db, pd.id, "GS2", 2, TierKind.GIANT_SET, rounds=3, rest_seconds=90)
    _add_te(db, gs2.id, "d6_g2a", "T-Bar Row Wide", lib, 1, "semi",
            pattern="horizontal_pull", rep_low=8, rep_high=10,
            scheme="DOUBLE_PROGRESSION")
    _add_te(db, gs2.id, "d6_g2b", "DB Seal Row", lib, 2, "free",
            pattern="horizontal_pull", rep_low=10, rep_high=12,
            scheme="DOUBLE_PROGRESSION")
    _add_te(db, gs2.id, "d6_g2c", "Lateral Raise", lib, 3, "free",
            pattern="lateral_raise", rep_low=12, rep_high=15,
            scheme="DOUBLE_PROGRESSION")

    # GS3 — Cross-Body Rear Delt Fly / Cable V-Bar Pushdown / Reverse Hyper Recovery
    gs3 = _add_tier(db, pd.id, "GS3", 3, TierKind.GIANT_SET, rounds=3, rest_seconds=60)
    _add_te(db, gs3.id, "d6_g3a", "Cross-Body Rear Delt Fly", lib, 1, "free",
            pattern="rear_delt", rep_low=12, rep_high=15,
            scheme="DOUBLE_PROGRESSION")
    _add_te(db, gs3.id, "d6_g3b", "Cable V-Bar Pushdown", lib, 2, "semi",
            pattern="triceps", rep_low=8, rep_high=12, scheme="SINGLE_SESSION")
    _add_te(db, gs3.id, "d6_g3c", "Reverse Hyper Recovery", lib, 3, "free",
            pattern="reverse_hyper", rep_low=15, rep_high=20, scheme="FIXED",
            rpe_cap=6.0)
