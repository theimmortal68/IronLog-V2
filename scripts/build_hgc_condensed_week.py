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

# 2026-08-10 (STAB maintenance-block redesign): this script re-derives each
# mini-session's PlannedExercise/PlannedSet rows from a FRESH generate_session
# call against the live program (see `apply()` below), so the movement names
# here must resolve in whatever D1 (etc.) currently looks like -- they are
# not a frozen historical record. D1's T2 GS turned over entirely (Lying
# Tricep Extension / Incline DB Press / Face-Up Incline Knee Raise all
# dropped), so the two D1 entries below are repointed to movements that
# actually exist in D1's current wiring: "Face-Up Incline Knee Raise" ->
# "Stryker Pad Seated OHP [DB]", "Incline DB Press [DB + BENCH]" ->
# "Matrix Machine Preacher Curl [EZ]". Dates/rationale/ordering are
# historical bookkeeping and are left unchanged.
#
# 2026-08-11 (STAB maintenance-block redesign, Task 2): D2's T2 GS also
# turned over entirely (Lying Leg Curl [GHR] / Scout Reverse Hyper both
# dropped, T1b Hip Thrust tier removed entirely), so the single-movement
# 7/28 D2 entry below is repointed to "Nordic Curl Max [Ares]" -- the new
# T2 GS movement closest in role to the dropped Lying Leg Curl (hamstring-
# focused accessory), and not already referenced by the 7/27 D2 entry above.
#
# 2026-08-11 (STAB maintenance-block redesign, Task 3): D4's T1/T1b also
# turned over (Standing OHP [PB] -> Seated BTN OHP [PB], Wide-Grip Pull-up
# [TOWER] -> Better Fly Lat Pulldown [FT]), so the 7/29 D4 entry below is
# repointed to the movements that actually exist in D4's current wiring --
# same anchor roles (T1/T1b), closest-role replacements. PureTorque Pro
# Rotation (T3) is unchanged, already IS D4's real T3 wiring.
#
# 2026-08-12 (STAB maintenance-block redesign, Task 4): D5's T1/T1b/T2 GS
# fully turned over (RDL [PB] -> Kickstand RDL [DB]; Hip Thrust [HIP_THRUST]
# tier removed entirely, no replacement -- repointed to Better Fly Kickback
# [FT], the closest-role T2 GS accessory not already referenced elsewhere in
# this same entry; Nordic Curl [GHR] fully unwired program-wide, its old
# 7/29 single-movement entry repointed to Nordic Max Bulgarian Split Squat).
# Reverse Nordic Curl [GHR] (d5_t3b) is unchanged, already IS D5's real T3
# wiring. D2's 7/27 entry's "Cable Tibialis Raise" -> "Hybrid Board Tib
# Raise [D2]" (small standalone D2 fix bundled into this task, Cable Tib
# Raise replaced program-wide).
#
# 2026-08-12 (STAB maintenance-block redesign, Task 5): D6's T1 tier
# (Dips) eliminated -- Dips folds into GS1, same movement name ("Dips
# [TOWER + TUBES]"), still resolves unchanged in the 7/29 entry below
# despite the underlying progression config reverting to cable-loaded (see
# ironlog/seed.py). GS2 fully turned over (DB Seal Row / Lateral Raise
# dropped) -- 7/28 entry repointed to "Stryker Pad CSR Cables [FT]"
# (closest-role replacement for DB Seal Row, both horizontal_pull) and
# "Better Fly Rear Delt Extension [FT]" (closest-role replacement for
# Lateral Raise, both shoulder isolation). GS3's Cable V-Bar Pushdown and
# T-Bar Row - Wide dropped -- 7/29 entry repointed to "Better Fly OH
# Tricep Extension [FT]" (closest-role replacement for Cable V-Bar
# Pushdown, both tricep isolation) and "AbMat Ab Bench Pad Cable Crunch
# [FT]" (T-Bar Row - Wide's role has no direct GS3 successor; this is the
# one remaining new D6 movement not already referenced elsewhere in this
# entry, same fallback pattern as Task 4's Better Fly Kickback pick
# above). Face Pull [FT] (7/27 entry) is unchanged, already IS D6's real
# GS3 wiring (only its rep range changed, which this script doesn't carry).
#
# 2026-08-14: D5's T2 GS turned over again -- Nordic Max Bulgarian Split
# Squat dropped (Nordic Max rig conflict with Nordic Curl Max in the same
# giant set, athlete directive), so the 7/29 D5 entry below is repointed to
# "Matrix Machine Bulgarian Split Squat" (its direct replacement, same
# role/slot).
MINI_SESSIONS = [
    (date(2026, 7, 27), "D1 Upper Push", ["Bench Press [PB]", "Pendlay Row - Narrow [OB]", "Stryker Pad Seated OHP [DB]"]),
    # 2026-08-19: briefly repointed "Hybrid Board Tib Raise [D2]" ->
    # "Hybrid Board Calf Raise [D2]" when Tib Raise traded out of T3 GS
    # into T2 GS (deconflicting bench-attachment contention with Ab Trainer
    # Decline Sit-up). Revised same day: Tib Raise's shoe requirement
    # (flat, for ankle dorsiflexion) conflicted with T2's heeled shoe, so
    # Calf Raise (less shoe-sensitive) traded into T2 instead and Tib Raise
    # moved back to T3 GS. Reverted to "Hybrid Board Tib Raise [D2]" here to
    # match -- it's ATG Split Squat's current T3 GS co-member again,
    # preserving this entry's 2-member shared-giant-set clustering premise
    # (see test_hgc_condensed_week.py).
    (date(2026, 7, 27), "D2 Lower A", ["Belt Squat [GHR + FT]", "ATG Split Squat", "Hybrid Board Tib Raise [D2]"]),
    (date(2026, 7, 27), "D6 Weak Points", ["Face Pull [FT]"]),
    (date(2026, 7, 28), "D5 Lower B", ["Kickstand RDL [DB]", "Better Fly Kickback [FT]", "Reverse Nordic Curl [GHR]"]),
    # 2026-08-20: repointed from "Nordic Curl Max [Ares]" -- that slot
    # (d2_t2e) now rotates A/B via WeekParityRotation (Apex angle / Ares
    # flat+band), so which movement `lay_skeleton(day_role, db)` resolves
    # here (called with no `as_of`, defaulting to real date.today()) is no
    # longer stable across test runs. This entry only needs ANY real,
    # non-rotating D2 movement to exercise the single-movement-mini-session
    # path -- Matrix Machine Sissy Squat (T2 GS, not part of the rotation)
    # is not already referenced by the 7/27 D2 entry above, matching the
    # original selection rule.
    (date(2026, 7, 28), "D2 Lower A", ["Matrix Machine Sissy Squat"]),
    (date(2026, 7, 28), "D6 Weak Points", ["Stryker Pad CSR Cables [FT]", "Better Fly Rear Delt Extension [FT]"]),
    (date(2026, 7, 28), "D1 Upper Push", ["Ab Wheel [WHEEL]"]),
    (date(2026, 7, 29), "D4 Upper Pull", ["Seated BTN OHP [PB]", "Better Fly Lat Pulldown [FT]", "PureTorque Pro Rotation"]),
    (date(2026, 7, 29), "D6 Weak Points", ["Dips [TOWER + TUBES]", "Better Fly OH Tricep Extension [FT]", "AbMat Ab Bench Pad Cable Crunch [FT]"]),
    (date(2026, 7, 29), "D1 Upper Push", ["Matrix Machine Preacher Curl [EZ]"]),
    (date(2026, 7, 29), "D5 Lower B", ["Matrix Machine Bulgarian Split Squat"]),
]


