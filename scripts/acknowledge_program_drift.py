import argparse
import sys

from sqlmodel import Session, select
from ironlog.db import engine
from ironlog.models.periodization import Mesocycle, Microcycle, AdvancementLog, MicrocycleLifecycleStatus
from ironlog.models.program import Program
from ironlog.engine.program_hash import compute_program_prescription_hash, compute_slot_topology_hash

def main(args_list=None):
    parser = argparse.ArgumentParser(description="Acknowledge PROGRAM_DRIFT for a Mesocycle")
    parser.add_argument("--mesocycle", type=int, required=True, help="Mesocycle ID")
    parser.add_argument("--accept-current-program-revision", action="store_true", help="Acknowledge drift and overwrite prescription hash")
    args = parser.parse_args(args_list)

    with Session(engine) as db:
        mesocycle = db.get(Mesocycle, args.mesocycle)
        if not mesocycle:
            print(f"Error: Mesocycle {args.mesocycle} not found.", file=sys.stderr)
            sys.exit(1)
        
        if mesocycle.program_id is None:
            print(f"Error: Mesocycle {args.mesocycle} does not have an associated Program.", file=sys.stderr)
            sys.exit(1)
            
        program = db.get(Program, mesocycle.program_id)
        if not program:
            print(f"Error: Program {mesocycle.program_id} not found.", file=sys.stderr)
            sys.exit(1)

        current_hash = compute_program_prescription_hash(program)
        planned_hash = mesocycle.program_prescription_hash

        status = "MATCH" if current_hash == planned_hash else "MISMATCH"
        print(f"Prescription Status: {status}")
        print(f"Planned hash: {planned_hash}")
        print(f"Current hash: {current_hash}")

        if not args.accept_current_program_revision:
            sys.exit(0)

        stmt = select(Microcycle).where(
            Microcycle.mesocycle_id == mesocycle.id,
            Microcycle.lifecycle_status == MicrocycleLifecycleStatus.ACTIVE
        )
        active_microcycle = db.exec(stmt).first()

        if active_microcycle is not None:
            current_topology_hash = compute_slot_topology_hash(program)
            if current_topology_hash != active_microcycle.slot_topology_hash:
                print(
                    "REFUSE: Topology change under running training detected. "
                    "A day flipped TRAINING/REST, was added/removed, or reordered. "
                    "This requires an explicit interruption/replan path instead. "
                    "Call mark_microcycle_incomplete as the next step.",
                    file=sys.stderr
                )
                sys.exit(1)

        mesocycle.program_prescription_hash = current_hash
        log = AdvancementLog(
            entity_type="mesocycle",
            entity_id=mesocycle.id,
            reason="PROGRAM_DRIFT_ACKNOWLEDGED",
            reconcile_run_id=None,
            details_json={
                "old_hash": planned_hash,
                "new_hash": current_hash
            }
        )
        db.add(mesocycle)
        db.add(log)
        db.commit()

        print(f"Successfully acknowledged drift for Mesocycle {mesocycle.id}.")

if __name__ == "__main__":
    main()
