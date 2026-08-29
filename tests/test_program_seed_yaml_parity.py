"""
test_program_seed_yaml_parity.py — the anti-drift keystone.

Parses the authoritative seed source (docs/program/phase1-seed-source.yaml) and
asserts the seeded Phase-1 program matches it on the fields the DEFINITION layer
stores, per (day, tier, slot):

  - Tier level:  tier_label (group_key), rest_seconds, rounds, shoe — in tier order.
  - Slot level:  resolved library Movement.name, rep_low, rep_high — in exercise
                 order (meso-1 base movement).
  - Meso-2:      the set of MesoRotation (movement name + optional rep override)
                 matches the YAML meso-2 rotations (inline `meso: {2: ...}` dicts
                 AND the standalone `meso: 2` single-leg entry).

YAML-only fields (load / assist_level / ht_* / unilateral / pattern / rule) are
baseline/Movement-layer, NOT stored on the definition rows, so they are skipped.

The YAML `m:` ids (e.g. pull_up_d6) are NOT the `_seed` program-name strings, so
this test carries its own explicit YAML_M_TO_LIBRARY map (YAML m-id -> canonical
library Movement.name) and compares by resolved library name. This makes the test
authoritative on movement identity independently of the seeder's own mapping.

NO from __future__ import annotations (project-wide constraint).
"""
from pathlib import Path

import yaml
from sqlmodel import select

from ironlog.models.library import Movement
from ironlog.models.program import MesoRotation, ProgramDay, Tier, TierExercise

