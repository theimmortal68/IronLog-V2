"""rule_wiring.py — wire Movement.progression_rule from the authoritative YAML.

The progression engine (`ironlog/engine/advance.py`) dispatches on
`Movement.progression_rule`; when it is None, `advance()` no-ops (spec §9
fallback invariant). Every seeded movement shipped with progression_rule=None
("live config is a deferred follow-on"), so the engine was DORMANT: it computed
e1RM every session but never advanced any load, tier, assist level, or rep target.

This module resolves the per-exercise `rule:` from
`docs/program/phase1-seed-source.yaml` to a `ProgressionRule` enum member and
writes it onto the canonical library Movement, making the engine live.

Two consumers:
  - `wire_progression_rules(db)` — idempotent UPDATE, called from
    `seed_phase1_program` so a from-scratch DB comes up with rules set, and
    re-runnable against the live DB (via `main()` below) without a reseed.
  - `derive_movement_rules()` — the pure {library-name -> ProgressionRule} map,
    with halt-and-flag on an unmapped rule string or a per-movement rule conflict.

Halt-and-flag discipline (mirrors program_seed._resolve): a YAML rule with no
enum member, an unknown YAML m-id, a movement whose slots map to DIFFERENT enum
values, or a derived movement name absent from the seeded library all RAISE —
never invent, never silently pick or skip.

NO from __future__ import annotations (project-wide constraint).
"""
from pathlib import Path
from typing import Dict, Iterator, Tuple

import yaml
from sqlmodel import Session, select

from ironlog.models.enums import ProgressionRule
from ironlog.models.library import Movement

YAML_PATH = Path(__file__).resolve().parents[2] / "docs" / "program" / "phase1-seed-source.yaml"

# ---------------------------------------------------------------------------
# YAML rule string -> ProgressionRule enum
# ---------------------------------------------------------------------------
# rule_driven and rule_driven_fixed_increment BOTH resolve to RULE_DRIVEN (the
# fixed-increment HT variant is the same rule; the "+5 vs band" split is handled
# downstream by ht_next_setup / _is_ht_composite, not by a distinct rule).
RULE_STRING_TO_ENUM: Dict[str, ProgressionRule] = {
    "rpe_8_standard":              ProgressionRule.RPE_8_STANDARD,
    "pull_up_rolling_max":         ProgressionRule.PULL_UP_ROLLING_MAX,
    "rule_driven":                 ProgressionRule.RULE_DRIVEN,
    "rule_driven_fixed_increment": ProgressionRule.RULE_DRIVEN,
    "single_session_progression":  ProgressionRule.SINGLE_SESSION,
    "fixed_load":                  ProgressionRule.FIXED_LOAD,
    "incline_reduction":           ProgressionRule.INCLINE_REDUCTION,
    "assistance_reduction":        ProgressionRule.ASSISTANCE_REDUCTION,
    "rep_ladder":                  ProgressionRule.REP_LADDER,
    "body_position_progression":   ProgressionRule.BODY_POSITION,
}

# ---------------------------------------------------------------------------
# YAML m-id -> canonical library Movement.name (base + standalone meso-2 slots)
# ---------------------------------------------------------------------------
# Every `m:` id that can appear on an `ex` entry carrying a `rule:`. This is the
# same identity map the anti-drift parity test asserts against
# (tests/test_program_seed_yaml_parity.py::YAML_M_TO_LIBRARY); kept here as the
# production home so the wiring resolves movement identity independently.
YAML_M_TO_LIBRARY: Dict[str, str] = {
    # ── d1 Upper Push ────────────────────────────────────────────────────────
    "bench_press":                       "Bench Press [PB]",
    "pendlay_row_narrow":                "Pendlay Row - Narrow [OB]",
    "lying_tricep_extension_d1":         "Lying Tricep Extension [SB]",
    "incline_db_press":                  "Incline DB Press [DB + BENCH]",
    "face_up_incline_knee_raise_d1":     "Face-Up Incline Knee Raise",
    "pull_up_d1":                        "Pull-up [TOWER + TUBES]",
    "cross_body_lateral_raise":          "Cross-Body Cable Lateral Raise [FT]",
    "lat_prayer":                        "Lat Prayer [ANDREONI + FT]",
    "seated_cable_row":                  "Seated Cable Row [FT]",
    "ab_wheel_rollout":                  "Ab Wheel [WHEEL]",
    "cross_body_rear_delt_fly_d1":       "Cross-Body Cable Rear Delt Fly [FT]",
    # ── d2 Lower A ───────────────────────────────────────────────────────────
    "belt_squat":                        "Belt Squat [GHR + FT]",
    "hip_thrust_d2":                     "Hip Thrust [HIP_THRUST]",
    "leg_curl_d2":                        "Lying Leg Curl [GHR]",
    "scout_reverse_hyper_bilateral_d2":  "Reverse Hyper [REV_HYPER]",
    "atg_split_squat":                   "ATG Split Squat",
    "cable_tib_raise_d2":                "Cable Tibialis Raise",
    "reverse_nordic_assisted_d2":         "Reverse Nordic Curl [GHR]",
    # ── d4 Upper Pull ────────────────────────────────────────────────────────
    "pull_up_d4":                        "Wide-Grip Pull-up [TOWER]",
    "standing_ohp_d4":                   "Standing OHP [PB]",
    "meadows_row_bruno_bar":             "Meadows Row [OB + LM]",
    "single_arm_db_row":                 "Single-Arm DB Row [DB]",
    "face_up_incline_knee_raise_d4":     "Face-Up Incline Knee Raise",
    "db_rear_delt_fly":                  "Rear Delt Fly [DB]",
    "andreoni_bar_cable_pullover":       "Andreoni Cable Pullover",
    "dragon_flag":                       "Dragon Flag",
    # ── d5 Lower B ───────────────────────────────────────────────────────────
    "rdl_d5":                            "RDL [PB]",
    "hip_thrust_d5":                     "Hip Thrust [HIP_THRUST]",
    "bulgarian_split_squat":             "Bulgarian Split Squat [DB]",
    "scout_reverse_hyper_bilateral_d5_90cap": "Light Reverse Hyper [REV_HYPER]",
    "scout_reverse_hyper_single_leg_d5": "Reverse Hyper - Single Leg [REV_HYPER]",
    "assisted_nordic_curl_d5":           "Nordic Curl [GHR]",
    "poliquin_step_up":                  "Poliquin Step-up",
    "reverse_nordic_assisted":           "Reverse Nordic Curl [GHR]",
    "cable_tib_raise_d5":                "Cable Tibialis Raise",
    "hyper_pro_calf_raise":              "Calf Raise [GHR]",
    # ── d6 Weak Points ───────────────────────────────────────────────────────
    "pull_up_d6":                        "Wide-Grip Pull-up [TOWER]",
    "dips":                              "Dips [TOWER + TUBES]",
    "cable_bicep_curl_d6":               "Cable Bicep Curl [FT]",
    "hip_thrust_d6":                     "Hip Thrust [HIP_THRUST]",
    "reverse_hyper_recovery":            "Reverse Hyper Recovery [REV_HYPER]",
    "db_seal_row":                       "DB Seal Row [DB + UTIL_SEAT]",
    "lateral_raise_ares":                "Lateral Raise [FT]",
    "face_pull":                         "Face Pull [FT]",
    "cable_v_bar_pushdown":              "Cable V-Bar Pushdown [FT]",
    "t_bar_row_wide_kleva":              "T-Bar Row - Wide [OB + KLEVA + LM]",
}


