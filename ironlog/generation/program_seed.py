"""
program_seed.py — seed the Phase 1 program definition (the evolving-seed prior).

Seeded: main-work tiers, warmups, finishers (EMOM), TierExercises, MesoRotations.
NOT seeded: Z2 (deferred to v0.7).

Movement resolution rule (the library-import lesson — halt-and-flag):
- Every program movement name is looked up in PROGRAM_TO_LIBRARY to get the
  canonical library name, then matched by exact name against the seeded library.
- Any name that resolves to NEITHER raises ValueError immediately.
  NEVER invent a movement, NEVER silently skip.

NO from __future__ import annotations (project-wide constraint).
"""
from typing import Dict, Optional

from sqlmodel import Session, select

from ironlog.models.enums import (
    KneeModality, LiftCategory, ProgressionMode, ProgressionRule,
)
from ironlog.models.library import Movement, MovementState
from ironlog.models.program import (
    DayFinisher, MesoRotation, Program, ProgramDay, Tier, TierExercise, TierKind,
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
    "Lying Tricep Extension":                       "Lying Tricep Extension [SB]",
    "Incline DB Press":                             "Incline DB Press [DB + BENCH]",
    "Better Fly Standing Lateral Raise":            "Better Fly Standing Lateral Raise [FT]",
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
    "Wide-Grip Pull-up":                             "Wide-Grip Pull-up [TOWER]",
    "Meadows Row":                                  "Meadows Row [OB + LM]",
    "Meadows SA Row":                               "Meadows Row [OB + LM]",
    "DB Rear Delt Fly":                             "Rear Delt Fly [DB]",
    # ── D5 Lower B ───────────────────────────────────────────────────────────
    "RDL":                                          "RDL [PB]",
    "Barbell Hip Thrust (220 cap, independent track)": "Hip Thrust [HIP_THRUST]",
    "Bulgarian Split Squat":                        "Bulgarian Split Squat [DB]",
    "Scout Reverse Hyper (90 cap)":                 "Light Reverse Hyper [REV_HYPER]",
    "Scout Reverse Hyper - Single Leg":             "Reverse Hyper - Single Leg [REV_HYPER]",
    "Assisted Nordic (eccentric)":                  "Nordic Curl [GHR]",
    "Reverse Nordic (assisted)":                    "Reverse Nordic Curl [GHR]",
    "Hyper Pro Calf Raise":                         "Calf Raise [GHR]",
    # ── D6 Weak Points ───────────────────────────────────────────────────────
    # 2026-07-26: 3-way pull-up split -- D6 no longer shares D4's Wide-Grip
    # Pull-up, gets its own neutral-grip-paused movement.
    "Pull-up - Neutral Grip (Paused)":               "Pull-up - Neutral Grip (Paused) [TOWER]",
    "Dips":                                         "Dips [TOWER + TUBES]",
    "Cable Bicep Curl":                             "Cable Bicep Curl [FT]",
    "Hip Thrust (D5 × 0.80, FIXED)":               "Hip Thrust [HIP_THRUST]",
    "T-Bar Row Wide":                               "T-Bar Row - Wide [OB + KLEVA + LM]",
    "DB Seal Row":                                  "DB Seal Row [DB + UTIL_SEAT]",
    "Lateral Raise":                                "Lateral Raise [FT]",
    "Cable V-Bar Pushdown":                         "Cable V-Bar Pushdown [FT]",
    "Reverse Hyper Recovery":                       "Reverse Hyper Recovery [REV_HYPER]",
    "Face Pull":                                    "Face Pull [FT]",
    # ── Meso-2 rotation variants ─────────────────────────────────────────────
    "Back Squat":                                   "Back Squat [PB]",
    "Pendlay Row":                                  "Pendlay Row - Medium [OB]",
    "Staggered RDL":                                "Staggered RDL [PB]",
    "Single-Arm DB Row":                            "Single-Arm DB Row [DB]",
}

RAMP_ELIGIBLE_MOVEMENT_NAMES = {
    "Bench Press [PB]",
    "Belt Squat [GHR + FT]",
    "Back Squat [PB]",
    # The authoritative YAML ids rdl_d5 and rdl_conventional both resolve here.
    "RDL [PB]",
    "Staggered RDL [PB]",
}

