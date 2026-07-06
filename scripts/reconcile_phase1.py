#!/usr/bin/env python3
"""Apply the Phase-1 seed reconciliation to the live DB.

Task 2 of the in-gym-logging chunk: seed.py / program_seed.py (the seed
SOURCE) were reconciled against the design doc's §S1 rep table + tier-rest
map (literal rep targets, straight schemes, unilateral flags, one rpe_cap).
This script applies the SAME reconciliation as idempotent UPDATEs directly
to the live DB (which was already seeded from the pre-reconciliation source),
so the running program matches the reconciled seed without a full reseed.

Idempotent: every UPDATE is unconditional-but-safe (re-running produces the
same end state; rows already at the target value are simply re-set to the
same value). Prints a per-category changed-row count.

Covers:
  - Movement.scheme: Belt Squat [GHR + FT], RDL [PB] -> STRAIGHT
  - Movement.unilateral: 8 named movements -> True
  - TierExercise.rep_low/rep_high: 13 slot_ids (see REP_TARGETS)
  - TierExercise.rpe_cap: d6_g3c (Reverse Hyper Recovery) -> 6.0
  - Tier.rest_seconds: all Tier rows, keyed by tier_label (see TIER_RESTS)

Also (re)generates deploy/migrations/013_phase1_reconciliation.sql — the same
reconciliation expressed as guarded SQL UPDATEs (idempotent, no schema change).

Run from the repo root:
    python scripts/reconcile_phase1.py

BUILD-AND-TEST-ONLY project convention: the live DB is freely reseedable
pre-launch. This script does NOT touch current_load, MovementState, or any
logged-outcome table (seed/reference data only, two-writer boundary intact).
"""
from pathlib import Path

from sqlmodel import Session, select

from ironlog.db import engine
from ironlog.models.enums import Scheme
from ironlog.models.library import Movement
from ironlog.models.program import ProgramDay, Tier, TierExercise

# slot_id -> (rep_low, rep_high) — post-YAML-reconciliation final values.
REP_TARGETS = {
    "d1_t1": (6, 8),
    "d1_t2a": (8, 8),
    "d1_t2b": (10, 10),
    "d1_t2c": (15, 15),
    "d1_t3a": (6, 10),
    "d1_t3b": (12, 12),
    "d1_t3c": (12, 12),
    "d1_t4a": (12, 12),
    "d1_t4b": (8, 8),
    "d1_t4c": (12, 12),
    "d4_t1": (6, 8),
    "d6_g1b": (8, 12),
    "d5_t3d": (10, 15),
}

# rpe_cap now lives on d6_g2a (Reverse Hyper Recovery moved GS3 -> GS2).
RPE_CAPS = {
    "d6_g2a": 6.0,
}

SCHEME_FLIPS = {
    "Belt Squat [GHR + FT]": Scheme.STRAIGHT,
    "RDL [PB]": Scheme.STRAIGHT,
}

UNILATERAL_MOVEMENTS = [
    "Meadows Row [OB + LM]",
    "Bulgarian Split Squat [DB]",
    "ATG Split Squat",
    "Cross-Body Cable Rear Delt Fly [FT]",
    "Cross-Body Cable Lateral Raise [FT]",
    "Single-Arm DB Row [DB]",
    "Poliquin Step-up",
    "Staggered RDL [PB]",
]

# (day_role, tier_label) -> rest_seconds. Keyed per-day because rests are no
# longer uniform per label after the YAML reconciliation (T1: D1/D2=120 vs
# D4/D5=180; T3 GS: D1/D4=75 vs D5=60).
TIER_RESTS = {
    ("D1 Upper Push", "T1"): 120,
    ("D1 Upper Push", "T2 GS"): 90,
    ("D1 Upper Push", "T3 GS"): 75,
    ("D1 Upper Push", "T4 GS"): 60,
    ("D2 Lower A", "T1"): 120,
    ("D2 Lower A", "T1b"): 150,
    ("D2 Lower A", "T2 GS"): 90,
    ("D2 Lower A", "T3"): 75,
    ("D4 Upper Pull", "T1"): 180,
    ("D4 Upper Pull", "T2 GS"): 90,
    ("D4 Upper Pull", "T3 GS"): 75,
    ("D5 Lower B", "T1"): 180,
    ("D5 Lower B", "T1b"): 150,
    ("D5 Lower B", "T2 GS"): 90,
    ("D5 Lower B", "T3 GS"): 60,
    ("D6 Weak Points", "GS1"): 90,
    ("D6 Weak Points", "GS2"): 90,
    ("D6 Weak Points", "GS3"): 60,
}

MIGRATION_PATH = Path(__file__).resolve().parent.parent / "deploy" / "migrations" / "013_phase1_reconciliation.sql"


