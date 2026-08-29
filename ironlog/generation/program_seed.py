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
    WeekParityRotation,
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
    "Stryker Pad Seated OHP":                       "Stryker Pad Seated OHP [DB]",
    "Matrix Machine Preacher Curl":                 "Matrix Machine Preacher Curl [EZ]",
    "Pull-up (2-phase)":                            "Pull-up [TOWER + TUBES]",
    "Cross-Body Lateral Raise":                     "Cross-Body Cable Lateral Raise [FT]",
    "Cross-Body Rear Delt Fly":                     "Cross-Body Cable Rear Delt Fly [FT]",
    "Seated Cable Row":                             "Seated Cable Row [FT]",
    "Ab Wheel Rollout":                             "Ab Wheel [WHEEL]",
    "Lat Prayer":                                   "Lat Prayer [ANDREONI + FT]",
    "Better Fly Sagittal Lat Pulldown":              "Better Fly Sagittal Lat Pulldown [FT]",
    # ── D2 Lower A ───────────────────────────────────────────────────────────
    "Belt Squat":                                   "Belt Squat [GHR + FT]",
    "Barbell Hip Thrust (220 cap)":                 "Hip Thrust [HIP_THRUST]",
    "Assisted Nordic":                              "Nordic Curl [GHR]",
    "Scout Reverse Hyper (180 cap)":               "Reverse Hyper [REV_HYPER]",
    "Cable Tib Raise":                              "Cable Tibialis Raise",
    "Matrix Machine Sissy Squat":                   "Matrix Machine Sissy Squat",
    "Nordic Curl Max":                               "Nordic Curl Max [Ares]",
    "Nordic Curl Max [Apex]":                        "Nordic Curl Max [Apex]",
    "Lying Leg Curl [GHR + Ares]":                   "Lying Leg Curl [GHR + Ares]",
    "Hybrid Board Calf Raise D2":                   "Hybrid Board Calf Raise [D2]",
    "Ab Trainer Decline Sit-up":                    "Ab Trainer Decline Sit-up",
    # ── D4 Upper Pull ────────────────────────────────────────────────────────
    "Wide-Grip Pull-up":                             "Wide-Grip Pull-up [TOWER]",
    "Meadows Row":                                  "Meadows Row [OB + LM]",
    "Meadows SA Row":                               "Meadows Row [OB + LM]",
    "DB Rear Delt Fly":                             "Rear Delt Fly [DB]",
    "Seated BTN OHP":                               "Seated BTN OHP [PB]",
    "Better Fly Lat Pulldown":                      "Better Fly Lat Pulldown [FT]",
    "Stryker Pad CSR Barbell":                      "Stryker Pad CSR Barbell [PB]",
    "Better Fly Cable Pullover":                    "Better Fly Cable Pullover [FT]",
    "Ab Trainer Hanging Leg Raise":                 "Ab Trainer Hanging Leg Raise",
    # ── D5 Lower B ───────────────────────────────────────────────────────────
    "RDL":                                          "RDL [PB]",
    "Barbell Hip Thrust (220 cap, independent track)": "Hip Thrust [HIP_THRUST]",
    "Bulgarian Split Squat":                        "Bulgarian Split Squat [DB]",
    "Scout Reverse Hyper (90 cap)":                 "Light Reverse Hyper [REV_HYPER]",
    "Scout Reverse Hyper - Single Leg":             "Reverse Hyper - Single Leg [REV_HYPER]",
    "Assisted Nordic (eccentric)":                  "Nordic Curl [GHR]",
    "Reverse Nordic (assisted)":                    "Reverse Nordic Curl [GHR]",
    "Hyper Pro Calf Raise":                         "Calf Raise [GHR]",
    # 2026-08-12 (STAB maintenance-block redesign, Task 4): D5 turnover.
    # 2026-08-29: athlete directive -- actually trained with a barbell,
    # repointed to the new "Kickstand RDL [PB]" row (see seed.py comment).
    "Kickstand RDL":                                "Kickstand RDL [PB]",
    "Nordic Max Bulgarian Split Squat":              "Nordic Max Bulgarian Split Squat",
    "Matrix Machine Bulgarian Split Squat":          "Matrix Machine Bulgarian Split Squat",
    "Better Fly Kickback":                          "Better Fly Kickback [FT]",
    "Hybrid Board Calf Raise D5":                   "Hybrid Board Calf Raise [D5]",
    "Hybrid Board Tib Raise D5":                    "Hybrid Board Tib Raise [D5]",
    "Better Fly Hip Adduction":                     "Better Fly Hip Adduction [FT]",
    "Ab Trainer Russian Twist":                      "Ab Trainer Russian Twist",
    # 2026-08-12: D2 follow-up correction, bundled into Task 4's branch (see
    # _seed_d2 below) -- Cable Tib Raise replaced by the Hybrid Board variant.
    "Hybrid Board Tib Raise D2":                    "Hybrid Board Tib Raise [D2]",
    # ── D6 Weak Points ───────────────────────────────────────────────────────
    # 2026-07-26: 3-way pull-up split -- D6 no longer shares D4's Wide-Grip
    # Pull-up, gets its own neutral-grip-paused movement.
    #
    # 2026-08-12 (STAB maintenance-block redesign, Task 5): D6's Hip Thrust
    # (d6_g1c) removed entirely -- 3rd and final removal of this redesign
    # (D2 Task 2, D5 Task 4, D6 here); D6's own T1 tier (Dips) eliminated,
    # Dips folds into GS1 alongside the pull-up and the new close-grip bench;
    # GS2 fully turned over (Reverse Hyper Recovery/DB Seal Row/Lateral Raise
    # all drop out -- NOTE: the task-5-brief.md's "Removed" list mislabeled
    # T-Bar Row Wide / Cable V-Bar Pushdown as "GS2's current members" and
    # omitted GS2's real members entirely -- actual current-state code has
    # T-Bar Row Wide / Cable V-Bar Pushdown in GS3, not GS2; verified against
    # ironlog/generation/program_seed.py's pre-Task-5 _seed_d6, corrected
    # here per this task's own "verify against actual code" instruction).
    # Cable Bicep Curl (old d6_g1d) also drops out, replaced by Better Fly
    # Cable Bicep Curl in GS2.
    # 2026-08-12 (STAB maintenance-block redesign fix, post-Task-5): D6's
    # pull-up slot repointed from the retired "Pull-up - Neutral Grip
    # (Paused)" (mapping kept below, harmless orphan) to the new "Wide-Grip
    # Pull-up (D6 Assisted)" -> "Wide-Grip Pull-up [TOWER + TUBES]" -- see
    # docs/superpowers/specs/2026-08-10-stab-maintenance-block-redesign-
    # design.md §5. Distinct program-name key from D1's plain "Wide-Grip
    # Pull-up" (-> "Wide-Grip Pull-up [TOWER]", unassisted) since they now
    # resolve to two different Movement rows.
    "Wide-Grip Pull-up (D6 Assisted)":               "Wide-Grip Pull-up [TOWER + TUBES]",
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
    # 2026-08-12 (Task 5): "Close-Grip Bench Camber-14" REUSES the existing
    # "Swiss Bar CG Press [SB]" movement (derived_from Bench Press [PB],
    # start_ratio 0.90 -- 155 * 0.90 = 139.5, dead center of the FINAL doc's
    # own wk1_calibration_estimate 135-145) rather than creating a new
    # movement. This deliberately deviates from task-5-brief.md, which
    # called for a NEW movement ("3rd grip variant alongside D1's 21" and
    # D4's 7""); the repo's own established precedent (D1 Bench Press's 21"
    # grip note, D4's Lying Tricep Extension [SB] reused as-is for its 7"
    # grip -- see that movement's seed.py comment: "grip is a PHYSICAL-SETUP
    # DETAIL, not a schema field or a new movement identity... matches the
    # EZ-curl-family precedent of separate rows only where named grip
    # variants actually COEXIST and need disambiguation, which isn't the
    # case here") says grip width alone does NOT warrant a new Movement row,
    # and "Swiss Bar CG Press [SB]" (unwired anywhere in the current program)
    # is exactly the close-grip-bench-on-the-camber-bar movement already in
    # the library. Was never wired anywhere in the program before this task.
    "Close-Grip Bench Camber-14":                   "Swiss Bar CG Press [SB]",
    "Better Fly Cable Bicep Curl":                  "Better Fly Cable Bicep Curl [FT]",
    "D-Handle Cable Bicep Curl":                    "D-Handle Cable Bicep Curl [FT]",
    "Stryker Pad CSR Cables":                       "Stryker Pad CSR Cables [FT]",
    "Better Fly Rear Delt Extension":               "Better Fly Rear Delt Extension [FT]",
    "Better Fly OH Tricep Extension":               "Better Fly OH Tricep Extension [FT]",
    "AbMat Ab Bench Pad Cable Crunch":              "AbMat Ab Bench Pad Cable Crunch [FT]",
    "Seated Leg Extension":                         "Seated Leg Extension [GHR + FT]",
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
    # 2026-08-13: D4's T1 anchor since the STAB maintenance-block redesign
    # (Task 3, 2026-08-11) -- plate-loaded barbell press, same heavy-anchor
    # ramp treatment as every other T1 primary. Missed when Task 3 swapped
    # it in for the old Standing OHP [PB] anchor; found live (no warmup
    # ramp sets were generating for it, unlike D1's Bench Press).
    "Seated BTN OHP [PB]",
    # 2026-08-29: D5's T1 anchor, repointed from the DB variant to a barbell
    # (athlete directive). Same recurring omission class as Seated BTN OHP
    # above -- flagging it here at the same time as the anchor swap itself.
    "Kickstand RDL [PB]",
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
      - d2_t1: Belt Squat → Back Squat (meso-2) -- the ONLY real meso-2
        rotation left program-wide as of 2026-08-12 (STAB Task 4): D4's
        d4_t2a → Pendlay Row rotation was fully retired in Task 3 (not
        carried to any new slot), and D5's d5_t1 → Staggered RDL /
        d5_t2b → Reverse Hyper - Single Leg rotations were both retired in
        this task (Task 4) along with the slots that carried them. There is
        currently no real adaptive/"free"-role meso rotation anywhere in
        the program -- d2_t1 is an anchor-role example only. Tests needing
        an adaptive-role example now use a synthetic, test-only
        MesoRotation row (see test_generation_context.py, test_slot_
        override_skeleton.py, test_generation_fallback.py).

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


