"""baseline_seed.py — seed MovementState calibrated baselines for the go-live.

Keyed on (movement_id, day_id=day_role). Sets scalar current_load / assist_level,
or HT ht_plates + ht_band_config = [orange band id]. Idempotent upsert on
(movement_id, day_id). NO from __future__ import annotations.
"""
from typing import Dict, Optional

from sqlmodel import Session, delete, select

from ironlog.models.enums import CalibrationStatus, Phase
from ironlog.models.library import (
    BandPair, E1rmHistory, EngineState, GenerationLog, MovementState,
)
from ironlog.models.program import ProgramDay, Tier, TierExercise
from ironlog.models.session import (
    ExerciseGroup, ExerciseSurvey, Note, PlannedExercise, PlannedSet,
    Session as WorkoutSession, SetLog,
)

# slot_id -> ("load"|"assist"|"ht", value, band_label_or_None)
#
# d2_t2a (Lying Leg Curl [GHR], renamed 2026-07-26 from "Leg Curl [GHR]") has
# NO entry -- intentionally needs-calibration at go-live (2026-07-22
# Nordic Curl -> Leg Curl swap), not a completeness gap.
#
# d6_t1 (Dips, renamed 2026-07-26 from cable-loaded to bodyweight+band
# assist, moved from d6_g1b to its own T1 slot) seeds assist_level=40 from
# docs/program/phase1-seed-source.yaml. The stale d6_g1b cable-load baseline
# stays removed. d6_g1d (Cable Bicep Curl, new movement filling Dips'
# vacated GS1 slot) has NO entry -- intentionally needs-calibration, zero
# prior history.
#
# d1_t3a (Pull-up [TOWER + TUBES]) seeded assist_level=60 as of 2026-07-26
# (3 stacked 20lb bands, athlete directive). SUPERSEDED 2026-08-10: d1_t3a
# is now Wide-Grip Pull-up [TOWER] (unassisted dead-hang,
# PULL_UP_ROLLING_MAX) -- the assist baseline is removed, not carried
# forward, matching every other Wide-Grip Pull-up slot in the program.
#
# d1_t2a (Pendlay Row Narrow) keeps its slot_id and baseline unchanged even
# though it moved tiers 2026-07-26 (T2 GS -> its own T1b) -- the movement's
# stable slot_id, not the tier label, is what this table keys on. d1_t2d
# (Lying Tricep Extension, new movement filling the vacated T2 GS slot) has
# NO entry -- intentionally needs-calibration, zero prior history.
#
# d4_t2c (Face-Up Incline Knee Raise) is seeded "assist" (degrees), not
# "load": the movement is bodyweight/incline (progression_mode=ASSISTED,
# ironlog/seed.py), so resolve_start_load reads assist_level, not current_load.
# The design doc's "Face-Up-Knee 25°"/"10°" values
# (docs/superpowers/specs/2026-07-04-config-seed-reconciliation-design.md) are
# the incline angle, tracked on assist_level like any other assisted movement
# (Nordic Curl [GHR]). Task 7 briefly seeded these as "load" because the
# movement was (incorrectly) LADDER-typed at the time — Fix C retypes the
# movement ASSISTED and reverts these two slots back to "assist".
#
# 2026-08-10 (STAB maintenance-block redesign, D1 reconciled to already-
# executed Wk1 reality): d1_t1 165 -> 155 (real Wk1 lock). d1_t2b
# (Incline DB Press), d1_t2c (Face-Up Incline Knee Raise), d1_t3b
# (Cross-Body Lateral Raise), d1_t4a (Seated Cable Row), d1_t4c
# (Cross-Body Rear Delt Fly) all REMOVED -- their movements drop out of
# D1's wiring entirely (T4 GS tier removed), leaving these baselines dead;
# the underlying MovementState rows are left in place at those slot_ids,
# not deleted, per the never-delete-orphans convention. d1_t3a's assist
# baseline REMOVED -- D1's pull-up slot is now Wide-Grip Pull-up
# (PULL_UP_ROLLING_MAX, unassisted dead-hang), and Wide-Grip Pull-up
# movements never carry a BASELINES entry anywhere in this program
# (matches D4/D6's Wide-Grip Pull-up slots). New d1_t2f/d1_t2g/d1_t2e
# (Stryker Pad Seated OHP / Matrix Machine Preacher Curl / Better Fly
# Standing Lateral Raise) seed real Wk1 locks 65/55/20. d1_t3d (Ab Wheel
# Rollout, relocated from its old d1_t4b slot into T3 GS) has NO entry --
# bodyweight/REP_LADDER, matches the fact it never had a scalar baseline
# at its old slot_id either.
#
# 2026-08-13: d1_t3c's ("load", 70, None) baseline (Lat Prayer, real Wk1
# lock) REMOVED -- Lat Prayer [ANDREONI + FT] drops out of D1's wiring
# entirely (replaced by Better Fly Sagittal Lat Pulldown [FT] at fresh slot
# d1_t3e, athlete directive). The underlying MovementState row at d1_t3c is
# left in place, not deleted, per the never-delete-orphans convention. The
# new movement is needs-calibration, zero prior history -- no BASELINES
# entry, matching this program's convention for genuinely new movements.
#
# 2026-08-11 (STAB maintenance-block redesign, Task 2 -- D2 rewritten to
# match the FINAL doc's real D2 session): d2_t1b (Hip Thrust, "ht" 205 +
# #0 Orange) REMOVED -- T1b tier dropped entirely, not just the movement,
# first of three Hip Thrust removals across this redesign (D5/D6 follow in
# later tasks). d2_t2b (Scout Reverse Hyper, "load" 180) REMOVED -- that
# movement drops out of D2's wiring entirely (T2 GS turned over to Matrix
# Machine Sissy Squat / Nordic Curl Max [Ares], both brand new, zero prior
# history, NO BASELINES entry -- needs-calibration is correct). d2_t3c
# (Reverse Nordic, "load" 0) REMOVED -- not in the FINAL doc's D2 T3 GS,
# still wired on D5 unaffected. All three removed slot_ids' underlying
# MovementState rows are left in place, not deleted, per the never-delete-
# orphans convention. New d2_t3d (Hybrid Board Calf Raise [D2]) and d2_t4a
# (Ab Trainer Decline Sit-up, new T4 straight tier) also have NO entry --
# both brand new, zero prior history. d2_t1 (Belt Squat, 260) and d2_t3a/
# d2_t3b (ATG Split Squat 25 / Cable Tib Raise 25) are UNCHANGED -- retained
# movements at their existing stable slot_ids, only their rep ranges (T1)
# or tier rest_seconds (T3, see program_seed.py) changed.
#
# 2026-08-11 (STAB maintenance-block redesign, Task 3 -- D4 rewritten to
# match the FINAL doc's real D4 session): d4_t2a (Meadows Row, "load" 35),
# d4_t2b (Single-Arm DB Row, "load" 40), and d4_t2c (Face-Up Incline Knee
# Raise, "assist" 10) all REMOVED -- T2 GS fully turned over to Stryker Pad
# CSR Barbell / Ab Trainer Hanging Leg Raise / Better Fly Cable Pullover,
# all three brand new with zero prior history (needs-calibration is
# correct, no BASELINES entry). d4_t3b (Andreoni Cable Pullover, "load" 70)
# REMOVED -- that movement drops out of D4's wiring entirely, replaced by
# the reused Lying Tricep Extension [SB] at a fresh slot ("d4_t3e", no
# BASELINES entry -- zero prior history at that slot_id, matches every
# other newly-wired needs-cal movement this redesign). All four removed
# slot_ids' underlying MovementState rows are left in place, not deleted.
# d4_t1_ohp (Standing OHP [PB]) never had a BASELINES entry (needs-cal
# already) and is simply superseded by the new "d4_t1_btn_ohp" slot, which
# likewise has NO entry (Seated BTN OHP [PB] is brand new, zero prior
# history). d4_t1 (Better Fly Lat Pulldown, reused slot_id) never had a
# BASELINES entry either (its prior occupant, Wide-Grip Pull-up, was
# PULL_UP_ROLLING_MAX and never carried one) -- stays with no entry, needs-
# calibration is correct for the brand-new movement now at that slot_id.
# d4_t3a (DB Rear Delt Fly, "load" 10) and d4_t3d (PureTorque Pro Rotation,
# already no entry) are UNCHANGED -- d4_t3a's rep range widened (8-12 ->
# 10-15, program_seed.py) but its slot_id and calibrated load carry
# forward unaffected.
#
# 2026-08-12 (STAB maintenance-block redesign, Task 4 -- D5 rewritten to
# match the FINAL doc's real D5 session): d5_t1 ("load" 255) and d5_t1b
# ("ht" 205 + #0 Orange) REMOVED -- T1 anchor swapped to Kickstand RDL [DB]
# (fresh slot "d5_t1_kickstand_rdl", zero prior history) and T1b (Hip
# Thrust) tier dropped entirely, second of three Hip Thrust removals across
# this redesign (D2 done in Task 2, D6 still to come). d5_t2a ("load" 30),
# d5_t2b ("load" 180), d5_t2c ("assist" 25) REMOVED -- T2 GS fully turned
# over to Nordic Max Bulgarian Split Squat / Nordic Curl Max [Ares] / Better
# Fly Kickback, all needs-calibration at their new slots (d5_t2d/e/f), no
# BASELINES entry. d5_t3a ("load" 20) and d5_t3c ("load" 30) REMOVED --
# Poliquin Step-up and Cable Tib Raise drop out of D5's T3 GS entirely, not
# in the FINAL doc's composition. d5_t3d ("load" 245) REMOVED -- Hyper Pro
# Calf Raise drops out, replaced by the Hybrid Board equipment variant at a
# fresh slot (d5_t3e, needs-calibration). New d5_t3f (Hybrid Board Tib
# Raise [D5]) and d5_t4a (Ab Trainer Russian Twist) also have NO entry --
# both brand new, zero prior history. d5_t3b (Reverse Nordic Curl [GHR],
# "load" 0) is UNCHANGED -- retained movement at its existing stable
# slot_id, no change this task. All seven removed slot_ids' underlying
# MovementState rows are left in place, not deleted, per the never-delete-
# orphans convention.
#
# 2026-08-12 (Task 4/D5 plan-owner addendum, small standalone D2 fix bundled
# onto this same branch): d2_t3b ("load" 25, Cable Tib Raise) REMOVED -- D2's
# TIB slot is replaced program-wide by Hybrid Board Tib Raise [D2] at a
# fresh slot_id (d2_t3e, never-reassign-slot_id), needs-calibration, zero
# prior history, no BASELINES entry. d2_t1/d2_t3a are UNCHANGED.
#
# 2026-08-12 (STAB maintenance-block redesign, Task 5 -- D6 rewritten to
# match the FINAL doc's real D6 session): d6_t1 ("assist" 40, Dips) REMOVED
# -- T1 tier dropped entirely, Dips folds back into GS1 at a fresh slot
# (d6_g1e, never-reassign-slot_id) with a NEW "load" 150 baseline (reverted
# to cable-loaded, see ironlog/seed.py's Dips comment -- this is the
# movement's exact original pre-2026-07-26 baseline, not a fresh
# calibration guess). d6_g1c ("ht" 155 + #0 Orange, Hip Thrust) REMOVED --
# 3rd and final Hip Thrust removal across this redesign (D2 Task 2, D5 Task
# 4, D6 here); this was the LAST Hip Thrust BASELINES entry in the program,
# now zero remain. d6_g1d ("load" not set -- Cable Bicep Curl never had an
# entry) REMOVED -- drops out of D6 entirely, replaced by Better Fly Cable
# Bicep Curl at a fresh slot (d6_g2d), needs-calibration, no entry. d6_g2a
# ("load" 90, Reverse Hyper Recovery), d6_g2b ("load" 30, DB Seal Row),
# d6_g2c ("load" 10, Lateral Raise) all REMOVED -- GS2 fully turned over to
# Better Fly Cable Bicep Curl / Stryker Pad CSR Cables / Better Fly Rear
# Delt Extension, all needs-calibration at fresh slots (d6_g2d/e/f), no
# BASELINES entry. d6_g3b ("load" 60, Cable V-Bar Pushdown) and d6_g3c
# ("load" 105, T-Bar Row Wide) REMOVED -- drop out of D6's GS3 entirely,
# replaced by Better Fly OH Tricep Extension / AbMat Ab Bench Pad Cable
# Crunch at fresh slots (d6_g3d/e), both needs-calibration, no entry.
# New d6_g1f (Close-Grip Bench Camber-14, reused "Swiss Bar CG Press [SB]"
# movement) has NO entry -- FINAL doc gives only a wk1_calibration_estimate
# range (135-145), not a locked value, so needs-calibration is correct.
# d6_g1a: CORRECTED 2026-08-12 (STAB redesign fix, post-Task-5) -- repointed
# from "Pull-up - Neutral Grip (Paused)" to the new "Wide-Grip Pull-up
# [TOWER + TUBES]" (see docs/superpowers/specs/2026-08-10-stab-maintenance-
# block-redesign-design.md §5). Still NO BASELINES entry -- brand-new
# movement, needs-calibration is correct (the design doc's "7 unassisted
# Set 1" note is context only, not a seeded number). d6_g3a (Face Pull,
# "load" 30) is UNCHANGED -- retained movement at its existing stable
# slot_id; its rep range widened (15-20 -> 10-15, program_seed.py) but its
# slot_id and calibrated load carry forward
# unaffected. All six removed slot_ids' underlying MovementState rows are
# left in place, not deleted, per the never-delete-orphans convention.
#
# 2026-08-16 (athlete directive): d6_g1e's ("load", 150, None) baseline
# REMOVED -- Dips converted back to band assist (2nd flip, see ironlog/
# seed.py's Dips comment), no longer cable-loaded. Real Wk1 last-set data:
# purple band alone (rated 25-80lb), 12 reps @ RPE 8.5 -- seeded as
# ("assist", 50, None), the rated midpoint (athlete's call), which lands
# exactly on an existing assist_ladder rung.
BASELINES = {
    "d1_t1": ("load", 155, None), "d1_t2a": ("load", 170, None),
    "d1_t2f": ("load", 65, None), "d1_t2g": ("load", 55, None),
    "d1_t2e": ("load", 20, None),
    "d2_t1": ("load", 260, None),
    "d2_t3a": ("load", 25, None),
    "d2_t2f": ("assist", 15, None),  # Ab Trainer Decline Sit-up, real Wk1 incline angle (2026-08-19: relocated from d2_t4a into T2 GS)
    "d4_t3a": ("load", 10, None),
    "d5_t3b": ("load", 0, None),
    "d6_g1e": ("assist", 50, None),  # Dips, real Wk1 last-set band (purple alone, rated midpoint)
    "d6_g3a": ("load", 30, None),
}