# ---------------------------------------------------------------------------
# YAML m-id -> canonical library Movement.name (base / meso-1 movements)
# ---------------------------------------------------------------------------
YAML_M_TO_LIBRARY = {
    # ── d1 Upper Push ────────────────────────────────────────────────────────
    "bench_press":                      "Bench Press [PB]",
    "pendlay_row_narrow":               "Pendlay Row - Narrow [OB]",
    "lying_tricep_extension_d1":        "Lying Tricep Extension [SB]",
    "incline_db_press":                 "Incline DB Press [DB + BENCH]",
    "face_up_incline_knee_raise_d1":    "Face-Up Incline Knee Raise",
    "pull_up_d1":                       "Wide-Grip Pull-up [TOWER]",       # was "Pull-up [TOWER + TUBES]"
    "cross_body_lateral_raise":         "Cross-Body Cable Lateral Raise [FT]",
    "lat_prayer":                       "Lat Prayer [ANDREONI + FT]",
    "better_fly_sagittal_lat_pulldown_d1": "Better Fly Sagittal Lat Pulldown [FT]",
    "seated_cable_row":                 "Seated Cable Row [FT]",
    "ab_wheel_rollout":                 "Ab Wheel [WHEEL]",
    "cross_body_rear_delt_fly_d1":      "Cross-Body Cable Rear Delt Fly [FT]",
    "stryker_pad_seated_ohp_d1":        "Stryker Pad Seated OHP [DB]",
    "matrix_machine_preacher_curl_d1":  "Matrix Machine Preacher Curl [EZ]",
    "better_fly_standing_lateral_raise_d1": "Better Fly Standing Lateral Raise [FT]",
    "ab_wheel_rollout_d1":              "Ab Wheel [WHEEL]",
    # ── d2 Lower A ───────────────────────────────────────────────────────────
    "belt_squat":                       "Belt Squat [GHR + FT]",
    "hip_thrust_d2":                    "Hip Thrust [HIP_THRUST]",
    "leg_curl_d2":                       "Lying Leg Curl [GHR]",
    "scout_reverse_hyper_bilateral_d2": "Reverse Hyper [REV_HYPER]",
    "atg_split_squat":                  "ATG Split Squat",
    "cable_tib_raise_d2":               "Cable Tibialis Raise",
    "reverse_nordic_assisted_d2":        "Reverse Nordic Curl [GHR]",
    "matrix_machine_sissy_squat":       "Matrix Machine Sissy Squat",
    "nordic_curl_max_d2":               "Nordic Curl Max [Ares]",
    "nordic_curl_max_apex_d2":          "Nordic Curl Max [Apex]",
    "hybrid_board_calf_raise_d2":       "Hybrid Board Calf Raise [D2]",
    "ab_trainer_decline_situp_d2":      "Ab Trainer Decline Sit-up",
    "hybrid_board_tib_raise_d2":        "Hybrid Board Tib Raise [D2]",
    # ── d4 Upper Pull ────────────────────────────────────────────────────────
    "pull_up_d4":                       "Wide-Grip Pull-up [TOWER]",
    "standing_ohp_d4":                  "Standing OHP [PB]",
    "meadows_row_bruno_bar":            "Meadows Row [OB + LM]",
    "single_arm_db_row":                "Single-Arm DB Row [DB]",
    "face_up_incline_knee_raise_d4":    "Face-Up Incline Knee Raise",
    "db_rear_delt_fly":                 "Rear Delt Fly [DB]",
    "andreoni_bar_cable_pullover":      "Andreoni Cable Pullover",
    "puretorque_pro_rotation_d4":       "PureTorque Pro Rotation",
    "better_fly_rear_delt_ext_d4":      "Better Fly Rear Delt Extension [FT]",
    "seated_btn_ohp_d4":                "Seated BTN OHP [PB]",
    "better_fly_lat_pulldown_d4":       "Better Fly Lat Pulldown [FT]",
    "stryker_pad_csr_barbell_d4":       "Stryker Pad CSR Barbell [PB]",
    "ab_trainer_hanging_leg_raise_d4":  "Ab Trainer Hanging Leg Raise",
    "better_fly_cable_pullover_d4":     "Better Fly Cable Pullover [FT]",
    "lying_tricep_extension_camber7_d4": "Lying Tricep Extension [SB]",
    # ── d5 Lower B ───────────────────────────────────────────────────────────
    "rdl_d5":                           "RDL [PB]",
    "hip_thrust_d5":                    "Hip Thrust [HIP_THRUST]",
    "bulgarian_split_squat":            "Bulgarian Split Squat [DB]",
    "scout_reverse_hyper_bilateral_d5_90cap": "Light Reverse Hyper [REV_HYPER]",
    "scout_reverse_hyper_single_leg_d5": "Reverse Hyper - Single Leg [REV_HYPER]",
    "assisted_nordic_curl_d5":          "Nordic Curl [GHR]",
    "poliquin_step_up":                 "Poliquin Step-up",
    "reverse_nordic_assisted":          "Reverse Nordic Curl [GHR]",
    "cable_tib_raise_d5":               "Cable Tibialis Raise",
    "hyper_pro_calf_raise":             "Calf Raise [GHR]",
    "kickstand_rdl_d5":                 "Kickstand RDL [PB]",
    "nordic_max_bss_d5":                "Nordic Max Bulgarian Split Squat",
    "matrix_machine_bss_d5":            "Matrix Machine Bulgarian Split Squat",
    "lying_leg_curl_ghr_ares_d5":        "Lying Leg Curl [GHR + Ares]",
    "better_fly_kickback_d5":           "Better Fly Kickback [FT]",
    "hybrid_board_calf_raise_d5":       "Hybrid Board Calf Raise [D5]",
    "hybrid_board_tib_raise_d5":        "Hybrid Board Tib Raise [D5]",
    "better_fly_hip_adduction_d5":      "Better Fly Hip Adduction [FT]",
    "ab_trainer_russian_twist_d5":      "Ab Trainer Russian Twist",
    # ── d6 Weak Points ───────────────────────────────────────────────────────
    # 2026-08-12 (STAB redesign fix, post-Task-5): pull_up_neutral_paused_d6
    # -> pull_up_d6_wide_grip_assisted (see docs/superpowers/specs/2026-08-10-
    # stab-maintenance-block-redesign-design.md §5).
    "pull_up_d6_wide_grip_assisted":    "Wide-Grip Pull-up [TOWER + TUBES]",
    "pull_up_neutral_paused_d6":        "Pull-up - Neutral Grip (Paused) [TOWER]",
    "dips":                             "Dips [TOWER + TUBES]",
    "cable_bicep_curl_d6":              "Cable Bicep Curl [FT]",
    "hip_thrust_d6":                    "Hip Thrust [HIP_THRUST]",
    "reverse_hyper_recovery":           "Reverse Hyper Recovery [REV_HYPER]",
    "db_seal_row":                      "DB Seal Row [DB + UTIL_SEAT]",
    "lateral_raise_ares":               "Lateral Raise [FT]",
    "face_pull":                        "Face Pull [FT]",
    "cable_v_bar_pushdown":             "Cable V-Bar Pushdown [FT]",
    "t_bar_row_wide_kleva":             "T-Bar Row - Wide [OB + KLEVA + LM]",
    # 2026-08-12 (STAB maintenance-block redesign, Task 5)
    "close_grip_bench_camber_14_d6":    "Swiss Bar CG Press [SB]",
    "better_fly_cable_bicep_curl_d6":   "Better Fly Cable Bicep Curl [FT]",
    "d_handle_cable_bicep_curl_d6":      "D-Handle Cable Bicep Curl [FT]",
    "stryker_pad_csr_cables_d6":        "Stryker Pad CSR Cables [FT]",
    "better_fly_rear_delt_ext_d6":      "Better Fly Rear Delt Extension [FT]",
    "better_fly_oh_tricep_ext_d6":      "Better Fly OH Tricep Extension [FT]",
    "abmat_ab_bench_pad_cable_crunch_d6": "AbMat Ab Bench Pad Cable Crunch [FT]",
    "seated_leg_extension_d6":          "Seated Leg Extension [GHR + FT]",
}

