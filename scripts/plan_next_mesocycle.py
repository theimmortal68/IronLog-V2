import argparse
import sys
import json
from datetime import date, timedelta
from typing import Optional

from sqlmodel import Session, select

from ironlog.db import engine
from ironlog.models.periodization import (
    Macrocycle, Mesocycle, MesocycleTemplate, AdvancementLog,
    MacroPlanningState, PlanStatus
)
from ironlog.models.program import Program
from ironlog.engine.program_hash import compute_program_prescription_hash
from ironlog.engine.advancement import ensure_first_microcycle_instantiated


def _validate_template_cardinality(mesocycle: Mesocycle, template: MesocycleTemplate) -> None:
    expected_weeks = ((mesocycle.planned_end_date - mesocycle.planned_start_date).days + 1) // 7
    if expected_weeks != len(template.postures):
        raise ValueError(f"Template cardinality mismatch: dates imply {expected_weeks} weeks, but template has {len(template.postures)} postures.")

def main(argv: Optional[list[str]] = None, engine_override=None):
    parser = argparse.ArgumentParser(description="Plan next mesocycle")
    parser.add_argument("--macrocycle", type=int, required=True, help="Macrocycle ID")
    parser.add_argument("--template", type=int, required=True, help="MesocycleTemplate ID")
    parser.add_argument("--program", type=int, required=True, help="Program ID")
    parser.add_argument("--ordinal", type=int, default=None, help="Specific ordinal to use (optional)")
    parser.add_argument("--start-date", type=date.fromisoformat, default=None, help="Planned start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", type=date.fromisoformat, default=None, help="Planned end date (YYYY-MM-DD)")
    
    args = parser.parse_args(argv)
    
    engine_to_use = engine_override or engine

    with Session(engine_to_use) as db:
        with db.begin():
            macrocycle = db.exec(
                select(Macrocycle).where(Macrocycle.id == args.macrocycle)
            ).first()
            if not macrocycle:
                print(f"Error: Macrocycle {args.macrocycle} not found", file=sys.stderr)
                sys.exit(1)

            template = db.exec(
                select(MesocycleTemplate).where(MesocycleTemplate.id == args.template)
            ).first()
            if not template:
                print(f"Error: Template {args.template} not found", file=sys.stderr)
                sys.exit(1)

            program = db.exec(
                select(Program).where(Program.id == args.program)
            ).first()
            if not program:
                print(f"Error: Program {args.program} not found", file=sys.stderr)
                sys.exit(1)

            # Determine ordinal and predecessor
            if args.ordinal is not None:
                ordinal = args.ordinal
                predecessor = db.exec(
                    select(Mesocycle)
                    .where(Mesocycle.macrocycle_id == args.macrocycle)
                    .where(Mesocycle.ordinal == ordinal - 1)
                ).first()
            else:
                existing_mesos = db.exec(
                    select(Mesocycle)
                    .where(Mesocycle.macrocycle_id == args.macrocycle)
                    .order_by(Mesocycle.ordinal.desc())
                ).all()
                if existing_mesos:
                    predecessor = existing_mesos[0]
                    ordinal = (predecessor.ordinal or 0) + 1
                else:
                    predecessor = None
                    ordinal = 1
                    
            existing_successor = db.exec(
                select(Mesocycle)
                .where(Mesocycle.macrocycle_id == args.macrocycle)
                .where(Mesocycle.ordinal == ordinal)
            ).first()

            if existing_successor:
                successor = existing_successor
                print(f"Idempotent: Found existing Mesocycle {successor.id} (ordinal {successor.ordinal}).")
            else:
                # Determine dates
                if args.start_date:
                    start_date = args.start_date
                else:
                    if predecessor and predecessor.planned_end_date:
                        start_date = predecessor.planned_end_date + timedelta(days=1)
                    else:
                        start_date = date.today()
                
                if args.end_date:
                    end_date = args.end_date
                else:
                    end_date = start_date + timedelta(days=7 * len(template.postures) - 1)

                successor = Mesocycle(
                    macrocycle_id=macrocycle.id,
                    template_id=template.id,
                    program_id=program.id,
                    ordinal=ordinal,
                    planned_start_date=start_date,
                    planned_end_date=end_date,
                    status=PlanStatus.PLANNED,
                    program_prescription_hash=compute_program_prescription_hash(program)
                )

                # Validate cardinality
                try:
                    _validate_template_cardinality(successor, template)
                except ValueError as e:
                    print(f"Error: {e}", file=sys.stderr)
                    sys.exit(1)
                
                db.add(successor)
                db.flush()
                print(f"Success: Mesocycle {successor.id} (ordinal {successor.ordinal}) created.")

            # Handle instantiation
            instantiated = False
            if predecessor is None or predecessor.status == PlanStatus.COMPLETE:
                # instantiate (this function is idempotent itself)
                ensure_first_microcycle_instantiated(db, successor, reconcile_run_id=None)
                instantiated = True
            
            # Update macrocycle planning state
            if macrocycle.planning_state == MacroPlanningState.AWAITING_NEXT_MESOCYCLE:
                macrocycle.planning_state = MacroPlanningState.ACTIVE
                db.add(macrocycle)
                
                details = {
                    "successor_mesocycle_id": successor.id,
                    "successor_ordinal": successor.ordinal,
                    "microcycle_1_instantiated": instantiated
                }
                log = AdvancementLog(
                    entity_type="macrocycle",
                    entity_id=macrocycle.id,
                    reason="SUCCESSOR_PLANNED",
                    reconcile_run_id=None,
                    details_json=details
                )
                db.add(log)

            print(f"Microcycle 1 instantiated: {instantiated}")
            print(f"Macrocycle planning_state: {macrocycle.planning_state.value}")

if __name__ == "__main__":
    main()
