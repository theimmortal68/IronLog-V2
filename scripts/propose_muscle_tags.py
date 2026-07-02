"""
propose_muscle_tags.py — one-shot heuristic proposer for Movement muscle tags.

Reads MOVEMENTS from ironlog/seed.py, classifies each movement using
lift_category + base_name / name keyword heuristics, and writes a proposal
record for every movement to scripts/muscle_tags_proposed.json.

All proposed values are valid Muscle enum members.  Uncertain proposals are
flagged with a non-empty "uncertain_reason" field for human review.

Run from the repo root:
    .venv/bin/python scripts/propose_muscle_tags.py
"""

import json
import os
import sys

# ── Make sure we can import from the project root regardless of cwd ──────────
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from ironlog.models.enums import Muscle  # noqa: E402
from ironlog.seed import MOVEMENTS       # noqa: E402

_VALID_MUSCLES = {m.value for m in Muscle}

# ---------------------------------------------------------------------------
# Heuristic classification
# ---------------------------------------------------------------------------

def _str(val):
    """Normalise enum or string to plain string."""
    if val is None:
        return "NONE"
    if hasattr(val, "value"):
        return val.value
    return str(val)


def _classify(m):
    """
    Return (primary, secondaries, pattern, uncertain_reason).

    primary        — str, valid Muscle value
    secondaries    — list[str], valid Muscle values
    pattern        — str, human-readable label for the heuristic branch matched
    uncertain_reason — str, non-empty when the mapping is ambiguous
    """
    name   = m["name"]
    base   = m.get("base_name", "")
    lc     = _str(m.get("lift_category"))
    region = _str(m.get("region"))
    pm     = _str(m.get("progression_mode"))

    name_l = name.lower()
    base_l = base.lower()

    uncertain = ""

    # ── 1. lift_category — most reliable signal ──────────────────────────────
    if lc == "BACK_SQUAT" or lc == "FRONT_SQUAT":
        return "QUADS", ["GLUTES", "ADDUCTORS"], "squat", uncertain

    if lc == "RDL":
        return "HAMSTRINGS", ["GLUTES", "SPINAL_ERECTORS"], "rdl", uncertain

    if lc == "DEADLIFT":
        if "sumo" in name_l:
            return "HAMSTRINGS", ["GLUTES", "ADDUCTORS", "SPINAL_ERECTORS"], "sumo_deadlift", uncertain
        return "HAMSTRINGS", ["GLUTES", "SPINAL_ERECTORS", "QUADS"], "deadlift", uncertain

    if lc == "BENCH":
        return "MID_LOWER_CHEST", ["FRONT_DELT", "TRICEPS"], "bench", uncertain

    if lc == "OHP":
        return "FRONT_DELT", ["SIDE_DELT", "TRICEPS"], "ohp", uncertain

    if lc == "ROW":
        return "MID_BACK", ["LATS", "REAR_DELT", "BICEPS"], "row", uncertain

    if lc == "HIP_THRUST":
        return "GLUTES", ["HAMSTRINGS"], "hip_thrust", uncertain

    if lc == "REV_HYPER":
        return "GLUTES", ["HAMSTRINGS", "SPINAL_ERECTORS"], "rev_hyper", uncertain

    if lc == "CG_PRESS":
        return "TRICEPS", ["MID_LOWER_CHEST", "FRONT_DELT"], "cg_press", uncertain

    # ── 2. Name / base_name keyword matching ─────────────────────────────────

    # --- LOWER body keywords ------------------------------------------------

    if "nordic curl" in base_l:
        if "reverse" in base_l:
            return "QUADS", [], "reverse_nordic", uncertain
        return "HAMSTRINGS", [], "nordic_curl", uncertain

    if "hip thrust" in base_l:
        return "GLUTES", ["HAMSTRINGS"], "hip_thrust", uncertain

    if "atg split squat" in base_l or "atg squat" in base_l:
        return "QUADS", ["GLUTES", "ADDUCTORS"], "atg_squat", uncertain

    if "bulgarian split squat" in base_l:
        return "QUADS", ["GLUTES", "ADDUCTORS"], "split_squat", uncertain

    if "reverse lunge" in base_l:
        return "QUADS", ["GLUTES", "ADDUCTORS"], "lunge", uncertain

    if "heels-elevated goblet squat" in base_l:
        return "QUADS", ["GLUTES", "ADDUCTORS"], "goblet_squat", uncertain

    if "leg curl" in base_l:
        return "HAMSTRINGS", [], "leg_curl", uncertain

    if "leg extension" in base_l:
        return "QUADS", [], "leg_extension", uncertain

    if "calf raise" in base_l:
        return "CALVES", [], "calf_raise", uncertain

    if "tibialis" in base_l:
        uncertain = (
            "Tibialis anterior is not in the Muscle enum; CALVES chosen as closest "
            "lower-leg structure. User should verify or accept."
        )
        return "CALVES", [], "tibialis_raise", uncertain

    if "sissy squat" in base_l:
        return "QUADS", [], "sissy_squat", uncertain

    if "poliquin step-up" in base_l:
        return "QUADS", ["GLUTES"], "step_up", uncertain

    if "rdl" in base_l or "stiff-leg" in base_l:
        return "HAMSTRINGS", ["GLUTES", "SPINAL_ERECTORS"], "rdl", uncertain

    # --- UPPER body / pulling -----------------------------------------------

    if "pull-up" in base_l or "pullup" in base_l:
        return "LATS", ["BICEPS", "MID_BACK"], "pullup", uncertain

    if "lat pulldown" in base_l:
        return "LATS", ["BICEPS", "MID_BACK"], "lat_pulldown", uncertain

    if "lat pullaround" in base_l or "lat prayer" in base_l or "lat pullover" in base_l:
        return "LATS", [], "lat_pullover", uncertain

    if "cable pullover" in base_l:
        return "LATS", ["MID_BACK"], "cable_pullover", uncertain

    if "pullover" in base_l:
        return "LATS", ["MID_BACK"], "pullover", uncertain

    if "t-bar row" in base_l:
        return "MID_BACK", ["LATS", "REAR_DELT", "BICEPS"], "t_bar_row", uncertain

    if "meadows row" in base_l:
        return "MID_BACK", ["LATS", "REAR_DELT", "BICEPS"], "row", uncertain

    if "chest supported row" in base_l or "seal row" in base_l:
        return "MID_BACK", ["LATS", "REAR_DELT", "BICEPS"], "row", uncertain

    if "seated cable row" in base_l:
        return "MID_BACK", ["LATS", "REAR_DELT", "BICEPS"], "row", uncertain

    if "single-arm db row" in base_l:
        return "MID_BACK", ["LATS", "REAR_DELT", "BICEPS"], "row", uncertain

    if "row" in base_l:
        return "MID_BACK", ["LATS", "REAR_DELT", "BICEPS"], "row", uncertain

    # --- UPPER body / pressing ----------------------------------------------

    if "z-press" in base_l:
        return "FRONT_DELT", ["SIDE_DELT", "TRICEPS"], "ohp_variant", uncertain

    if "seated db press" in base_l:
        return "FRONT_DELT", ["SIDE_DELT", "TRICEPS"], "ohp_variant", uncertain

    if "single-arm landmine press" in base_l:
        uncertain = (
            "Landmine press angle is between FRONT_DELT and UPPER_CHEST; "
            "FRONT_DELT chosen as primary. User should verify."
        )
        return "FRONT_DELT", ["UPPER_CHEST", "TRICEPS"], "landmine_press", uncertain

    if "single-arm cable chest press" in base_l:
        return "MID_LOWER_CHEST", ["FRONT_DELT", "TRICEPS"], "chest_press", uncertain

    if "incline db press" in base_l:
        return "UPPER_CHEST", ["FRONT_DELT", "TRICEPS"], "incline_press", uncertain

    if "andreoni dips" in base_l or "dips" in base_l:
        uncertain = (
            "Grip width on Andreoni station unknown; standard dip assumption "
            "MID_LOWER_CHEST primary. User should verify grip style."
        )
        return "MID_LOWER_CHEST", ["TRICEPS", "FRONT_DELT"], "dip", uncertain

    if "jm press" in base_l or "skull crusher" in base_l:
        return "TRICEPS", [], "skull_crusher", uncertain

    # --- UPPER body / triceps ----------------------------------------------

    if "tricep" in base_l or "pushdown" in base_l or "tricep extension" in base_l:
        return "TRICEPS", [], "tricep_isolation", uncertain

    if "andreoni tricep" in base_l:
        return "TRICEPS", [], "tricep_isolation", uncertain

    # --- UPPER body / biceps -----------------------------------------------

    if "curl" in base_l:
        if "hammer" in base_l:
            return "BICEPS", ["FOREARMS"], "hammer_curl", uncertain
        return "BICEPS", [], "curl", uncertain

    # --- UPPER body / delts ------------------------------------------------

    if "lateral raise" in base_l:
        return "SIDE_DELT", [], "lateral_raise", uncertain

    if "rear delt fly" in base_l or "cross-body cable rear delt" in base_l:
        return "REAR_DELT", [], "rear_delt_fly", uncertain

    if "face pull" in base_l:
        return "REAR_DELT", ["UPPER_TRAPS"], "face_pull", uncertain

    if "band pull-aparts" in base_l or "band pull aparts" in base_l:
        return "REAR_DELT", ["UPPER_TRAPS"], "band_pull_apart", uncertain

    if "incline db y-raise" in base_l:
        uncertain = (
            "Y-raise primarily targets lower traps (not in enum); REAR_DELT chosen "
            "as closest available. User should verify."
        )
        return "REAR_DELT", ["UPPER_TRAPS"], "y_raise", uncertain

    if "andreoni lat prayer" in base_l:
        return "LATS", [], "lat_pullover", uncertain

    # --- UPPER body / chest flies ------------------------------------------

    if "low-to-high fly" in base_l or "cable fly" in base_l:
        return "UPPER_CHEST", ["FRONT_DELT"], "cable_fly", uncertain

    if "cable external rotation" in base_l:
        uncertain = (
            "Cable External Rotation targets infraspinatus / teres minor (rotator cuff), "
            "not directly in enum; REAR_DELT chosen as anatomical neighbour. "
            "User should verify."
        )
        return "REAR_DELT", [], "external_rotation", uncertain

    # --- CORE ---------------------------------------------------------------

    if region == "CORE":
        if "copenhagen" in base_l:
            return "ADDUCTORS", [], "copenhagen", uncertain
        if "rotation" in base_l or "landmine rotation" in base_l or "puretorque" in base_l:
            return "ABS", [], "rotational_core", uncertain
        if "bird dog" in base_l:
            return "ABS", ["SPINAL_ERECTORS"], "bird_dog", uncertain
        # All remaining CORE movements are ab-focused
        return "ABS", [], "core_stability", uncertain

    # --- CONDITIONING (region == NONE) -------------------------------------

    if pm == "CONDITIONING":
        if "farmer" in base_l or "sandbag carry" in base_l:
            uncertain = (
                "Carry movements are full-body; FOREARMS chosen as primary structural "
                "driver. UPPER_TRAPS is equally defensible. User should verify."
            )
            return "FOREARMS", ["UPPER_TRAPS", "ABS"], "carry", uncertain

        if "jump rope" in base_l:
            uncertain = (
                "Jump rope is cardio-dominant; CALVES chosen as the primary structural "
                "muscle engaged. User may prefer a different primary or use a "
                "dedicated cardio tag if added to enum."
            )
            return "CALVES", [], "jump_rope", uncertain

        if "kb swing" in base_l or "swing" in base_l:
            return "GLUTES", ["HAMSTRINGS", "SPINAL_ERECTORS"], "kb_swing", uncertain

        if "sandbag over-shoulder" in base_l:
            uncertain = (
                "Sandbag Over-Shoulder is a complex explosive hip/shoulder movement; "
                "GLUTES chosen as primary driver of the hip extension. "
                "User should verify."
            )
            return "GLUTES", ["HAMSTRINGS", "SPINAL_ERECTORS"], "sandbag_toss", uncertain

        if "slam ball" in base_l:
            uncertain = (
                "Slam Ball is a full-body explosive movement; ABS chosen as primary "
                "driver of the slam. GLUTES / HAMSTRINGS also highly involved. "
                "User should verify."
            )
            return "ABS", ["GLUTES", "HAMSTRINGS"], "slam_ball", uncertain

        # Generic conditioning fallback
        uncertain = f"Conditioning movement '{base}' did not match a specific heuristic."
        return "ABS", [], "conditioning_generic", uncertain

    # ── 3. Fallback — should not be reached if all movements are covered ────
    uncertain = f"No heuristic matched for '{name}' (base='{base}', lc={lc}, region={region}). Manual review required."
    return "ABS", [], "fallback", uncertain


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    proposals = []
    uncertain_list = []

    for m in MOVEMENTS:
        primary, secondaries, pattern, uncertain_reason = _classify(m)

        # Defensive validation — catch heuristic bugs before writing the file
        assert primary in _VALID_MUSCLES, f"BUG: proposed primary '{primary}' for '{m['name']}' is not a valid Muscle"
        for s in secondaries:
            assert s in _VALID_MUSCLES, f"BUG: proposed secondary '{s}' for '{m['name']}' is not a valid Muscle"

        lc_val  = _str(m.get("lift_category"))
        reg_val = _str(m.get("region"))

        record = {
            "name":               m["name"],
            "base_name":          m.get("base_name", ""),
            "lift_category":      lc_val,
            "pattern":            pattern,
            "region":             reg_val,
            "proposed_primary":   primary,
            "proposed_secondary": secondaries,
        }
        if uncertain_reason:
            record["uncertain_reason"] = uncertain_reason
            uncertain_list.append({"name": m["name"], "reason": uncertain_reason})

        proposals.append(record)

    out_path = os.path.join(_ROOT, "scripts", "muscle_tags_proposed.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(proposals, fh, indent=2, ensure_ascii=False)

    print(f"Wrote {len(proposals)} proposals to {out_path}")
    if uncertain_list:
        print(f"\n{len(uncertain_list)} movements flagged as uncertain:")
        for u in uncertain_list:
            print(f"  - {u['name']}: {u['reason']}")


if __name__ == "__main__":
    main()