# meso-variant ids used inside `meso: {...}` dicts -> canonical library name
MESO_VARIANT_TO_LIBRARY = {
    "back_squat":       "Back Squat [PB]",
    "pendlay_row":      "Pendlay Row - Medium [OB]",
    "rdl_conventional": "RDL [PB]",       # meso-1 default (no MesoRotation row)
    "rdl_staggered":    "Staggered RDL [PB]",
}

DAY_MAP = {
    "d1": "D1 Upper Push",
    "d2": "D2 Lower A",
    "d4": "D4 Upper Pull",
    "d5": "D5 Lower B",
    "d6": "D6 Weak Points",
}


def _yaml_days():
    p = Path(__file__).resolve().parents[1] / "docs/program/phase1-seed-source.yaml"
    return yaml.safe_load(p.read_text())["days"]


def _is_meso2_rotation(ex):
    """A separate ex entry whose `meso` is the scalar 2 is a meso-2 rotation
    (a distinct movement id), NOT a base slot. Inline `meso: {..}` dicts and
    `meso: 1` scalars are base slots (meso-1 default)."""
    m = ex.get("meso")
    return isinstance(m, int) and m == 2


def _seeded_day(gen_db, role):
    """Return (tiers, slots) for a training day.

    tiers: [(tier_label, rest_seconds, rounds, shoe), ...] in tier_order.
    slots: [(movement_name, rep_low, rep_high), ...] across tiers, in
           (tier_order, exercise_order) order (meso-1 base movements only).
    """
    mv = {m.id: m.name for m in gen_db.exec(select(Movement)).all()}
    pd = gen_db.exec(
        select(ProgramDay).where(ProgramDay.day_role == role)
    ).one()
    tiers = sorted(
        gen_db.exec(select(Tier).where(Tier.program_day_id == pd.id)).all(),
        key=lambda t: t.tier_order,
    )
    tier_rows = []
    slot_rows = []
    for t in tiers:
        tier_rows.append((t.tier_label, t.rest_seconds, t.rounds, t.shoe))
        tes = sorted(
            gen_db.exec(select(TierExercise).where(TierExercise.tier_id == t.id)).all(),
            key=lambda te: te.exercise_order,
        )
        for te in tes:
            slot_rows.append((mv[te.movement_id], te.rep_low, te.rep_high))
    return tier_rows, slot_rows


