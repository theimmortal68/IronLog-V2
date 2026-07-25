"""build_hgc_condensed_week.py — one-off idempotent script for Spec 51."""
import argparse
from datetime import date

from sqlmodel import Session, select
from sqlalchemy.orm.attributes import flag_modified

from ironlog.models.session import (
    Session as LogSession, ExerciseGroup, PlannedExercise, PlannedSet
)
from ironlog.models.enums import SessionStatus, GroupType
from ironlog.models.program import ProgramDay
from ironlog.models.library import Movement, EngineState
from ironlog.generation.loop import generate_session
from ironlog.api.app import _make_proposer, _week_keyer
from ironlog.generation.skeleton import lay_skeleton

RATIONALE = "HGC condensed week"

MINI_SESSIONS = [
    (date(2026, 7, 27), "D1 Upper Push", ["Bench Press [PB]", "Pendlay Row - Narrow [OB]", "Face-Up Incline Knee Raise"]),
    (date(2026, 7, 27), "D2 Lower A", ["Belt Squat [GHR + FT]", "ATG Split Squat", "Cable Tibialis Raise"]),
    (date(2026, 7, 27), "D6 Weak Points", ["Face Pull [FT]"]),
    (date(2026, 7, 28), "D5 Lower B", ["RDL [PB]", "Hip Thrust [HIP_THRUST]", "Reverse Nordic Curl [GHR]"]),
    (date(2026, 7, 28), "D2 Lower A", ["Leg Curl [GHR]"]),
    (date(2026, 7, 28), "D6 Weak Points", ["DB Seal Row [DB + UTIL_SEAT]", "Lateral Raise [FT]"]),
    (date(2026, 7, 28), "D1 Upper Push", ["Ab Wheel [WHEEL]"]),
    (date(2026, 7, 29), "D4 Upper Pull", ["Standing OHP [PB]", "Pull-up [TOWER + TUBES]", "Dragon Flag"]),
    (date(2026, 7, 29), "D6 Weak Points", ["Dips [ANDREONI + FT]", "T-Bar Row - Wide [OB + KLEVA + LM]", "Cable V-Bar Pushdown [FT]"]),
    (date(2026, 7, 29), "D1 Upper Push", ["Incline DB Press [DB + BENCH]"]),
    (date(2026, 7, 29), "D5 Lower B", ["Nordic Curl [GHR]"]),
]

def apply(db: Session, dry_run: bool = False) -> None:
    # Get current phase
    engine_state = db.exec(select(EngineState)).first()
    if not engine_state:
        raise ValueError("EngineState not found.")
    phase = engine_state.current_phase.value

    recent_sessions = db.exec(
        select(LogSession).where(
            LogSession.status == SessionStatus.PLANNED,
            LogSession.analyzed_at.is_(None)
        ).order_by(LogSession.id.desc())
    ).all()

    real_in_progress = [s for s in recent_sessions if not (s.rationale and s.rationale.startswith(RATIONALE))]
    if real_in_progress:
        print(f"HALT-AND-FLAG: found {len(real_in_progress)} existing non-HGC PLANNED+unanalyzed sessions.")
        for s in real_in_progress:
            print(f"  Session ID {s.id} on {s.date} ({s.day_role})")
        raise RuntimeError("Refusing to insert HGC sessions that would supersede a real in-progress session.")

    origin_day_roles = list(set(day_role for _, day_role, _ in MINI_SESSIONS))
    generated_sources = {}
    for day_role in origin_day_roles:
        print(f"Generating source for {day_role}...")
        sk = lay_skeleton(day_role, db)
        proposer = _make_proposer(sk)
        outcome = generate_session(day_role, db, proposer, _week_keyer)
        assert outcome.assembled is not None, f"{day_role}: generation exhausted (rejections: {outcome.rejections})"
        generated_sources[day_role] = outcome.assembled.session

    for idx, (sess_date, day_role, m_names) in enumerate(MINI_SESSIONS, 1):
        rationale_str = f"{RATIONALE} — mini-session {idx}/{len(MINI_SESSIONS)}"
        
        existing = db.exec(
            select(LogSession).where(LogSession.rationale == rationale_str)
        ).first()
        if existing:
            print(f"[{idx}/{len(MINI_SESSIONS)}] already exists, skipping.")
            continue

        program_day = db.exec(select(ProgramDay).where(ProgramDay.day_role == day_role)).one()
        
        if dry_run:
            print(f"[{idx}/{len(MINI_SESSIONS)}] DRY-RUN: would create session for {day_role} on {sess_date}")
            continue

        session = LogSession(
            date=sess_date,
            day_role=day_role,
            phase=phase,
            status=SessionStatus.PLANNED,
            signature={"program_day_id": program_day.id},
            rationale=rationale_str,
        )
        db.add(session)
        db.flush()

        group = ExerciseGroup(
            session_id=session.id,
            order_index=1,
            group_type=GroupType.STRAIGHT,
            rounds=1,
            rest_seconds=120,
        )
        db.add(group)
        db.flush()

        source_session = generated_sources[day_role]
        
        for p_idx, m_name in enumerate(m_names, 1):
            m = db.exec(select(Movement).where(Movement.name == m_name)).first()
            if not m:
                fuzzies = [xm.name for xm in db.exec(select(Movement)).all() if m_name.split('[')[0].strip() in xm.name or xm.base_name == m_name.split('[')[0].strip()]
                raise ValueError(f"Movement '{m_name}' not found. Did you mean: {fuzzies}?")
            
            source_ex = None
            for g in source_session.groups:
                for ex in g.exercises:
                    if ex.movement_id == m.id:
                        source_ex = ex
                        break
                if source_ex:
                    break
            
            if not source_ex:
                present_names = []
                for g in source_session.groups:
                    for ex in g.exercises:
                        mex = db.exec(select(Movement).where(Movement.id == ex.movement_id)).first()
                        if mex:
                            present_names.append(mex.name)
                raise ValueError(f"Movement '{m_name}' not found in generated source for {day_role}. Present: {present_names}")

            new_ex = PlannedExercise(
                group_id=group.id,
                movement_id=m.id,
                order_index=p_idx,
                scheme=source_ex.scheme,
                objective=source_ex.objective,
            )
            db.add(new_ex)
            db.flush()

            for pset in source_ex.planned_sets:
                new_pset = PlannedSet(
                    planned_exercise_id=new_ex.id,
                    set_index=pset.set_index,
                    set_role=pset.set_role,
                    is_warmup=pset.is_warmup,
                    target_load=pset.target_load,
                    target_reps_low=pset.target_reps_low,
                    target_reps_high=pset.target_reps_high,
                    target_rpe=pset.target_rpe,
                    target_unassisted_reps=pset.target_unassisted_reps,
                    target_assisted_reps=pset.target_assisted_reps,
                    target_plates=pset.target_plates,
                    band_pair_id=pset.band_pair_id,
                    target_felt_peak=pset.target_felt_peak,
                    band_config=pset.band_config,
                )
                db.add(new_pset)

        print(f"[{idx}/{len(MINI_SESSIONS)}] created session {session.id} for {day_role} on {sess_date} ({len(m_names)} exercises).")

    if not dry_run:
        db.commit()
        print("Done. Inserted HGC sessions.")

def main() -> None:
    parser = argparse.ArgumentParser(description="HGC condensed week script.")
    parser.add_argument("--dry-run", action="store_true", help="Print plan without writing to DB")
    args = parser.parse_args()

    from ironlog.db import engine
    with Session(engine) as db:
        apply(db, dry_run=args.dry_run)

if __name__ == "__main__":
    main()
