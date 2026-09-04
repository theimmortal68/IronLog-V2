import argparse
import sys
from datetime import date, timedelta
from typing import List, Optional

from sqlmodel import Session as DbSession, select

from ironlog.db import engine
from ironlog.models.library import EngineState, DailyReadiness
from ironlog.models.session import Session as TrainingSession
from ironlog.models.program import MesoRotation
from ironlog.models.periodization import (
    BodyCompState, BodyCompStateValue, RecoveryStatus, RecoveryStatusValue,
    Macrocycle, Mesocycle, Microcycle, MesocycleTemplate
)
from ironlog.engine.readiness import (
    DailyReadinessInput, compute_sleep_ok, compute_subjective_ok, BOOL_MIN_READINGS, BOOL_WINDOW_DAYS
)
from ironlog.engine.periodization_resolver import resolve_envelope


def _compute_recovery_status(readiness_inputs: List[DailyReadinessInput], as_of: date) -> RecoveryStatusValue:
    """
    Computes initial RecoveryStatus from existing pipeline functions.
    If both sleep and subjective readiness are OK (or undefined due to lack of data), NORMAL.
    If one is explicitly failing, CAUTION.
    If both are failing, POOR.
    """
    # Window must match readiness.py's own _trailing_rows cutoff EXACTLY
    # (cutoff = as_of - (BOOL_WINDOW_DAYS - 1), inclusive both ends) -- using
    # `<= BOOL_WINDOW_DAYS` here (one day wider) let this pre-check see 5+
    # readings and "trust the real function" while compute_sleep_ok's own
    # narrower window saw only 4 and failed closed to False, producing a
    # false POOR from what was actually borderline/insufficient data (caught
    # live, before applying to production: real recent readiness data was
    # sleep_ok=True/subjective_ok=True almost every day, yet this bug
    # produced RecoveryStatus.POOR).
    recent = [r for r in readiness_inputs if 0 <= (as_of - r.date).days <= BOOL_WINDOW_DAYS - 1]
    
    sleep_readings = [r.sleep_ok for r in recent if r.sleep_ok is not None]
    subj_readings = [r.subjective_ok for r in recent if r.subjective_ok is not None]
    
    has_sleep = len(sleep_readings) >= BOOL_MIN_READINGS
    has_subj = len(subj_readings) >= BOOL_MIN_READINGS

    sleep_ok = compute_sleep_ok(readiness_inputs, as_of) if has_sleep else True
    subj_ok = compute_subjective_ok(readiness_inputs, as_of) if has_subj else True

    if sleep_ok and subj_ok:
        return RecoveryStatusValue.NORMAL
    elif not sleep_ok and not subj_ok:
        return RecoveryStatusValue.POOR
    else:
        return RecoveryStatusValue.CAUTION


