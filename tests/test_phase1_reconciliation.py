"""Tests for the Phase-1 seed reconciliation (Task 2 of the in-gym-logging chunk).

Covers: literal rep targets on 13 TierExercises, Tier.rest_seconds across all
9 tier_label buckets, Movement.scheme flips (Belt Squat + RDL -> STRAIGHT),
Movement.unilateral flags on 8 movements, and rpe_cap=6.0 on the D6
Reverse-Hyper-Recovery TierExercise (slot_id="d6_g3c").

Uses the real `gen_db` fixture from tests/conftest.py (in-memory DB seeded via
seed.seed() + seed_phase1_program()). NO from __future__ import annotations
(project-wide constraint).
"""
from sqlmodel import select

from ironlog.models.enums import Scheme
from ironlog.models.library import Movement
from ironlog.models.program import Tier, TierExercise

# slot_id -> (rep_low, rep_high) for the 13 reconciled TierExercises
CHANGED_REP_TARGETS = {
    "d1_t1": (8, 8),
    "d1_t2a": (8, 8),
    "d1_t2b": (10, 10),
    "d1_t2c": (15, 15),
    "d1_t3a": (8, 8),
    "d1_t3b": (12, 12),
    "d1_t3c": (12, 12),
    "d1_t4a": (12, 12),
    "d1_t4b": (8, 8),
    "d1_t4c": (12, 12),
    "d4_t1": (5, 8),
    "d6_g1b": (5, 8),
    "d5_t3d": (10, 12),
}

# slot_id -> (rep_low, rep_high) that must stay UNCHANGED (guard against over-application)
UNCHANGED_REP_TARGETS = {
    "d2_t1": (5, 8),      # Belt Squat anchor
    "d5_t1": (4, 6),      # RDL anchor
    "d4_t3a": (10, 12),   # Cross-Body Rear Delt Fly (D4) — already matched, no edit needed
}

# tier_label -> rest_seconds
TIER_REST_MAP = {
    "T1": 120,
    "T1b": 120,
    "T2 GS": 90,
    "GS1": 90,
    "GS2": 90,
    "T3": 60,
    "T3 GS": 60,
    "T4 GS": 60,
    "GS3": 60,
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
    by_label = {}
    for t in gen_db.exec(select(Tier)).all():
        by_label.setdefault(t.tier_label, []).append(t.rest_seconds)

    assert set(by_label.keys()) >= set(TIER_REST_MAP.keys())
    for label, expected_rest in TIER_REST_MAP.items():
        rests = by_label[label]
        assert rests, f"no Tier rows found for label {label!r}"
        assert all(r == expected_rest for r in rests), (
            f"tier_label {label!r}: expected all rest_seconds == {expected_rest}, "
            f"got {rests}"
        )


def test_schemes_straight(gen_db):
    names = {m.name: m for m in gen_db.exec(select(Movement)).all()}
    assert names["Belt Squat [GHR + FT]"].scheme == Scheme.STRAIGHT
    assert names["RDL [PB]"].scheme == Scheme.STRAIGHT


def test_unilateral_flags(gen_db):
    names = {m.name: m for m in gen_db.exec(select(Movement)).all()}
    for name in UNILATERAL_MOVEMENTS:
        assert names[name].unilateral is True, f"{name}: expected unilateral=True"
    assert names["Bench Press [PB]"].unilateral is False


def test_reverse_hyper_recovery_rpe_cap(gen_db):
    tes = {te.slot_id: te for te in gen_db.exec(select(TierExercise)).all()}
    assert tes["d6_g3c"].rpe_cap == 6.0
