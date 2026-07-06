"""tests/test_golive_phase1.py — Task 7: go-live orchestration script + verify.

End-to-end gate: seed the Phase-1 calibrated baselines onto the standard gen_db
fixture (103-movement library + Phase-1 program, no reset needed — gen_db is
already fresh), then run verify_all_days() through the REAL generate_session
path for every training day and assert each comes up structurally clean
(loaded_slots > 0) with zero needs-calibration movements.

Uses the real `gen_db` fixture from tests/conftest.py (in-memory DB seeded via
seed.seed() + seed_phase1_program()). NO from __future__ import annotations
(project-wide constraint).
"""


def test_golive_all_days_generate_clean(gen_db):
    from scripts.golive_phase1 import verify_all_days
    from ironlog.generation.baseline_seed import seed_movement_baselines
    seed_movement_baselines(gen_db)
    report = verify_all_days(gen_db)   # returns {day_role: {"loaded_slots": int, "needs_cal": [..]}}
    for role in ("D1 Upper Push", "D2 Lower A", "D4 Upper Pull", "D5 Lower B", "D6 Weak Points"):
        assert report[role]["needs_cal"] == [], f"{role} has needs-calibration slots: {report[role]['needs_cal']}"
        assert report[role]["loaded_slots"] > 0