def migrate(
    session: DbSession,
    apply: bool,
    current_mesocycle_ordinal: int,
    current_microcycle_ordinal: int,
    mesocycle_length_weeks: int,
    seed_posture: str,
    
    calibration_maps_to: Optional[str] = None,
    rebuild_maps_to: Optional[str] = None,
    shadow_validation_sessions: int = 10,
    as_of: Optional[date] = None
) -> dict:
    if as_of is None:
        as_of = date.today()

    existing_macro = session.exec(select(Macrocycle)).first()
    if existing_macro and apply:
        print("Error: Migration looks like it has already been run (Macrocycle rows exist).", file=sys.stderr)
        raise RuntimeError("Migration idempotency guard failed.")

    engine_state = session.exec(select(EngineState).where(EngineState.id == 1)).first()
    
    current_phase = engine_state.current_phase.value if engine_state else "UNKNOWN"
    
    body_comp_value = None
    if current_phase == "CUT":
        body_comp_value = "CUT"
    elif current_phase == "STAB":
        body_comp_value = "MAINTENANCE"
    elif current_phase == "CALIBRATION":
        if not calibration_maps_to:
            raise ValueError("EngineState is in CALIBRATION phase. You must provide --calibration-maps-to=CUT|MAINTENANCE|GAIN")
        body_comp_value = calibration_maps_to
    elif current_phase == "REBUILD":
        if not rebuild_maps_to:
            raise ValueError("EngineState is in REBUILD phase. You must provide --rebuild-maps-to=CUT|MAINTENANCE|GAIN")
        body_comp_value = rebuild_maps_to
    
    if not body_comp_value:
        print(f"Error: Could not map EngineState current_phase={current_phase} to a BodyCompState.", file=sys.stderr)
        print("Aborting migration because core periodization state cannot be determined.", file=sys.stderr)
        raise RuntimeError("Missing BodyCompState mapping.")

    template = session.exec(select(MesocycleTemplate)).first()
    if template:
        template_action = f"reusing existing template id={template.id}"
        template_name = template.name
    else:
        template_action = "creating new template"
        template_name = "Cutover Template"

    planned_end_date = as_of + timedelta(weeks=mesocycle_length_weeks)
    micro_end_date = as_of + timedelta(days=6)
    expected_sessions = 4
    macrocycle_goal = "Initial Cutover Goal"

    plan = {
        "body_comp_state": body_comp_value,
        "recovery_status": None,
        "seed_posture": seed_posture,
        "mesocycle_ordinal": current_mesocycle_ordinal,
        "mesocycle_length_weeks": mesocycle_length_weeks,
        "mesocycle_template_action": template_action,
        "mesocycle_template_name": template_name,
        "macrocycle_goal": macrocycle_goal,
        "expected_sessions": expected_sessions,
        "planned_start_date": as_of.isoformat(),
        "planned_end_date": planned_end_date.isoformat(),
        "micro_end_date": micro_end_date.isoformat(),
        "meso_rotations": [],
        "shadow_validation": []
    }

    print("=== MIGRATION PLAN ===")
    print(f"Proposed BodyCompState: {body_comp_value} (from {current_phase})")
    
    print(f"Proposed Macrocycle Goal: '{macrocycle_goal}'")
    print(f"Proposed Seed Posture: {seed_posture}")
    print(f"Proposed Expected Sessions: {expected_sessions}")
    print(f"Proposed Mesocycle Ordinal: {current_mesocycle_ordinal}")
    print(f"Proposed Mesocycle Length: {mesocycle_length_weeks} weeks")
    print(f"Proposed Template Action: {template_action} ('{template_name}')")

    db_readiness = session.exec(select(DailyReadiness)).all()
    inputs = [
        DailyReadinessInput(
            date=r.date,
            bodyweight=r.bodyweight,
            body_fat_pct=r.body_fat_pct,
            resting_hr=r.resting_hr,
            sleep_ok=r.sleep_ok,
            subjective_ok=r.subjective_ok,
        )
        for r in db_readiness
    ]
    recovery_status_val = _compute_recovery_status(inputs, as_of)
    plan["recovery_status"] = recovery_status_val.value
    print(f"Proposed RecoveryStatus: {recovery_status_val.value} (computed fresh from readiness pipeline)")

    print(f"Proposed Microcycle Ordinal: {current_microcycle_ordinal}")
    
    recent_sessions = session.exec(
        select(TrainingSession)
        .order_by(TrainingSession.date.desc())
        .limit(shadow_validation_sessions)
    ).all()
    
    print("\n--- Shadow Validation Pass ---")
    if not recent_sessions:
        print("No recent sessions found for shadow validation.")
    
    shadow_errors = 0
    for sess in recent_sessions:
        try:
            resolved = resolve_envelope(
                planned_posture=seed_posture,
                body_comp_state=body_comp_value,
                recovery_status=recovery_status_val.value,
                deload_active=False
            )
            res_str = f"RPE Cap: {resolved.rpe_cap}, Vol Multiplier: {resolved.volume_multiplier}"
        except Exception as e:
            res_str = f"Error resolving: {e}"
            shadow_errors += 1
        
        val_entry = {
            "session_id": sess.id,
            "date": sess.date.isoformat(),
            "historical_phase": sess.phase,
            "simulated_envelope": res_str
        }
        plan["shadow_validation"].append(val_entry)
        print(f"Session {sess.id} ({sess.date}) - Historical Phase: {sess.phase} -> Simulated (Posture={seed_posture}): {res_str}")

    rotations = session.exec(select(MesoRotation)).all()
    
    if rotations:
        existing_mesos = list(set(r.meso_number for r in rotations))
        print("\n--- MesoRotation Backfill ---")
        print(f"Collapsing {len(rotations)} MesoRotation rows (original meso_numbers: {existing_mesos}) into the new single Mesocycle.")
        plan["meso_rotations"] = [r.id for r in rotations]
    
    if shadow_errors > 0:
        print(f"\nWARNING: Shadow validation encountered {shadow_errors} error(s). Please review the logs above.", file=sys.stderr)
        raise RuntimeError("Shadow validation errors occurred.")

    if apply:
        print("\n=== APPLYING CHANGES ===")
        macro = Macrocycle(goal=macrocycle_goal, planned_start_date=as_of)
        session.add(macro)
        session.flush()
        
        if not template:
            template = MesocycleTemplate(name=template_name, postures=["BUILD", "PUSH", "CONSOLIDATE", "DELOAD"])
            session.add(template)
            session.flush()
            
        meso = Mesocycle(
            template_id=template.id,
            macrocycle_id=macro.id,
            ordinal=current_mesocycle_ordinal,
            planned_start_date=as_of,
            planned_end_date=planned_end_date,
        )
        session.add(meso)
        session.flush()
        
        micro = Microcycle(
            mesocycle_id=meso.id,
            ordinal=current_microcycle_ordinal,
            planned_start_date=as_of,
            planned_end_date=micro_end_date,
            expected_sessions=expected_sessions,
            planned_posture=seed_posture
        )
        session.add(micro)
        
        bcs = BodyCompState(state=BodyCompStateValue(body_comp_value), effective_from=as_of)
        session.add(bcs)
            
        rs = RecoveryStatus(as_of_date=as_of, status=RecoveryStatusValue(recovery_status_val.value))
        session.add(rs)
        
        for rot in rotations:
            rot.mesocycle_id = meso.id
            session.add(rot)
            
        session.commit()
        print("Changes committed to database.")
    else:
        print("\n[DRY RUN] No changes were written to the database. Run with --apply to commit.")
        
    return plan