def _copy_planned_exercise(
    db: Session,
    group: ExerciseGroup,
    movement: Movement,
    source_ex: PlannedExercise,
    order_index: int,
) -> None:
    new_ex = PlannedExercise(
        group_id=group.id,
        movement_id=movement.id,
        order_index=order_index,
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


def _is_first_occurrence_of_day_role(mini_sessions, idx0):
    """idx0: 0-based index into mini_sessions. True iff no EARLIER entry
    (any date) shares this entry's day_role."""
    this_role = mini_sessions[idx0][1]
    return not any(
        other_role == this_role
        for _, other_role, _ in mini_sessions[:idx0]
    )


def _is_first_for_date(mini_sessions, idx0):
    """idx0: 0-based index into mini_sessions. True iff no earlier entry shares this entry's date."""
    this_date = mini_sessions[idx0][0]
    return not any(
        other_date == this_date
        for other_date, _, _ in mini_sessions[:idx0]
    )


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
            signature={
                "program_day_id": program_day.id,
                "show_finisher": _is_first_occurrence_of_day_role(MINI_SESSIONS, idx - 1),
                "show_warmup": _is_first_for_date(MINI_SESSIONS, idx - 1),
            },
            rationale=rationale_str,
        )
        db.add(session)
        db.flush()

        source_session = generated_sources[day_role]

        selected = []
        for p_idx, m_name in enumerate(m_names, 1):
            m = db.exec(select(Movement).where(Movement.name == m_name)).first()
            if not m:
                fuzzies = [xm.name for xm in db.exec(select(Movement)).all() if m_name.split('[')[0].strip() in xm.name or xm.base_name == m_name.split('[')[0].strip()]
                raise ValueError(f"Movement '{m_name}' not found. Did you mean: {fuzzies}?")
            
            source_ex = None
            source_group = None
            for g in sorted(source_session.groups, key=lambda group: group.order_index):
                for ex in sorted(g.exercises, key=lambda exercise: exercise.order_index):
                    if ex.movement_id == m.id:
                        source_ex = ex
                        source_group = g
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

            selected.append(
                {
                    "movement": m,
                    "source_ex": source_ex,
                    "source_group": source_group,
                    "selected_order": p_idx,
                }
            )

        clusters = {}
        cluster_order = []
        for item in selected:
            cluster_key = id(item["source_group"])
            if cluster_key not in clusters:
                clusters[cluster_key] = {
                    "source_group": item["source_group"],
                    "items": [],
                }
                cluster_order.append(cluster_key)
            clusters[cluster_key]["items"].append(item)

        group_order = 1
        for cluster_key in cluster_order:
            cluster = clusters[cluster_key]
            source_group = cluster["source_group"]
            items = cluster["items"]

            if len(items) >= 2 and source_group.group_type == GroupType.GIANT_SET:
                group = ExerciseGroup(
                    session_id=session.id,
                    order_index=group_order,
                    group_type=GroupType.GIANT_SET,
                    rounds=source_group.rounds,
                    rest_seconds=source_group.rest_seconds,
                )
                db.add(group)
                db.flush()
                for ex_order, item in enumerate(
                    sorted(items, key=lambda x: x["source_ex"].order_index),
                    1,
                ):
                    _copy_planned_exercise(
                        db,
                        group,
                        item["movement"],
                        item["source_ex"],
                        ex_order,
                    )
                group_order += 1
                continue

            for item in sorted(items, key=lambda x: x["selected_order"]):
                rest_seconds = source_group.rest_seconds
                if rest_seconds is None:
                    rest_seconds = 120
                group = ExerciseGroup(
                    session_id=session.id,
                    order_index=group_order,
                    group_type=GroupType.STRAIGHT,
                    rounds=1,
                    rest_seconds=rest_seconds,
                )
                db.add(group)
                db.flush()
                _copy_planned_exercise(
                    db,
                    group,
                    item["movement"],
                    item["source_ex"],
                    1,
                )
                group_order += 1

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
