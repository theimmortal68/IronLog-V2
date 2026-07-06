"""
test_baseline_seed.py — day-scoped MovementState baseline seeding gate.

Verifies seed_movement_baselines() upserts calibrated baselines keyed on
(movement_id, day_id=day_role), so movements shared across days (e.g. the
Hip Thrust composite on D2/D5/D6) keep independent tracks rather than
collapsing onto a single MovementState row.

NO from __future__ import annotations (project-wide constraint).
"""
from sqlmodel import select


def test_baselines_seeded_day_scoped(gen_db):
    from ironlog.generation.baseline_seed import seed_movement_baselines
    from ironlog.models.library import MovementState, Movement, BandPair
    from ironlog.models.program import TierExercise
    seed_movement_baselines(gen_db)
    states = gen_db.exec(select(MovementState)).all()
    by_key = {(s.movement_id, s.day_id): s for s in states}
    te = {t.slot_id: t for t in gen_db.exec(select(TierExercise)).all()}
    # scalar load lands on the right (movement, day)
    d1t1 = te["d1_t1"]
    assert by_key[(d1t1.movement_id, "D1 Upper Push")].current_load == 165
    # HT gets plates + band config = [orange id]
    orange = gen_db.exec(select(BandPair).where(BandPair.label == "#0 Orange")).one()
    d6ht = te["d6_g1c"]
    st = by_key[(d6ht.movement_id, "D6 Weak Points")]
    assert st.ht_plates == 155 and st.ht_band_config == [orange.id]
    # three independent HT tracks exist (D2/D5/D6), NOT one collapsed row
    ht_rows = [s for s in states if s.ht_plates is not None]
    assert {s.ht_plates for s in ht_rows} == {180, 205, 155}