def _day_role_for_tier(db: Session, tier: Tier) -> str:
    pd = db.exec(select(ProgramDay).where(ProgramDay.id == tier.program_day_id)).one()
    return pd.day_role


def _upsert(db: Session, movement_id: int, day_id: str) -> MovementState:
    st = db.exec(
        select(MovementState).where(
            MovementState.movement_id == movement_id,
            MovementState.day_id == day_id,
        )
    ).first()
    if st is None:
        st = MovementState(movement_id=movement_id, day_id=day_id)
        db.add(st)
    return st


def seed_movement_baselines(db: Session) -> None:
    tes = {t.slot_id: t for t in db.exec(select(TierExercise)).all()}
    tiers = {t.id: t for t in db.exec(select(Tier)).all()}
    bands = {b.label: b.id for b in db.exec(select(BandPair)).all()}
    for slot_id, (kind, value, band_label) in BASELINES.items():
        te = tes.get(slot_id)
        if te is None:
            raise ValueError(f"baseline slot_id not seeded: {slot_id}")
        day_id = _day_role_for_tier(db, tiers[te.tier_id])
        st = _upsert(db, te.movement_id, day_id)
        st.calibration_status = CalibrationStatus.MEASURED
        if kind == "load":
            st.current_load = value
        elif kind == "assist":
            st.assist_level = value
        elif kind == "ht":
            band_id = bands.get(band_label)
            if band_id is None:
                raise ValueError(f"band not seeded: {band_label}")
            st.ht_plates = value
            st.ht_band_config = [band_id]
    db.commit()


