#!/usr/bin/env python3
"""golive_phase1.py — reseed program + calibrated baselines + reset, with --verify.

Fresh DB: seed the Phase-1 program (seed_phase1_program) then the calibrated
MovementState baselines (seed_movement_baselines). Library seeding
(ironlog.seed.seed()) is a SEPARATE prior step at the ops layer — NOT called
here (golive() assumes the library is already seeded, e.g. via `python -m
ironlog.seed`).

Existing DB: pass --reset to wipe transactional/derived state first
(reset_transactional_and_state) WITHOUT touching the seeded calibrated
baselines, before reseeding program + baselines.

--verify generates D1/D2/D4/D5/D6 through the REAL generate_session path (the
same §3A conditional gate / assembler / validator used in production) and
reports, per day, how many exercise slots came up loaded vs which movements
still need calibration. Entirely read-only: no commits, no writes.

DESTRUCTIVE — pre-launch only. NO from __future__ import annotations
(project-wide constraint).
"""
import argparse

from sqlmodel import Session, select

from ironlog.api.app import _make_proposer, _week_keyer
from ironlog.db import engine
from ironlog.generation.baseline_seed import (
    reset_transactional_and_state, seed_movement_baselines,
)
from ironlog.generation.load_trust import load_field_for_mode
from ironlog.generation.loop import generate_session
from ironlog.generation.program_seed import seed_phase1_program
from ironlog.generation.skeleton import lay_skeleton
from ironlog.models.library import Movement

TRAINING_DAYS = ["D1 Upper Push", "D2 Lower A", "D4 Upper Pull", "D5 Lower B", "D6 Weak Points"]

# Movements that are structurally load-bearing (progression_mode != bodyweight)
# but are deliberately NOT pre-seeded with a scalar current_load/assist_level
# baseline, per the design doc and Task 4's own brief ("Bodyweight/rolling
# slots ... d1_t3a, d4_t1, d6_g1a" = the three Pull-up slots across D1/D4/D6):
# docs/superpowers/specs/2026-07-04-config-seed-reconciliation-design.md calls
# it out explicitly — "Pull-up(rolling-max)" on D1/D4, "Pull-up(rolling-max,
# red-band assist, Wk1 Set1 max 7 PR)" on D6. Pull-up's real starting point is
# measured in-gym via the "Set 1 unassisted max test" slot (d6_g1a) and tracked
# via MovementState.unassisted_max_rolling (run_analysis / roll_unassisted_max),
# NOT a pre-launch guess. Fabricating an assist_level number here would plant
# fake athlete data instead of an honest day-1 measurement, so verify treats
# this movement the same way it already treats bodyweight movements: exempt
# from the needs-calibration signal, not silently "fixed" with an invented load.
ROLLING_MAX_EXEMPT = {"Pull-up [TOWER + TUBES]"}


def verify_all_days(db):
    """Generate every training day via the real generate_session path and report
    loaded-slot counts + needs-calibration movements. READ-ONLY (no writes/commits) —
    generate_session only returns an in-memory RepairOutcome/AssembledSession;
    nothing is persisted unless commit_session is called separately, which this
    never does.

    "Loaded" vs "needs-calibration" mirrors exactly how the assembler
    (ironlog/generation/assembler.py:_build_exercise) actually represents state
    on the assembled PlannedExercise/PlannedSet, NOT a dedicated boolean flag
    (no such attribute exists on the models):
      - A movement is loaded if ANY of its planned sets carries a real
        target_load (scalar current_load / assist_level path) OR a real
        target_plates (HT band-composite path, which always leaves
        target_load=None even when fully calibrated).
      - A movement needs calibration if it carries NEITHER target_load nor
        target_plates on any set AND it is not legitimately bodyweight
        (load_field_for_mode(progression_mode) is None for PROTOCOL/
        CONDITIONING/NONE movements — those are correctly loadless and must
        NOT be flagged) AND it is not a rolling-max-calibrated movement
        (ROLLING_MAX_EXEMPT — see comment above).
    """
    report = {}
    movements_by_id = {m.id: m for m in db.exec(select(Movement)).all()}
    for role in TRAINING_DAYS:
        sk = lay_skeleton(role, db)
        proposer = _make_proposer(sk)
        outcome = generate_session(role, db, proposer, _week_keyer)
        if outcome.assembled is None:
            raise RuntimeError(
                f"{role}: generation exhausted with no assembled session "
                f"(rejections: {outcome.rejections})"
            )
        sess = outcome.assembled.session
        loaded = 0
        needs_cal = []
        for g in sess.groups:
            for ex in g.exercises:
                movement = movements_by_id[ex.movement_id]
                if load_field_for_mode(movement.progression_mode) is None:
                    continue  # bodyweight: legitimately loadless, never needs-cal
                if movement.name in ROLLING_MAX_EXEMPT:
                    continue  # calibrates in-gym via unassisted_max_rolling, not a pre-seed
                has_load = any(
                    ps.target_load is not None or ps.target_plates is not None
                    for ps in ex.planned_sets
                )
                if has_load:
                    loaded += 1
                else:
                    needs_cal.append(movement.name)
        report[role] = {"loaded_slots": loaded, "needs_cal": needs_cal}
    return report


def golive(db, reset=False):
    """Fresh DB (reset=False): seed_phase1_program -> seed_movement_baselines.
    Existing DB (reset=True): reset_transactional_and_state first, then the same
    reseed. Assumes the movement library is already seeded (separate ops-layer step,
    see module docstring) — does NOT call ironlog.seed.seed()."""
    if reset:
        reset_transactional_and_state(db)
    seed_phase1_program(db)
    seed_movement_baselines(db)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reset", action="store_true",
                    help="wipe transactional/derived state first (existing DB); "
                         "keeps seeded calibrated baselines")
    ap.add_argument("--verify", action="store_true",
                    help="generate + check all training days, read-only, no writes")
    args = ap.parse_args()
    with Session(engine) as db:
        if args.verify:
            import json
            report = verify_all_days(db)
            print(json.dumps(report, indent=2))
            failed = [role for role, r in report.items() if r["needs_cal"] or r["loaded_slots"] <= 0]
            if failed:
                raise SystemExit(f"verify FAILED for: {', '.join(failed)}")
            print("verify OK — all training days loaded and calibrated")
            return
        golive(db, reset=args.reset)
        print("go-live seed complete" + (" (with reset)" if args.reset else ""))


if __name__ == "__main__":
    main()
