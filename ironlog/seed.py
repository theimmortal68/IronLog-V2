"""
seed.py — create the database and load the locked reference data, plus all
103 library movements. Run from the repo root:

    python -m ironlog.seed
"""
from datetime import datetime, timezone

from sqlmodel import select

from . import migrate
from .db import create_db_and_tables, engine, get_session
from .models import (
    BandCalStatus, BandPair, CalibrationStatus, EngineState, Equipment,
    KneeModality, LiftCategory, LoadUnit, Movement, MovementState, Objective,
    Phase, PhasePolicy, ProgressionMode, Region, Scheme, Status,
    StickingPointTaxonomy,
)

# locked equipment floors (verification sweep)
EQUIPMENT = [
    ("Barbell - Double Black Diamond", 45, 2.5, LoadUnit.LB),
    ("Barbell - Gladiator WL", 45, 2.5, LoadUnit.LB),
    ("BMF Camber Bar", 30, 2.5, LoadUnit.LB),
    ("Kyoto EZ Curl Bar", 35, 2.5, LoadUnit.LB),
    ("Dumbbells (MX100)", 10, 2.5, LoadUnit.LB_PER_HAND),
    ("Ares cable (single)", 10, 2.5, LoadUnit.CABLE_LB),
    ("Ares cable (dual)", 20, 5, LoadUnit.CABLE_LB),
    ("Hyper Pro belt attach", 0, 2.5, LoadUnit.LB),
    ("Scout reverse hyper", 0, 2.5, LoadUnit.LB),
    ("GMWD hip thrust", None, None, LoadUnit.LB),
    ("PureTorque Pro", 10, 2.5, LoadUnit.CABLE_LB),
    ("Kettlebell", 13, None, LoadUnit.LB),
    ("Pull-up tower", None, None, LoadUnit.BODYWEIGHT),
    ("Tubes", None, None, LoadUnit.TUBE),
]

# bracket-code -> Equipment.name (Fork 2 dictionary). Codes NOT here are
# tag-only/support/conditioning and never become load_equipment_id.
CODE_TO_EQUIP = {
    "PB": "Barbell - Double Black Diamond",
    "OB": "Barbell - Gladiator WL",
    "SB": "BMF Camber Bar",
    "EZ": "Kyoto EZ Curl Bar",
    "DB": "Dumbbells (MX100)",
    "FT": "Ares cable (single)",
    "ANDREONI": "Ares cable (dual)",
    "GHR": "Hyper Pro belt attach",
    "HIP_THRUST": "GMWD hip thrust",
    "REV_HYPER": "Scout reverse hyper",
    "TOWER": "Pull-up tower",
    "TUBES": "Tubes",
    "KB": "Kettlebell",
}

# progression-model phase envelopes (locked)
PHASES = [
    (Phase.CALIBRATION, Objective.MEASURE, 7, 8, 8, 8, False, "normal", None, None),
    (Phase.CUT, Objective.MAINTAIN, 6, 7.5, 8, 8, False,
     "trimmed (1 top + 1-2 backoff)", 5.0, 3),
    (Phase.STAB, Objective.MAINTAIN, 6, 7.5, 8, 8, False,
     "maintenance (+1 backoff vs CUT)", 5.0, 3),
    (Phase.REBUILD, Objective.PROGRESS, 7, 9, 9, 9, True,
     "graduates over 12 wks, deload/5", None, None),
]

# calibrated HT band pairs (x1.15 table, #0 anchored, #5 unusable)
# rest = rated/side x2, peak = rated/side x5 (HT band-composite formula, Task 1)
BANDS = [("#0 Orange", 18, 45, True), ("#1 Red", 36, 90, True),
         ("#2 Blue", 60, 150, True), ("#3 Green", 80, 200, True),
         ("#4 Black", 130, 325, True), ("#5 Purple", 190, 475, True)]

# per-lift sticking-point options (seed)
TAXONOMY = {
    "BENCH": ["OFF_CHEST", "MIDRANGE", "LOCKOUT", "ELBOWS_FLARED", "LEFT_RIGHT", "SOLID"],
    "BACK_SQUAT": ["OUT_OF_HOLE", "MIDRANGE", "HIPS_SHOOT_UP", "KNEES_CAVE", "LEFT_RIGHT", "SOLID"],
    "OHP": ["OFF_SHOULDER", "MIDRANGE", "LOCKOUT", "LOWER_BACK_ARCH", "LEFT_RIGHT", "SOLID"],
    "RDL": ["OFF_BOTTOM", "MIDRANGE", "LOCKOUT_HIPS", "GRIP", "BACK_ROUNDING", "LEFT_RIGHT", "SOLID"],
}

# Schema per entry (omit a key to take the model default):
#   name (str, required)            base_name (str, required)
#   region (Region)                 lift_category (LiftCategory)
#   is_primary (bool)               status (Status)
#   knee_modality (KneeModality|None)
#   load_code (str|None)            # the SINGLE load-bearing bracket code, or None
#   tags (list[str])                # ALL bracket codes (equipment_tags)
#   progression_mode (ProgressionMode)   scheme (Scheme)
#   increment_ladder (list[float])  min_step (float|None)  load_floor (float|None)  cap (float|None)
#   rpe_capped (bool)               rpe_cap_exempt (bool)   band_eligible (bool)
#   family (str|None)               is_family_anchor (bool)
#   derived_from (str|None)         # the ANCHOR's `name` (resolved to id in pass 2)
#   start_ratio (float|None)        objective_override (Objective|None)   notes (str|None)