def _add_wpr(db: Session, te: TierExercise, week_parity: str,
             prog_name: str, lib: Dict[str, int],
             rep_low: Optional[int] = None,
             rep_high: Optional[int] = None) -> WeekParityRotation:
    wpr = WeekParityRotation(
        tier_exercise_id=te.id,
        week_parity=week_parity,
        movement_id=_resolve(prog_name, lib),
        rep_low=rep_low,
        rep_high=rep_high,
    )
    db.add(wpr)
    return wpr


def _seed_finishers(db: Session, days_by_index: Dict[int, ProgramDay]) -> None:
    finishers = {
        1: {
            "name": "kb_swing",
            "params": {
                "weight_lb": 30,
                "work_seconds_per_minute": 40,
                "rest_seconds_per_minute": 20,
                "target_reps_per_minute": 15,
                "equipment": ["kettlebell_30"],
            },
        },
        2: {
            "name": "sled_push",
            "params": {
                "resistance_level": 8,
                "work_seconds_per_minute": 20,
                "rest_seconds_per_minute": 30,
                "equipment": ["dreadmill"],
            },
        },
        4: {
            "name": "slam_ball",
            "params": {
                "weight_lb": 30,
                "target_reps_per_minute": 8,
                "scheme": "emom",
                "equipment": ["slam_ball_30"],
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
                "work_seconds": 20,
                "rest_seconds": 10,
                "rounds_per_block": 8,
                "blocks": 2,
                "inter_block_rest_seconds": 75,
                # legacy field, superseded by work_seconds/rest_seconds below for
                # the new "scheme": "tabata" clients -- kept for any code path
                # still reading it (live_seed_ramp_and_finishers.py)
                "work_seconds_per_minute": 30,
                "target_reps_per_minute": 40,
                "scheme": "tabata",
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
    # T1 — Bench Press [PB] (anchor). 2026-08-10: global T1/T1b rep range
    # drop 6-8 -> 4-6 (maintenance block, athlete directive, real Wk1
    # execution locked 155x3x6 @ RPE8). Equipment note (Belle Mere BMF
    # Camber Bar, 21" grip) is a physical-setup detail, not a schema field
    # -- same movement, load_code unchanged.
    t1 = _add_tier(db, pd.id, "T1", 1, TierKind.T1_STRAIGHT, rounds=1, rest_seconds=120, shoe="Metcon 9")
    _add_te(db, t1.id, "d1_t1", "Bench Press [PB]", lib, 1, "anchor",
            pattern="bench", rep_low=4, rep_high=6, rpe_cap=8.0,
            scheme="STRAIGHT")

    # T1b — Pendlay Row Narrow (anchor). 2026-08-10: held at 170 while the
    # strain heals (real Wk1 executed 170x3x8, over the new 4-6 rep cap --
    # logged as-is, the hold is on load not on stopping mid-set). Rep range
    # drops 6-8 -> 4-6 alongside every other T1/T1b primary. slot_id stays
    # "d1_t2a" -- the movement's original stable slot_id, unchanged by the
    # 2026-07-26 tier-label move (T2 GS -> its own T1b).
    t1b = _add_tier(db, pd.id, "T1b", 2, TierKind.PAIR, rounds=1, rest_seconds=120, shoe="Metcon 9")
    _add_te(db, t1b.id, "d1_t2a", "Pendlay Row Narrow", lib, 1, "anchor",
            pattern="horizontal_pull", rep_low=4, rep_high=6,
            scheme="DOUBLE_PROGRESSION")

    # T2 GS — Stryker Pad Seated OHP / Matrix Machine Preacher Curl / Better
    # Fly Standing Lateral Raise. 2026-08-10: full T2 GS turnover -- Lying
    # Tricep Extension, Incline DB Press, and Face-Up Incline Knee Raise
    # (old d1_t2d/d1_t2b/d1_t2c) all drop out of D1 entirely, replaced by
    # three movements from real Wk1 execution (all WK1_LOCKED, all new
    # slots per the never-reassign-a-slot_id convention -- none of the
    # vacated slot_ids are reused). D1's core requirement is fully covered
    # by Ab Wheel in T3 below, so no core movement returns to T2.
    t2 = _add_tier(db, pd.id, "T2 GS", 3, TierKind.GIANT_SET, rounds=3, rest_seconds=90, shoe="Metcon 9")
    _add_te(db, t2.id, "d1_t2f", "Stryker Pad Seated OHP", lib, 1, "free",
            pattern="vertical_push", rep_low=8, rep_high=12,
            scheme="DOUBLE_PROGRESSION")
    _add_te(db, t2.id, "d1_t2g", "Matrix Machine Preacher Curl", lib, 2, "free",
            pattern="bicep_curl", rep_low=8, rep_high=12,
            scheme="DOUBLE_PROGRESSION")
    _add_te(db, t2.id, "d1_t2e", "Better Fly Standing Lateral Raise", lib, 3, "free",
            pattern="lateral_raise", rep_low=10, rep_high=15,
            scheme="DOUBLE_PROGRESSION")

    # T3 GS — Wide-Grip Pull-up (dead-hang) / Better Fly Sagittal Lat Pulldown /
    # Ab Wheel Rollout.
    # 2026-08-10: switched from assisted neutral-grip (Pull-up [TOWER +
    # TUBES]) to unassisted Wide-Grip dead-hang -- athlete directive, real
    # Wk1 executed 4/4/4. Cross-Body Lateral Raise (old d1_t3b) dropped
    # entirely (Better Fly Standing Lateral Raise in T2 above covers that
    # role now). Ab Wheel Rollout RELOCATES here from its old T4 GS slot
    # (d1_t4b) -- D1's mandatory core slot (anti-extension pattern), kept
    # after the athlete confirmed proper bracing technique resolves the
    # earlier hyperextension-strain concern. It's an existing movement
    # moving tiers, not a new one, but gets a fresh slot_id (d1_t3d) per
    # the never-reassign convention -- d1_t3b/d1_t4b are vacated, not
    # reused.
    # 2026-08-13: Lat Prayer [ANDREONI + FT] (old d1_t3c) replaced by Better
    # Fly Sagittal Lat Pulldown [FT] (athlete directive -- the Andreoni
    # station's lat-prayer motion isn't reproducible on the Better Fly cuff;
    # the sagittal-plane pulldown is the correct Better Fly substitute).
    # Genuinely new movement filling a vacated spot -> fresh slot_id
    # "d1_t3e" (never-reassign-slot_id); d1_t3c is vacated, not reused. Lat
    # Prayer [ANDREONI + FT] stays ACTIVE in the library, unwired from every
    # day now -- not deleted, per the never-delete-orphans convention.
    t3 = _add_tier(db, pd.id, "T3 GS", 4, TierKind.GIANT_SET, rounds=3, rest_seconds=75, shoe="Metcon 9")
    _add_te(db, t3.id, "d1_t3a", "Wide-Grip Pull-up", lib, 1, "free",
            pattern="vertical_pull", rep_low=4, rep_high=6, scheme="REP_RATIO")
    _add_te(db, t3.id, "d1_t3e", "Better Fly Sagittal Lat Pulldown", lib, 2, "free",
            pattern="lat", rep_low=8, rep_high=12, scheme="DOUBLE_PROGRESSION")
    _add_te(db, t3.id, "d1_t3d", "Ab Wheel Rollout", lib, 3, "free",
            pattern="core", rep_low=8, rep_high=12, scheme="REP_LADDER")

    # T4 GS tier is fully removed. 2026-08-10: once Ab Wheel Rollout moves
    # to T3 above, its only other two members -- Seated Cable Row (old
    # d1_t4a, semi) and Cross-Body Rear Delt Fly (old d1_t4c) -- both drop
    # out of D1 entirely per the real Wk1 execution (neither appears in the
    # FINAL source doc's D1 session). No Tier row or TierExercise rows are
    # created for them; the Movement rows themselves stay in the library,
    # just unwired from D1. Orphaned MovementState rows at their old
    # slot_ids are left in place, not deleted.


# ---------------------------------------------------------------------------
# D2 — Lower A
# ---------------------------------------------------------------------------

def _seed_d2(db: Session, pd: ProgramDay, lib: Dict[str, int]) -> None:
    # T1 — Belt Squat (anchor; meso-2 rotation → Back Squat). 2026-08-11:
    # global T1/T1b rep range drop 6-8 -> 4-6 (maintenance block, matches
    # every other day's T1 in this redesign). Belt Squat itself is otherwise
    # unchanged (still 260 cap, still STRAIGHT scheme, REP_LADDER rule).
    t1 = _add_tier(db, pd.id, "T1", 1, TierKind.T1_STRAIGHT, rounds=1, rest_seconds=150, shoe="Adipower II")
    d2_t1 = _add_te(db, t1.id, "d2_t1", "Belt Squat", lib, 1, "anchor",
                    pattern="squat", rep_low=4, rep_high=6, rpe_cap=8.0,
                    scheme="STRAIGHT")
    _add_mr(db, d2_t1, 2, "Back Squat", lib)

    # T1b (Barbell Hip Thrust) is REMOVED ENTIRELY -- 2026-08-11 STAB
    # maintenance-block redesign, Task 2. Not just the movement: the whole
    # tier drops (docs/program/source/2026-08-10-maintenance-block-seed-data-
    # FINAL.md's D2 section has no Hip Thrust anywhere). No Tier or
    # TierExercise row is created for it. The Movement itself
    # (Hip Thrust [HIP_THRUST]) stays ACTIVE in the library -- it's still
    # wired on D5 and D6 -- just unwired from D2. The orphaned MovementState
    # row at the old d2_t1b slot is left in place, not deleted, per the
    # never-delete-orphans convention (this is the first of three Hip Thrust
    # removals across this redesign -- D5/D6 follow in later tasks).

    # T2 GS — Matrix Machine Sissy Squat / Nordic Curl Max [Ares]. 2026-08-11:
    # full T2 GS turnover -- Lying Leg Curl [GHR] and Scout Reverse Hyper
    # (180 cap) both drop out of D2 entirely (old d2_t2a/d2_t2b slots
    # vacated, not reused), replaced by two brand-new movements matching the
    # FINAL doc's real T2 GS composition. Both new slots are needs-
    # calibration (zero prior history, no BASELINES entry -- baseline_seed.py).
    # Nordic Curl Max [Ares] carries knee_modality=NORDIC on this
    # TierExercise (plan-owner directive, 2026-08-11): it's a literal Nordic
    # curl variant and the program's only other NORDIC-tagged slot is D5's
    # d5_t2c, against KNEE_TARGETS["NORDIC"]=2/week -- this closes that gap.
    # Same Movement row is referenced again by D5/Task 4 (shared identity,
    # day-scoped MovementState) -- Task 4 must independently tag ITS
    # TierExercise knee_modality=NORDIC too; knee_modality lives on
    # TierExercise per-slot, not on Movement, so this tag does not carry
    # forward automatically. Matrix Machine Sissy Squat carries
    # knee_modality=SISSY (plan-owner directive) -- matches the FINAL doc's
    # own knee_health_note on this movement (VMO/deep knee flexion) and is
    # the program's first-ever wired SISSY slot (KNEE_TARGETS["SISSY"]=1/week,
    # previously unmet program-wide).
    #
    # 2026-08-12: Matrix Machine Sissy Squat temporarily swapped for Reverse
    # Nordic Curl [GHR] on this same slot (d2_t2d, knee_modality KOT) --
    # equipment unavailable (attachment part on order), explicitly temporary/
    # reversible, live-DB-only (no code/seed.py change at the time).
    #
    # 2026-08-19 (athlete directive): Sissy Squat attachment now available --
    # RESTORED. Back to Matrix Machine Sissy Squat / knee_modality=SISSY on
    # d2_t2d, matching this function's original (and intended long-term)
    # design.
    #
    # 2026-08-19 (athlete directive): D2's T4 (Ab Trainer Decline Sit-up,
    # previously its own standalone straight tier) merged INTO this giant
    # set as a 3rd member -- see the T4 removal note below. Fresh slot
    # "d2_t2f" (never-reassign-slot_id -- d2_t4a is vacated, not reused;
    # mirrors D6's Dips T1->GS1 tier-move precedent). tier_role="free"
    # (GIANT_SET-member convention, not "anchor" -- it was T4's own solo
    # anchor role, which doesn't carry over to a giant-set slot). Same rep
    # target (10-15) and pattern ("core"), scheme left unset so it inherits
    # the movement's own REP_RATIO (assisted incline-angle progression, per
    # the 2026-08-12 fix) -- unchanged from T4's own TierExercise, which
    # also left scheme unset.
    #
    # 2026-08-19 (athlete directive, same day): Ab Trainer Decline Sit-up
    # (d2_t2f) directly traded GS placement with T3 GS to deconflict
    # bench-attachment contention within the giant set (mirrors the D6
    # Dips/CG-Press direct-trade precedent). Ab Trainer now lives in T3 GS
    # (see that block below). First attempt paired this with Hybrid Board
    # Tib Raise [D2] moving here in exchange, but Tib Raise needs the same
    # full-ankle-dorsiflexion flat shoe as ATG Split Squat (2026-08-14 T3
    # shoe fix) -- moving it to T2's heeled Adipower II reintroduced that
    # exact conflict. Revised same day (athlete directive): Hybrid Board
    # Calf Raise [D2] (d2_t3d) takes this 3rd T2 GS slot instead -- calf
    # raises are far less shoe-sensitive than the TIB pattern's ankle
    # dorsiflexion requirement, so no new shoe conflict here. Tib Raise
    # (d2_t3e) stays in T3 GS with the flat shoe it needs. Swapped
    # tier_id/exercise_order only; slot_id, pattern, rep targets, scheme
    # are unchanged/movement-intrinsic, per this session's established
    # tier-swap convention.
    t2 = _add_tier(db, pd.id, "T2 GS", 2, TierKind.GIANT_SET, rounds=3, rest_seconds=90, shoe="Adipower II")
    _add_te(db, t2.id, "d2_t2d", "Matrix Machine Sissy Squat", lib, 1, "free",
            knee_modality=KneeModality.SISSY, rep_low=8, rep_high=12,
            scheme="DOUBLE_PROGRESSION")
    # 2026-08-20 (athlete directive): d2_t2e now rotates A/B automatically
    # via WeekParityRotation (feature/week-parity-rotation) instead of
    # staying pinned to Nordic Curl Max [Ares] year-round -- week "A" =
    # Nordic Curl Max [Apex] (Apex bench attachment, angle-adjustable,
    # unassisted, 4-8 reps, working toward a true flat/0deg Nordic), week
    # "B" = Nordic Curl Max [Ares] (flat + 2x Rogue Monster band assist,
    # 8-12 reps). te.movement_id/rep_low/rep_high stay Nordic Curl Max
    # [Ares]'s own values as the fallback (used only if a WeekParityRotation
    # row is ever missing). knee_modality=NORDIC stays on the TierExercise
    # regardless of which movement resolves for a given week -- both
    # variants are the same NORDIC knee pattern.
    d2_t2e = _add_te(db, t2.id, "d2_t2e", "Nordic Curl Max", lib, 2, "free",
            knee_modality=KneeModality.NORDIC, rep_low=8, rep_high=12,
            scheme="REP_RATIO")
    _add_wpr(db, d2_t2e, "A", "Nordic Curl Max [Apex]", lib, rep_low=4, rep_high=8)
    _add_wpr(db, d2_t2e, "B", "Nordic Curl Max", lib, rep_low=8, rep_high=12)
    _add_te(db, t2.id, "d2_t3d", "Hybrid Board Calf Raise D2", lib, 3, "free",
            pattern="calf", rep_low=10, rep_high=15,
            scheme="DOUBLE_PROGRESSION")

    # T3 GS — ATG Split Squat (unchanged) / Hybrid Board Calf Raise [D2]
    # (new) / Hybrid Board Tib Raise [D2] (new, 2026-08-12 follow-up
    # correction). 2026-08-11: Reverse Nordic Curl [GHR] (old d2_t3c) drops
    # out of D2 entirely -- not in the FINAL doc's D2 T3 GS composition
    # (still wired on D5, unaffected). ATG Split Squat keeps its existing
    # stable slot_id (d2_t3a) unchanged -- retained movement, not a new one,
    # so the never-reassign-slot_id convention doesn't apply there (that
    # rule is about not giving an OLD slot_id to a DIFFERENT movement, not
    # about renaming a movement's own stable identity). Hybrid Board Calf
    # Raise [D2] gets a fresh slot_id (d2_t3d), no knee_modality (calf work,
    # not part of the docs/06 §4 knee taxonomy). Rest 75 -> 60: the FINAL
    # doc explicitly states "T3 GS -- 3 items, 60s rest, 3 rounds" for D2
    # (current 75s was pre-existing staleness this task reconciles away),
    # corroborated by D5's already-implemented T3 GS at rest_seconds=60 for
    # the identical tier shape.
    #
    # 2026-08-12 follow-up (plan-owner directive, delivered mid-Task-4/D5,
    # small standalone fix bundled onto this branch -- see ironlog/seed.py
    # for the corresponding new Movement row): Cable Tib Raise (the OLD
    # shared D2/D5 TIB movement, mapped to "Cable Tibialis Raise") is being
    # replaced program-wide by a new "Hybrid Board Tib Raise" movement,
    # mirroring the existing Hybrid Board Calf Raise per-day pattern -- D2
    # gets its own separate "[D2]" row (D5's is a genuinely separate "[D5]"
    # row too, see _seed_d5, NOT shared between the two days, same treatment
    # as the calf-raise pair). The old d2_t3b slot_id is VACATED, not
    # reused, per the never-reassign-slot_id rule -- Hybrid Board Tib Raise
    # is a genuinely different movement filling this role, not a rename of
    # Cable Tibialis Raise. Fresh slot_id d2_t3e carries knee_modality=TIB
    # forward (Cable Tibialis Raise itself stays ACTIVE in the library, now
    # fully unwired program-wide since D5 also drops it -- not deleted, per
    # the never-delete-orphans convention).
    # 2026-08-14 (athlete directive): T3 GS's shoe switched from Adipower II
    # to Metcon 9 (flat) -- Hybrid Board Tib Raise [D2] needs full ankle
    # dorsiflexion range that Adipower's elevated heel restricts. ATG Split
    # Squat also wants a flat shoe for the same reason (maximal ankle
    # dorsiflexion is the whole point of "ATG"), so this fixes both members
    # at once. No documented rationale existed for T3 needing the heel in
    # the first place (unlike T2's BSS-family movements, which do).
    t3 = _add_tier(db, pd.id, "T3 GS", 3, TierKind.GIANT_SET, rounds=3, rest_seconds=60, shoe="Metcon 9")
    _add_te(db, t3.id, "d2_t3a", "ATG Split Squat", lib, 1, "free",
            knee_modality=KneeModality.KOT, rep_low=8, rep_high=12,
            scheme="DOUBLE_PROGRESSION")
    # 2026-08-19 (athlete directive, revised): Hybrid Board Tib Raise [D2]
    # (was T2 GS's temporary d2_t3e placement) moved back here -- see the
    # T2 GS block's 2026-08-19 note for why (shoe conflict with the
    # Calf-Raise-for-Tib-Raise revision). Keeps the flat Metcon 9 shoe this
    # movement needs (2026-08-14 fix). Slot_id/config unchanged.
    _add_te(db, t3.id, "d2_t3e", "Hybrid Board Tib Raise D2", lib, 2, "free",
            knee_modality=KneeModality.TIB, rep_low=10, rep_high=15,
            scheme="DOUBLE_PROGRESSION")
    # 2026-08-19 (athlete directive): direct trade with T2 GS's Ab Trainer
    # Decline Sit-up (see that block's 2026-08-19 note) -- deconflicts
    # bench-attachment contention. Slot_id d2_t2f carries over unchanged
    # (movement-intrinsic identity, tier-swap convention).
    _add_te(db, t3.id, "d2_t2f", "Ab Trainer Decline Sit-up", lib, 3, "free",
            pattern="core", rep_low=10, rep_high=15)

    # T4 straight tier (added 2026-08-11, Ab Trainer Decline Sit-up) removed
    # 2026-08-19 -- merged into T2 GS above as slot d2_t2f (3rd giant-set
    # member, athlete directive). See the T2 GS block's 2026-08-19 note.
    # (Same day, later: d2_t2f traded into T3 GS here; d2_t3e traded into
    # T2 GS above.)


# ---------------------------------------------------------------------------
# D4 — Upper Pull
# ---------------------------------------------------------------------------

def _seed_d4(db: Session, pd: ProgramDay, lib: Dict[str, int]) -> None:
    # T1 — Seated BTN OHP (anchor). 2026-08-11 (maintenance block, STAB
    # redesign, Task 3): replaces Standing OHP [PB] -- FINAL doc's D4 T1 is
    # seated, behind-the-neck, on the APEX Bench upright (Config D), same
    # [PB] bracket. Standing OHP [PB] stays ACTIVE in the library (still
    # needs-cal, unwired from every day now) -- not deleted, per the never-
    # delete-orphans convention. Fresh slot_id "d4_t1_btn_ohp" -- this is a
    # genuinely different movement filling a vacated anchor, not the one
    # explicitly-allowed reassignment case (that's T1b below), so it does
    # NOT reuse the old "d4_t1_ohp" slot_id. Rep range drops 6-8 -> 4-6,
    # matching the FINAL doc and every other T1 primary this redesign.
    t1 = _add_tier(db, pd.id, "T1", 1, TierKind.T1_STRAIGHT, rounds=1, rest_seconds=120, shoe="Metcon 9")
    _add_te(db, t1.id, "d4_t1_btn_ohp", "Seated BTN OHP", lib, 1, "anchor",
            pattern="vertical_push", rep_low=4, rep_high=6, scheme="STRAIGHT")

    # T1b — Better Fly Lat Pulldown (anchor, REPLACES Wide-Grip Pull-up).
    # 2026-08-11: athlete directive (grip-free vertical pull isolation, cable
    # tension throughout ROM) -- FINAL doc explicitly frames this as D4's
    # T1b anchor slot changing content, not a new slot (D4 loses pull-ups,
    # gains Better Fly Lat Pulldown; D1/D6 keep their own pull-up work,
    # total direct pull-up frequency drops 3x/week -> 2x/week program-wide).
    # Reuses slot_id "d4_t1" -- the ONE explicitly-allowed reassignment case
    # per this task's brief, same treatment as D1's T1b promotion precedent.
    # Rep range 6-8, RPE_8_STANDARD (cable double-progression, not the old
    # PULL_UP_ROLLING_MAX rule).
    t1b = _add_tier(db, pd.id, "T1b", 2, TierKind.PAIR, rounds=1, rest_seconds=180, shoe="Metcon 9")
    _add_te(db, t1b.id, "d4_t1", "Better Fly Lat Pulldown", lib, 1, "anchor",
            pattern="vertical_pull", rep_low=6, rep_high=8, scheme="DOUBLE_PROGRESSION")

    # T2 GS — Stryker Pad CSR Barbell / Ab Trainer Hanging Leg Raise / Better
    # Fly Cable Pullover. 2026-08-11: FULL T2 GS turnover -- Meadows Row [OB
    # + LM] (old d4_t2a, carried a meso-2 rotation to Pendlay Row -- that
    # rotation is DROPPED, not carried to any new slot; the program's other
    # adaptive-slot meso-rotation example lives at D5's d5_t2b, unaffected),
    # Single-Arm DB Row [DB] (old d4_t2b), and Face-Up Incline Knee Raise
    # (old d4_t2c) all drop out of D4's wiring entirely -- none are
    # referenced by any other day, so all three become fully unwired
    # (Movement rows stay ACTIVE in the library, MovementState rows at their
    # old slot_ids are left in place, per the never-delete-orphans
    # convention). Old slot_ids d4_t2a/d4_t2b/d4_t2c are VACATED, not reused
    # -- all three new members get fresh slot_ids (d4_t2d/d4_t2e/d4_t2f).
    # All three new movements are needs-calibration (zero prior history).
    #
    # 2026-08-20 (code/live reconciliation, athlete confirmed live is
    # correct): Ab Trainer Hanging Leg Raise (d4_t2e) and PureTorque Pro
    # Rotation (d4_t3d) direct-traded T2/T3 placement at some earlier point
    # this session live-DB-only (an Apex/Stryker Pad equipment-conflict fix,
    # never committed to code -- this closes that gap). PureTorque now
    # lives in T2 GS; Ab Trainer Hanging Leg Raise moves to T3 GS (see that
    # block below). Swapped tier_id/exercise_order only; slot_id, pattern,
    # rep targets, scheme are unchanged/movement-intrinsic, per this
    # session's established tier-swap convention.
    t2 = _add_tier(db, pd.id, "T2 GS", 3, TierKind.GIANT_SET, rounds=3, rest_seconds=90, shoe="Metcon 9")
    _add_te(db, t2.id, "d4_t2d", "Stryker Pad CSR Barbell", lib, 1, "free",
            pattern="horizontal_pull", rep_low=8, rep_high=12,
            scheme="DOUBLE_PROGRESSION")
    _add_te(db, t2.id, "d4_t3d", "PureTorque Pro Rotation", lib, 2, "free",
            pattern="rotation", rep_low=8, rep_high=12,
            scheme="DOUBLE_PROGRESSION")
    _add_te(db, t2.id, "d4_t2f", "Better Fly Cable Pullover", lib, 3, "free",
            pattern="lat", rep_low=10, rep_high=15, scheme="DOUBLE_PROGRESSION")

    # T3 GS — DB Rear Delt Fly (unchanged slot, rep range widens 8-12 ->
    # 10-15 per the FINAL doc) / Lying Tricep Extension [SB] (REUSED, not a
    # new movement -- see the dated comment on that Movement row in
    # ironlog/seed.py; its D1-original slot dropped it in Task 1, D4 now
    # wires it fresh at "d4_t3e", not the vacated Andreoni slot "d4_t3b" --
    # never-reassign-slot_id) / PureTorque Pro Rotation (unchanged slot
    # "d4_t3d", unchanged reps -- already IS the FINAL doc's
    # `cable_woodchopper` entry, same equipment [ares_high_pulley,
    # puretorque_pro], confirmed via Step 1 verification, no rewiring
    # needed). Andreoni Cable Pullover (old d4_t3b) drops out of D4's wiring
    # entirely, not referenced by any other day -- fully unwired, not
    # deleted.
    #
    # 2026-08-20 (athlete directive): DB Rear Delt Fly (d4_t3a) replaced by
    # Better Fly Rear Delt Extension [FT] -- genuinely different Movement
    # row (not a reconfiguration of the same one), so per this session's
    # never-reassign-slot_id precedent (mirrors D1's Lat Prayer -> Better
    # Fly Sagittal Lat Pulldown swap) this gets a fresh slot_id "d4_t3f";
    # d4_t3a is VACATED, not reused. Same rep target (10-15) and pattern
    # ("rear_delt") as the movement it replaces, matching this Movement's
    # own D6 wiring (d6_g2f) for consistency. Better Fly Rear Delt
    # Extension [FT] is an EXISTING shared Movement row (already wired on
    # D6 GS1) -- day-scoped MovementState, independent assist/load track.
    #
    # 2026-08-20 (same reconciliation as T2 GS above): PureTorque Pro
    # Rotation moved OUT of this tier into T2 GS; Ab Trainer Hanging Leg
    # Raise (d4_t2e) moves IN here instead (was T2 GS). Slot_id/config
    # unchanged for both, tier-swap only.
    t3 = _add_tier(db, pd.id, "T3 GS", 4, TierKind.GIANT_SET, rounds=3, rest_seconds=75, shoe="Metcon 9")
    _add_te(db, t3.id, "d4_t3f", "Better Fly Rear Delt Extension", lib, 1, "free",
            pattern="rear_delt", rep_low=10, rep_high=15,
            scheme="DOUBLE_PROGRESSION")
    _add_te(db, t3.id, "d4_t3e", "Lying Tricep Extension", lib, 2, "free",
            pattern="tricep_extension", rep_low=8, rep_high=12,
            scheme="DOUBLE_PROGRESSION")
    _add_te(db, t3.id, "d4_t2e", "Ab Trainer Hanging Leg Raise", lib, 3, "free",
            pattern="core", rep_low=8, rep_high=12)


# ---------------------------------------------------------------------------
# D5 — Lower B
# ---------------------------------------------------------------------------

def _seed_d5(db: Session, pd: ProgramDay, lib: Dict[str, int]) -> None:
    # T1 — Kickstand RDL [PB] (anchor). 2026-08-12 (maintenance block, STAB
    # redesign, Task 4): replaces RDL [PB] -- unilateral DB RDL, B-stance
    # (front foot flat, back foot on ball for balance), rep range 6-8 -> 4-6
    # (FINAL doc, matches every other T1 primary this redesign). Fresh
    # slot_id "d5_t1_kickstand_rdl" -- this is a genuinely different
    # movement filling a vacated anchor (mirrors Task 3's D4 "d4_t1_btn_ohp"
    # precedent for the same class of T1 swap), NOT a reuse of the old
    # "d5_t1" slot_id. RDL [PB] stays ACTIVE in the library, now unwired
    # from every day -- not deleted, per the never-delete-orphans
    # convention (D5's meso-2 rotation to Staggered RDL, previously seeded
    # on the old d5_t1, is dropped along with it -- not carried to this new
    # slot; the FINAL doc's D5 T1 has no meso-rotation entry, and Kickstand
    # RDL has no obvious same-family bilateral/staggered variant to rotate
    # into anyway). Needs-calibration, zero prior history.
    t1 = _add_tier(db, pd.id, "T1", 1, TierKind.T1_STRAIGHT, rounds=1, rest_seconds=180, shoe="Metcon 9")
    _add_te(db, t1.id, "d5_t1_kickstand_rdl", "Kickstand RDL", lib, 1, "anchor",
            pattern="rdl", rep_low=4, rep_high=6, rpe_cap=8.0,
            scheme="STRAIGHT")

    # T1b (Barbell Hip Thrust) is REMOVED ENTIRELY -- 2026-08-12 STAB
    # maintenance-block redesign, Task 4. Second of three Hip Thrust
    # removals across this redesign (D2 done in Task 2, D5 here, D6 still
    # to come in Task 6). No Tier or TierExercise row is created for it.
    # Hip Thrust [HIP_THRUST] stays ACTIVE in the library -- still wired on
    # D6 -- just unwired from D5. The orphaned MovementState row at the old
    # d5_t1b slot is left in place, not deleted. D6's derived HT slot
    # (`derived_from_unified_group="main", derive_ratio=0.8`) still reads
    # the shared HtProgressionState("main") row created by the 2026-07
    # backfill -- that row is left untouched (per this task's explicit
    # instruction): it becomes progressively more orphaned/frozen now that
    # neither D2 nor D5 has a live `unified_ht_group="main"` TierExercise
    # to keep it updated, which is expected, not a bug to fix here.
    #
    # UPDATE (Task 5, 2026-08-12): D6's derived HT slot (d6_g1c) referenced
    # above is now ALSO removed (see _seed_d6 below) -- the 3rd and final Hip
    # Thrust removal across this redesign. Zero TierExercise rows anywhere in
    # the program now set derived_from_unified_group or unified_ht_group; the
    # HtProgressionState("main") row and the whole derive-push loop in
    # loop.py's commit_session are now fully orphaned/dead code paths (never
    # fire -- confirmed loop.py's `derived_tes` query against an empty result
    # set is a clean no-op, does not raise). Left in place per this task's
    # explicit instruction; flagged for Task 7 (final verification) to note
    # as a cleanup candidate, not something to remove here.

    # 2026-08-22 (athlete directive): full T2 GS/T3 GS/T4 restructure into
    # two 4-member giant sets, GS1 and GS2 -- T4 (Ab Trainer Russian Twist)
    # is ELIMINATED as its own tier, folded into GS1 instead (mirrors D6's
    # Dips T1->GS1 fold-in precedent exactly). All member slot_ids carry
    # over unchanged from their prior tiers (movement-intrinsic identity,
    # this session's established tier-swap convention -- no movement here
    # is changing identity, only tier placement). Below is a historical
    # index of what used to live in the T2 GS / T3 GS / T4 tiers this
    # replaces, kept for the never-reassign-slot_id audit trail:
    #
    # Old T2 GS (2026-08-11 through 2026-08-20 history): Bulgarian Split
    # Squat [DB] (old d5_t2a) / Light Reverse Hyper [REV_HYPER] (old
    # d5_t2b, carried a meso-2 rotation to Reverse Hyper - Single Leg --
    # dropped) / Nordic Curl [GHR] (old d5_t2c) all dropped 2026-08-12,
    # replaced by Nordic Max Bulgarian Split Squat (d5_t2d, itself replaced
    # 2026-08-14 by Matrix Machine Bulgarian Split Squat at fresh slot
    # d5_t2h -- Nordic Max rig conflict with Nordic Curl Max), Nordic Curl
    # Max [Ares] (d5_t2e, itself replaced 2026-08-20 by Lying Leg Curl
    # [GHR + Ares] at fresh slot d5_t2i -- D5 dropped its Nordic slot
    # entirely, D2 carries the program's sole weekly Nordic exposure now),
    # and Better Fly Kickback (d5_t2f, unchanged since 2026-08-12).
    #
    # Old T3 GS: Poliquin Step-up (old d5_t3a, KOT) and Cable Tib Raise
    # (old d5_t3c, TIB) dropped 2026-08-12, not in the FINAL doc's D5 T3 GS;
    # Hyper Pro Calf Raise (old d5_t3d) also dropped, replaced by the
    # Hybrid Board equipment variant. Reverse Nordic Curl [GHR] (d5_t3b)
    # unchanged since inception. Hybrid Board Calf Raise [D5] (d5_t3e) and
    # Better Fly Hip Adduction (d5_t3g) new 2026-08-12. Hybrid Board Tib
    # Raise [D5] (d5_t3f) new 2026-08-12 plan-owner addendum, preserving
    # the program's 2x/week TIB-modality invariant.
    #
    # Old T4 (straight, tier_role="anchor"): Ab Trainer Russian Twist
    # (d5_t4a), D5's mandatory rotational-pattern core slot since
    # 2026-08-12. Now folds into GS1 as tier_role="free" (GIANT_SET-member
    # convention, not "anchor" -- mirrors every other tier-fold-in this
    # session, e.g. D6's Dips, D2's T4->T2GS merge).
    #
    # New GS1 (Metcon 9, flat shoe): Lying Leg Curl [GHR + Ares] (d5_t2i),
    # Ab Trainer Russian Twist (d5_t4a), Hybrid Board Tib Raise [D5]
    # (d5_t3f), Better Fly Hip Adduction (d5_t3g). Flat shoe carries
    # forward from the movements' own prior shoe assignments (Tib Raise
    # specifically needs full ankle dorsiflexion, per the 2026-08-14 T3
    # shoe fix -- Adipower's heel would restrict it).
    gs1 = _add_tier(db, pd.id, "GS1", 2, TierKind.GIANT_SET, rounds=3, rest_seconds=60, shoe="Metcon 9")
    _add_te(db, gs1.id, "d5_t2i", "Lying Leg Curl [GHR + Ares]", lib, 1, "free",
            pattern="leg_curl", rep_low=10, rep_high=15,
            scheme="DOUBLE_PROGRESSION")
    _add_te(db, gs1.id, "d5_t4a", "Ab Trainer Russian Twist", lib, 2, "free",
            pattern="core", rep_low=10, rep_high=15)
    _add_te(db, gs1.id, "d5_t3f", "Hybrid Board Tib Raise D5", lib, 3, "free",
            knee_modality=KneeModality.TIB, rep_low=10, rep_high=15,
            scheme="DOUBLE_PROGRESSION")
    _add_te(db, gs1.id, "d5_t3g", "Better Fly Hip Adduction", lib, 4, "free",
            pattern="adduction", rep_low=10, rep_high=15,
            scheme="DOUBLE_PROGRESSION")

    # New GS2 (Adipower II, heeled shoe): Matrix Machine Bulgarian Split
    # Squat (d5_t2h), Reverse Nordic Curl [GHR] (d5_t3b), Hybrid Board Calf
    # Raise [D5] (d5_t3e), Better Fly Kickback (d5_t2f). Heeled shoe
    # carries forward from BSS's prior shoe assignment (needs the heel for
    # depth, per the 2026-08-11/14 history above).
    gs2 = _add_tier(db, pd.id, "GS2", 3, TierKind.GIANT_SET, rounds=3, rest_seconds=90, shoe="Adipower II")
    _add_te(db, gs2.id, "d5_t2h", "Matrix Machine Bulgarian Split Squat", lib, 1, "free",
            pattern="lunge", rep_low=8, rep_high=12,
            scheme="DOUBLE_PROGRESSION")
    _add_te(db, gs2.id, "d5_t3b", "Reverse Nordic (assisted)", lib, 2, "free",
            knee_modality=KneeModality.KOT, rep_low=8, rep_high=12,
            scheme="DOUBLE_PROGRESSION")
    _add_te(db, gs2.id, "d5_t3e", "Hybrid Board Calf Raise D5", lib, 3, "free",
            pattern="calf", rep_low=10, rep_high=15,
            scheme="DOUBLE_PROGRESSION")
    _add_te(db, gs2.id, "d5_t2f", "Better Fly Kickback", lib, 4, "free",
            pattern="glute", rep_low=10, rep_high=15,
            scheme="DOUBLE_PROGRESSION")


# ---------------------------------------------------------------------------
# D6 — Weak Points
# ---------------------------------------------------------------------------

def _seed_d6(db: Session, pd: ProgramDay, lib: Dict[str, int]) -> None:
    # 2026-08-12 (maintenance block, STAB redesign, Task 5): D6's standalone
    # T1 tier (Dips) is ELIMINATED ENTIRELY -- the FINAL doc's D6 section has
    # NO standalone T1 tier at all; Dips folds back into GS1 (3 items: pull-
    # up, dips, close-grip bench), matching GS1's original pre-2026-07-26
    # shape. Tier orders renumber sequentially now that T1 is gone: GS1=1,
    # GS2=2, GS3=3 (matches D2/D5's identical renumbering precedent when
    # they lost a leading tier).
    #
    # d6_g1a (pull-up) -- CORRECTED 2026-08-12 (STAB maintenance-block
    # redesign fix, post-Task-5). Task 5 originally left this slot pointed
    # at "Pull-up - Neutral Grip (Paused) [TOWER]" after confirming via
    # `git log --all -S` that no movement literally named "Wide-Grip
    # Pull-up [TOWER + TUBES]" had ever existed in this repo -- that check
    # was correct, but the conclusion drawn from it was wrong: the fix was
    # to CREATE that movement, not leave the old slot unchanged.
    # docs/superpowers/specs/2026-08-10-stab-maintenance-block-redesign-
    # design.md §5 is explicit and was never revised on this point --
    # "Pull-up - Neutral Grip (Paused) [TOWER]" is named there as "D6's
    # earlier in-conversation unassisted variant, also superseded," and the
    # design doc's confirmed-final pull-up architecture (2 days/week: D1
    # unassisted wide-grip dead-hang, D6 ASSISTED wide-grip via sling +
    # single 20lb band, D4 loses pull-ups entirely) puts a NEW movement,
    # "Wide-Grip Pull-up [TOWER + TUBES]", at D6's d6_g1a slot -- same
    # slot_id (same conceptual anchor position, matches the Tasks 1/3
    # same-slot-reassignment precedent), rep range unchanged (5-8), scheme
    # unchanged (REP_RATIO), but the underlying movement is new and its
    # progression_rule is ASSISTANCE_REDUCTION (wired via rule_wiring.py's
    # YAML, not the pull_up_rolling_max the FINAL source doc's raw yaml
    # block literally lists for this slot -- the design doc, dated the same
    # day and framed as authoritative over any earlier draft, is followed
    # here per explicit coordinator instruction; flagged as a FINAL-doc-vs-
    # design-doc inconsistency worth the plan owner's attention, not
    # resolved further here). "Pull-up - Neutral Grip (Paused) [TOWER]"
    # stays ACTIVE in the library, now unwired from every day -- not
    # deleted, per the never-delete-orphans convention.
    gs1 = _add_tier(db, pd.id, "GS1", 1, TierKind.GIANT_SET, rounds=3, rest_seconds=90, shoe="Metcon 9")
    _add_te(db, gs1.id, "d6_g1a", "Wide-Grip Pull-up (D6 Assisted)", lib,
            1, "anchor", pattern="vertical_pull", rep_low=5, rep_high=8,
            scheme="REP_RATIO")
    # Dips -- moves from its own vacated T1 tier (d6_t1) into GS1. Per the
    # never-reassign-slot_id convention (D1's Ab Wheel Rollout precedent:
    # a tier move gets a FRESH slot_id even for an unchanged movement), this
    # is a new slot "d6_g1e" -- d6_t1 (and the earlier-vacated d6_g1b) stay
    # vacated, not reused. Reps 6-8 -> 8-12, scheme STRAIGHT ->
    # DOUBLE_PROGRESSION, rule assistance_reduction -> rpe_8_standard,
    # matching the Movement-level ASSISTED->LADDER conversion in
    # ironlog/seed.py (see that file's comment for the full reasoning: FINAL
    # doc's D6 dips equipment lists NO assistance gear and current_load: 150
    # is the movement's exact original pre-2026-07-26 cable-loaded baseline).
    # 2026-08-16 (athlete directive, revised): Close-Grip Bench Camber-14
    # (d6_g1f) and Dips (d6_g1e) directly traded GS placement -- Dips +
    # CG Press are both heavy compound pressing movements (triceps/chest
    # dominant), rotating them together in the same giant set creates
    # interference (no recovery between them). CG Press now sits here in
    # GS1 (Dips moves to GS2, see below); Better Fly Rear Delt Extension
    # (d6_g2f) stays in GS1 from the earlier interim fix. Swapped tier_id/
    # exercise_order only; slot_id, pattern, rep targets, scheme are
    # unchanged/movement-intrinsic, per this session's established
    # tier-swap convention (mirrors the D4 Ab Trainer/PureTorque
    # Apex-conflict fix).
    _add_te(db, gs1.id, "d6_g1f", "Close-Grip Bench Camber-14", lib, 2, "free",
            pattern="bench", rep_low=4, rep_high=6, rpe_cap=8.0,
            scheme="DOUBLE_PROGRESSION")
    _add_te(db, gs1.id, "d6_g2f", "Better Fly Rear Delt Extension", lib, 3, "free",
            pattern="rear_delt", rep_low=10, rep_high=15,
            scheme="DOUBLE_PROGRESSION")

    # GS2 -- FULL turnover (2026-08-12, Task 5). Reverse Hyper Recovery,
    # DB Seal Row, and Lateral Raise (old d6_g2a/b/c) all drop out of D6's
    # wiring entirely -- none referenced by any other day, so all three
    # become fully unwired (Movement rows stay ACTIVE in the library,
    # MovementState rows at their old slot_ids left in place, per the
    # never-delete-orphans convention). Old slot_ids d6_g2a/b/c VACATED, not
    # reused -- new members get fresh slots d6_g2d/e/f. All three new
    # movements are needs-calibration (zero prior history).
    #
    # 2026-08-16 (athlete directive, revised): Dips (was GS1's d6_g1e)
    # traded places with Close-Grip Bench Camber-14 -- now paired with
    # Bicep Curl and Stryker Pad CSR Cables (both pull-ish/isolation), no
    # press-press overlap.
    #
    # 2026-08-16 (athlete directive, effective next week): Better Fly Cable
    # Bicep Curl (d6_g2d) replaced by D-Handle Cable Bicep Curl -- the Better
    # Fly cuff doesn't work well for curls, athlete switched to D-handles
    # mid-session. Genuinely new movement filling a vacated spot -> fresh
    # slot_id "d6_g2g" (never-reassign-slot_id); d6_g2d is vacated, not
    # reused. Better Fly Cable Bicep Curl [FT] stays ACTIVE in the library,
    # unwired -- not deleted.
    #
    # 2026-08-23 (athlete directive): reverted back to Better Fly Cable
    # Bicep Curl [FT] -- d6_g2g (D-Handle) VACATED in turn, not reused
    # (never-reassign-slot_id applies to reverts too, even back to a
    # movement that previously occupied an earlier vacated slot -- d6_g2d
    # stays vacated, this is a genuinely fresh identity-change event, not
    # an undo of a live-only deviation). Fresh slot_id "d6_g2h". D-Handle
    # Cable Bicep Curl stays ACTIVE in the library, unwired -- not deleted.
    gs2 = _add_tier(db, pd.id, "GS2", 2, TierKind.GIANT_SET, rounds=3, rest_seconds=90, shoe="Metcon 9")
    _add_te(db, gs2.id, "d6_g2h", "Better Fly Cable Bicep Curl", lib, 1, "free",
            pattern="bicep_curl", rep_low=10, rep_high=15,
            scheme="DOUBLE_PROGRESSION")
    _add_te(db, gs2.id, "d6_g2e", "Stryker Pad CSR Cables", lib, 2, "free",
            pattern="horizontal_pull", rep_low=8, rep_high=12,
            scheme="DOUBLE_PROGRESSION")
    # 2026-08-16 (athlete directive): scheme DOUBLE_PROGRESSION -> REP_RATIO,
    # matching the Movement-level LADDER->ASSISTED conversion (see
    # ironlog/seed.py's Dips comment) -- back to band-assisted, this time
    # as a plain CABLE_LB assist value, not the old discrete band-count
    # ladder from the 2026-07-26 experiment.
    _add_te(db, gs2.id, "d6_g1e", "Dips", lib, 3, "free",
            pattern="vertical_push", rep_low=8, rep_high=12,
            scheme="REP_RATIO")

    # GS3 -- Face Pull RETAINED (unchanged slot_id d6_g3a, existing movement)
    # but rep range corrected 15-20 -> 10-15 per the FINAL doc's face_pull
    # entry (rep_low 10, rep_high 15) -- same treatment as D5's retained
    # Reverse Nordic Curl slot (d5_t3b), which also kept its slot_id while
    # gaining a fresh exercise_order/rep range. Cable V-Bar Pushdown and
    # T-Bar Row Wide (old d6_g3b/c) drop out of D6's wiring entirely -- not
    # in the FINAL doc's D6 GS3 composition, no other day references them.
    # Old slot_ids d6_g3b/c VACATED, not reused -- new members get fresh
    # slots d6_g3d/e.
    gs3 = _add_tier(db, pd.id, "GS3", 3, TierKind.GIANT_SET, rounds=3, rest_seconds=60, shoe="Metcon 9")
    _add_te(db, gs3.id, "d6_g3a", "Face Pull", lib, 1, "free",
            pattern="rear_delt", rep_low=10, rep_high=15,
            scheme="DOUBLE_PROGRESSION")
    _add_te(db, gs3.id, "d6_g3d", "Better Fly OH Tricep Extension", lib, 2, "free",
            pattern="tricep_extension", rep_low=8, rep_high=12,
            scheme="DOUBLE_PROGRESSION")
    _add_te(db, gs3.id, "d6_g3e", "AbMat Ab Bench Pad Cable Crunch", lib, 3, "free",
            pattern="core", rep_low=10, rep_high=15,
            scheme="DOUBLE_PROGRESSION")
    _add_te(db, gs3.id, "d6_g3f", "Seated Leg Extension", lib, 4, "free",
            pattern="leg_extension", rep_low=10, rep_high=15,
            scheme="DOUBLE_PROGRESSION")
