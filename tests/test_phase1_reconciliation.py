"""Tests for the Phase-1 seed reconciliation (Task 2 of the in-gym-logging chunk).

Covers: literal rep targets on 13 TierExercises, Tier.rest_seconds across all
9 tier_label buckets, Movement.scheme flips (Belt Squat + RDL -> STRAIGHT;
Bench Press -> STRAIGHT via the Task 2 review fix), the matching
TierExercise.scheme sync for d1_t1/d2_t1/d5_t1, Movement.unilateral flags on
8 movements, and rpe_cap=6.0 on the D6 Reverse-Hyper-Recovery TierExercise
(slot_id="d6_g2a" post-YAML-reconciliation; moved GS3 -> GS2).

Uses the real `gen_db` fixture from tests/conftest.py (in-memory DB seeded via
seed.seed() + seed_phase1_program()). NO from __future__ import annotations
(project-wide constraint).
"""
from sqlmodel import select

from ironlog.models.enums import Scheme
from ironlog.models.library import Movement
from ironlog.models.program import ProgramDay, Tier, TierExercise

# slot_id -> (rep_low, rep_high) — post-YAML-reconciliation final values.
CHANGED_REP_TARGETS = {
    "d1_t1": (6, 8),
    "d1_t2a": (8, 8),
    "d1_t2b": (10, 10),
    "d1_t2c": (15, 15),
    "d1_t3a": (8, 12),
    "d1_t3b": (12, 12),
    "d1_t3c": (12, 12),
    "d1_t4a": (12, 12),
    "d1_t4b": (8, 8),
    "d1_t4c": (12, 12),
    "d4_t1": (6, 8),
    "d6_g1b": (8, 12),
    "d5_t3d": (10, 15),
}

# slot_id -> (rep_low, rep_high) — anchor slots reconciled to the YAML.
UNCHANGED_REP_TARGETS = {
    "d2_t1": (6, 8),      # Belt Squat anchor
    "d5_t1": (6, 8),      # RDL anchor
    "d4_t3a": (12, 12),   # DB Rear Delt Fly (D4 T3 slot-1, post-restructure)
}

# (day_role, tier_label) -> rest_seconds. Per-day because rests are non-uniform
# per label after the YAML reconciliation.
TIER_REST_MAP = {
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


def test_rep_targets_reconciled(gen_db):
    tes = {te.slot_id: te for te in gen_db.exec(select(TierExercise)).all()}
    for slot_id, (rep_low, rep_high) in CHANGED_REP_TARGETS.items():
        te = tes[slot_id]
        assert (te.rep_low, te.rep_high) == (rep_low, rep_high), (
            f"{slot_id}: expected ({rep_low}, {rep_high}), "
            f"got ({te.rep_low}, {te.rep_high})"
        )


def test_rep_targets_unchanged_controls(gen_db):
    tes = {te.slot_id: te for te in gen_db.exec(select(TierExercise)).all()}
    for slot_id, (rep_low, rep_high) in UNCHANGED_REP_TARGETS.items():
        te = tes[slot_id]
        assert (te.rep_low, te.rep_high) == (rep_low, rep_high), (
            f"{slot_id}: expected UNCHANGED ({rep_low}, {rep_high}), "
            f"got ({te.rep_low}, {te.rep_high})"
        )


def test_tier_rests_seeded(gen_db):
    day_role_by_id = {
        pd.id: pd.day_role for pd in gen_db.exec(select(ProgramDay)).all()
    }
    by_key = {}
    for t in gen_db.exec(select(Tier)).all():
        role = day_role_by_id[t.program_day_id]
        by_key[(role, t.tier_label)] = t.rest_seconds

    for key, expected_rest in TIER_REST_MAP.items():
        assert key in by_key, f"no Tier row found for {key}"
        assert by_key[key] == expected_rest, (
            f"{key}: expected rest_seconds == {expected_rest}, got {by_key[key]}"
        )


def test_schemes_straight(gen_db):
    names = {m.name: m for m in gen_db.exec(select(Movement)).all()}
    assert names["Belt Squat [GHR + FT]"].scheme == Scheme.STRAIGHT
    assert names["RDL [PB]"].scheme == Scheme.STRAIGHT
    # Task 2 review fix: Bench Press seed-source parity with the live-only
    # fix (the 148.5-class bug — Bench must not regress to a 2-set
    # top+backoff on a from-scratch reseed).
    assert names["Bench Press [PB]"].scheme == Scheme.STRAIGHT


def test_te_schemes_synced_to_straight(gen_db):
    # Task 2 review fix: TierExercise.scheme (the string field that flows
    # into generation/context.py's slot_rep_schemes -> the injected LLM
    # payload) must match the reconciled Movement.scheme for the three
    # flipped T1 anchors, not just the deterministic-assembler-authoritative
    # Movement.scheme.
    tes = {te.slot_id: te for te in gen_db.exec(select(TierExercise)).all()}
    for slot_id in ("d1_t1", "d2_t1", "d5_t1"):
        assert tes[slot_id].scheme == "STRAIGHT", (
            f"{slot_id}: expected TierExercise.scheme == 'STRAIGHT', "
            f"got {tes[slot_id].scheme!r}"
        )


def test_unilateral_flags(gen_db):
    names = {m.name: m for m in gen_db.exec(select(Movement)).all()}
    for name in UNILATERAL_MOVEMENTS:
        assert names[name].unilateral is True, f"{name}: expected unilateral=True"
    assert names["Bench Press [PB]"].unilateral is False


def test_reverse_hyper_recovery_rpe_cap(gen_db):
    # Reverse Hyper Recovery moved GS3 -> GS2 in the YAML reconciliation; its
    # rpe_cap=6.0 now lives on d6_g2a.
    tes = {te.slot_id: te for te in gen_db.exec(select(TierExercise)).all()}
    assert tes["d6_g2a"].rpe_cap == 6.0
