"""
seed.py — create the database and load the locked reference data, plus a few
example movements. Run from the repo root:

    python -m ironlog.seed
"""
from datetime import datetime, timezone

from sqlmodel import select

from . import migrate
from .db import create_db_and_tables, engine, get_session
from .models import (
    BandCalStatus, BandPair, CalibrationStatus, EngineState, Equipment,
    LiftCategory, LoadUnit, Movement, MovementState, Objective, Phase,
    PhasePolicy, ProgressionMode, Region, Scheme, StickingPointTaxonomy,
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

        back_squat = Movement(
            name="Back Squat [PB]", base_name="Back Squat", region=Region.LOWER,
            lift_category=LiftCategory.BACK_SQUAT, is_primary=True, rpe_capped=True,
            load_equipment_id=eq["Barbell - Double Black Diamond"], equipment_tags=["PB"],
            progression_mode=ProgressionMode.LADDER, scheme=Scheme.TOPSET_BACKOFF,
            increment_ladder=[10, 5, 2.5], min_step=2.5, load_floor=45,
            family="back_squat", is_family_anchor=True)
        hip_thrust = Movement(
            name="Hip Thrust [HIP_THRUST]", base_name="Hip Thrust", region=Region.LOWER,
            lift_category=LiftCategory.HIP_THRUST, load_equipment_id=eq["GMWD hip thrust"],
            equipment_tags=["HIP_THRUST"], progression_mode=ProgressionMode.COMPOSITE,
            scheme=Scheme.STRAIGHT, rpe_cap_exempt=True, band_eligible=True)
        lateral_raise = Movement(
            name="Lateral Raise [FT]", base_name="Lateral Raise", region=Region.UPPER,
            load_equipment_id=eq["Ares cable (single)"], equipment_tags=["FT"],
            progression_mode=ProgressionMode.LADDER, scheme=Scheme.DOUBLE_PROGRESSION,
            increment_ladder=[2.5], min_step=2.5, load_floor=10)
        s.add_all([back_squat, hip_thrust, lateral_raise])
        s.commit()

        front_squat = Movement(
            name="Front Squat [PB]", base_name="Front Squat", region=Region.LOWER,
            lift_category=LiftCategory.FRONT_SQUAT,
            load_equipment_id=eq["Barbell - Double Black Diamond"], equipment_tags=["PB"],
            progression_mode=ProgressionMode.LADDER, scheme=Scheme.TOPSET_BACKOFF,
            increment_ladder=[10, 5, 2.5], min_step=2.5, load_floor=45,
            derived_from_id=back_squat.id, start_ratio=0.80)
        pullup = Movement(
            name="Pull-up [TOWER + TUBES]", base_name="Pull-up", region=Region.UPPER,
            load_equipment_id=eq["Pull-up tower"], equipment_tags=["TOWER", "TUBES"],
            progression_mode=ProgressionMode.ASSISTED, scheme=Scheme.REP_RATIO,
            objective_override=Objective.PROGRESS, is_tracked=True)
        s.add_all([front_squat, pullup])
        s.commit()

        s.add(MovementState(movement_id=back_squat.id, e1rm=278,
                            e1rm_updated_at=datetime.now(timezone.utc),
                            current_load=220, calibration_status=CalibrationStatus.INHERITED))
        s.add(EngineState(id=1, current_phase=Phase.CUT, bodyweight=231))
        s.commit()
        print("Seeded ironlog.db")


if __name__ == "__main__":
    seed()