def apply_reconciliation(db: Session) -> dict:
    """Apply all reconciliation updates in the given session. Returns a dict
    of {category: changed_row_count} (only rows whose value actually differed
    from the target are counted as "changed")."""
    counts = {
        "scheme": 0, "unilateral": 0, "rep_targets": 0, "rpe_cap": 0, "rest_seconds": 0,
    }

    # -- Movement.scheme flips --
    movements_by_name = {m.name: m for m in db.exec(select(Movement)).all()}
    for name, target_scheme in SCHEME_FLIPS.items():
        mv = movements_by_name[name]
        if mv.scheme != target_scheme:
            counts["scheme"] += 1
        mv.scheme = target_scheme
        db.add(mv)

    # -- Movement.unilateral flags --
    for name in UNILATERAL_MOVEMENTS:
        mv = movements_by_name[name]
        if mv.unilateral is not True:
            counts["unilateral"] += 1
        mv.unilateral = True
        db.add(mv)

    # -- TierExercise rep_low/rep_high + rpe_cap --
    tes_by_slot = {te.slot_id: te for te in db.exec(select(TierExercise)).all()}
    for slot_id, (rep_low, rep_high) in REP_TARGETS.items():
        te = tes_by_slot[slot_id]
        if (te.rep_low, te.rep_high) != (rep_low, rep_high):
            counts["rep_targets"] += 1
        te.rep_low = rep_low
        te.rep_high = rep_high
        db.add(te)

    for slot_id, rpe_cap in RPE_CAPS.items():
        te = tes_by_slot[slot_id]
        if te.rpe_cap != rpe_cap:
            counts["rpe_cap"] += 1
        te.rpe_cap = rpe_cap
        db.add(te)

    # -- Tier.rest_seconds (keyed per (day_role, tier_label)) --
    day_role_by_id = {
        pd.id: pd.day_role for pd in db.exec(select(ProgramDay)).all()
    }
    for t in db.exec(select(Tier)).all():
        day_role = day_role_by_id.get(t.program_day_id)
        target = TIER_RESTS.get((day_role, t.tier_label))
        if target is None:
            continue  # rest day / out of scope (all 18 training tiers covered)
        if t.rest_seconds != target:
            counts["rest_seconds"] += 1
        t.rest_seconds = target
        db.add(t)

    db.commit()
    return counts


def write_migration_sql() -> None:
    """(Re)generate deploy/migrations/013_phase1_reconciliation.sql — the same
    reconciliation expressed as idempotent guarded SQL UPDATEs. Table names
    are lowercase, no underscores (movement, tier, tierexercise), per the
    parity-tested schema."""
    lines = [
        "-- 013_phase1_reconciliation.sql — Phase-1 seed reconciliation (Task 2)",
        "-- Data-only: no schema change (unilateral, rest_seconds, rpe_cap, rep_low/",
        "-- rep_high, scheme columns already exist). Every UPDATE is guarded with a",
        "-- WHERE clause that also checks the column doesn't already equal the target",
        "-- value, so this migration is idempotent and safe to re-run.",
        "",
        "-- Movement.scheme: Belt Squat + RDL -> STRAIGHT (TOPSET_BACKOFF was wrong;",
        "-- Bench Press is intentionally NOT included here, see task-2 report).",
    ]
    for name, target in SCHEME_FLIPS.items():
        esc = name.replace("'", "''")
        lines.append(
            f"UPDATE movement SET scheme = '{target.value}' "
            f"WHERE name = '{esc}' AND scheme != '{target.value}';"
        )

    lines.append("")
    lines.append("-- Movement.unilateral: per-side movement flag on 8 movements.")
    for name in UNILATERAL_MOVEMENTS:
        esc = name.replace("'", "''")
        lines.append(
            f"UPDATE movement SET unilateral = 1 "
            f"WHERE name = '{esc}' AND (unilateral IS NULL OR unilateral != 1);"
        )

    lines.append("")
    lines.append("-- TierExercise.rep_low/rep_high: literal rep targets (13 slot_ids).")
    for slot_id, (rep_low, rep_high) in REP_TARGETS.items():
        esc = slot_id.replace("'", "''")
        lines.append(
            f"UPDATE tierexercise SET rep_low = {rep_low}, rep_high = {rep_high} "
            f"WHERE slot_id = '{esc}' "
            f"AND (rep_low IS NULL OR rep_low != {rep_low} "
            f"OR rep_high IS NULL OR rep_high != {rep_high});"
        )

    lines.append("")
    lines.append("-- TierExercise.rpe_cap: Reverse Hyper Recovery (D6 GS2, moved from GS3).")
    for slot_id, rpe_cap in RPE_CAPS.items():
        esc = slot_id.replace("'", "''")
        lines.append(
            f"UPDATE tierexercise SET rpe_cap = {rpe_cap} "
            f"WHERE slot_id = '{esc}' AND (rpe_cap IS NULL OR rpe_cap != {rpe_cap});"
        )

    lines.append("")
    lines.append("-- Tier.rest_seconds: all 18 seeded tiers, keyed per (day_role, tier_label)")
    lines.append("-- (rests are non-uniform per label after the YAML reconciliation).")
    for (day_role, label), rest_seconds in TIER_RESTS.items():
        esc_day = day_role.replace("'", "''")
        esc_label = label.replace("'", "''")
        lines.append(
            f"UPDATE tier SET rest_seconds = {rest_seconds} "
            f"WHERE tier_label = '{esc_label}' "
            f"AND program_day_id IN "
            f"(SELECT id FROM programday WHERE day_role = '{esc_day}') "
            f"AND (rest_seconds IS NULL OR rest_seconds != {rest_seconds});"
        )

    lines.append("")
    MIGRATION_PATH.write_text("\n".join(lines))


def main() -> None:
    with Session(engine) as db:
        counts = apply_reconciliation(db)
    write_migration_sql()
    print("Phase-1 reconciliation applied to live DB.")
    print(f"  Movement.scheme flips changed:      {counts['scheme']} / {len(SCHEME_FLIPS)}")
    print(f"  Movement.unilateral flags changed:  {counts['unilateral']} / {len(UNILATERAL_MOVEMENTS)}")
    print(f"  TierExercise rep targets changed:   {counts['rep_targets']} / {len(REP_TARGETS)}")
    print(f"  TierExercise rpe_cap changed:        {counts['rpe_cap']} / {len(RPE_CAPS)}")
    print(f"  Tier.rest_seconds rows changed:     {counts['rest_seconds']}")
    print(f"Wrote {MIGRATION_PATH.relative_to(MIGRATION_PATH.parent.parent.parent)}")


if __name__ == "__main__":
    main()
