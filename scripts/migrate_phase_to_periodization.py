import argparse
import sys
from datetime import date
from typing import List, Optional, Tuple, Dict, Any

from sqlmodel import Session as DbSession, select

from ironlog.db import engine
from ironlog.models.library import EngineState, DailyReadiness
from ironlog.models.session import Session as TrainingSession
from ironlog.models.program import MesoRotation
from ironlog.models.periodization import (
    BodyCompState, BodyCompStateValue, RecoveryStatus, RecoveryStatusValue,
    Macrocycle, Mesocycle, Microcycle, PlanStatus, MicrocycleLifecycleStatus,
    MicrocycleDriftStatus
)
from ironlog.engine.readiness import (
    DailyReadinessInput, compute_sleep_ok, compute_subjective_ok
)
from ironlog.engine.periodization_resolver import resolve_envelope


def _compute_recovery_status(readiness_inputs: List[DailyReadinessInput], as_of: date) -> RecoveryStatusValue:
    """
    Computes initial RecoveryStatus from existing pipeline functions.
    If both sleep and subjective readiness are OK (or undefined due to lack of data), NORMAL.
    If one is explicitly failing, CAUTION.
    If both are failing, POOR.
    (Note: the underlying compute_* functions return False on sparse data, so we need to be careful.
    Actually, if they return False, it means they are failing the check.
    We'll treat False as a negative signal.)
    """
    # compute_sleep_ok and compute_subjective_ok return False if sparse data OR bad ratio.
    # To avoid defaulting to POOR when there's simply no data, let's check if we have data.
    recent = [r for r in readiness_inputs if (as_of - r.date).days <= 10]
    
    has_sleep = any(r.sleep_ok is not None for r in recent)
    has_subj = any(r.subjective_ok is not None for r in recent)

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
    current_microcycle_ordinal: int,
    calibration_maps_to: Optional[str] = None,
    rebuild_maps_to: Optional[str] = None,
    shadow_validation_sessions: int = 10,
    as_of: Optional[date] = None
) -> dict:
    if as_of is None:
        as_of = date.today()

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
    else:
        # Fallback if engine state is missing or unknown
        pass

    plan = {
        "body_comp_state": body_comp_value,
        "recovery_status": None,
        "meso_rotations": [],
        "shadow_validation": []
    }

    print("=== MIGRATION PLAN ===")
    if body_comp_value:
        print(f"Proposed BodyCompState: {body_comp_value} (from {current_phase})")
    else:
        print(f"Skipping BodyCompState creation (current_phase={current_phase}, not mapped)")

    # 2. Compute initial RecoveryStatus
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

    # 3. Seed Macrocycle/Mesocycle/Microcycle representing "now"
    print(f"Proposed Microcycle Ordinal: {current_microcycle_ordinal}")
    
    # 4. Shadow Validation Pass
    recent_sessions = session.exec(
        select(TrainingSession)
        .order_by(TrainingSession.date.desc())
        .limit(shadow_validation_sessions)
    ).all()
    
    print("\n--- Shadow Validation Pass ---")
    if not recent_sessions:
        print("No recent sessions found for shadow validation.")
    
    for sess in recent_sessions:
        if body_comp_value:
            # We assume a default planned_posture for the validation, or try to guess.
            # We'll use BUILD as a generic posture just to see the resolver output.
            # But wait, what posture to use? Let's use PUSH.
            test_posture = "PUSH"
            try:
                resolved = resolve_envelope(
                    planned_posture=test_posture,
                    body_comp_state=body_comp_value,
                    recovery_status=recovery_status_val.value,
                    deload_active=False
                )
                res_str = f"RPE Cap: {resolved.rpe_cap}, Vol Multiplier: {resolved.volume_multiplier}"
            except Exception as e:
                res_str = f"Error resolving: {e}"
            
            val_entry = {
                "session_id": sess.id,
                "date": sess.date,
                "historical_phase": sess.phase,
                "simulated_envelope": res_str
            }
            plan["shadow_validation"].append(val_entry)
            print(f"Session {sess.id} ({sess.date}) - Historical Phase: {sess.phase} -> Simulated (Posture={test_posture}): {res_str}")
        else:
            print(f"Session {sess.id} ({sess.date}) - Historical Phase: {sess.phase} -> Cannot simulate without BodyCompState")

    # 5. MesoRotation Backfill
    rotations = session.exec(select(MesoRotation)).all()
    
    if rotations:
        print("\n--- MesoRotation Backfill ---")
        # For simplicity, we just map them to the new mesocycle ID which we will create as 1 (or next val)
        plan["meso_rotations"] = [r.id for r in rotations]
        print(f"Will map {len(rotations)} MesoRotation rows to the new Mesocycle ID.")
    
    if apply:
        print("\n=== APPLYING CHANGES ===")
        # Insert Macrocycle, Mesocycle, Microcycle
        macro = Macrocycle(goal="Initial Cutover Goal", planned_start_date=as_of)
        session.add(macro)
        session.flush()
        
        # We need a MesocycleTemplate for the Mesocycle.
        # But wait, Mesocycle requires a template_id. We might need to create a dummy template if one doesn't exist?
        # Let's just create a dummy one for the cutover, or wait, is there an existing one?
        # The design says Mesocycle needs template_id. Let's create one.
        from ironlog.models.periodization import MesocycleTemplate
        template = session.exec(select(MesocycleTemplate)).first()
        if not template:
            template = MesocycleTemplate(name="Cutover Template", postures=["BUILD", "PUSH", "CONSOLIDATE", "DELOAD"])
            session.add(template)
            session.flush()
            
        meso = Mesocycle(
            template_id=template.id,
            macrocycle_id=macro.id,
            ordinal=1,
            planned_start_date=as_of,
            planned_end_date=as_of,
        )
        session.add(meso)
        session.flush()
        
        micro = Microcycle(
            mesocycle_id=meso.id,
            ordinal=current_microcycle_ordinal,
            planned_start_date=as_of,
            planned_end_date=as_of,
            expected_sessions=4,
            planned_posture="BUILD"
        )
        session.add(micro)
        
        if body_comp_value:
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
    parser.add_argument("--current-microcycle-ordinal", type=int, required=True,
                        help="The ordinal of the athlete's current microcycle")
    
    args = parser.parse_args()
    
    try:
        with DbSession(engine) as session:
            migrate(
                session=session,
                apply=args.apply,
                current_microcycle_ordinal=args.current_microcycle_ordinal,
                calibration_maps_to=args.calibration_maps_to,
                rebuild_maps_to=args.rebuild_maps_to
            )
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