def _iter_yaml_rules() -> Iterator[Tuple[str, str, str]]:
    """Yield (m_id, rule_string, provenance) for every `ex` entry that carries a
    `rule:` across all training days in the seed YAML."""
    days = yaml.safe_load(YAML_PATH.read_text())["days"]
    for day_key, day in days.items():
        for tier_key, tier in day.items():
            for ex in tier.get("ex", []):
                rule = ex.get("rule")
                if rule is None:
                    continue
                yield ex["m"], rule, f"{day_key}.{tier_key}"


def derive_movement_rules() -> Dict[str, ProgressionRule]:
    """Return {canonical library Movement.name -> ProgressionRule}, derived from
    the authoritative YAML. Halt-and-flag (raise) on:
      - an unknown YAML m-id (not in YAML_M_TO_LIBRARY),
      - a rule string with no enum member (not in RULE_STRING_TO_ENUM),
      - a movement whose slots map to DIFFERENT ProgressionRule values.
    """
    resolved: Dict[str, ProgressionRule] = {}
    for m_id, rule_string, prov in _iter_yaml_rules():
        name = YAML_M_TO_LIBRARY.get(m_id)
        if name is None:
            raise ValueError(
                f"HALT-AND-FLAG: YAML m-id {m_id!r} ({prov}) not in YAML_M_TO_LIBRARY. "
                "Add the m-id -> canonical library name mapping before wiring. NEVER invent."
            )
        rule = RULE_STRING_TO_ENUM.get(rule_string)
        if rule is None:
            raise ValueError(
                f"HALT-AND-FLAG: YAML rule {rule_string!r} ({prov}, m={m_id!r}) has no "
                f"ProgressionRule enum member. Add it to RULE_STRING_TO_ENUM or fix the "
                "YAML. NEVER invent a rule."
            )
        prior = resolved.get(name)
        if prior is not None and prior != rule:
            raise ValueError(
                f"HALT-AND-FLAG: movement {name!r} maps to conflicting progression rules "
                f"across slots ({prior.value} vs {rule.value}, latest at {prov}). "
                "A movement must carry ONE rule — resolve the YAML conflict; NEVER silently pick."
            )
        resolved[name] = rule
    return resolved


def wire_progression_rules(db: Session) -> Dict[str, int]:
    """Idempotent UPDATE of Movement.progression_rule from the derived map.

    Every UPDATE is unconditional-but-safe (re-running produces the same end
    state; rows already at the target value are left as-is and not counted).
    Halt-and-flag if a derived movement name is absent from the seeded library.
    Returns {"changed": n, "total": m}.
    """
    rules = derive_movement_rules()
    by_name = {m.name: m for m in db.exec(select(Movement)).all()}
    changed = 0
    for name, rule in rules.items():
        mv = by_name.get(name)
        if mv is None:
            raise ValueError(
                f"HALT-AND-FLAG: derived movement {name!r} not found in seeded library. "
                "Seed the library MOVEMENTS (or fix YAML_M_TO_LIBRARY) before wiring."
            )
        if mv.progression_rule != rule.value:
            mv.progression_rule = rule.value
            db.add(mv)
            changed += 1
    db.commit()
    return {"changed": changed, "total": len(rules)}


def main() -> None:
    """Apply the wiring to the live DB (idempotent). BUILD-AND-TEST-ONLY project
    convention; run from the repo root after review:

        python -m ironlog.generation.rule_wiring
    """
    from ironlog.db import engine
    with Session(engine) as db:
        counts = wire_progression_rules(db)
    print("Progression-rule wiring applied to live DB.")
    print(f"  Movement.progression_rule changed: {counts['changed']} / {counts['total']}")


if __name__ == "__main__":
    main()