def main():
    parser = argparse.ArgumentParser(description="Migrate legacy Phase to Periodization model.")
    parser.add_argument("--apply", action="store_true", help="Apply the migration to the database")
    parser.add_argument("--calibration-maps-to", type=str, choices=["CUT", "MAINTENANCE", "GAIN"],
                        help="Explicit mapping for CALIBRATION phase")
    parser.add_argument("--rebuild-maps-to", type=str, choices=["CUT", "MAINTENANCE", "GAIN"],
                        help="Explicit mapping for REBUILD phase")
    parser.add_argument("--current-mesocycle-ordinal", type=int, required=True,
                        help="The ordinal of the athlete's current mesocycle")
    parser.add_argument("--current-microcycle-ordinal", type=int, required=True,
                        help="The ordinal of the athlete's current microcycle")
    parser.add_argument("--mesocycle-length-weeks", type=int, default=4,
                        help="The expected length of the new mesocycle in weeks")
    parser.add_argument("--seed-posture", type=str, default="BUILD", choices=["BUILD", "PUSH", "CONSOLIDATE", "DELOAD"],
                        help="The posture to use for the cutover validation and seeding (default BUILD)")
    
    args = parser.parse_args()
    
    try:
        with DbSession(engine) as session:
            migrate(
                session=session,
                apply=args.apply,
                current_mesocycle_ordinal=args.current_mesocycle_ordinal,
                current_microcycle_ordinal=args.current_microcycle_ordinal,
                mesocycle_length_weeks=args.mesocycle_length_weeks,
                seed_posture=args.seed_posture,
                
                calibration_maps_to=args.calibration_maps_to,
                rebuild_maps_to=args.rebuild_maps_to
            )
    except Exception as e:
        sys.exit(1)

if __name__ == "__main__":
    main()