def reset_transactional_and_state(db: Session) -> None:
    """Wipe logged/transactional data + derived MovementState fields for a
    go-live on a non-fresh DB, WITHOUT touching seeded calibrated baselines
    (current_load / assist_level / ht_plates / ht_band_config / calibration_status).

    Deletes all rows of: SetLog, ExerciseSurvey, Note, GenerationLog,
    PlannedSet, PlannedExercise, ExerciseGroup (session scaffolding),
    WorkoutSession (the `Session` table model — not sqlmodel.Session, the
    db connection), E1rmHistory. Clears MovementState's derived-state fields.
    Resets EngineState.current_phase to CUT (keeps bodyweight).
    """
    # children referencing session_id (or chained via it) first, WorkoutSession
    # (the parent) last: SetLog/ExerciseSurvey/Note/GenerationLog/E1rmHistory
    # reference session_id directly; PlannedSet -> PlannedExercise ->
    # ExerciseGroup -> session_id is the scaffolding chain (must delete
    # leaf-to-root or a non-fresh DB is left with orphaned rows pointing at a
    # deleted session).
    for model in (
        SetLog, ExerciseSurvey, Note, GenerationLog, E1rmHistory,
        PlannedSet, PlannedExercise, ExerciseGroup, WorkoutSession,
    ):
        db.exec(delete(model))

    for st in db.exec(select(MovementState)).all():
        st.e1rm = None
        st.e1rm_updated_at = None
        st.consecutive_ceiling_sessions = 0
        st.consecutive_failed_progressions = 0
        st.consecutive_advance_count = 0
        st.stall_signal = None
        st.active_rule = None
        st.unassisted_max_rolling = None

    es = db.exec(select(EngineState)).first()
    if es is not None:
        es.current_phase = Phase.CUT

    db.commit()