WARMUP_CONFIGS: Dict[int, dict] = {
    1: {
        "movement_flow_seconds": 90,
        "items": [
            {"name": "scap_cars", "sets": 2, "reps": 5},
            {"name": "floor_slides", "sets": 1, "reps": 5},
            {"name": "jump_rope", "seconds": 90, "rope": "standard", "style": "light_bounce"},
        ],
        "activation_seconds": 60,
        "items_activation": [
            {"name": "prone_y_raise", "sets": 2, "reps": 12, "incline_degrees": 30},
            {"name": "sa_waiters_carry", "seconds_per_side": 20, "sets": 1},
        ],
    },
    2: {
        "movement_flow_seconds": 90,
        "items": [
            {"name": "deep_squat_hold_rock", "seconds": 20},
            {"name": "worlds_greatest", "sets": 1, "reps_per_side": 2},
            {"name": "cossack_squat", "sets": 1, "reps_per_side": 3},
        ],
        "activation_seconds": 60,
        "items_activation": [
            {"name": "glute_bridge", "sets": 1, "reps": 10, "hold_seconds": 2},
            {"name": "banded_clamshell", "sets": 1, "reps_per_side": 10},
        ],
    },
    4: {
        "movement_flow_seconds": 90,
        "items": [
            {"name": "scapular_pulls", "sets": 2, "reps": 5, "notes": "assisted via Mingmc 4-5 bands if needed"},
            {"name": "open_book", "sets": 1, "reps_per_side": 5},
            {"name": "jump_rope", "seconds": 90, "rope": "standard", "style": "light_bounce"},
        ],
        "activation_seconds": 60,
        "items_activation": [
            {"name": "prone_y_raise", "sets": 2, "reps": 12, "incline_degrees": 30},
            {"name": "sa_waiters_carry", "seconds_per_side": 20, "sets": 1},
        ],
    },
    5: {
        "movement_flow_seconds": 90,
        "items": [
            {"name": "cat_cow", "sets": 1, "reps": 5},
            {"name": "worlds_greatest", "sets": 1, "reps_per_side": 2},
            {"name": "dead_bug", "sets": 1, "reps_per_side": 5},
        ],
        "activation_seconds": 60,
        "items_activation": [
            {"name": "glute_bridge", "sets": 1, "reps": 10, "hold_seconds": 2},
            {"name": "banded_clamshell", "sets": 1, "reps_per_side": 10},
        ],
    },
    6: {
        "movement_flow_seconds": 60,
        "items": [
            {"name": "scap_cars", "sets": 2, "reps": 5},
            {"name": "open_book", "sets": 1, "reps_per_side": 5},
            {"name": "banded_pull_apart", "sets": 1, "reps": 15},
        ],
        "activation_seconds": 30,
        "items_activation": [
            {"name": "prone_y_raise", "sets": 1, "reps": 12},
        ],
    },
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

    Seeded: Program, 7 ProgramDays (5 training + 2 rest), warmups on training
    days, Tiers, TierExercises, finishers, and all MesoRotations:
      - d2_t1: Belt Squat → Back Squat (meso-2)
      - d4_t2a: Meadows Row → Pendlay Row - Medium (meso-2)
      - d5_t1: RDL → Staggered RDL (meso-2)
      - d5_t2b: Scout Reverse Hyper → Reverse Hyper - Single Leg (meso-2, 12–15 rep override)

    Intentionally excluded (same library movement, no distinct rotation):
      - d1_t1 meso-2: BMF 21" bench variant is an equipment note, not a library change

    The guard contract: _resolve() is called on EVERY rotation movement name; any
    unresolved name raises ValueError immediately (halt-and-flag, never invent/skip).

    Z2 is NOT seeded (deferred to v0.7).
    The session is committed at the end.
    NEVER call this on the production DB without a reseed/migration plan.
    """
    lib = _build_lib_map(db)
    _mark_ramp_eligible_movements(db, lib)

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
                          day_role=role, is_rest=is_rest,
                          warmup_config=WARMUP_CONFIGS.get(idx)))
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
    _seed_finishers(db, {pd.day_index: pd for pd in day_objs.values()})

    db.commit()

    # Wire Movement.progression_rule from the authoritative YAML so a from-scratch
    # DB comes up with the progression engine LIVE (no schema change — the column
    # already exists). Without this, every movement has progression_rule=None and
    # advance() no-ops on every session (the engine is dormant). Idempotent.
    from ironlog.generation.rule_wiring import wire_progression_rules
    wire_progression_rules(db)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _add_tier(db: Session, program_day_id: int, label: str, order: int,
              kind: TierKind, rounds: int = 1,
              rest_seconds: Optional[int] = None,
              shoe: Optional[str] = None) -> Tier:
    t = Tier(program_day_id=program_day_id, tier_label=label, tier_order=order,
             tier_kind=kind, rounds=rounds, rest_seconds=rest_seconds, shoe=shoe)
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
            scheme: Optional[str] = None,
            derived_from_unified_group: Optional[str] = None,
            derive_ratio: Optional[float] = None) -> TierExercise:
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
        derived_from_unified_group=derived_from_unified_group,
        derive_ratio=derive_ratio,
    )
    db.add(te)
    db.flush()
    return te


def _mark_ramp_eligible_movements(db: Session, lib: Dict[str, int]) -> None:
    missing = sorted(name for name in RAMP_ELIGIBLE_MOVEMENT_NAMES if name not in lib)
    if missing:
        raise ValueError(
            "HALT-AND-FLAG: ramp-eligible movement(s) missing from seeded library: "
            f"{missing!r}"
        )
    movements = db.exec(
        select(Movement).where(Movement.name.in_(RAMP_ELIGIBLE_MOVEMENT_NAMES))
    ).all()
    for movement in movements:
        movement.ramp_eligible = True
        db.add(movement)
    db.flush()


def _add_mr(db: Session, te: TierExercise, meso_number: int,
            prog_name: str, lib: Dict[str, int],
            rep_low: Optional[int] = None,
            rep_high: Optional[int] = None) -> MesoRotation:
    mr = MesoRotation(
        tier_exercise_id=te.id,
        meso_number=meso_number,
        movement_id=_resolve(prog_name, lib),
        rep_low=rep_low,
        rep_high=rep_high,
    )
    db.add(mr)
    return mr


def _seed_finishers(db: Session, days_by_index: Dict[int, ProgramDay]) -> None:
    finishers = {
        1: {
            "name": "kb_swing",
            "params": {
                "weight_lb": 30,
                "target_reps_per_minute": 15,
                "equipment": ["kettlebell_30"],
            },
        },
        2: {
            "name": "sled_push",
            "params": {
                "resistance_level": 8,
                "work_seconds_per_minute": 30,
                "equipment": ["dreadmill"],
            },
        },
        4: {
            "name": "sandbag_load_to_utility_seat",
            "params": {
                "weight_lb": 100,
                "utility_seat_height_inches": 52,
                "target_reps_per_minute": 4,
                "equipment": ["sandbag_100", "utility_seat", "spotter_arms"],
            },
        },
        5: {
            "name": "heavy_farmer_carry",
            "params": {
                "weight_lb": 55,
                "work_seconds_per_minute": 40,
                "rest_seconds_per_minute": 20,
                "equipment": ["dreadmill", "farmer_handles"],
            },
        },
        6: {
            "name": "jump_rope",
            "params": {
                "rope_type": "crossrope_quarter_lb",
                "work_seconds_per_minute": 30,
                "target_reps_per_minute": 40,
                "equipment": ["crossrope_quarter_lb"],
            },
            "duration_ladder": [35, 40, 45, 50],
            "rope_ladder": ["quarter_lb", "half_lb", "one_lb"],
            "current_duration_seconds": 35,
            "current_rope": "quarter_lb",
        },
    }

    for day_index, spec in finishers.items():
        movement = Movement(
            name=spec["name"],
            base_name=spec["name"],
            lift_category=LiftCategory.NONE,
            progression_mode=ProgressionMode.FINISHER,
            progression_rule=(
                ProgressionRule.FINISHER_DURATION_THEN_ROPE
                if day_index == 6 else None
            ),
            rope_ladder=spec.get("rope_ladder"),
        )
        db.add(movement)
        db.flush()

        db.add(MovementState(
            movement_id=movement.id,
            active_rule=(
                ProgressionRule.FINISHER_DURATION_THEN_ROPE
                if day_index == 6 else None
            ),
            duration_ladder=spec.get("duration_ladder"),
            current_duration_seconds=spec.get("current_duration_seconds"),
            current_rope=spec.get("current_rope"),
        ))
        db.add(DayFinisher(
            program_day_id=days_by_index[day_index].id,
            movement_id=movement.id,
            duration_minutes=6,
            params=spec["params"],
        ))
    db.flush()


# ---------------------------------------------------------------------------
# D1 — Upper Push
# ---------------------------------------------------------------------------

def _seed_d1(db: Session, pd: ProgramDay, lib: Dict[str, int]) -> None:
    # T1 — Bench Press [PB] (anchor; meso-2 bar swap is equipment-level, not a
    #      library movement change, so no MesoRotation row)
    t1 = _add_tier(db, pd.id, "T1", 1, TierKind.T1_STRAIGHT, rounds=1, rest_seconds=120, shoe="Metcon 9")
    _add_te(db, t1.id, "d1_t1", "Bench Press [PB]", lib, 1, "anchor",
            pattern="bench", rep_low=6, rep_high=8, rpe_cap=8.0,
            scheme="STRAIGHT")

    # T1b — Pendlay Row Narrow (anchor). 2026-07-26: promoted out of T2 GS
    # into its own straight-set tier (athlete directive). slot_id stays
    # "d1_t2a" -- the movement's original stable slot_id, unchanged by the
    # tier-label move (same convention as D4's Wide-Grip Pull-up, which kept
    # slot_id "d4_t1" after its T1->T1b tier-label change).
    t1b = _add_tier(db, pd.id, "T1b", 2, TierKind.PAIR, rounds=1, rest_seconds=120, shoe="Metcon 9")
    _add_te(db, t1b.id, "d1_t2a", "Pendlay Row Narrow", lib, 1, "anchor",
            pattern="horizontal_pull", rep_low=6, rep_high=8,
            scheme="DOUBLE_PROGRESSION")

    # T2 GS — Lying Tricep Extension / Incline DB Press / Face-Up Incline Knee Raise
    # (2026-07-26: Lying Tricep Extension is a NEW movement/slot filling the
    # spot vacated by Pendlay Row Narrow's T1b promotion above -- "d1_t2a" is
    # NOT reused here, per the never-reassign-a-slot_id convention.)
    t2 = _add_tier(db, pd.id, "T2 GS", 3, TierKind.GIANT_SET, rounds=3, rest_seconds=90, shoe="Metcon 9")
    _add_te(db, t2.id, "d1_t2d", "Lying Tricep Extension", lib, 1, "free",
            pattern="tricep_extension", rep_low=8, rep_high=12,
            scheme="DOUBLE_PROGRESSION")
    _add_te(db, t2.id, "d1_t2b", "Incline DB Press", lib, 2, "free",
            pattern="vertical_push", rep_low=8, rep_high=12,
            scheme="DOUBLE_PROGRESSION")
    _add_te(db, t2.id, "d1_t2c", "Face-Up Incline Knee Raise", lib, 3, "free",
            pattern="core", rep_low=10, rep_high=15)

    # T3 GS — Pull-up / Cross-Body Lateral Raise / Lat Prayer
    t3 = _add_tier(db, pd.id, "T3 GS", 4, TierKind.GIANT_SET, rounds=3, rest_seconds=75, shoe="Metcon 9")
    _add_te(db, t3.id, "d1_t3a", "Pull-up (2-phase)", lib, 1, "free",
            pattern="vertical_pull", rep_low=8, rep_high=12, scheme="REP_RATIO")
    _add_te(db, t3.id, "d1_t3b", "Cross-Body Lateral Raise", lib, 2, "free",
            pattern="lateral_raise", rep_low=10, rep_high=15,
            scheme="DOUBLE_PROGRESSION")
    _add_te(db, t3.id, "d1_t3c", "Lat Prayer", lib, 3, "free",
            pattern="lat", rep_low=8, rep_high=12, scheme="DOUBLE_PROGRESSION")

    # T4 GS — Seated Cable Row / Ab Wheel Rollout / Cross-Body Rear Delt Fly
    t4 = _add_tier(db, pd.id, "T4 GS", 5, TierKind.GIANT_SET, rounds=3, rest_seconds=60, shoe="Metcon 9")
    _add_te(db, t4.id, "d1_t4a", "Seated Cable Row", lib, 1, "semi",
            pattern="horizontal_pull", rep_low=8, rep_high=12,
            scheme="DOUBLE_PROGRESSION")
    _add_te(db, t4.id, "d1_t4b", "Ab Wheel Rollout", lib, 2, "free",
            pattern="core", rep_low=8, rep_high=8)
    _add_te(db, t4.id, "d1_t4c", "Cross-Body Rear Delt Fly", lib, 3, "free",
            pattern="rear_delt", rep_low=10, rep_high=15,
            scheme="DOUBLE_PROGRESSION")


# ---------------------------------------------------------------------------
# D2 — Lower A
# ---------------------------------------------------------------------------

def _seed_d2(db: Session, pd: ProgramDay, lib: Dict[str, int]) -> None:
    # T1 — Belt Squat (anchor; meso-2 rotation → Back Squat)
    t1 = _add_tier(db, pd.id, "T1", 1, TierKind.T1_STRAIGHT, rounds=1, rest_seconds=150, shoe="Adipower II")
    d2_t1 = _add_te(db, t1.id, "d2_t1", "Belt Squat", lib, 1, "anchor",
                    pattern="squat", rep_low=6, rep_high=8, rpe_cap=8.0,
                    scheme="STRAIGHT")
    _add_mr(db, d2_t1, 2, "Back Squat", lib)

    # T1b — Barbell Hip Thrust (anchor)
    t1b = _add_tier(db, pd.id, "T1b", 2, TierKind.PAIR, rounds=1, rest_seconds=120, shoe="Adipower II")
    _add_te(db, t1b.id, "d2_t1b", "Barbell Hip Thrust (220 cap)", lib, 1, "anchor",
            pattern="hip_thrust", rep_low=6, rep_high=8, rpe_cap=8.0,
            scheme="COMPOSITE")

    # T2 GS — Leg Curl / Scout Reverse Hyper
    t2 = _add_tier(db, pd.id, "T2 GS", 3, TierKind.GIANT_SET, rounds=3, rest_seconds=90, shoe="Adipower II")
    _add_te(db, t2.id, "d2_t2a", "Lying Leg Curl [GHR]", lib, 1, "free",
            rep_low=8, rep_high=12,
            scheme="DOUBLE_PROGRESSION")
    _add_te(db, t2.id, "d2_t2b", "Scout Reverse Hyper (180 cap)", lib, 2, "free",
            pattern="reverse_hyper", rep_low=8, rep_high=12, scheme="REP_AT_CAP")

    # T3 GS — ATG Split Squat / Cable Tib Raise / Reverse Nordic (assisted)
    # (2026-07-22: Reverse Nordic added as D2's 3rd knee-modality slot,
    # replacing the knee-slot count lost when d2_t2a became Leg Curl.)
    t3 = _add_tier(db, pd.id, "T3 GS", 4, TierKind.GIANT_SET, rounds=3, rest_seconds=75, shoe="Adipower II")
    _add_te(db, t3.id, "d2_t3a", "ATG Split Squat", lib, 1, "free",
            knee_modality=KneeModality.KOT, rep_low=8, rep_high=12,
            scheme="DOUBLE_PROGRESSION")
    _add_te(db, t3.id, "d2_t3b", "Cable Tib Raise", lib, 2, "free",
            knee_modality=KneeModality.TIB, rep_low=10, rep_high=15,
            scheme="DOUBLE_PROGRESSION")
    _add_te(db, t3.id, "d2_t3c", "Reverse Nordic (assisted)", lib, 3, "free",
            knee_modality=KneeModality.KOT, rep_low=8, rep_high=12,
            scheme="DOUBLE_PROGRESSION")


# ---------------------------------------------------------------------------
# D4 — Upper Pull
# ---------------------------------------------------------------------------

def _seed_d4(db: Session, pd: ProgramDay, lib: Dict[str, int]) -> None:
    # T1 — Standing Barbell OHP (anchor; 2026-07-23, closes the program's
    # overhead-pressing gap -- no vertical press existed anywhere in the split
    # before this, only horizontal pressing (Bench/Incline) and lateral-raise
    # side-delt work. Placed BEFORE Pull-up (not after, athlete's original
    # request) because Movement.is_primary=True lifts must precede all
    # non-primary movements in session order (validator's PRIMARY_NOT_FIRST
    # rule, session-wide, not tier-label-scoped) -- Pull-up is is_primary=False
    # (assistance-based), so "Pull-up then OHP" is structurally unassemblable
    # regardless of tier naming. Athlete confirmed OHP-first for this phase,
    # intends to revisit ordering (Pull-up first) in a future phase.
    # slot_id "d4_t1b" (not "d4_t1") is intentional: Pull-up keeps its ORIGINAL
    # stable slot_id below despite moving to the T1b tier label -- slot_id must
    # never be reassigned to a different movement (see the T2 GS comment
    # further down for the same convention applied to an exercise_order swap).
    t1 = _add_tier(db, pd.id, "T1", 1, TierKind.T1_STRAIGHT, rounds=1, rest_seconds=120, shoe="Metcon 9")
    _add_te(db, t1.id, "d4_t1_ohp", "Standing OHP [PB]", lib, 1, "anchor",
            pattern="vertical_push", rep_low=6, rep_high=8, scheme="STRAIGHT")

    # T1b — Wide-Grip Pull-up (anchor). 2026-07-26: switched from neutral-grip
    # (shared "Pull-up [TOWER + TUBES]") to a new movement, wide-grip
    # unassisted (athlete directive: neutral-grip 3x8 milestone cleared,
    # switching grips for fresh stimulus). D1's Pull-up slot is unaffected.
    t1b = _add_tier(db, pd.id, "T1b", 2, TierKind.PAIR, rounds=1, rest_seconds=180, shoe="Metcon 9")
    _add_te(db, t1b.id, "d4_t1", "Wide-Grip Pull-up", lib, 1, "anchor",
            pattern="vertical_pull", rep_low=6, rep_high=8, scheme="REP_RATIO")

    # T2 GS — Meadows Row / Face-Up Incline Knee Raise / Single-Arm DB Row
    # (Face-Up Knee moved between Meadows and DB Row, athlete request 2026-07-09;
    # slot_id -> movement mapping is UNCHANGED -- d4_t2b is still Single-Arm DB
    # Row's slot, d4_t2c is still Face-Up Knee's slot -- only the exercise_order
    # values are swapped, since slot_id is a stable key elsewhere (overrides,
    # rep-scheme lookups) and shouldn't be reassigned to a different movement.)
    t2 = _add_tier(db, pd.id, "T2 GS", 3, TierKind.GIANT_SET, rounds=3, rest_seconds=90, shoe="Metcon 9")
    d4_t2a = _add_te(db, t2.id, "d4_t2a", "Meadows Row", lib, 1, "semi",
                     pattern="horizontal_pull", rep_low=8, rep_high=12,
                     scheme="DOUBLE_PROGRESSION")
    _add_mr(db, d4_t2a, 2, "Pendlay Row", lib)
    _add_te(db, t2.id, "d4_t2b", "Single-Arm DB Row", lib, 3, "free",
            pattern="horizontal_pull", rep_low=8, rep_high=12,
            scheme="DOUBLE_PROGRESSION")
    _add_te(db, t2.id, "d4_t2c", "Face-Up Incline Knee Raise", lib, 2, "free",
            pattern="core", rep_low=10, rep_high=15)

    # T3 GS — DB Rear Delt Fly / Andreoni Cable Pullover / PureTorque Pro Rotation
    # (2026-07-26: Dragon Flag replaced by PureTorque Pro Rotation, athlete
    # directive -- new slot "d4_t3d", not a reuse of Dragon Flag's old
    # "d4_t3c" slot_id, per the never-reassign-a-slot_id convention.)
    t3 = _add_tier(db, pd.id, "T3 GS", 4, TierKind.GIANT_SET, rounds=3, rest_seconds=75, shoe="Metcon 9")
    _add_te(db, t3.id, "d4_t3a", "DB Rear Delt Fly", lib, 1, "free",
            pattern="rear_delt", rep_low=8, rep_high=12,
            scheme="DOUBLE_PROGRESSION")
    _add_te(db, t3.id, "d4_t3b", "Andreoni Cable Pullover", lib, 2, "free",
            pattern="lat", rep_low=8, rep_high=12, scheme="DOUBLE_PROGRESSION")
    _add_te(db, t3.id, "d4_t3d", "PureTorque Pro Rotation", lib, 3, "free",
            pattern="rotation", rep_low=8, rep_high=12,
            scheme="DOUBLE_PROGRESSION")


# ---------------------------------------------------------------------------
# D5 — Lower B
# ---------------------------------------------------------------------------

def _seed_d5(db: Session, pd: ProgramDay, lib: Dict[str, int]) -> None:
    # T1 — RDL (anchor; meso-2 rotation → Staggered RDL)
    t1 = _add_tier(db, pd.id, "T1", 1, TierKind.T1_STRAIGHT, rounds=1, rest_seconds=180, shoe="Metcon 9")
    d5_t1 = _add_te(db, t1.id, "d5_t1", "RDL", lib, 1, "anchor",
                    pattern="rdl", rep_low=6, rep_high=8, rpe_cap=8.0,
                    scheme="STRAIGHT")
    # meso-2: RDL → Staggered RDL (distinct movement → resolve-or-raise, MesoRotation row)
    _add_mr(db, d5_t1, 2, "Staggered RDL", lib)

    # T1b — Barbell Hip Thrust (anchor, independent track)
    t1b = _add_tier(db, pd.id, "T1b", 2, TierKind.PAIR, rounds=1, rest_seconds=150, shoe="Metcon 9")
    _add_te(db, t1b.id, "d5_t1b",
            "Barbell Hip Thrust (220 cap, independent track)", lib, 1, "anchor",
            pattern="hip_thrust", rep_low=6, rep_high=8, rpe_cap=8.0,
            scheme="COMPOSITE")

    # T2 GS — Bulgarian Split Squat / Scout Reverse Hyper / Assisted Nordic
    # Shoe swap moved here (was T3): BSS needs the Adipower II heel, so the
    # swap must land before BSS is performed, not after (athlete correction
    # 2026-07-17 -- was previously Metcon 9 through T2, swapping only at T3).
    t2 = _add_tier(db, pd.id, "T2 GS", 3, TierKind.GIANT_SET, rounds=3, rest_seconds=90, shoe="Adipower II")
    _add_te(db, t2.id, "d5_t2a", "Bulgarian Split Squat", lib, 1, "free",
            pattern="lunge", rep_low=8, rep_high=12, scheme="DOUBLE_PROGRESSION")
    # Remapped to the cap=90 "Light Reverse Hyper" movement (athlete correction
    # 2026-07-17: D5 runs a 90lb cap, not D2's 180lb -- previously both days
    # pointed at the same cap=180 movement, so D5 was training the heavier
    # variant by mistake). D6's recovery slot, which used to share this same
    # cap=90 movement with a conflicting FIXED_LOAD rule, got its own new
    # "Reverse Hyper Recovery [REV_HYPER]" movement instead (see seed.py) so
    # each movement now carries exactly one progression rule. Rep range
    # (15-20) was already correct, unchanged.
    d5_t2b = _add_te(db, t2.id, "d5_t2b", "Scout Reverse Hyper (90 cap)", lib, 2, "free",
                     pattern="reverse_hyper", rep_low=15, rep_high=20,
                     scheme="DOUBLE_PROGRESSION")
    # d5_t2b meso-2: the single-leg Reverse Hyper is a DISTINCT library movement
    # (Reverse Hyper - Single Leg [REV_HYPER]) with a 12–15 rep override — a real
    # MesoRotation row (resolve-or-raise), not a coach-side technique note.
    _add_mr(db, d5_t2b, 2, "Scout Reverse Hyper - Single Leg", lib,
            rep_low=12, rep_high=15)
    _add_te(db, t2.id, "d5_t2c", "Assisted Nordic (eccentric)", lib, 3, "free",
            knee_modality=KneeModality.NORDIC, rep_low=8, rep_high=12,
            scheme="ASSISTED")

    # T3 GS — Poliquin / Reverse Nordic (assisted) / Cable Tib / Hyper Pro Calf
    t3 = _add_tier(db, pd.id, "T3 GS", 4, TierKind.GIANT_SET, rounds=3, rest_seconds=60, shoe="Adipower II")
    _add_te(db, t3.id, "d5_t3a", "Poliquin Step-up", lib, 1, "free",
            knee_modality=KneeModality.KOT, rep_low=8, rep_high=12,
            scheme="DOUBLE_PROGRESSION")
    _add_te(db, t3.id, "d5_t3b", "Reverse Nordic (assisted)", lib, 2, "free",
            knee_modality=KneeModality.KOT, rep_low=8, rep_high=12,
            scheme="DOUBLE_PROGRESSION")
    _add_te(db, t3.id, "d5_t3c", "Cable Tib Raise", lib, 3, "free",
            knee_modality=KneeModality.TIB, rep_low=10, rep_high=15,
            scheme="DOUBLE_PROGRESSION")
    _add_te(db, t3.id, "d5_t3d", "Hyper Pro Calf Raise", lib, 4, "free",
            pattern="calf", rep_low=10, rep_high=15, scheme="DOUBLE_PROGRESSION")


# ---------------------------------------------------------------------------
# D6 — Weak Points
# ---------------------------------------------------------------------------

def _seed_d6(db: Session, pd: ProgramDay, lib: Dict[str, int]) -> None:
    # T1 — Dips (bodyweight + band assist). 2026-07-26: moved out of GS1
    # (athlete directive) into its own T1 straight-set slot, 6-8 reps.
    t1 = _add_tier(db, pd.id, "T1", 1, TierKind.T1_STRAIGHT, rounds=1, rest_seconds=120, shoe="Metcon 9")
    _add_te(db, t1.id, "d6_t1", "Dips", lib, 1, "anchor",
            pattern="vertical_push", rep_low=6, rep_high=8,
            scheme="STRAIGHT")

    # GS1 — Pull-up / Hip Thrust / Cable Bicep Curl (fills Dips' vacated slot)
    gs1 = _add_tier(db, pd.id, "GS1", 2, TierKind.GIANT_SET, rounds=3, rest_seconds=90, shoe="Metcon 9")
    # 2026-07-26: 3-way pull-up split (athlete directive) -- D6 gets its own
    # neutral-grip-paused variant, no longer shares D4's Wide-Grip Pull-up.
    _add_te(db, gs1.id, "d6_g1a", "Pull-up - Neutral Grip (Paused)", lib,
            1, "anchor", pattern="vertical_pull", rep_low=5, rep_high=8,
            scheme="REP_RATIO")
    _add_te(db, gs1.id, "d6_g1c", "Hip Thrust (D5 × 0.80, FIXED)", lib, 2, "free",
            pattern="hip_thrust", rep_low=12, rep_high=12, scheme="FIXED",
            derived_from_unified_group="main", derive_ratio=0.8)
    _add_te(db, gs1.id, "d6_g1d", "Cable Bicep Curl", lib, 3, "free",
            pattern="bicep_curl", rep_low=8, rep_high=12,
            scheme="DOUBLE_PROGRESSION")

    # GS2 — Reverse Hyper Recovery / DB Seal Row / Lateral Raise
    gs2 = _add_tier(db, pd.id, "GS2", 3, TierKind.GIANT_SET, rounds=3, rest_seconds=90, shoe="Metcon 9")
    _add_te(db, gs2.id, "d6_g2a", "Reverse Hyper Recovery", lib, 1, "free",
            pattern="reverse_hyper", rep_low=15, rep_high=20, scheme="FIXED",
            rpe_cap=6.0)
    _add_te(db, gs2.id, "d6_g2b", "DB Seal Row", lib, 2, "free",
            pattern="horizontal_pull", rep_low=8, rep_high=12,
            scheme="DOUBLE_PROGRESSION")
    _add_te(db, gs2.id, "d6_g2c", "Lateral Raise", lib, 3, "free",
            pattern="lateral_raise", rep_low=10, rep_high=15,
            scheme="DOUBLE_PROGRESSION")

    # GS3 — Face Pull / Cable V-Bar Pushdown / T-Bar Row Wide
    gs3 = _add_tier(db, pd.id, "GS3", 4, TierKind.GIANT_SET, rounds=3, rest_seconds=60, shoe="Metcon 9")
    _add_te(db, gs3.id, "d6_g3a", "Face Pull", lib, 1, "free",
            pattern="rear_delt", rep_low=15, rep_high=20,
            scheme="DOUBLE_PROGRESSION")
    _add_te(db, gs3.id, "d6_g3b", "Cable V-Bar Pushdown", lib, 2, "semi",
            pattern="triceps", rep_low=8, rep_high=12, scheme="SINGLE_SESSION")
    _add_te(db, gs3.id, "d6_g3c", "T-Bar Row Wide", lib, 3, "semi",
            pattern="horizontal_pull", rep_low=8, rep_high=12,
            scheme="DOUBLE_PROGRESSION")
