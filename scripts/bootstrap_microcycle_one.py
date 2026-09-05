import argparse
import sys
from datetime import datetime, timedelta

from sqlalchemy.engine import Engine
from sqlmodel import Session as SQLModelSession, select

from ironlog.db import engine as default_engine
from ironlog.models.periodization import (
    Microcycle, Mesocycle, MicrocycleSlot, MicrocycleSlotType,
    MicrocycleSlotResolution, MicrocycleSlotResolutionSource,
    MicrocycleLifecycleStatus
)
from ironlog.models.program import Program, ProgramDay
from ironlog.models.session import Session as IronSession
from ironlog.models.enums import SessionPlanStatus, SessionStatus
from ironlog.engine.program_hash import compute_slot_topology_hash

def run_bootstrap(engine: Engine, is_dry_run: bool):
    with SQLModelSession(engine) as session:
        try:
            # 1. Find Microcycle #1
            statement = select(Microcycle, Mesocycle).join(
                Mesocycle, Microcycle.mesocycle_id == Mesocycle.id
            ).where(
                Microcycle.lifecycle_status == MicrocycleLifecycleStatus.ACTIVE,
                Microcycle.ordinal == 1,
                Mesocycle.ordinal == 1
            )
            results = session.exec(statement).all()
            
            if not results:
                print("Error: Could not find Microcycle #1 (ACTIVE, ordinal=1, mesocycle ordinal=1).")
                sys.exit(1)
            if len(results) > 1:
                print("Error: Found multiple matching Microcycles. Expected exactly one.")
                sys.exit(1)
                
            micro, meso = results[0]
            
            # 2. Load Program
            if meso.program_id is None:
                print("Error: Mesocycle has no bound Program, cannot proceed.")
                sys.exit(1)
                
            program = session.exec(select(Program).where(Program.id == meso.program_id)).one_or_none()
            if not program:
                print(f"Error: Program {meso.program_id} not found.")
                sys.exit(1)
                
            # Check if slots already exist
            existing_slots = session.exec(
                select(MicrocycleSlot).where(MicrocycleSlot.microcycle_id == micro.id)
            ).all()
            if existing_slots:
                print(f"Error: Microcycle already has {len(existing_slots)} slots -- refusing to duplicate, this bootstrap is meant to run against a zero-slot Microcycle only.")
                sys.exit(1)
                
            # 3. Query ProgramDay
            program_days = session.exec(
                select(ProgramDay).where(ProgramDay.program_id == program.id).order_by(ProgramDay.day_index)
            ).all()
            
            created_slots = []
            for position, pd in enumerate(program_days, start=1):
                planned_date = micro.planned_start_date + timedelta(days=position - 1)
                slot_type = MicrocycleSlotType.REST if pd.is_rest else MicrocycleSlotType.TRAINING
                resolution = MicrocycleSlotResolution.NOT_APPLICABLE if pd.is_rest else MicrocycleSlotResolution.PENDING
                
                slot = MicrocycleSlot(
                    microcycle_id=micro.id,
                    ordinal=position,
                    day_code=f"D{position}",
                    day_label=pd.day_role,
                    planned_date=planned_date,
                    slot_type=slot_type,
                    resolution=resolution
                )
                session.add(slot)
                created_slots.append(slot)
            
            slot_by_label = {s.day_label: s for s in created_slots}
            
            # 4. Compute slot_topology_hash
            topo_hash = compute_slot_topology_hash(program)
            micro.slot_topology_hash = topo_hash
            session.add(micro)
            
            # 5. Query all Sessions
            all_sessions = session.exec(select(IronSession)).all()
            sessions_bound = 0
            sessions_resolved_completed = 0
            
            for s in all_sessions:
                if not s.prescription_snapshot:
                    continue
                if not isinstance(s.prescription_snapshot, dict):
                    continue
                if s.prescription_snapshot.get('microcycle_id') != micro.id:
                    continue
                    
                # a. match slot
                slot = slot_by_label.get(s.day_role)
                if not slot:
                    print(f"Error: Session {s.id} with day_role '{s.day_role}' cannot be mapped to any slot in Microcycle #1.")
                    session.rollback()
                    sys.exit(1)
                
                # b. Set plan_status, microcycle_id
                s.plan_status = SessionPlanStatus.PLANNED
                s.microcycle_id = micro.id
                
                # c. d.
                slot.session_id = s.id
                if s.status == SessionStatus.COMPLETED:
                    slot.resolution = MicrocycleSlotResolution.COMPLETED
                    slot.resolution_source = MicrocycleSlotResolutionSource.SESSION
                    slot.resolved_at = s.approved_at or datetime.utcnow()
                    sessions_resolved_completed += 1
                
                session.add(s)
                sessions_bound += 1

            session.flush()

            # 7. Verify
            training_slots = [s for s in created_slots if s.slot_type == MicrocycleSlotType.TRAINING]
            if not training_slots:
                print("Error: Microcycle has zero TRAINING slots. Halting.")
                session.rollback()
                sys.exit(1)
            
            # 8. Summary
            print("--- Bootstrap Summary ---")
            print(f"Slots created: {len(created_slots)} ({len(training_slots)} TRAINING, {len(created_slots) - len(training_slots)} REST)")
            print(f"Sessions bound: {sessions_bound}")
            print(f"  - Resolved COMPLETED: {sessions_resolved_completed}")
            print(f"  - Left PENDING: {sessions_bound - sessions_resolved_completed}")
            print(f"Computed slot_topology_hash: {topo_hash}")
            
            if is_dry_run:
                print("\nDRY RUN complete. Rolling back transaction.")
                session.rollback()
            else:
                print("\nAPPLY complete. Committing transaction.")
                session.commit()
                
        except Exception as e:
            session.rollback()
            raise e

def main():
    parser = argparse.ArgumentParser(description="Bootstrap Microcycle #1")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="Print plan, do not modify DB.")
    group.add_argument("--apply", action="store_true", help="Apply changes to DB.")
    
    args = parser.parse_args()
    
    run_bootstrap(default_engine, args.dry_run)

if __name__ == "__main__":
    main()
