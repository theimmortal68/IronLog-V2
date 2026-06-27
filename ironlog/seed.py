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
BANDS = [("#0 Orange", 14, 30, True), ("#1 Red", 29, 60, True),
         ("#2 Blue", 47, 100, True), ("#3 Green", 63, 133, True),
         ("#4 Black", 102, 217, True), ("#5 Purple", 151, 317, False)]

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
    # T1 TOPSET_BACKOFF lifts (6 — rotating squat slot + bench/OHP/RDL)
    # ─────────────────────────────────────────────────────────────────────────
    dict(name="Back Squat [PB]", base_name="Back Squat", region=Region.LOWER,
         lift_category=LiftCategory.BACK_SQUAT, is_primary=True, status=Status.ACTIVE,
         load_code="PB", tags=["PB"], progression_mode=ProgressionMode.LADDER,
         scheme=Scheme.TOPSET_BACKOFF, increment_ladder=[10, 5, 2.5], min_step=2.5,
         load_floor=45, rpe_capped=True, family="back_squat", is_family_anchor=True),
    dict(name="Front Squat [PB]", base_name="Front Squat", region=Region.LOWER,
         lift_category=LiftCategory.FRONT_SQUAT, is_primary=True, status=Status.ACTIVE,
         load_code="PB", tags=["PB"], progression_mode=ProgressionMode.LADDER,
         scheme=Scheme.TOPSET_BACKOFF, increment_ladder=[10, 5, 2.5], min_step=2.5,
         load_floor=45, rpe_capped=True,
         derived_from="Back Squat [PB]", start_ratio=0.80),
    dict(name="Belt Squat [GHR + FT]", base_name="Belt Squat", region=Region.LOWER,
         lift_category=LiftCategory.BACK_SQUAT, is_primary=True, status=Status.ACTIVE,
         load_code="GHR", tags=["GHR", "FT"], progression_mode=ProgressionMode.LADDER,
         scheme=Scheme.TOPSET_BACKOFF, increment_ladder=[10, 5, 2.5], min_step=2.5,
         load_floor=0, rpe_capped=True, family="belt_squat", is_family_anchor=True),
    dict(name="Bench Press [PB]", base_name="Bench Press", region=Region.UPPER,
         lift_category=LiftCategory.BENCH, is_primary=True, status=Status.ACTIVE,
         load_code="PB", tags=["PB"], progression_mode=ProgressionMode.LADDER,
         scheme=Scheme.TOPSET_BACKOFF, increment_ladder=[5, 2.5], min_step=2.5,
         load_floor=45, rpe_capped=True, family="bench", is_family_anchor=True),
    dict(name="Standing OHP [PB]", base_name="Standing OHP", region=Region.UPPER,
         lift_category=LiftCategory.OHP, is_primary=True, status=Status.ACTIVE,
         load_code="PB", tags=["PB"], progression_mode=ProgressionMode.LADDER,
         scheme=Scheme.TOPSET_BACKOFF, increment_ladder=[5, 2.5], min_step=2.5,
         load_floor=45, rpe_capped=True, family="ohp", is_family_anchor=True),
    dict(name="RDL [PB]", base_name="RDL", region=Region.LOWER,
         lift_category=LiftCategory.RDL, is_primary=True, status=Status.ACTIVE,
         load_code="PB", tags=["PB"], progression_mode=ProgressionMode.LADDER,
         scheme=Scheme.TOPSET_BACKOFF, increment_ladder=[10, 5, 2.5], min_step=2.5,
         load_floor=45, rpe_capped=True, family="rdl", is_family_anchor=True),

    # ─────────────────────────────────────────────────────────────────────────
    # Primary STRAIGHT lifts (Box rides back_squat e1RM; DLs own-baseline)
    # ─────────────────────────────────────────────────────────────────────────
    dict(name="Box Squat [PB]", base_name="Box Squat", region=Region.LOWER,
         lift_category=LiftCategory.BACK_SQUAT, is_primary=True, status=Status.ACTIVE,
         load_code="PB", tags=["PB"], progression_mode=ProgressionMode.LADDER,
         scheme=Scheme.STRAIGHT, increment_ladder=[10, 5, 2.5], min_step=2.5,
         load_floor=45, derived_from="Back Squat [PB]", start_ratio=0.90),
    dict(name="Conventional DL [PB]", base_name="Conventional DL", region=Region.LOWER,
         lift_category=LiftCategory.DEADLIFT, is_primary=True, status=Status.ACTIVE,
         load_code="PB", tags=["PB"], progression_mode=ProgressionMode.LADDER,
         scheme=Scheme.STRAIGHT, increment_ladder=[10, 5, 2.5], min_step=2.5,
         load_floor=45, family="conventional_dl", is_family_anchor=True),
    dict(name="Sumo DL [PB]", base_name="Sumo DL", region=Region.LOWER,
         lift_category=LiftCategory.DEADLIFT, is_primary=True, status=Status.ACTIVE,
         load_code="PB", tags=["PB"], progression_mode=ProgressionMode.LADDER,
         scheme=Scheme.STRAIGHT, increment_ladder=[10, 5, 2.5], min_step=2.5,
         load_floor=45, family="sumo_dl", is_family_anchor=True),
    dict(name="Bent Over Row [PB]", base_name="Bent Over Row", region=Region.UPPER,
         lift_category=LiftCategory.ROW, is_primary=True, status=Status.ACTIVE,
         load_code="PB", tags=["PB"], progression_mode=ProgressionMode.LADDER,
         scheme=Scheme.STRAIGHT, increment_ladder=[5, 2.5], min_step=2.5, load_floor=45),

    # ─────────────────────────────────────────────────────────────────────────
    # Hip Thrust composite family
    # ─────────────────────────────────────────────────────────────────────────
    dict(name="Hip Thrust [HIP_THRUST]", base_name="Hip Thrust", region=Region.LOWER,
         lift_category=LiftCategory.HIP_THRUST, status=Status.ACTIVE,
         load_code="HIP_THRUST", tags=["HIP_THRUST"],
         progression_mode=ProgressionMode.COMPOSITE, scheme=Scheme.STRAIGHT,
         rpe_cap_exempt=True, band_eligible=True,
         family="hip_thrust", is_family_anchor=True),
    dict(name="Banded Hip Thrust [HIP_THRUST]", base_name="Banded Hip Thrust",
         region=Region.LOWER, lift_category=LiftCategory.HIP_THRUST, status=Status.ACTIVE,
         load_code="HIP_THRUST", tags=["HIP_THRUST"],
         progression_mode=ProgressionMode.COMPOSITE, scheme=Scheme.STRAIGHT,
         rpe_cap_exempt=True, band_eligible=True, family="hip_thrust"),
    dict(name="Banded BW Hip Thrust [BAND]", base_name="Banded BW Hip Thrust",
         region=Region.LOWER, status=Status.ACTIVE,
         load_code=None, tags=["BAND"],
         progression_mode=ProgressionMode.PROTOCOL, scheme=Scheme.STRAIGHT,
         band_eligible=True, family="hip_thrust"),

    # ─────────────────────────────────────────────────────────────────────────
    # Assisted (ASSISTED → REP_RATIO)
    # ─────────────────────────────────────────────────────────────────────────
    dict(name="Nordic Curl [GHR]", base_name="Nordic Curl", region=Region.LOWER,
         status=Status.ACTIVE, load_code="GHR", tags=["GHR"],
         progression_mode=ProgressionMode.ASSISTED, scheme=Scheme.REP_RATIO,
         knee_modality=KneeModality.NORDIC, family="nordic", is_family_anchor=True),
    dict(name="Nordic Curl - Volume [GHR]", base_name="Nordic Curl - Volume",
         region=Region.LOWER, status=Status.ACTIVE, load_code="GHR", tags=["GHR"],
         progression_mode=ProgressionMode.ASSISTED, scheme=Scheme.REP_RATIO,
         knee_modality=KneeModality.NORDIC, family="nordic"),
    dict(name="Reverse Nordic Curl [GHR]", base_name="Reverse Nordic Curl",
         region=Region.LOWER, status=Status.ACTIVE, load_code="GHR", tags=["GHR"],
         progression_mode=ProgressionMode.ASSISTED, scheme=Scheme.REP_RATIO,
         knee_modality=KneeModality.KOT, min_step=2.5, load_floor=10),
    dict(name="Pull-up [TOWER + TUBES]", base_name="Pull-up", region=Region.UPPER,
         status=Status.ACTIVE, load_code="TOWER", tags=["TOWER", "TUBES"],
         progression_mode=ProgressionMode.ASSISTED, scheme=Scheme.REP_RATIO,
         objective_override=Objective.PROGRESS),

    # ─────────────────────────────────────────────────────────────────────────
    # Reverse hyper (LADDER, STRAIGHT, cap-and-reps; own baselines — no e1RM ratio)
    # ─────────────────────────────────────────────────────────────────────────
    dict(name="Reverse Hyper [REV_HYPER]", base_name="Reverse Hyper", region=Region.LOWER,
         lift_category=LiftCategory.REV_HYPER, status=Status.ACTIVE,
         load_code="REV_HYPER", tags=["REV_HYPER"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.STRAIGHT,
         load_floor=0, cap=180, family="reverse_hyper", is_family_anchor=True),
    dict(name="Light Reverse Hyper [REV_HYPER]", base_name="Light Reverse Hyper",
         region=Region.LOWER, lift_category=LiftCategory.REV_HYPER, status=Status.ACTIVE,
         load_code="REV_HYPER", tags=["REV_HYPER"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.STRAIGHT,
         load_floor=0, cap=90, family="reverse_hyper"),

    # ─────────────────────────────────────────────────────────────────────────
    # Lower accessories — LADDER / DOUBLE_PROGRESSION  (ACTIVE)
    # ─────────────────────────────────────────────────────────────────────────
    # ATG Split Squat: no bracket in name → tags=[], load_code=None; min_step movement-level
    dict(name="ATG Split Squat", base_name="ATG Split Squat", region=Region.LOWER,
         status=Status.ACTIVE, load_code=None, tags=[],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[2.5], min_step=2.5, load_floor=10,
         knee_modality=KneeModality.KOT),
    dict(name="ATG Split Squat [BW]", base_name="ATG Split Squat", region=Region.LOWER,
         status=Status.ACTIVE, load_code=None, tags=["BW"],
         progression_mode=ProgressionMode.PROTOCOL, scheme=Scheme.STRAIGHT,
         knee_modality=KneeModality.KOT),
    dict(name="ATG Squat Hold", base_name="ATG Squat Hold", region=Region.LOWER,
         status=Status.ACTIVE, load_code=None, tags=[],
         progression_mode=ProgressionMode.PROTOCOL, scheme=Scheme.STRAIGHT,
         knee_modality=KneeModality.KOT),
    dict(name="Bulgarian Split Squat [DB]", base_name="Bulgarian Split Squat",
         region=Region.LOWER, status=Status.ACTIVE, load_code="DB", tags=["DB"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[2.5], min_step=2.5, load_floor=10),
    dict(name="Calf Raise [GHR]", base_name="Calf Raise", region=Region.LOWER,
         status=Status.ACTIVE, load_code="GHR", tags=["GHR"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=0),
    dict(name="Heels-Elevated Goblet Squat [DB]", base_name="Heels-Elevated Goblet Squat",
         region=Region.LOWER, status=Status.ACTIVE, load_code="DB", tags=["DB"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[2.5], min_step=2.5, load_floor=10),
    dict(name="Leg Curl [GHR]", base_name="Leg Curl", region=Region.LOWER,
         status=Status.ACTIVE, load_code="GHR", tags=["GHR"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=10),
    dict(name="Leg Extension [GHR]", base_name="Leg Extension", region=Region.LOWER,
         status=Status.ACTIVE, load_code="GHR", tags=["GHR"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=10),
    dict(name="Reverse Lunge [DB]", base_name="Reverse Lunge", region=Region.LOWER,
         status=Status.ACTIVE, load_code="DB", tags=["DB"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[2.5], min_step=2.5, load_floor=10),

    # ─────────────────────────────────────────────────────────────────────────
    # Upper accessories — LADDER / DOUBLE_PROGRESSION  (ACTIVE)
    # bench-family ratio-variant
    # ─────────────────────────────────────────────────────────────────────────
    dict(name="Swiss Bar CG Press [SB]", base_name="Swiss Bar CG Press",
         region=Region.UPPER, lift_category=LiftCategory.CG_PRESS, status=Status.ACTIVE,
         load_code="SB", tags=["SB"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=30,
         derived_from="Bench Press [PB]", start_ratio=0.90),
    dict(name="Swiss Bar Press [SB]", base_name="Swiss Bar Press",
         region=Region.UPPER, lift_category=LiftCategory.CG_PRESS, status=Status.ACTIVE,
         load_code="SB", tags=["SB"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=30),
    dict(name="JM Press [SB]", base_name="JM Press",
         region=Region.UPPER, lift_category=LiftCategory.CG_PRESS, status=Status.ACTIVE,
         load_code="SB", tags=["SB"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=30),
    # OHP ratio-variant
    dict(name="Z-Press [DB]", base_name="Z-Press", region=Region.UPPER,
         status=Status.ACTIVE, load_code="DB", tags=["DB"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[2.5], min_step=2.5, load_floor=10,
         derived_from="Standing OHP [PB]", start_ratio=0.85),
    # Pendlay Row family (Medium anchor + grip variants at 1.0)
    dict(name="Pendlay Row - Medium [OB]", base_name="Pendlay Row - Medium",
         region=Region.UPPER, lift_category=LiftCategory.ROW, status=Status.ACTIVE,
         load_code="OB", tags=["OB"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=45,
         family="pendlay_row", is_family_anchor=True),
    dict(name="Pendlay Row - Narrow [OB]", base_name="Pendlay Row - Narrow",
         region=Region.UPPER, lift_category=LiftCategory.ROW, status=Status.ACTIVE,
         load_code="OB", tags=["OB"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=45,
         derived_from="Pendlay Row - Medium [OB]", start_ratio=1.0),
    dict(name="Pendlay Row - Wide [OB]", base_name="Pendlay Row - Wide",
         region=Region.UPPER, lift_category=LiftCategory.ROW, status=Status.ACTIVE,
         load_code="OB", tags=["OB"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=45,
         derived_from="Pendlay Row - Medium [OB]", start_ratio=1.0),
    # T-Bar Row family (Medium anchor; floor non-binding → None)
    dict(name="T-Bar Row - Medium [OB + KLEVA + LM]", base_name="T-Bar Row - Medium",
         region=Region.UPPER, status=Status.ACTIVE,
         load_code="OB", tags=["OB", "KLEVA", "LM"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=0,
         family="t_bar_row", is_family_anchor=True),
    dict(name="T-Bar Row - Narrow [OB + KLEVA + LM]", base_name="T-Bar Row - Narrow",
         region=Region.UPPER, status=Status.ACTIVE,
         load_code="OB", tags=["OB", "KLEVA", "LM"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=0,
         derived_from="T-Bar Row - Medium [OB + KLEVA + LM]", start_ratio=1.0),
    dict(name="T-Bar Row - Wide [OB + KLEVA + LM]", base_name="T-Bar Row - Wide",
         region=Region.UPPER, status=Status.ACTIVE,
         load_code="OB", tags=["OB", "KLEVA", "LM"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=0,
         derived_from="T-Bar Row - Medium [OB + KLEVA + LM]", start_ratio=1.0),
    # Andreoni station (load = dual cable, floor=20 matches ANDREONI Equipment row)
    dict(name="Andreoni Dips [ANDREONI + FT]", base_name="Andreoni Dips",
         region=Region.UPPER, status=Status.ACTIVE,
         load_code="ANDREONI", tags=["ANDREONI", "FT"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5], min_step=5, load_floor=20),
    dict(name="Andreoni Lat Prayer [ANDREONI + FT]", base_name="Andreoni Lat Prayer",
         region=Region.UPPER, status=Status.ACTIVE,
         load_code="ANDREONI", tags=["ANDREONI", "FT"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5], min_step=5, load_floor=20),
    dict(name="Andreoni Tricep Extension [ANDREONI + FT]",
         base_name="Andreoni Tricep Extension",
         region=Region.UPPER, status=Status.ACTIVE,
         load_code="ANDREONI", tags=["ANDREONI", "FT"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5], min_step=5, load_floor=20),
    # Cable upper accessories
    dict(name="Cable Low-to-High Fly [FT]", base_name="Cable Low-to-High Fly",
         region=Region.UPPER, status=Status.ACTIVE, load_code="FT", tags=["FT"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[2.5], min_step=2.5, load_floor=10),
    dict(name="Cable Tricep Pushdown [FT]", base_name="Cable Tricep Pushdown",
         region=Region.UPPER, status=Status.ACTIVE, load_code="FT", tags=["FT"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=10),
    dict(name="Cable V-Bar Pushdown [FT]", base_name="Cable V-Bar Pushdown",
         region=Region.UPPER, status=Status.ACTIVE, load_code="FT", tags=["FT"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=10),
    dict(name="Chest Supported Row [DB + BENCH]", base_name="Chest Supported Row",
         region=Region.UPPER, status=Status.ACTIVE,
         load_code="DB", tags=["DB", "BENCH"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[2.5], min_step=2.5, load_floor=10),
    dict(name="Cross-Body Cable Lateral Raise [FT]",
         base_name="Cross-Body Cable Lateral Raise",
         region=Region.UPPER, status=Status.ACTIVE, load_code="FT", tags=["FT"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[2.5], min_step=2.5, load_floor=10),
    dict(name="Cross-Body Cable Rear Delt Fly [FT]",
         base_name="Cross-Body Cable Rear Delt Fly",
         region=Region.UPPER, status=Status.ACTIVE, load_code="FT", tags=["FT"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[2.5], min_step=2.5, load_floor=10),
    dict(name="DB Lateral Raise [DB]", base_name="DB Lateral Raise",
         region=Region.UPPER, status=Status.ACTIVE, load_code="DB", tags=["DB"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[2.5], min_step=2.5, load_floor=10),
    dict(name="DB Seal Row [DB + UTIL_SEAT]", base_name="DB Seal Row",
         region=Region.UPPER, status=Status.ACTIVE,
         load_code="DB", tags=["DB", "UTIL_SEAT"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[2.5], min_step=2.5, load_floor=10),
    dict(name="Decline Swiss Bar Skull Crusher [SB + BENCH]",
         base_name="Decline Swiss Bar Skull Crusher",
         region=Region.UPPER, status=Status.ACTIVE,
         load_code="SB", tags=["SB", "BENCH"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=30),
    dict(name="Eccentric Pull-up [TOWER]", base_name="Eccentric Pull-up",
         region=Region.UPPER, status=Status.ACTIVE, load_code="TOWER", tags=["TOWER"],
         progression_mode=ProgressionMode.PROTOCOL, scheme=Scheme.STRAIGHT),
    dict(name="Face Pull w/ ER Hold [FT]", base_name="Face Pull w/ ER Hold",
         region=Region.UPPER, status=Status.ACTIVE, load_code="FT", tags=["FT"],
         progression_mode=ProgressionMode.PROTOCOL, scheme=Scheme.STRAIGHT,
         min_step=2.5, load_floor=10),
    dict(name="Heavy Lat Pulldown [FT]", base_name="Heavy Lat Pulldown",
         region=Region.UPPER, status=Status.ACTIVE, load_code="FT", tags=["FT"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5], min_step=5, load_floor=20),
    dict(name="Incline DB Press [DB + BENCH]", base_name="Incline DB Press",
         region=Region.UPPER, status=Status.ACTIVE,
         load_code="DB", tags=["DB", "BENCH"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[2.5], min_step=2.5, load_floor=10),
    dict(name="Incline DB Y-Raise [DB + BENCH]", base_name="Incline DB Y-Raise",
         region=Region.UPPER, status=Status.ACTIVE,
         load_code="DB", tags=["DB", "BENCH"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[2.5], min_step=2.5, load_floor=10),
    dict(name="Kneeling Cross-Body Lat Pullaround [FT]",
         base_name="Kneeling Cross-Body Lat Pullaround",
         region=Region.UPPER, status=Status.ACTIVE, load_code="FT", tags=["FT"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=10),
    dict(name="Lat Pulldown [FT]", base_name="Lat Pulldown",
         region=Region.UPPER, status=Status.ACTIVE, load_code="FT", tags=["FT"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5], min_step=5, load_floor=20),
    dict(name="Lateral Raise [FT]", base_name="Lateral Raise",
         region=Region.UPPER, status=Status.ACTIVE, load_code="FT", tags=["FT"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[2.5], min_step=2.5, load_floor=10),
    dict(name="Meadows Row [OB + LM]", base_name="Meadows Row",
         region=Region.UPPER, status=Status.ACTIVE,
         load_code="OB", tags=["OB", "LM"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[2.5], min_step=2.5, load_floor=0),
    dict(name="Rear Delt Fly [DB]", base_name="Rear Delt Fly",
         region=Region.UPPER, status=Status.ACTIVE, load_code="DB", tags=["DB"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[2.5], min_step=2.5, load_floor=10),
    dict(name="Seated Cable Row [FT]", base_name="Seated Cable Row",
         region=Region.UPPER, status=Status.ACTIVE, load_code="FT", tags=["FT"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=10),
    dict(name="Seated DB Press [DB]", base_name="Seated DB Press",
         region=Region.UPPER, status=Status.ACTIVE, load_code="DB", tags=["DB"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[2.5], min_step=2.5, load_floor=10),
    dict(name="Single-Arm Cable Chest Press [FT + D-handle]",
         base_name="Single-Arm Cable Chest Press",
         region=Region.UPPER, status=Status.ACTIVE,
         load_code="FT", tags=["FT", "D-handle"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=10),
    dict(name="Single-Arm Landmine Press [OB + LM]",
         base_name="Single-Arm Landmine Press",
         region=Region.UPPER, status=Status.ACTIVE,
         load_code="OB", tags=["OB", "LM"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[2.5], min_step=2.5, load_floor=0),

    # ─────────────────────────────────────────────────────────────────────────
    # Core — LADDER (PureTorque Pro: no bracket → min_step movement-level)
    # ─────────────────────────────────────────────────────────────────────────
    dict(name="PureTorque Pro Rotation", base_name="PureTorque Pro Rotation",
         region=Region.CORE, status=Status.ACTIVE, load_code=None, tags=[],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=10),

    # ─────────────────────────────────────────────────────────────────────────
    # Core — PROTOCOL  (ACTIVE)
    # ─────────────────────────────────────────────────────────────────────────
    dict(name="Ab Wheel [WHEEL]", base_name="Ab Wheel",
         region=Region.CORE, status=Status.ACTIVE, load_code=None, tags=["WHEEL"],
         progression_mode=ProgressionMode.PROTOCOL, scheme=Scheme.STRAIGHT),
    dict(name="Bear Hover", base_name="Bear Hover",
         region=Region.CORE, status=Status.ACTIVE, load_code=None, tags=[],
         progression_mode=ProgressionMode.PROTOCOL, scheme=Scheme.STRAIGHT),
    dict(name="Bear Hover + Shoulder Tap", base_name="Bear Hover + Shoulder Tap",
         region=Region.CORE, status=Status.ACTIVE, load_code=None, tags=[],
         progression_mode=ProgressionMode.PROTOCOL, scheme=Scheme.STRAIGHT),
    dict(name="Bird Dog", base_name="Bird Dog",
         region=Region.CORE, status=Status.ACTIVE, load_code=None, tags=[],
         progression_mode=ProgressionMode.PROTOCOL, scheme=Scheme.STRAIGHT),
    dict(name="Cable Crunch [FT]", base_name="Cable Crunch",
         region=Region.CORE, status=Status.ACTIVE, load_code="FT", tags=["FT"],
         progression_mode=ProgressionMode.PROTOCOL, scheme=Scheme.STRAIGHT,
         min_step=5, load_floor=20),
    dict(name="Cable External Rotation [FT]", base_name="Cable External Rotation",
         region=Region.CORE, status=Status.ACTIVE, load_code="FT", tags=["FT"],
         progression_mode=ProgressionMode.PROTOCOL, scheme=Scheme.STRAIGHT,
         min_step=2.5, load_floor=10),
    dict(name="Copenhagen Hold [BENCH]", base_name="Copenhagen Hold",
         region=Region.CORE, status=Status.ACTIVE, load_code=None, tags=["BENCH"],
         progression_mode=ProgressionMode.PROTOCOL, scheme=Scheme.STRAIGHT),
    dict(name="Dead Bug", base_name="Dead Bug",
         region=Region.CORE, status=Status.ACTIVE, load_code=None, tags=[],
         progression_mode=ProgressionMode.PROTOCOL, scheme=Scheme.STRAIGHT),
    dict(name="Decline Lying Leg Raise [GHR]", base_name="Decline Lying Leg Raise",
         region=Region.CORE, status=Status.ACTIVE, load_code="GHR", tags=["GHR"],
         progression_mode=ProgressionMode.PROTOCOL, scheme=Scheme.STRAIGHT),
    dict(name="Hanging Knee Raise [TOWER]", base_name="Hanging Knee Raise",
         region=Region.CORE, status=Status.ACTIVE, load_code="TOWER", tags=["TOWER"],
         progression_mode=ProgressionMode.PROTOCOL, scheme=Scheme.STRAIGHT),
    dict(name="Hard-Style Plank", base_name="Hard-Style Plank",
         region=Region.CORE, status=Status.ACTIVE, load_code=None, tags=[],
         progression_mode=ProgressionMode.PROTOCOL, scheme=Scheme.STRAIGHT),
    dict(name="Hollow Body Hold", base_name="Hollow Body Hold",
         region=Region.CORE, status=Status.ACTIVE, load_code=None, tags=[],
         progression_mode=ProgressionMode.PROTOCOL, scheme=Scheme.STRAIGHT),
    dict(name="Hollow Body Tuck Hold", base_name="Hollow Body Tuck Hold",
         region=Region.CORE, status=Status.ACTIVE, load_code=None, tags=[],
         progression_mode=ProgressionMode.PROTOCOL, scheme=Scheme.STRAIGHT),
    dict(name="Landmine Rotation [OB + LM]", base_name="Landmine Rotation",
         region=Region.CORE, status=Status.ACTIVE,
         load_code="OB", tags=["OB", "LM"],
         progression_mode=ProgressionMode.PROTOCOL, scheme=Scheme.STRAIGHT,
         min_step=2.5, cap=25),
    dict(name="Plank", base_name="Plank",
         region=Region.CORE, status=Status.ACTIVE, load_code=None, tags=[],
         progression_mode=ProgressionMode.PROTOCOL, scheme=Scheme.STRAIGHT),
    dict(name="Short-Lever Copenhagen [BENCH]", base_name="Short-Lever Copenhagen",
         region=Region.CORE, status=Status.ACTIVE, load_code=None, tags=["BENCH"],
         progression_mode=ProgressionMode.PROTOCOL, scheme=Scheme.STRAIGHT),

    # ─────────────────────────────────────────────────────────────────────────
    # PREP (1)
    # ─────────────────────────────────────────────────────────────────────────
    dict(name="Band Pull-Aparts", base_name="Band Pull-Aparts",
         region=Region.UPPER, status=Status.PREP,
         load_code=None, tags=["BAND"],
         progression_mode=ProgressionMode.PROTOCOL, scheme=Scheme.STRAIGHT,
         band_eligible=True),

    # ─────────────────────────────────────────────────────────────────────────
    # Conditioning (10) — no load FK; KB keeps its Equipment row
    # ─────────────────────────────────────────────────────────────────────────
    dict(name="Farmer Carries [FARMER HANDLES]", base_name="Farmer Carries",
         region=Region.NONE, status=Status.ACTIVE,
         load_code=None, tags=["FARMER HANDLES"],
         progression_mode=ProgressionMode.CONDITIONING, scheme=Scheme.STRAIGHT),
    dict(name="Farmer Walk [FARMER]", base_name="Farmer Walk",
         region=Region.NONE, status=Status.ACTIVE,
         load_code=None, tags=["FARMER"],
         progression_mode=ProgressionMode.CONDITIONING, scheme=Scheme.STRAIGHT),
    dict(name="Jump Rope Intervals [JR]", base_name="Jump Rope Intervals",
         region=Region.NONE, status=Status.ACTIVE,
         load_code=None, tags=["JR"],
         progression_mode=ProgressionMode.CONDITIONING, scheme=Scheme.STRAIGHT),
    dict(name="Jump Rope Tabata [JR]", base_name="Jump Rope Tabata",
         region=Region.NONE, status=Status.ACTIVE,
         load_code=None, tags=["JR"],
         progression_mode=ProgressionMode.CONDITIONING, scheme=Scheme.STRAIGHT),
    dict(name="Jump Rope [JR]", base_name="Jump Rope",
         region=Region.NONE, status=Status.ACTIVE,
         load_code=None, tags=["JR"],
         progression_mode=ProgressionMode.CONDITIONING, scheme=Scheme.STRAIGHT),
    dict(name="KB Swing Tabata [KB]", base_name="KB Swing Tabata",
         region=Region.NONE, status=Status.ACTIVE,
         load_code="KB", tags=["KB"],
         progression_mode=ProgressionMode.CONDITIONING, scheme=Scheme.STRAIGHT,
         load_floor=13),
    dict(name="KB Swings [KB]", base_name="KB Swings",
         region=Region.NONE, status=Status.ACTIVE,
         load_code="KB", tags=["KB"],
         progression_mode=ProgressionMode.CONDITIONING, scheme=Scheme.STRAIGHT,
         load_floor=13),
    dict(name="Sandbag Carry [SANDBAG]", base_name="Sandbag Carry",
         region=Region.NONE, status=Status.ACTIVE,
         load_code=None, tags=["SANDBAG"],
         progression_mode=ProgressionMode.CONDITIONING, scheme=Scheme.STRAIGHT),
    dict(name="Sandbag Over-Shoulder [SANDBAG]", base_name="Sandbag Over-Shoulder",
         region=Region.NONE, status=Status.ACTIVE,
         load_code=None, tags=["SANDBAG"],
         progression_mode=ProgressionMode.CONDITIONING, scheme=Scheme.STRAIGHT),
    dict(name="Slam Ball [BALL]", base_name="Slam Ball",
         region=Region.NONE, status=Status.ACTIVE,
         load_code=None, tags=["BALL"],
         progression_mode=ProgressionMode.CONDITIONING, scheme=Scheme.STRAIGHT),

    # ─────────────────────────────────────────────────────────────────────────
    # INACTIVE (8) — kept, dormant, eligible for future blocks
    # ─────────────────────────────────────────────────────────────────────────
    dict(name="Bayesian Cable Curl [FT]", base_name="Bayesian Cable Curl",
         region=Region.UPPER, status=Status.INACTIVE,
         load_code="FT", tags=["FT"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=10),
    # Dips: BW/protocol on the Andreoni dip station → load_code=None
    dict(name="Dips [ANDREONI + FT]", base_name="Dips",
         region=Region.UPPER, status=Status.INACTIVE,
         load_code=None, tags=["ANDREONI", "FT"],
         progression_mode=ProgressionMode.PROTOCOL, scheme=Scheme.STRAIGHT),
    # EZ curl family (all INACTIVE; grip variants ride the Medium anchor at 1.0)
    dict(name="EZ Bar Curl - Medium Grip [EZ]", base_name="EZ Bar Curl - Medium Grip",
         region=Region.UPPER, status=Status.INACTIVE,
         load_code="EZ", tags=["EZ"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=35,
         family="ez_curl", is_family_anchor=True),
    dict(name="EZ Bar Curl - Narrow Grip [EZ]", base_name="EZ Bar Curl - Narrow Grip",
         region=Region.UPPER, status=Status.INACTIVE,
         load_code="EZ", tags=["EZ"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=35,
         derived_from="EZ Bar Curl - Medium Grip [EZ]", start_ratio=1.0),
    dict(name="EZ Bar Curl - Wide Grip [EZ]", base_name="EZ Bar Curl - Wide Grip",
         region=Region.UPPER, status=Status.INACTIVE,
         load_code="EZ", tags=["EZ"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=35,
         derived_from="EZ Bar Curl - Medium Grip [EZ]", start_ratio=1.0),
    dict(name="Hammer Curl [DB]", base_name="Hammer Curl",
         region=Region.UPPER, status=Status.INACTIVE,
         load_code="DB", tags=["DB"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[2.5], min_step=2.5, load_floor=10),
    dict(name="Incline DB Curl [DB + BENCH]", base_name="Incline DB Curl",
         region=Region.UPPER, status=Status.INACTIVE,
         load_code="DB", tags=["DB", "BENCH"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[2.5], min_step=2.5, load_floor=10),
    # Lat Prayer uses single-stack FT (not ANDREONI dual despite bracket)
    dict(name="Lat Prayer [ANDREONI + FT]", base_name="Lat Prayer",
         region=Region.UPPER, status=Status.INACTIVE,
         load_code="FT", tags=["ANDREONI", "FT"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[5, 2.5], min_step=2.5, load_floor=10),

    # ─────────────────────────────────────────────────────────────────────────
    # 3 knee movements ADDED (beyond the 100 sheet rows — closes docs/06 §4 gap)
    # ─────────────────────────────────────────────────────────────────────────
    dict(name="Sissy Squat", base_name="Sissy Squat", region=Region.LOWER,
         status=Status.ACTIVE, knee_modality=KneeModality.SISSY,
         load_code="DB", tags=["BW", "DB"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[2.5], min_step=2.5, load_floor=0,
         notes="Single continuous added-load track from BW (0); plate->DB/KB is a tag, "
               "not a load-track break — no e1RM reset."),
    dict(name="Cable Tibialis Raise", base_name="Cable Tibialis Raise",
         region=Region.LOWER, status=Status.ACTIVE,
         knee_modality=KneeModality.TIB, load_code="FT", tags=["FT"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[2.5], min_step=2.5, load_floor=10),
    dict(name="Poliquin Step-up", base_name="Poliquin Step-up",
         region=Region.LOWER, status=Status.ACTIVE,
         knee_modality=KneeModality.KOT, load_code="DB", tags=["DB"],
         progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
         increment_ladder=[2.5], min_step=2.5, load_floor=10),
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
                load_equipment_id=_eq_id(m.get("load_code")),
                equipment_tags=m.get("tags", []),
                progression_mode=m.get("progression_mode", ProgressionMode.LADDER),
                scheme=m.get("scheme", Scheme.STRAIGHT),
                objective_override=m.get("objective_override"),
                increment_ladder=m.get("increment_ladder", []),
                min_step=m.get("min_step"), load_floor=m.get("load_floor"),
                cap=m.get("cap"),
                rpe_capped=m.get("rpe_capped", False),
                rpe_cap_exempt=m.get("rpe_cap_exempt", False),
                band_eligible=m.get("band_eligible", False),
                family=m.get("family"), is_family_anchor=m.get("is_family_anchor", False),
                start_ratio=m.get("start_ratio"), notes=m.get("notes"),
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