MOVEMENTS = [
    # ─────────────────────────────────────────────────────────────────────────
    # T1 rpe_capped lifts (6 — rotating squat slot + bench/OHP/RDL). ALL are
    # STRAIGHT as of the kill-TOPSET_BACKOFF fix (deploy/migrations/015) — the
    # program has no top-set/back-off anywhere; the last TOPSET_BACKOFF
    # stragglers (Back Squat, Front Squat, OHP) were flipped here because Back
    # Squat is d2_t1's meso-2 rotation and would otherwise reintroduce the
    # 148.5-class fractional-backoff bug when the program rotates to it.
    # ─────────────────────────────────────────────────────────────────────────
    dict(name="Back Squat [PB]", base_name="Back Squat", region=Region.LOWER,
         lift_category=LiftCategory.BACK_SQUAT, is_primary=True, status=Status.ACTIVE,
         load_code="PB", tags=["PB"], progression_mode=ProgressionMode.LADDER,
         scheme=Scheme.STRAIGHT, increment_ladder=[10, 5, 2.5], min_step=2.5,
         load_floor=45, rpe_capped=True, family="back_squat", is_family_anchor=True, primary_muscle="QUADS", secondary_muscles=["GLUTES", "ADDUCTORS"]),
    dict(name="Front Squat [PB]", base_name="Front Squat", region=Region.LOWER,
         lift_category=LiftCategory.FRONT_SQUAT, is_primary=True, status=Status.ACTIVE,
         load_code="PB", tags=["PB"], progression_mode=ProgressionMode.LADDER,
         scheme=Scheme.STRAIGHT, increment_ladder=[10, 5, 2.5], min_step=2.5,
         load_floor=45, rpe_capped=True,
         derived_from="Back Squat [PB]", start_ratio=0.80, primary_muscle="QUADS", secondary_muscles=["GLUTES", "ADDUCTORS"]),
    dict(name="Belt Squat [GHR + FT]", base_name="Belt Squat", region=Region.LOWER,
         lift_category=LiftCategory.BACK_SQUAT, is_primary=True, status=Status.ACTIVE,
         load_code="GHR", tags=["GHR", "FT"], progression_mode=ProgressionMode.LADDER,
         scheme=Scheme.STRAIGHT, increment_ladder=[10, 5, 2.5], min_step=2.5,
         load_floor=0, rpe_capped=True, family="belt_squat", is_family_anchor=True, primary_muscle="QUADS", secondary_muscles=["GLUTES", "ADDUCTORS"]),
    dict(name="Bench Press [PB]", base_name="Bench Press", region=Region.UPPER,
         lift_category=LiftCategory.BENCH, is_primary=True, status=Status.ACTIVE,
         load_code="PB", tags=["PB"], progression_mode=ProgressionMode.LADDER,
         scheme=Scheme.STRAIGHT, increment_ladder=[5, 2.5], min_step=2.5,
         load_floor=45, rpe_capped=True, family="bench", is_family_anchor=True, primary_muscle="MID_LOWER_CHEST", secondary_muscles=["FRONT_DELT", "TRICEPS"]),
    dict(name="Standing OHP [PB]", base_name="Standing OHP", region=Region.UPPER,
         lift_category=LiftCategory.OHP, is_primary=True, status=Status.ACTIVE,
         load_code="PB", tags=["PB"], progression_mode=ProgressionMode.LADDER,
         scheme=Scheme.STRAIGHT, increment_ladder=[5, 2.5], min_step=2.5,
         load_floor=45, rpe_capped=True, family="ohp", is_family_anchor=True, primary_muscle="FRONT_DELT", secondary_muscles=["SIDE_DELT", "TRICEPS"]),
    dict(name="RDL [PB]", base_name="RDL", region=Region.LOWER,
         lift_category=LiftCategory.RDL, is_primary=True, status=Status.ACTIVE,
         load_code="PB", tags=["PB"], progression_mode=ProgressionMode.LADDER,
         scheme=Scheme.STRAIGHT, increment_ladder=[10, 5, 2.5], min_step=2.5,
         load_floor=45, rpe_capped=True, family="rdl", is_family_anchor=True, primary_muscle="HAMSTRINGS", secondary_muscles=["GLUTES", "SPINAL_ERECTORS"]),

    # ─────────────────────────────────────────────────────────────────────────
    # Primary STRAIGHT lifts (Box rides back_squat e1RM; DLs own-baseline)
    # ─────────────────────────────────────────────────────────────────────────
    dict(name="Box Squat [PB]", base_name="Box Squat", region=Region.LOWER,
         lift_category=LiftCategory.BACK_SQUAT, is_primary=True, status=Status.ACTIVE,
         load_code="PB", tags=["PB"], progression_mode=ProgressionMode.LADDER,
         scheme=Scheme.STRAIGHT, increment_ladder=[10, 5, 2.5], min_step=2.5,
         load_floor=45, derived_from="Back Squat [PB]", start_ratio=0.90, primary_muscle="QUADS", secondary_muscles=["GLUTES", "ADDUCTORS"]),
    dict(name="Conventional DL [PB]", base_name="Conventional DL", region=Region.LOWER,
         lift_category=LiftCategory.DEADLIFT, is_primary=True, status=Status.ACTIVE,
         load_code="PB", tags=["PB"], progression_mode=ProgressionMode.LADDER,
         scheme=Scheme.STRAIGHT, increment_ladder=[10, 5, 2.5], min_step=2.5,
         load_floor=45, family="conventional_dl", is_family_anchor=True, primary_muscle="GLUTES", secondary_muscles=["HAMSTRINGS", "SPINAL_ERECTORS", "QUADS"]),
    dict(name="Sumo DL [PB]", base_name="Sumo DL", region=Region.LOWER,
         lift_category=LiftCategory.DEADLIFT, is_primary=True, status=Status.ACTIVE,
         load_code="PB", tags=["PB"], progression_mode=ProgressionMode.LADDER,
         scheme=Scheme.STRAIGHT, increment_ladder=[10, 5, 2.5], min_step=2.5,
         load_floor=45, family="sumo_dl", is_family_anchor=True, primary_muscle="GLUTES", secondary_muscles=["ADDUCTORS", "QUADS", "SPINAL_ERECTORS", "HAMSTRINGS"]),
    dict(name="Bent Over Row [PB]", base_name="Bent Over Row", region=Region.UPPER,
         lift_category=LiftCategory.ROW, is_primary=True, status=Status.ACTIVE,
         load_code="PB", tags=["PB"], progression_mode=ProgressionMode.LADDER,
         scheme=Scheme.STRAIGHT, increment_ladder=[5, 2.5], min_step=2.5, load_floor=45, primary_muscle="MID_BACK", secondary_muscles=["LATS", "REAR_DELT", "BICEPS"]),
    # Program-required meso-2 lower rotation (v0.6 fix wave — STRAIGHT, not TOPSET_BACKOFF)
    dict(name="Staggered RDL [PB]", base_name="Staggered RDL", region=Region.LOWER,
         lift_category=LiftCategory.RDL, is_primary=True, status=Status.ACTIVE,
         load_code="PB", tags=["PB"], progression_mode=ProgressionMode.LADDER,
         scheme=Scheme.STRAIGHT, increment_ladder=[10, 5, 2.5], min_step=2.5,
         load_floor=45, unilateral=True, primary_muscle="HAMSTRINGS", secondary_muscles=["GLUTES", "SPINAL_ERECTORS"]),

    # ─────────────────────────────────────────────────────────────────────────
    # Hip Thrust composite family
    # ─────────────────────────────────────────────────────────────────────────
    dict(name="Hip Thrust [HIP_THRUST]", base_name="Hip Thrust", region=Region.LOWER,
         lift_category=LiftCategory.HIP_THRUST, status=Status.ACTIVE,
         load_code="HIP_THRUST", tags=["HIP_THRUST"],
         progression_mode=ProgressionMode.COMPOSITE, scheme=Scheme.STRAIGHT,
         rpe_cap_exempt=True, band_eligible=True,
         family="hip_thrust", is_family_anchor=True, primary_muscle="GLUTES", secondary_muscles=["HAMSTRINGS"]),
    dict(name="Banded Hip Thrust [HIP_THRUST]", base_name="Banded Hip Thrust",
         region=Region.LOWER, lift_category=LiftCategory.HIP_THRUST, status=Status.ACTIVE,
         load_code="HIP_THRUST", tags=["HIP_THRUST"],
         progression_mode=ProgressionMode.COMPOSITE, scheme=Scheme.STRAIGHT,
         rpe_cap_exempt=True, band_eligible=True, family="hip_thrust", primary_muscle="GLUTES", secondary_muscles=["HAMSTRINGS"]),
    dict(name="Banded BW Hip Thrust [BAND]", base_name="Banded BW Hip Thrust",
         region=Region.LOWER, status=Status.ACTIVE,
         load_code=None, tags=["BAND"],
         progression_mode=ProgressionMode.PROTOCOL, scheme=Scheme.STRAIGHT,
         band_eligible=True, family="hip_thrust", primary_muscle="GLUTES", secondary_muscles=["HAMSTRINGS"]),

    # ─────────────────────────────────────────────────────────────────────────
    # Assisted (ASSISTED → REP_RATIO)
    # ─────────────────────────────────────────────────────────────────────────
    dict(name="Nordic Curl [GHR]", base_name="Nordic Curl", region=Region.LOWER,
         status=Status.ACTIVE, load_code="GHR", tags=["GHR"],
         progression_mode=ProgressionMode.ASSISTED, scheme=Scheme.REP_RATIO,
         assist_ladder=[25, 20, 15, 10, 5, 0],
         knee_modality=KneeModality.NORDIC, family="nordic", is_family_anchor=True, primary_muscle="HAMSTRINGS", secondary_muscles=[]),
    dict(name="Nordic Curl - Volume [GHR]", base_name="Nordic Curl - Volume",
         region=Region.LOWER, status=Status.ACTIVE, load_code="GHR", tags=["GHR"],
         progression_mode=ProgressionMode.ASSISTED, scheme=Scheme.REP_RATIO,
         assist_ladder=[25, 20, 15, 10, 5, 0],
         knee_modality=KneeModality.NORDIC, family="nordic", primary_muscle="HAMSTRINGS", secondary_muscles=[]),
    dict(name="Reverse Nordic Curl [GHR]", base_name="Reverse Nordic Curl",
         region=Region.LOWER, status=Status.ACTIVE, load_code="GHR", tags=["GHR"],
         # 2026-07-24: converted from assisted (INCLINE_REDUCTION-style band/degree
         # assistance) to loaded double-progression -- athlete directive: bodyweight
         # reps 8-12, add load once 12 reps is cleared, applies to both D2 and D5.
         # assist_ladder kept for historical reference; unused once progression_mode
         # is LADDER (RPE_8_STANDARD/DOUBLE_PROGRESSION never reads it).
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         assist_ladder=[20, 15, 10, 5, 0],
         knee_modality=KneeModality.KOT, min_step=2.5, load_floor=0, primary_muscle="QUADS", secondary_muscles=[]),
    # 2026-08-11: new D2 T2 GS movement (maintenance block, STAB redesign,
    # Task 2). Ares cable weighted assist (60lb, LOCKED), NOT monster bands --
    # supersedes the old band-based Nordic assist recommendation (docs/program/
    # source/2026-08-10-maintenance-block-seed-data-FINAL.md "Key Nordic Curl
    # Update"). Attach point is upper body (chest/shoulder), not hip/low back
    # (proved inefficient in testing). Shares identity with D5's Task 4 slot
    # (same Movement row, independent day-scoped MovementState/assist track).
    dict(name="Nordic Curl Max [Ares]", base_name="Nordic Curl Max",
         region=Region.LOWER, status=Status.ACTIVE, load_code="FT", tags=["FT", "NORDIC_MAX"],
         progression_mode=ProgressionMode.ASSISTED, scheme=Scheme.REP_RATIO,
         assist_ladder=[60, 55, 50, 45, 40, 35, 30, 25, 20, 15, 10, 5, 0],
         primary_muscle="HAMSTRINGS", secondary_muscles=["GLUTES"]),
    dict(name="Pull-up [TOWER + TUBES]", base_name="Pull-up", region=Region.UPPER,
         status=Status.ACTIVE, load_code="TOWER", tags=["TOWER", "TUBES"],
         progression_mode=ProgressionMode.ASSISTED, scheme=Scheme.REP_RATIO,
         # 2026-07-26: 3 identical 20lb bands stacked (60lb assist); drop a
         # band at 3x12 (athlete directive). assist_ladder now real (was None).
         assist_ladder=[60, 40, 20, 0],
         objective_override=Objective.PROGRESS, primary_muscle="LATS", secondary_muscles=["BICEPS", "MID_BACK"]),
    # 2026-07-26: D4 switched from the shared neutral-grip Pull-up above to
    # this new movement, wide-grip unassisted (athlete directive -- neutral-
    # grip 3x8 milestone cleared, switching grips for fresh stimulus). D1's
    # Pull-up slot is unaffected, stays assisted neutral-grip. D6 briefly
    # shared this movement too, then split to its own neutral-grip-paused
    # variant (see below) -- 3-way pull-up split across the program.
    dict(name="Wide-Grip Pull-up [TOWER]", base_name="Wide-Grip Pull-up", region=Region.UPPER,
         status=Status.ACTIVE, load_code="TOWER", tags=["TOWER"],
         progression_mode=ProgressionMode.ASSISTED, scheme=Scheme.REP_RATIO,
         primary_muscle="LATS", secondary_muscles=["BICEPS", "MID_BACK"]),
    # 2026-07-26: 3-way pull-up split (athlete directive) -- D1 stays
    # assisted neutral-grip, D4 stays Wide-Grip (unassisted), D6 gets its
    # own variant: neutral-grip with a paused rep (unassisted, same rolling-
    # max tracking as Wide-Grip Pull-up).
    dict(name="Pull-up - Neutral Grip (Paused) [TOWER]", base_name="Pull-up - Neutral Grip (Paused)",
         region=Region.UPPER, status=Status.ACTIVE, load_code="TOWER", tags=["TOWER"],
         progression_mode=ProgressionMode.ASSISTED, scheme=Scheme.REP_RATIO,
         primary_muscle="LATS", secondary_muscles=["BICEPS", "MID_BACK"]),

    # ─────────────────────────────────────────────────────────────────────────
    # Reverse hyper (LADDER, STRAIGHT, cap-and-reps; own baselines — no e1RM ratio)
    # ─────────────────────────────────────────────────────────────────────────
    dict(name="Reverse Hyper [REV_HYPER]", base_name="Reverse Hyper", region=Region.LOWER,
         lift_category=LiftCategory.REV_HYPER, status=Status.ACTIVE,
         load_code="REV_HYPER", tags=["REV_HYPER"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.STRAIGHT,
         load_floor=0, cap=180, family="reverse_hyper", is_family_anchor=True, primary_muscle="GLUTES", secondary_muscles=["HAMSTRINGS", "SPINAL_ERECTORS"]),
    dict(name="Light Reverse Hyper [REV_HYPER]", base_name="Light Reverse Hyper",
         region=Region.LOWER, lift_category=LiftCategory.REV_HYPER, status=Status.ACTIVE,
         load_code="REV_HYPER", tags=["REV_HYPER"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.STRAIGHT,
         load_floor=0, cap=90, family="reverse_hyper", primary_muscle="GLUTES", secondary_muscles=["HAMSTRINGS", "SPINAL_ERECTORS"]),
    # D6's recovery-day slot gets its OWN movement (2026-07-17): it previously
    # shared "Light Reverse Hyper [REV_HYPER]" with D5, but D6 runs FIXED_LOAD
    # (maintenance, no progression) while D5 needs real DOUBLE_PROGRESSION at
    # this same 90lb cap -- wire_progression_rules' HALT-AND-FLAG safety check
    # correctly refused letting one movement carry two conflicting rules.
    dict(name="Reverse Hyper Recovery [REV_HYPER]", base_name="Reverse Hyper Recovery",
         region=Region.LOWER, lift_category=LiftCategory.REV_HYPER, status=Status.ACTIVE,
         load_code="REV_HYPER", tags=["REV_HYPER"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.STRAIGHT,
         load_floor=0, cap=90, family="reverse_hyper", primary_muscle="GLUTES", secondary_muscles=["HAMSTRINGS", "SPINAL_ERECTORS"]),
    dict(name="Reverse Hyper - Single Leg [REV_HYPER]", base_name="Reverse Hyper - Single Leg",
         region=Region.LOWER, lift_category=LiftCategory.REV_HYPER, status=Status.ACTIVE,
         load_code="REV_HYPER", tags=["REV_HYPER"], unilateral=True,
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         load_floor=0, family="reverse_hyper",
         primary_muscle="GLUTES", secondary_muscles=["HAMSTRINGS", "SPINAL_ERECTORS"]),

    # ─────────────────────────────────────────────────────────────────────────
    # Lower accessories — LADDER / DOUBLE_PROGRESSION  (ACTIVE)
    # ─────────────────────────────────────────────────────────────────────────
    # ATG Split Squat: no bracket in name → tags=[], load_code=None; min_step movement-level
    dict(name="ATG Split Squat", base_name="ATG Split Squat", region=Region.LOWER,
         status=Status.ACTIVE, load_code=None, tags=[],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[2.5], min_step=2.5, load_floor=10,
         knee_modality=KneeModality.KOT, unilateral=True, primary_muscle="QUADS", secondary_muscles=["GLUTES", "ADDUCTORS"]),
    dict(name="ATG Split Squat [BW]", base_name="ATG Split Squat", region=Region.LOWER,
         status=Status.ACTIVE, load_code=None, tags=["BW"],
         progression_mode=ProgressionMode.PROTOCOL, scheme=Scheme.STRAIGHT,
         knee_modality=KneeModality.KOT, primary_muscle="QUADS", secondary_muscles=["GLUTES", "ADDUCTORS"]),
    dict(name="ATG Squat Hold", base_name="ATG Squat Hold", region=Region.LOWER,
         status=Status.ACTIVE, load_code=None, tags=[],
         progression_mode=ProgressionMode.PROTOCOL, scheme=Scheme.STRAIGHT,
         knee_modality=KneeModality.KOT, primary_muscle="QUADS", secondary_muscles=["GLUTES", "ADDUCTORS"]),
    dict(name="Bulgarian Split Squat [DB]", base_name="Bulgarian Split Squat",
         region=Region.LOWER, status=Status.ACTIVE, load_code="DB", tags=["DB"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[2.5], min_step=2.5, load_floor=10, unilateral=True, primary_muscle="QUADS", secondary_muscles=["GLUTES", "ADDUCTORS"]),
    dict(name="Calf Raise [GHR]", base_name="Calf Raise", region=Region.LOWER,
         status=Status.ACTIVE, load_code="GHR", tags=["GHR"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=0, primary_muscle="CALVES", secondary_muscles=[]),
    dict(name="Heels-Elevated Goblet Squat [DB]", base_name="Heels-Elevated Goblet Squat",
         region=Region.LOWER, status=Status.ACTIVE, load_code="DB", tags=["DB"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[2.5], min_step=2.5, load_floor=10, primary_muscle="QUADS", secondary_muscles=["GLUTES", "ADDUCTORS"]),
    dict(name="Lying Leg Curl [GHR]", base_name="Lying Leg Curl", region=Region.LOWER,
         status=Status.ACTIVE, load_code="GHR", tags=["GHR"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=10, primary_muscle="HAMSTRINGS", secondary_muscles=[]),
    dict(name="Leg Extension [GHR]", base_name="Leg Extension", region=Region.LOWER,
         status=Status.ACTIVE, load_code="GHR", tags=["GHR"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=10, primary_muscle="QUADS", secondary_muscles=[]),
    dict(name="Reverse Lunge [DB]", base_name="Reverse Lunge", region=Region.LOWER,
         status=Status.ACTIVE, load_code="DB", tags=["DB"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[2.5], min_step=2.5, load_floor=10, primary_muscle="QUADS", secondary_muscles=["GLUTES", "ADDUCTORS"]),
    # 2026-08-11: new D2 T2 GS movement (maintenance block, STAB redesign,
    # Task 2). Bodyweight first, add DBs once easy (per FINAL doc). Its
    # TierExercise carries knee_modality=SISSY (program_seed.py) -- the FINAL
    # doc's own knee_health_note on this movement ("trains VMO, deep knee
    # flexion") and the program's previously-unfilled SISSY weekly target
    # (KNEE_TARGETS, context.py) both point at this slot.
    dict(name="Matrix Machine Sissy Squat", base_name="Matrix Machine Sissy Squat",
         region=Region.LOWER, status=Status.ACTIVE, load_code=None, tags=["MATRIX"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=0,
         primary_muscle="QUADS", secondary_muscles=[]),
    # 2026-08-11: new D2 T3 GS movement (maintenance block, STAB redesign,
    # Task 2). Hybrid Board equipment note only -- same LADDER/DOUBLE_PROGRESSION
    # shape as Calf Raise [GHR]/Hyper Pro Calf Raise; no knee_modality (calf
    # work, not part of the docs/06 §4 knee taxonomy).
    dict(name="Hybrid Board Calf Raise [D2]", base_name="Hybrid Board Calf Raise",
         region=Region.LOWER, status=Status.ACTIVE, load_code=None, tags=["HYBRID_BOARD"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=0,
         primary_muscle="CALVES", secondary_muscles=[]),

    # ─────────────────────────────────────────────────────────────────────────
    # Upper accessories — LADDER / DOUBLE_PROGRESSION  (ACTIVE)
    # bench-family ratio-variant
    # ─────────────────────────────────────────────────────────────────────────
    dict(name="Swiss Bar CG Press [SB]", base_name="Swiss Bar CG Press",
         region=Region.UPPER, lift_category=LiftCategory.CG_PRESS, status=Status.ACTIVE,
         load_code="SB", tags=["SB"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=30,
         derived_from="Bench Press [PB]", start_ratio=0.90, primary_muscle="TRICEPS", secondary_muscles=["MID_LOWER_CHEST", "FRONT_DELT"]),
    dict(name="Swiss Bar Press [SB]", base_name="Swiss Bar Press",
         region=Region.UPPER, lift_category=LiftCategory.CG_PRESS, status=Status.ACTIVE,
         load_code="SB", tags=["SB"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=30, primary_muscle="MID_LOWER_CHEST", secondary_muscles=["TRICEPS", "FRONT_DELT"]),
    dict(name="JM Press [SB]", base_name="JM Press",
         region=Region.UPPER, lift_category=LiftCategory.CG_PRESS, status=Status.ACTIVE,
         load_code="SB", tags=["SB"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=30, primary_muscle="TRICEPS", secondary_muscles=["MID_LOWER_CHEST", "FRONT_DELT"]),
    # 2026-07-26: new D1 T2 GS accessory (athlete directive) -- fills the slot
    # vacated by Pendlay Row Narrow's promotion to its own T1b tier. Pure
    # tricep isolation (not a CG_PRESS variant), same BMF Camber Bar as the
    # rest of the [SB] family. D1's Task 1 (STAB maintenance-block redesign)
    # dropped this movement from D1's wiring entirely -- it sat unused until
    # 2026-08-11 (Task 3/D4), when D4's T3 GS picked it back up per the
    # FINAL doc's `lying_tricep_extension_camber_7` entry. That entry's
    # `grip: 7_inch` is a PHYSICAL-SETUP DETAIL, not a schema field or a new
    # movement identity -- same treatment as D1 Bench Press's own camber-bar
    # 21" grip note (plan doc, Task 1: "physical-setup detail, not a schema
    # field, same movement/load_code as before"). This row has no grip
    # encoded in its name/tags and had no other grip-variant sibling
    # anywhere in the program, so it's reused as-is (no new "Camber 7"
    # movement) rather than duplicated -- matches the EZ-curl-family
    # precedent of separate rows only where named grip variants actually
    # COEXIST and need disambiguation, which isn't the case here.
    dict(name="Lying Tricep Extension [SB]", base_name="Lying Tricep Extension",
         region=Region.UPPER, status=Status.ACTIVE,
         load_code="SB", tags=["SB"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=20,
         primary_muscle="TRICEPS", secondary_muscles=[]),
    # OHP ratio-variant
    dict(name="Z-Press [DB]", base_name="Z-Press", region=Region.UPPER,
         status=Status.ACTIVE, load_code="DB", tags=["DB"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[2.5], min_step=2.5, load_floor=10,
         derived_from="Standing OHP [PB]", start_ratio=0.85, primary_muscle="FRONT_DELT", secondary_muscles=["SIDE_DELT", "TRICEPS"]),
    # 2026-08-10: new D1 T2 GS accessory (maintenance block, STAB redesign,
    # real Wk1 execution). Per-hand DB seated press on the new Stryker Pad
    # bench attachment (APEX Config A) -- equipment note only, standard DB
    # press progression.
    dict(name="Stryker Pad Seated OHP [DB]", base_name="Stryker Pad Seated OHP",
         region=Region.UPPER, status=Status.ACTIVE, load_code="DB", tags=["DB", "STRYKER_PAD"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=20,
         primary_muscle="FRONT_DELT", secondary_muscles=["SIDE_DELT", "TRICEPS"]),
    # 2026-08-11: new D4 T1 movement (maintenance block, STAB redesign, Task
    # 3). Seated on the APEX Bench upright (Config D), Black Diamond DBD
    # barbell -- same bar/bracket as every other [PB] movement (FINAL doc's
    # `equipment: [black_diamond_dbd, apex_bench_upright]`), NOT the Gladiator
    # WL [OB] bar. Replaces D4's T1 anchor (Standing OHP [PB], which stays
    # ACTIVE and needs-cal, just unwired from D4). Needs-calibration start,
    # zero prior history.
    dict(name="Seated BTN OHP [PB]", base_name="Seated BTN OHP",
         region=Region.UPPER, status=Status.ACTIVE, load_code="PB", tags=["PB"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=45,
         primary_muscle="FRONT_DELT", secondary_muscles=["TRICEPS", "SIDE_DELT"]),
    # 2026-08-11: new D4 T1b movement (maintenance block, STAB redesign, Task
    # 3). Better Fly cuff on elbows, cable at high pulley (FINAL doc's
    # `equipment: [better_fly, ares_cable, ares_high_pulley]`) -- [FT] bracket
    # (Ares cable, single), same family as D1's Better Fly Standing Lateral
    # Raise. Replaces D4's T1b anchor (Wide-Grip Pull-up [TOWER], athlete
    # directive -- grip-free vertical pull isolation, cable provides
    # continuous tension throughout ROM; drops D4's direct pull-up work,
    # D1/D6 keep theirs). Needs-calibration start, zero prior history.
    dict(name="Better Fly Lat Pulldown [FT]", base_name="Better Fly Lat Pulldown",
         region=Region.UPPER, status=Status.ACTIVE, load_code="FT", tags=["FT", "BETTER_FLY"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=10,
         primary_muscle="LATS", secondary_muscles=["BICEPS", "MID_BACK"]),
    # 2026-08-11: new D4 T2 GS movement (maintenance block, STAB redesign,
    # Task 3). Chest-supported row on the new Stryker Pad bench attachment,
    # Black Diamond DBD barbell -- [PB] bracket (FINAL doc's `equipment:
    # [stryker_pad, apex_bench, black_diamond_dbd]`), NOT [OB] (task-3-brief.md
    # gave [OB], which is the Gladiator WL bar -- CODE_TO_EQUIP above confirms
    # black_diamond_dbd is PB; corrected here, logged as a brief error).
    # Replaces Meadows Row [OB + LM] (drops out of D4 entirely, still no
    # other day wires it). Needs-calibration start, zero prior history.
    dict(name="Stryker Pad CSR Barbell [PB]", base_name="Stryker Pad CSR Barbell",
         region=Region.UPPER, status=Status.ACTIVE, load_code="PB", tags=["PB", "STRYKER_PAD"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=45,
         primary_muscle="MID_BACK", secondary_muscles=["LATS", "REAR_DELT", "BICEPS"]),
    # 2026-08-11: new D4 T2 GS movement (maintenance block, STAB redesign,
    # Task 3). Better Fly cuff, cable at MID pulley (FINAL doc's `equipment:
    # [better_fly, ares_cable, ares_mid_pulley]` -- distinct attach point from
    # Better Fly Lat Pulldown's high pulley above). [FT] bracket, same family.
    # Replaces Andreoni Cable Pullover (drops out of D4 entirely, still no
    # other day wires it). Needs-calibration start, zero prior history.
    dict(name="Better Fly Cable Pullover [FT]", base_name="Better Fly Cable Pullover",
         region=Region.UPPER, status=Status.ACTIVE, load_code="FT", tags=["FT", "BETTER_FLY"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=10,
         primary_muscle="LATS", secondary_muscles=["MID_BACK"]),
    # Pendlay Row family (Medium anchor + grip variants at 1.0)
    dict(name="Pendlay Row - Medium [OB]", base_name="Pendlay Row - Medium",
         region=Region.UPPER, lift_category=LiftCategory.ROW, status=Status.ACTIVE,
         load_code="OB", tags=["OB"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=45,
         family="pendlay_row", is_family_anchor=True, primary_muscle="MID_BACK", secondary_muscles=["LATS", "REAR_DELT", "BICEPS"]),
    dict(name="Pendlay Row - Narrow [OB]", base_name="Pendlay Row - Narrow",
         region=Region.UPPER, lift_category=LiftCategory.ROW, status=Status.ACTIVE,
         load_code="OB", tags=["OB"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=45,
         derived_from="Pendlay Row - Medium [OB]", start_ratio=1.0, primary_muscle="MID_BACK", secondary_muscles=["LATS", "REAR_DELT", "BICEPS"]),
    dict(name="Pendlay Row - Wide [OB]", base_name="Pendlay Row - Wide",
         region=Region.UPPER, lift_category=LiftCategory.ROW, status=Status.ACTIVE,
         load_code="OB", tags=["OB"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=45,
         derived_from="Pendlay Row - Medium [OB]", start_ratio=1.0, primary_muscle="MID_BACK", secondary_muscles=["LATS", "REAR_DELT", "BICEPS"]),
    # T-Bar Row family (Medium anchor; floor non-binding → None)
    dict(name="T-Bar Row - Medium [OB + KLEVA + LM]", base_name="T-Bar Row - Medium",
         region=Region.UPPER, status=Status.ACTIVE,
         load_code="OB", tags=["OB", "KLEVA", "LM"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=0,
         family="t_bar_row", is_family_anchor=True, primary_muscle="MID_BACK", secondary_muscles=["LATS", "REAR_DELT", "BICEPS"]),
    dict(name="T-Bar Row - Narrow [OB + KLEVA + LM]", base_name="T-Bar Row - Narrow",
         region=Region.UPPER, status=Status.ACTIVE,
         load_code="OB", tags=["OB", "KLEVA", "LM"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=0,
         derived_from="T-Bar Row - Medium [OB + KLEVA + LM]", start_ratio=1.0, primary_muscle="MID_BACK", secondary_muscles=["LATS", "REAR_DELT", "BICEPS"]),
    dict(name="T-Bar Row - Wide [OB + KLEVA + LM]", base_name="T-Bar Row - Wide",
         region=Region.UPPER, status=Status.ACTIVE,
         load_code="OB", tags=["OB", "KLEVA", "LM"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=0,
         derived_from="T-Bar Row - Medium [OB + KLEVA + LM]", start_ratio=1.0, primary_muscle="MID_BACK", secondary_muscles=["LATS", "REAR_DELT", "BICEPS"]),
    # Andreoni station (load = dual cable, floor=20 matches ANDREONI Equipment row)
    dict(name="Andreoni Dips [ANDREONI + FT]", base_name="Andreoni Dips",
         region=Region.UPPER, status=Status.ACTIVE,
         load_code="ANDREONI", tags=["ANDREONI", "FT"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5], min_step=5, load_floor=20, primary_muscle="MID_LOWER_CHEST", secondary_muscles=["TRICEPS", "FRONT_DELT"]),
    dict(name="Andreoni Lat Prayer [ANDREONI + FT]", base_name="Andreoni Lat Prayer",
         region=Region.UPPER, status=Status.ACTIVE,
         load_code="ANDREONI", tags=["ANDREONI", "FT"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5], min_step=5, load_floor=20, primary_muscle="LATS", secondary_muscles=[]),
    dict(name="Andreoni Tricep Extension [ANDREONI + FT]",
         base_name="Andreoni Tricep Extension",
         region=Region.UPPER, status=Status.ACTIVE,
         load_code="ANDREONI", tags=["ANDREONI", "FT"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5], min_step=5, load_floor=20, primary_muscle="TRICEPS", secondary_muscles=[]),
    # Cable upper accessories
    dict(name="Cable Low-to-High Fly [FT]", base_name="Cable Low-to-High Fly",
         region=Region.UPPER, status=Status.ACTIVE, load_code="FT", tags=["FT"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[2.5], min_step=2.5, load_floor=10, primary_muscle="UPPER_CHEST", secondary_muscles=["FRONT_DELT"]),
    dict(name="Cable Tricep Pushdown [FT]", base_name="Cable Tricep Pushdown",
         region=Region.UPPER, status=Status.ACTIVE, load_code="FT", tags=["FT"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=10, primary_muscle="TRICEPS", secondary_muscles=[]),
    dict(name="Cable V-Bar Pushdown [FT]", base_name="Cable V-Bar Pushdown",
         region=Region.UPPER, status=Status.ACTIVE, load_code="FT", tags=["FT"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=10, primary_muscle="TRICEPS", secondary_muscles=[]),
    dict(name="Chest Supported Row [DB + BENCH]", base_name="Chest Supported Row",
         region=Region.UPPER, status=Status.ACTIVE,
         load_code="DB", tags=["DB", "BENCH"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[2.5], min_step=2.5, load_floor=10, primary_muscle="MID_BACK", secondary_muscles=["LATS", "REAR_DELT", "BICEPS"]),
    dict(name="Better Fly Standing Lateral Raise [FT]", base_name="Better Fly Standing Lateral Raise",
         region=Region.UPPER, status=Status.ACTIVE,
         load_code="FT", tags=["FT", "BETTER_FLY"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=10,
         primary_muscle="SIDE_DELT", secondary_muscles=["FRONT_DELT"]),
    dict(name="Cross-Body Cable Lateral Raise [FT]",
         base_name="Cross-Body Cable Lateral Raise",
         region=Region.UPPER, status=Status.ACTIVE, load_code="FT", tags=["FT"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[2.5], min_step=2.5, load_floor=10, unilateral=True, primary_muscle="SIDE_DELT", secondary_muscles=[]),
    dict(name="Cross-Body Cable Rear Delt Fly [FT]",
         base_name="Cross-Body Cable Rear Delt Fly",
         region=Region.UPPER, status=Status.ACTIVE, load_code="FT", tags=["FT"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[2.5], min_step=2.5, load_floor=10, unilateral=True, primary_muscle="REAR_DELT", secondary_muscles=[]),
    dict(name="DB Lateral Raise [DB]", base_name="DB Lateral Raise",
         region=Region.UPPER, status=Status.ACTIVE, load_code="DB", tags=["DB"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[2.5], min_step=2.5, load_floor=10, primary_muscle="SIDE_DELT", secondary_muscles=[]),
    dict(name="DB Seal Row [DB + UTIL_SEAT]", base_name="DB Seal Row",
         region=Region.UPPER, status=Status.ACTIVE,
         load_code="DB", tags=["DB", "UTIL_SEAT"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[2.5], min_step=2.5, load_floor=10, primary_muscle="MID_BACK", secondary_muscles=["LATS", "REAR_DELT", "BICEPS"]),
    dict(name="Decline Swiss Bar Skull Crusher [SB + BENCH]",
         base_name="Decline Swiss Bar Skull Crusher",
         region=Region.UPPER, status=Status.ACTIVE,
         load_code="SB", tags=["SB", "BENCH"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=30, primary_muscle="TRICEPS", secondary_muscles=[]),
    dict(name="Eccentric Pull-up [TOWER]", base_name="Eccentric Pull-up",
         region=Region.UPPER, status=Status.ACTIVE, load_code="TOWER", tags=["TOWER"],
         progression_mode=ProgressionMode.PROTOCOL, scheme=Scheme.STRAIGHT, primary_muscle="LATS", secondary_muscles=["BICEPS", "MID_BACK"]),
    dict(name="Face Pull w/ ER Hold [FT]", base_name="Face Pull w/ ER Hold",
         region=Region.UPPER, status=Status.ACTIVE, load_code="FT", tags=["FT"],
         progression_mode=ProgressionMode.PROTOCOL, scheme=Scheme.STRAIGHT,
         min_step=2.5, load_floor=10, primary_muscle="REAR_DELT", secondary_muscles=["UPPER_TRAPS"]),
    dict(name="Face Pull [FT]", base_name="Face Pull",
         region=Region.UPPER, status=Status.ACTIVE, load_code="FT", tags=["FT"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[2.5], min_step=2.5, load_floor=10,
         primary_muscle="REAR_DELT", secondary_muscles=["UPPER_TRAPS"]),
    dict(name="Heavy Lat Pulldown [FT]", base_name="Heavy Lat Pulldown",
         region=Region.UPPER, status=Status.ACTIVE, load_code="FT", tags=["FT"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5], min_step=5, load_floor=20, primary_muscle="LATS", secondary_muscles=["BICEPS", "MID_BACK"]),
    dict(name="Incline DB Press [DB + BENCH]", base_name="Incline DB Press",
         region=Region.UPPER, status=Status.ACTIVE,
         load_code="DB", tags=["DB", "BENCH"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[2.5], min_step=2.5, load_floor=10, primary_muscle="UPPER_CHEST", secondary_muscles=["FRONT_DELT", "TRICEPS"]),
    dict(name="Incline DB Y-Raise [DB + BENCH]", base_name="Incline DB Y-Raise",
         region=Region.UPPER, status=Status.ACTIVE,
         load_code="DB", tags=["DB", "BENCH"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[2.5], min_step=2.5, load_floor=10, primary_muscle="REAR_DELT", secondary_muscles=["UPPER_TRAPS"]),
    dict(name="Kneeling Cross-Body Lat Pullaround [FT]",
         base_name="Kneeling Cross-Body Lat Pullaround",
         region=Region.UPPER, status=Status.ACTIVE, load_code="FT", tags=["FT"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=10, primary_muscle="LATS", secondary_muscles=[]),
    dict(name="Lat Pulldown [FT]", base_name="Lat Pulldown",
         region=Region.UPPER, status=Status.ACTIVE, load_code="FT", tags=["FT"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5], min_step=5, load_floor=20, primary_muscle="LATS", secondary_muscles=["BICEPS", "MID_BACK"]),
    dict(name="Lateral Raise [FT]", base_name="Lateral Raise",
         region=Region.UPPER, status=Status.ACTIVE, load_code="FT", tags=["FT"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[2.5], min_step=2.5, load_floor=10, primary_muscle="SIDE_DELT", secondary_muscles=[]),
    dict(name="Meadows Row [OB + LM]", base_name="Meadows Row",
         region=Region.UPPER, status=Status.ACTIVE,
         load_code="OB", tags=["OB", "LM"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[2.5], min_step=2.5, load_floor=0, unilateral=True, primary_muscle="MID_BACK", secondary_muscles=["LATS", "REAR_DELT", "BICEPS"]),
    dict(name="Rear Delt Fly [DB]", base_name="Rear Delt Fly",
         region=Region.UPPER, status=Status.ACTIVE, load_code="DB", tags=["DB"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[2.5], min_step=2.5, load_floor=10, primary_muscle="REAR_DELT", secondary_muscles=[]),
    dict(name="Seated Cable Row [FT]", base_name="Seated Cable Row",
         region=Region.UPPER, status=Status.ACTIVE, load_code="FT", tags=["FT"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=10, primary_muscle="MID_BACK", secondary_muscles=["LATS", "REAR_DELT", "BICEPS"]),
    dict(name="Seated DB Press [DB]", base_name="Seated DB Press",
         region=Region.UPPER, status=Status.ACTIVE, load_code="DB", tags=["DB"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[2.5], min_step=2.5, load_floor=10, primary_muscle="FRONT_DELT", secondary_muscles=["SIDE_DELT", "TRICEPS"]),
    dict(name="Single-Arm Cable Chest Press [FT + D-handle]",
         base_name="Single-Arm Cable Chest Press",
         region=Region.UPPER, status=Status.ACTIVE,
         load_code="FT", tags=["FT", "D-handle"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=10, primary_muscle="MID_LOWER_CHEST", secondary_muscles=["FRONT_DELT", "TRICEPS"]),
    dict(name="Single-Arm Landmine Press [OB + LM]",
         base_name="Single-Arm Landmine Press",
         region=Region.UPPER, status=Status.ACTIVE,
         load_code="OB", tags=["OB", "LM"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[2.5], min_step=2.5, load_floor=0, primary_muscle="FRONT_DELT", secondary_muscles=["UPPER_CHEST", "TRICEPS"]),
    # Program-required meso-2 upper rotation (v0.6 fix wave)
    dict(name="Single-Arm DB Row [DB]", base_name="Single-Arm DB Row",
         region=Region.UPPER, lift_category=LiftCategory.ROW, status=Status.ACTIVE,
         load_code="DB", tags=["DB"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[2.5], min_step=2.5, load_floor=10, unilateral=True, primary_muscle="MID_BACK", secondary_muscles=["LATS", "REAR_DELT", "BICEPS"]),

    # ─────────────────────────────────────────────────────────────────────────
    # Core — LADDER (PureTorque Pro: no bracket → min_step movement-level)
    # ─────────────────────────────────────────────────────────────────────────
    # 2026-07-26: wired into D4's T3 GS (replaces Dragon Flag, athlete
    # directive -- "Cable Woodchopper" in the original proposal; this
    # existing unused movement IS that rotational/transverse-plane pattern,
    # so it's reused rather than duplicated). Marked unilateral (one side
    # at a time, per rep).
    dict(name="PureTorque Pro Rotation", base_name="PureTorque Pro Rotation",
         region=Region.CORE, status=Status.ACTIVE, load_code=None, tags=[],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=10, unilateral=True,
         primary_muscle="ABS", secondary_muscles=[]),

    # ─────────────────────────────────────────────────────────────────────────
    # Core — PROTOCOL  (ACTIVE)
    # ─────────────────────────────────────────────────────────────────────────
    # 2026-08-11: new D2 T4 (new straight tier) movement (maintenance block,
    # STAB redesign, Task 2). D2's mandatory core slot -- spine flexion,
    # bodyweight first, add plate on chest once 3x15 clears (per FINAL doc).
    # Mirrors Ab Wheel [WHEEL]'s PROTOCOL/STRAIGHT shape (bodyweight, no
    # scalar load track, REP_LADDER-driven via rep_ladder_at_cap mapping).
    dict(name="Ab Trainer Decline Sit-up", base_name="Ab Trainer Decline Sit-up",
         region=Region.CORE, status=Status.ACTIVE, load_code=None, tags=["AB_TRAINER"],
         progression_mode=ProgressionMode.PROTOCOL, scheme=Scheme.STRAIGHT,
         primary_muscle="ABS", secondary_muscles=[]),
    # 2026-08-11: new D4 T2 GS movement (maintenance block, STAB redesign,
    # Task 3). Anti-extension hip flexion on the Ab Trainer apparatus (FINAL
    # doc: `equipment: [ab_trainer, apex_bench]`) -- did NOT already exist in
    # the library (Step-1 verification per task-3-brief.md confirmed no
    # match). Same PROTOCOL/STRAIGHT/REP_LADDER shape as Ab Trainer Decline
    # Sit-up above (bodyweight, no scalar load track). Needs-calibration
    # start, zero prior history.
    dict(name="Ab Trainer Hanging Leg Raise", base_name="Ab Trainer Hanging Leg Raise",
         region=Region.CORE, status=Status.ACTIVE, load_code=None, tags=["AB_TRAINER"],
         progression_mode=ProgressionMode.PROTOCOL, scheme=Scheme.STRAIGHT,
         primary_muscle="ABS", secondary_muscles=[]),
    dict(name="Ab Wheel [WHEEL]", base_name="Ab Wheel",
         region=Region.CORE, status=Status.ACTIVE, load_code=None, tags=["WHEEL"],
         progression_mode=ProgressionMode.PROTOCOL, scheme=Scheme.STRAIGHT, primary_muscle="ABS", secondary_muscles=[]),
    dict(name="Bear Hover", base_name="Bear Hover",
         region=Region.CORE, status=Status.ACTIVE, load_code=None, tags=[],
         progression_mode=ProgressionMode.PROTOCOL, scheme=Scheme.STRAIGHT, primary_muscle="ABS", secondary_muscles=[]),
    dict(name="Bear Hover + Shoulder Tap", base_name="Bear Hover + Shoulder Tap",
         region=Region.CORE, status=Status.ACTIVE, load_code=None, tags=[],
         progression_mode=ProgressionMode.PROTOCOL, scheme=Scheme.STRAIGHT, primary_muscle="ABS", secondary_muscles=[]),
    dict(name="Bird Dog", base_name="Bird Dog",
         region=Region.CORE, status=Status.ACTIVE, load_code=None, tags=[],
         progression_mode=ProgressionMode.PROTOCOL, scheme=Scheme.STRAIGHT, primary_muscle="ABS", secondary_muscles=["SPINAL_ERECTORS"]),
    dict(name="Cable Crunch [FT]", base_name="Cable Crunch",
         region=Region.CORE, status=Status.ACTIVE, load_code="FT", tags=["FT"],
         progression_mode=ProgressionMode.PROTOCOL, scheme=Scheme.STRAIGHT,
         min_step=5, load_floor=20, primary_muscle="ABS", secondary_muscles=[]),
    dict(name="Cable External Rotation [FT]", base_name="Cable External Rotation",
         region=Region.CORE, status=Status.ACTIVE, load_code="FT", tags=["FT"],
         progression_mode=ProgressionMode.PROTOCOL, scheme=Scheme.STRAIGHT,
         min_step=2.5, load_floor=10, primary_muscle="ROTATOR_CUFF", secondary_muscles=["REAR_DELT"]),
    dict(name="Copenhagen Hold [BENCH]", base_name="Copenhagen Hold",
         region=Region.CORE, status=Status.ACTIVE, load_code=None, tags=["BENCH"],
         progression_mode=ProgressionMode.PROTOCOL, scheme=Scheme.STRAIGHT, primary_muscle="ADDUCTORS", secondary_muscles=[]),
    dict(name="Dead Bug", base_name="Dead Bug",
         region=Region.CORE, status=Status.ACTIVE, load_code=None, tags=[],
         progression_mode=ProgressionMode.PROTOCOL, scheme=Scheme.STRAIGHT, primary_muscle="ABS", secondary_muscles=[]),
    dict(name="Decline Lying Leg Raise [GHR]", base_name="Decline Lying Leg Raise",
         region=Region.CORE, status=Status.ACTIVE, load_code="GHR", tags=["GHR"],
         progression_mode=ProgressionMode.PROTOCOL, scheme=Scheme.STRAIGHT, primary_muscle="ABS", secondary_muscles=[]),
    dict(name="Hanging Knee Raise [TOWER]", base_name="Hanging Knee Raise",
         region=Region.CORE, status=Status.ACTIVE, load_code="TOWER", tags=["TOWER"],
         progression_mode=ProgressionMode.PROTOCOL, scheme=Scheme.STRAIGHT, primary_muscle="ABS", secondary_muscles=[]),
    dict(name="Hard-Style Plank", base_name="Hard-Style Plank",
         region=Region.CORE, status=Status.ACTIVE, load_code=None, tags=[],
         progression_mode=ProgressionMode.PROTOCOL, scheme=Scheme.STRAIGHT, primary_muscle="ABS", secondary_muscles=[]),
    dict(name="Hollow Body Hold", base_name="Hollow Body Hold",
         region=Region.CORE, status=Status.ACTIVE, load_code=None, tags=[],
         progression_mode=ProgressionMode.PROTOCOL, scheme=Scheme.STRAIGHT, primary_muscle="ABS", secondary_muscles=[]),
    dict(name="Hollow Body Tuck Hold", base_name="Hollow Body Tuck Hold",
         region=Region.CORE, status=Status.ACTIVE, load_code=None, tags=[],
         progression_mode=ProgressionMode.PROTOCOL, scheme=Scheme.STRAIGHT, primary_muscle="ABS", secondary_muscles=[]),
    dict(name="Landmine Rotation [OB + LM]", base_name="Landmine Rotation",
         region=Region.CORE, status=Status.ACTIVE,
         load_code="OB", tags=["OB", "LM"],
         progression_mode=ProgressionMode.PROTOCOL, scheme=Scheme.STRAIGHT,
         min_step=2.5, cap=25, primary_muscle="ABS", secondary_muscles=[]),
    dict(name="Plank", base_name="Plank",
         region=Region.CORE, status=Status.ACTIVE, load_code=None, tags=[],
         progression_mode=ProgressionMode.PROTOCOL, scheme=Scheme.STRAIGHT, primary_muscle="ABS", secondary_muscles=[]),
    dict(name="Short-Lever Copenhagen [BENCH]", base_name="Short-Lever Copenhagen",
         region=Region.CORE, status=Status.ACTIVE, load_code=None, tags=["BENCH"],
         progression_mode=ProgressionMode.PROTOCOL, scheme=Scheme.STRAIGHT, primary_muscle="ADDUCTORS", secondary_muscles=[]),

    # ─────────────────────────────────────────────────────────────────────────
    # PREP (1)
    # ─────────────────────────────────────────────────────────────────────────
    dict(name="Band Pull-Aparts", base_name="Band Pull-Aparts",
         region=Region.UPPER, status=Status.PREP,
         load_code=None, tags=["BAND"],
         progression_mode=ProgressionMode.PROTOCOL, scheme=Scheme.STRAIGHT,
         band_eligible=True, primary_muscle="REAR_DELT", secondary_muscles=["UPPER_TRAPS"]),

    # ─────────────────────────────────────────────────────────────────────────
    # Conditioning (10) — no load FK; KB keeps its Equipment row
    # ─────────────────────────────────────────────────────────────────────────
    dict(name="Farmer Carries [FARMER HANDLES]", base_name="Farmer Carries",
         region=Region.NONE, status=Status.ACTIVE,
         load_code=None, tags=["FARMER HANDLES"],
         progression_mode=ProgressionMode.CONDITIONING, scheme=Scheme.STRAIGHT, primary_muscle="FOREARMS", secondary_muscles=["UPPER_TRAPS", "ABS"]),
    dict(name="Farmer Walk [FARMER]", base_name="Farmer Walk",
         region=Region.NONE, status=Status.ACTIVE,
         load_code=None, tags=["FARMER"],
         progression_mode=ProgressionMode.CONDITIONING, scheme=Scheme.STRAIGHT, primary_muscle="FOREARMS", secondary_muscles=["UPPER_TRAPS", "ABS"]),
    dict(name="Jump Rope Intervals [JR]", base_name="Jump Rope Intervals",
         region=Region.NONE, status=Status.ACTIVE,
         load_code=None, tags=["JR"],
         progression_mode=ProgressionMode.CONDITIONING, scheme=Scheme.STRAIGHT, primary_muscle="CALVES", secondary_muscles=[]),
    dict(name="Jump Rope Tabata [JR]", base_name="Jump Rope Tabata",
         region=Region.NONE, status=Status.ACTIVE,
         load_code=None, tags=["JR"],
         progression_mode=ProgressionMode.CONDITIONING, scheme=Scheme.STRAIGHT, primary_muscle="CALVES", secondary_muscles=[]),
    dict(name="Jump Rope [JR]", base_name="Jump Rope",
         region=Region.NONE, status=Status.ACTIVE,
         load_code=None, tags=["JR"],
         progression_mode=ProgressionMode.CONDITIONING, scheme=Scheme.STRAIGHT, primary_muscle="CALVES", secondary_muscles=[]),
    dict(name="KB Swing Tabata [KB]", base_name="KB Swing Tabata",
         region=Region.NONE, status=Status.ACTIVE,
         load_code="KB", tags=["KB"],
         progression_mode=ProgressionMode.CONDITIONING, scheme=Scheme.STRAIGHT,
         load_floor=13, primary_muscle="GLUTES", secondary_muscles=["HAMSTRINGS", "SPINAL_ERECTORS"]),
    dict(name="KB Swings [KB]", base_name="KB Swings",
         region=Region.NONE, status=Status.ACTIVE,
         load_code="KB", tags=["KB"],
         progression_mode=ProgressionMode.CONDITIONING, scheme=Scheme.STRAIGHT,
         load_floor=13, primary_muscle="GLUTES", secondary_muscles=["HAMSTRINGS", "SPINAL_ERECTORS"]),
    dict(name="Sandbag Carry [SANDBAG]", base_name="Sandbag Carry",
         region=Region.NONE, status=Status.ACTIVE,
         load_code=None, tags=["SANDBAG"],
         progression_mode=ProgressionMode.CONDITIONING, scheme=Scheme.STRAIGHT, primary_muscle="FOREARMS", secondary_muscles=["UPPER_TRAPS", "ABS"]),
    dict(name="Sandbag Over-Shoulder [SANDBAG]", base_name="Sandbag Over-Shoulder",
         region=Region.NONE, status=Status.ACTIVE,
         load_code=None, tags=["SANDBAG"],
         progression_mode=ProgressionMode.CONDITIONING, scheme=Scheme.STRAIGHT, primary_muscle="GLUTES", secondary_muscles=["HAMSTRINGS", "SPINAL_ERECTORS"]),
    dict(name="Slam Ball [BALL]", base_name="Slam Ball",
         region=Region.NONE, status=Status.ACTIVE,
         load_code=None, tags=["BALL"],
         progression_mode=ProgressionMode.CONDITIONING, scheme=Scheme.STRAIGHT, primary_muscle="ABS", secondary_muscles=["GLUTES", "HAMSTRINGS"]),

    # ─────────────────────────────────────────────────────────────────────────
    # INACTIVE (8) — kept, dormant, eligible for future blocks
    # ─────────────────────────────────────────────────────────────────────────
    dict(name="Bayesian Cable Curl [FT]", base_name="Bayesian Cable Curl",
         region=Region.UPPER, status=Status.INACTIVE,
         load_code="FT", tags=["FT"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=10, primary_muscle="BICEPS", secondary_muscles=[]),
    # Dips: cable-loaded RPE-8 on the Andreoni dip station + FT (Ares cable)
    # — docs/program/phase1-seed-source.yaml:72 "CORRECTED: cable-loaded, not
    # BW rep-ladder" (d6_g1b baseline seeds current_load=150).
    # 2026-07-26: converted from cable-loaded (Andreoni bar) to bodyweight +
    # band assist (athlete directive) -- moved from D6 GS1 to its own T1
    # straight-set slot at 6-8 reps. Band assist ladder mirrors Reverse
    # Nordic Curl's old lb-based convention (higher lb = more assist = easier;
    # advancing walks toward LOWER lb, terminal = fully unassisted).
    # progression_rule is NOT set here -- seed()'s dict->Movement constructor
    # doesn't read that key at all; the real source of truth is rule_wiring.py
    # (derive_movement_rules/wire_progression_rules), driven by
    # phase1-seed-source.yaml's `rule:` field. See that yaml + rule_wiring.py
    # for the actual ASSISTANCE_REDUCTION / RPE_8_STANDARD wiring.
    dict(name="Dips [TOWER + TUBES]", base_name="Dips",
         region=Region.UPPER, status=Status.ACTIVE,
         load_code="TOWER", tags=["TOWER", "TUBES"],
         progression_mode=ProgressionMode.ASSISTED, scheme=Scheme.STRAIGHT,
         assist_ladder=[40, 30, 18, 9, 0], primary_muscle="MID_LOWER_CHEST", secondary_muscles=["TRICEPS", "FRONT_DELT"]),
    dict(name="Cable Bicep Curl [FT]", base_name="Cable Bicep Curl",
         region=Region.UPPER, status=Status.ACTIVE,
         load_code="FT", tags=["FT"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=10, primary_muscle="BICEPS", secondary_muscles=[]),
    # EZ curl family (all INACTIVE; grip variants ride the Medium anchor at 1.0)
    dict(name="EZ Bar Curl - Medium Grip [EZ]", base_name="EZ Bar Curl - Medium Grip",
         region=Region.UPPER, status=Status.INACTIVE,
         load_code="EZ", tags=["EZ"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=35,
         family="ez_curl", is_family_anchor=True, primary_muscle="BICEPS", secondary_muscles=[]),
    dict(name="EZ Bar Curl - Narrow Grip [EZ]", base_name="EZ Bar Curl - Narrow Grip",
         region=Region.UPPER, status=Status.INACTIVE,
         load_code="EZ", tags=["EZ"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=35,
         derived_from="EZ Bar Curl - Medium Grip [EZ]", start_ratio=1.0, primary_muscle="BICEPS", secondary_muscles=["FOREARMS"]),
    dict(name="EZ Bar Curl - Wide Grip [EZ]", base_name="EZ Bar Curl - Wide Grip",
         region=Region.UPPER, status=Status.INACTIVE,
         load_code="EZ", tags=["EZ"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=35,
         derived_from="EZ Bar Curl - Medium Grip [EZ]", start_ratio=1.0, primary_muscle="BICEPS", secondary_muscles=[]),
    # 2026-08-10: new D1 T2 GS accessory (maintenance block, STAB redesign,
    # real Wk1 execution) -- reintroduces bicep work to the program. Kyoto EZ
    # Curl bar seated in the new Matrix Machine (APEX Config A), separate
    # anchor from the INACTIVE straight-EZ-curl family above (different
    # equipment/movement pattern, not a grip variant of it).
    dict(name="Matrix Machine Preacher Curl [EZ]", base_name="Matrix Machine Preacher Curl",
         region=Region.UPPER, status=Status.ACTIVE,
         load_code="EZ", tags=["EZ", "MATRIX"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=20,
         primary_muscle="BICEPS", secondary_muscles=[]),
    dict(name="Hammer Curl [DB]", base_name="Hammer Curl",
         region=Region.UPPER, status=Status.INACTIVE,
         load_code="DB", tags=["DB"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[2.5], min_step=2.5, load_floor=10, primary_muscle="BICEPS", secondary_muscles=["FOREARMS"]),
    dict(name="Incline DB Curl [DB + BENCH]", base_name="Incline DB Curl",
         region=Region.UPPER, status=Status.INACTIVE,
         load_code="DB", tags=["DB", "BENCH"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[2.5], min_step=2.5, load_floor=10, primary_muscle="BICEPS", secondary_muscles=[]),
    # Lat Prayer uses single-stack FT (not ANDREONI dual despite bracket)
    dict(name="Lat Prayer [ANDREONI + FT]", base_name="Lat Prayer",
         region=Region.UPPER, status=Status.ACTIVE,
         load_code="FT", tags=["ANDREONI", "FT"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=10, primary_muscle="LATS", secondary_muscles=[]),

    # ─────────────────────────────────────────────────────────────────────────
    # 3 knee movements ADDED (beyond the 100 sheet rows — closes docs/06 §4 gap)
    # ─────────────────────────────────────────────────────────────────────────
    dict(name="Sissy Squat", base_name="Sissy Squat", region=Region.LOWER,
         status=Status.ACTIVE, knee_modality=KneeModality.SISSY,
         load_code="DB", tags=["BW", "DB"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[2.5], min_step=2.5, load_floor=0,
         notes="Single continuous added-load track from BW (0); plate->DB/KB is a tag, "
               "not a load-track break — no e1RM reset.", primary_muscle="QUADS", secondary_muscles=[]),
    dict(name="Cable Tibialis Raise", base_name="Cable Tibialis Raise",
         region=Region.LOWER, status=Status.ACTIVE,
         knee_modality=KneeModality.TIB, load_code="FT", tags=["FT"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[2.5], min_step=2.5, load_floor=10, primary_muscle="TIBIALIS", secondary_muscles=[]),
    dict(name="Poliquin Step-up", base_name="Poliquin Step-up",
         region=Region.LOWER, status=Status.ACTIVE,
         knee_modality=KneeModality.KOT, load_code="DB", tags=["DB"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[2.5], min_step=2.5, load_floor=10, unilateral=True, primary_muscle="QUADS", secondary_muscles=["GLUTES"]),

    # ─────────────────────────────────────────────────────────────────────────
    # 3 program-required core/upper movements ADDED (close v0.6 program gaps)
    # ─────────────────────────────────────────────────────────────────────────
    dict(name="Dragon Flag", base_name="Dragon Flag", region=Region.CORE,
         status=Status.ACTIVE, load_code=None, tags=["BW"],
         progression_mode=ProgressionMode.PROTOCOL, scheme=Scheme.STRAIGHT, primary_muscle="ABS", secondary_muscles=[]),
    dict(name="Face-Up Incline Knee Raise", base_name="Face-Up Incline Knee Raise",
         region=Region.CORE, status=Status.ACTIVE, load_code=None, tags=["BW"],
         progression_mode=ProgressionMode.ASSISTED, scheme=Scheme.REP_RATIO,
         assist_ladder=[25, 20, 15, 10, 5, 0], primary_muscle="ABS", secondary_muscles=[]),
    dict(name="Andreoni Cable Pullover", base_name="Andreoni Cable Pullover",
         region=Region.UPPER, status=Status.ACTIVE,
         load_code="ANDREONI", tags=["ANDREONI", "FT"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=10, primary_muscle="LATS", secondary_muscles=["MID_BACK"]),
]


def seed() -> None:
    create_db_and_tables()
    migrate.stamp_all(engine)   # fresh DB: schema built by create_all; record all migrations applied
    with get_session() as s:
        if s.exec(select(Equipment)).first():
            print("Already seeded — delete ironlog.db to reseed.")
            return

        eq = {}
        for name, floor, step, unit in EQUIPMENT:
            e = Equipment(name=name, load_floor=floor, min_step=step, load_unit=unit)
            s.add(e)
        s.commit()
        for e in s.exec(select(Equipment)).all():
            eq[e.name] = e.id

        for (ph, obj, lo, hi, cap, top, prog, vol, dpct, dses) in PHASES:
            s.add(PhasePolicy(phase=ph, default_objective=obj, rpe_band_low=lo,
                              rpe_band_high=hi, hard_cap=cap, top_set_rpe=top,
                              progression_attempted=prog, volume_posture=vol,
                              meaningful_drop_pct=dpct, meaningful_drop_sessions=dses))

        for label, b, p, usable in BANDS:
            s.add(BandPair(label=label, bottom_lb=b, peak_lb=p, usable=usable,
                           calibration_status=BandCalStatus.MODELED))

        for lift, opts in TAXONOMY.items():
            for i, code in enumerate(opts):
                s.add(StickingPointTaxonomy(lift_category=lift, option_code=code, order_index=i))
        s.commit()

        def _eq_id(code):
            return eq[CODE_TO_EQUIP[code]] if code else None

        created = {}
        for m in MOVEMENTS:                              # pass 1: create all
            mv = Movement(
                name=m["name"], base_name=m["base_name"],
                region=m.get("region", Region.NONE),
                lift_category=m.get("lift_category", LiftCategory.NONE),
                is_primary=m.get("is_primary", False),
                status=m.get("status", Status.ACTIVE),
                knee_modality=m.get("knee_modality"),
                unilateral=m.get("unilateral", False),
                load_equipment_id=_eq_id(m.get("load_code")),
                equipment_tags=m.get("tags", []),
                progression_mode=m.get("progression_mode", ProgressionMode.LADDER),
                scheme=m.get("scheme", Scheme.STRAIGHT),
                objective_override=m.get("objective_override"),
                increment_ladder=m.get("increment_ladder", []),
                assist_ladder=m.get("assist_ladder"),
                min_step=m.get("min_step"), load_floor=m.get("load_floor"),
                cap=m.get("cap"),
                rpe_capped=m.get("rpe_capped", False),
                rpe_cap_exempt=m.get("rpe_cap_exempt", False),
                band_eligible=m.get("band_eligible", False),
                family=m.get("family"), is_family_anchor=m.get("is_family_anchor", False),
                start_ratio=m.get("start_ratio"), notes=m.get("notes"),
                primary_muscle=m.get("primary_muscle"),
                secondary_muscles=m.get("secondary_muscles", []),
            )
            s.add(mv)
            created[m["name"]] = mv
        s.commit()

        for m in MOVEMENTS:                              # pass 2: resolve derived_from -> id
            if m.get("derived_from"):
                child = created[m["name"]]
                child.derived_from_id = created[m["derived_from"]].id
                s.add(child)
        s.commit()

        s.add(MovementState(movement_id=created["Back Squat [PB]"].id, e1rm=278,
                            e1rm_updated_at=datetime.now(timezone.utc),
                            current_load=220, calibration_status=CalibrationStatus.INHERITED))
        s.add(EngineState(id=1, current_phase=Phase.CUT, bodyweight=231))
        s.commit()
        print("Seeded ironlog.db")


if __name__ == "__main__":
    seed()
