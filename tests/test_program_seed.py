"""
test_program_seed.py — seed-correctness gate for the Phase 1 program definition.

Tests the movement-resolution guard (halt-and-flag), 5-day split, T1 anchor
presence on every training day, knee-frequency satisfiability, and main-work-only
scope (no warmup/finisher/emom/z2 rows).

NO from __future__ import annotations (project-wide constraint).
"""
from collections import defaultdict

from ironlog.models.enums import KneeModality
from ironlog.models.program import MesoRotation, Program, ProgramDay, Tier, TierExercise
from sqlmodel import select


def test_program_has_five_training_days(gen_db):
    days = gen_db.exec(
        select(ProgramDay).where(ProgramDay.is_rest == False)  # noqa: E712
    ).all()
    assert len(days) == 5, "Phase 1 is a 5-day split"


def test_each_training_day_has_a_t1_anchor(gen_db):
    for d in gen_db.exec(
        select(ProgramDay).where(ProgramDay.is_rest == False)  # noqa: E712
    ).all():
        anchors = gen_db.exec(
            select(TierExercise).join(Tier).where(
                Tier.program_day_id == d.id,
                TierExercise.tier_role == "anchor",
            )
        ).all()
        assert anchors, f"{d.day_role} has no T1 anchor"


def test_knee_frequencies_are_satisfiable(gen_db):
    # tib 2x (D2+D5), nordic 2x, KOT 2x, sissy 1x — count distinct days per modality
    rows = gen_db.exec(
        select(ProgramDay.day_role, TierExercise.knee_modality)
        .join(Tier, Tier.program_day_id == ProgramDay.id)
        .join(TierExercise, TierExercise.tier_id == Tier.id)
        .where(TierExercise.knee_modality.is_not(None))
    ).all()
    days_per = defaultdict(set)
    for day_role, km in rows:
        days_per[km].add(day_role)
    assert len(days_per[KneeModality.TIB]) >= 2, "tib must appear on >=2 days"
    assert len(days_per[KneeModality.NORDIC]) >= 2
    assert len(days_per[KneeModality.KOT]) >= 2
    assert len(days_per[KneeModality.SISSY]) >= 1


def test_every_tier_exercise_resolves_to_a_library_movement(gen_db):
    """MOVEMENT-RESOLUTION GUARD (the library-import lesson): every TierExercise
    references a real seeded Movement id.  The seed itself raises on any unresolved
    program name (halt-and-flag, NEVER invent/skip) — this asserts the result."""
    from ironlog.models.library import Movement
    lib_ids = {m.id for m in gen_db.exec(select(Movement)).all()}
    tes = gen_db.exec(select(TierExercise)).all()
    assert tes, "program must have tier exercises"
    for te in tes:
        assert te.movement_id in lib_ids, f"unresolved movement_id on slot {te.slot_id}"
    # MesoRotation variants resolve too
    for mr in gen_db.exec(select(MesoRotation)).all():
        assert mr.movement_id in lib_ids, \
            f"unresolved meso variant (te {mr.tier_exercise_id})"


def test_seed_is_main_work_only(gen_db):
    """Scope guard: only main-work tiers are seeded — no warmup/finisher/Z2 rows."""
    labels = [t.tier_label.lower() for t in gen_db.exec(select(Tier)).all()]
    for banned in ("warmup", "finisher", "emom", "z2", "ramp", "activation"):
        assert not any(banned in label for label in labels), \
            f"deferred block leaked into seed: {banned!r}"


def test_program_row_exists(gen_db):
    """Sanity: one Program row seeded."""
    programs = gen_db.exec(select(Program)).all()
    assert len(programs) == 1
    assert programs[0].phase == "P1_CUT"
    assert programs[0].duration_weeks == 4


def test_seven_program_days_total(gen_db):
    """7 calendar days: 5 training + 2 rest."""
    all_days = gen_db.exec(select(ProgramDay)).all()
    assert len(all_days) == 7
    rest_days = [d for d in all_days if d.is_rest]
    assert len(rest_days) == 2