def _yaml_day(ykey):
    """Return (tiers, slots) parsed from the YAML for one day, matching the
    seeded-day shape above (base slots only; meso-2 rotations excluded)."""
    day = _yaml_days()[ykey]
    tier_rows = []
    slot_rows = []
    for tier in day.values():
        tier_rows.append((
            tier["group_key"],
            tier["rest"],
            tier.get("rounds", 1),
            tier["shoe"],
        ))
        for ex in tier["ex"]:
            if _is_meso2_rotation(ex):
                continue  # meso-2 rotation, verified separately
            name = YAML_M_TO_LIBRARY[ex["m"]]
            slot_rows.append((name, ex["reps"][0], ex["reps"][1]))
    return tier_rows, slot_rows


# ---------------------------------------------------------------------------
# Keystone 1: base-slot movement identity + reps + order
# ---------------------------------------------------------------------------

def test_seeded_base_slots_match_yaml(gen_db):
    for ykey, role in DAY_MAP.items():
        _, seeded_slots = _seeded_day(gen_db, role)
        _, yaml_slots = _yaml_day(ykey)
        assert [s[0] for s in seeded_slots] == [e[0] for e in yaml_slots], (
            f"{role}: movement identity / order mismatch\n"
            f"  seeded: {[s[0] for s in seeded_slots]}\n"
            f"  yaml:   {[e[0] for e in yaml_slots]}"
        )
        assert [(s[1], s[2]) for s in seeded_slots] == [(e[1], e[2]) for e in yaml_slots], (
            f"{role}: rep_low/rep_high mismatch\n"
            f"  seeded: {[(s[1], s[2]) for s in seeded_slots]}\n"
            f"  yaml:   {[(e[1], e[2]) for e in yaml_slots]}"
        )


# ---------------------------------------------------------------------------
# Keystone 2: tier structure (label, rest, rounds, shoe) + order
# ---------------------------------------------------------------------------

def test_seeded_tiers_match_yaml(gen_db):
    for ykey, role in DAY_MAP.items():
        seeded_tiers, _ = _seeded_day(gen_db, role)
        yaml_tiers, _ = _yaml_day(ykey)
        assert seeded_tiers == yaml_tiers, (
            f"{role}: tier (label, rest, rounds, shoe) / order mismatch\n"
            f"  seeded: {seeded_tiers}\n"
            f"  yaml:   {yaml_tiers}"
        )


# ---------------------------------------------------------------------------
# Keystone 3: meso-2 rotations (movement identity + optional rep override)
# ---------------------------------------------------------------------------

def test_seeded_meso_rotations_match_yaml(gen_db):
    days = _yaml_days()
    expected = set()
    for ykey in DAY_MAP:
        for tier in days[ykey].values():
            for ex in tier["ex"]:
                m = ex.get("meso")
                if isinstance(m, dict):
                    # inline meso dict: {meso_number: variant_id} — no rep override
                    for meso_num, variant_id in m.items():
                        if int(meso_num) >= 2:
                            expected.add(
                                (MESO_VARIANT_TO_LIBRARY[variant_id], None, None)
                            )
                elif _is_meso2_rotation(ex):
                    # standalone meso-2 entry: distinct movement id + rep override
                    expected.add((
                        YAML_M_TO_LIBRARY[ex["m"]],
                        ex["reps"][0],
                        ex["reps"][1],
                    ))

    mv = {m.id: m.name for m in gen_db.exec(select(Movement)).all()}
    seeded = {
        (mv[mr.movement_id], mr.rep_low, mr.rep_high)
        for mr in gen_db.exec(select(MesoRotation)).all()
    }
    assert seeded == expected, (
        "meso-2 rotation set mismatch (movement, rep_low, rep_high)\n"
        f"  seeded:   {sorted(str(x) for x in seeded)}\n"
        f"  expected: {sorted(str(x) for x in expected)}"
    )
