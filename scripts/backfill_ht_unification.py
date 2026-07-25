"""backfill_ht_unification.py — one-off idempotent seed for D2/D5 HT unification.
"""
from sqlmodel import Session, select
from ironlog.models.enums import LiftCategory, CalibrationStatus
from ironlog.models.library import Movement, MovementState, BandPair, HtProgressionState
from ironlog.models.program import ProgramDay, Tier, TierExercise
from ironlog.engine.band_composite import Band, config_peak

def apply(db: Session) -> None:
    # 1. Find D2 and D5 Hip Thrust TierExercise rows
    stmt = (
        select(TierExercise, ProgramDay.day_role)
        .join(Tier, Tier.id == TierExercise.tier_id)
        .join(ProgramDay, ProgramDay.id == Tier.program_day_id)
        .join(Movement, Movement.id == TierExercise.movement_id)
        .where(
            ProgramDay.day_role.in_(["D2 Lower A", "D5 Lower B"]),
            Movement.lift_category == LiftCategory.HIP_THRUST
        )
    )
    rows = db.exec(stmt).all()
    if not rows:
        print("No D2/D5 Hip Thrust slots found, skipping.")
        return

    ht_slots = [slot for slot, _ in rows]
    day_role_by_slot_id = {slot.id: day_role for slot, day_role in rows}
    movement_id = ht_slots[0].movement_id

    # Make idempotent
    for slot in ht_slots:
        if slot.unified_ht_group != "main":
            slot.unified_ht_group = "main"
            db.add(slot)
            print(f"Updated TierExercise {slot.id} (day={day_role_by_slot_id[slot.id]}) unified_ht_group='main'.")
        else:
            print(f"TierExercise {slot.id} already unified_ht_group='main'.")

    # 4. Check for existing HtProgressionState
    ht_state = db.exec(
        select(HtProgressionState).where(
            HtProgressionState.movement_id == movement_id,
            HtProgressionState.unified_ht_group == "main"
        )
    ).first()

    if ht_state:
        print("already migrated, skipping HtProgressionState creation.")
        db.commit()
        return

    # 2. Load D2 and D5 current MovementState
    d2_state = db.exec(select(MovementState).where(MovementState.movement_id == movement_id, MovementState.day_id == "D2 Lower A")).first()
    d5_state = db.exec(select(MovementState).where(MovementState.movement_id == movement_id, MovementState.day_id == "D5 Lower B")).first()

    if not d2_state and not d5_state:
        print("Neither D2 nor D5 has a MovementState row, nothing to migrate.")
        db.commit()
        return
    
    # 3. Compare via config_peak. higher peak wins. On exact tie, prefer fewer bands.
    band_pairs = db.exec(select(BandPair)).all()
    by_id = {bp.id: Band(bp.id, bp.bottom_lb, bp.peak_lb, bp.usable) for bp in band_pairs}

    def _get_peak_and_count(state: MovementState):
        if not state or state.ht_plates is None:
            return -1, 0, None, None, None
        config = list(state.ht_band_config or [])
        peak = config_peak(state.ht_plates, config, by_id)
        return peak, len(config), state.ht_plates, config, state.calibration_status

    d2_peak, d2_count, d2_plates, d2_config, d2_cal = _get_peak_and_count(d2_state)
    d5_peak, d5_count, d5_plates, d5_config, d5_cal = _get_peak_and_count(d5_state)

    print(f"D2: plates={d2_plates} config={d2_config} peak={d2_peak}")
    print(f"D5: plates={d5_plates} config={d5_config} peak={d5_peak}")

    winner_plates, winner_config, winner_cal = None, None, None
    reason = ""
    if d2_peak > d5_peak:
        winner_plates, winner_config, winner_cal = d2_plates, d2_config, d2_cal
        reason = "D2 peak higher"
    elif d5_peak > d2_peak:
        winner_plates, winner_config, winner_cal = d5_plates, d5_config, d5_cal
        reason = "D5 peak higher"
    else:
        # Tie break: prefer fewer bands (simpler setup). (ht_next_setup itself prefers fewer bands when tiebreaking equal peaks)
        if d2_count <= d5_count:
            winner_plates, winner_config, winner_cal = d2_plates, d2_config, d2_cal
            reason = "tied peak, D2 has fewer/equal bands"
        else:
            winner_plates, winner_config, winner_cal = d5_plates, d5_config, d5_cal
            reason = "tied peak, D5 has fewer bands"

    if winner_plates is None:
        print("No valid plates found on either day, falling back to calibration defaults.")
        winner_plates = 0.0
        winner_config = []
        winner_cal = CalibrationStatus.MEASURED

    print(f"Winner: {reason} -> plates={winner_plates}, config={winner_config}")

    # Create HtProgressionState
    ht_row = HtProgressionState(
        movement_id=movement_id,
        unified_ht_group="main",
        ht_plates=winner_plates,
        ht_band_config=winner_config,
        calibration_status=winner_cal if winner_cal is not None else CalibrationStatus.MEASURED
    )
    db.add(ht_row)
    db.commit()
    print("Created HtProgressionState row.")

def main() -> None:
    from ironlog.db import engine
    with Session(engine) as db:
        apply(db)

if __name__ == "__main__":
    main()
