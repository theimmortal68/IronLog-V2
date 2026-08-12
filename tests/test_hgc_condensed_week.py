"""tests/test_hgc_condensed_week.py"""
from datetime import date

from sqlmodel import select

from ironlog.models.enums import GroupType
from ironlog.models.library import Movement
from ironlog.models.session import Session as LogSession
from scripts.build_hgc_condensed_week import apply, MINI_SESSIONS


def _hgc_sessions(db):
    return db.exec(
        select(LogSession)
        .where(LogSession.rationale.startswith("HGC condensed week"))
        .order_by(LogSession.id)
    ).all()


def _ordered_groups(session):
    return sorted(session.groups, key=lambda group: group.order_index)


def _ordered_exercises(group):
    return sorted(group.exercises, key=lambda exercise: exercise.order_index)


def _movement_name(db, movement_id):
    return db.exec(select(Movement).where(Movement.id == movement_id)).one().name


def _group_movement_names(db, group):
    return [_movement_name(db, exercise.movement_id) for exercise in _ordered_exercises(group)]


def test_hgc_condensed_week_creates_sessions(gen_db_calibrated):
    # Apply script
    apply(gen_db_calibrated)
    
    # Verify sessions
    sessions = _hgc_sessions(gen_db_calibrated)
    
    assert len(sessions) == 11
    
    for idx, (expected_date, expected_role, expected_movements) in enumerate(MINI_SESSIONS):
        s = sessions[idx]
        assert s.date == expected_date
        assert s.day_role == expected_role

        exercises = [
            exercise
            for group in _ordered_groups(s)
            for exercise in _ordered_exercises(group)
        ]
        assert len(exercises) == len(expected_movements)
        assert sorted(
            _movement_name(gen_db_calibrated, ex.movement_id)
            for ex in exercises
        ) == sorted(expected_movements)
        
        for ex in exercises:
            assert len(ex.planned_sets) > 0


def test_hgc_condensed_week_marks_only_first_occurrence_of_day_role_for_finisher(gen_db_calibrated):
    apply(gen_db_calibrated)

    sessions = _hgc_sessions(gen_db_calibrated)

    assert [
        (idx, s.date, s.day_role, s.signature["show_finisher"])
        for idx, s in enumerate(sessions, 1)
    ] == [
        (1, date(2026, 7, 27), "D1 Upper Push", True),
        (2, date(2026, 7, 27), "D2 Lower A", True),
        (3, date(2026, 7, 27), "D6 Weak Points", True),
        (4, date(2026, 7, 28), "D5 Lower B", True),
        (5, date(2026, 7, 28), "D2 Lower A", False),
        (6, date(2026, 7, 28), "D6 Weak Points", False),
        (7, date(2026, 7, 28), "D1 Upper Push", False),
        (8, date(2026, 7, 29), "D4 Upper Pull", True),
        (9, date(2026, 7, 29), "D6 Weak Points", False),
        (10, date(2026, 7, 29), "D1 Upper Push", False),
        (11, date(2026, 7, 29), "D5 Lower B", False),
    ]


def test_hgc_condensed_week_marks_only_first_mini_session_per_date_for_warmup(gen_db_calibrated):
    apply(gen_db_calibrated)

    sessions = _hgc_sessions(gen_db_calibrated)

    assert [
        (idx, s.date, s.day_role, s.signature["show_warmup"])
        for idx, s in enumerate(sessions, 1)
    ] == [
        (1, date(2026, 7, 27), "D1 Upper Push", True),
        (2, date(2026, 7, 27), "D2 Lower A", False),
        (3, date(2026, 7, 27), "D6 Weak Points", False),
        (4, date(2026, 7, 28), "D5 Lower B", True),
        (5, date(2026, 7, 28), "D2 Lower A", False),
        (6, date(2026, 7, 28), "D6 Weak Points", False),
        (7, date(2026, 7, 28), "D1 Upper Push", False),
        (8, date(2026, 7, 29), "D4 Upper Pull", True),
        (9, date(2026, 7, 29), "D6 Weak Points", False),
        (10, date(2026, 7, 29), "D1 Upper Push", False),
        (11, date(2026, 7, 29), "D5 Lower B", False),
    ]


def test_hgc_condensed_week_clusters_shared_source_giant_set(gen_db_calibrated):
    # 2026-07-26: mini-session 1 (D1) no longer demonstrates giant-set
    # clustering -- Pendlay Row Narrow was promoted out of D1's T2 GS into
    # its own T1b straight-set tier, so it and its former T2 GS co-member no
    # longer share a source ExerciseGroup (2026-08-10: that co-member is now
    # Stryker Pad Seated OHP [DB], per the STAB maintenance-block redesign's
    # T2 GS turnover -- same "doesn't cluster" fact, different movement).
    # Mini-session 2 (D2: Belt Squat / ATG Split Squat / Hybrid Board Tib
    # Raise [D2]) still has a genuine 2-member shared giant-set cluster
    # (D2's T3 GS), so this test moved there. 2026-08-12 (STAB maintenance-
    # block redesign, Task 4 addendum): "Cable Tibialis Raise" -> "Hybrid
    # Board Tib Raise [D2]" (program-wide TIB movement replacement).
    apply(gen_db_calibrated)

    session = _hgc_sessions(gen_db_calibrated)[1]
    groups = _ordered_groups(session)

    assert [group.order_index for group in groups] == [1, 2]
    assert [group.group_type for group in groups] == [
        GroupType.STRAIGHT,
        GroupType.GIANT_SET,
    ]
    assert [group.rounds for group in groups] == [1, 3]
    assert _group_movement_names(gen_db_calibrated, groups[0]) == ["Belt Squat [GHR + FT]"]
    assert _group_movement_names(gen_db_calibrated, groups[1]) == [
        "ATG Split Squat",
        "Hybrid Board Tib Raise [D2]",
    ]


def test_hgc_condensed_week_incomplete_clusters_are_straight_sets(gen_db_calibrated):
    """2026-08-12 (STAB maintenance-block redesign, Task 4): mini-session 4
    (D5, 7/28) repointed from ["RDL [PB]", "Hip Thrust [HIP_THRUST]",
    "Reverse Nordic Curl [GHR]"] to ["Kickstand RDL [DB]", "Better Fly
    Kickback [FT]", "Reverse Nordic Curl [GHR]"] -- RDL [PB] and Hip Thrust
    [HIP_THRUST] both drop out of D5's wiring entirely (T1 anchor swap, T1b
    tier removed), Reverse Nordic Curl [GHR] unchanged. None of these three
    movements share a source ExerciseGroup (T1 straight / removed T1b /
    T3 GS member), so this remains a 3-way straight-sets-only proof.
    """
    apply(gen_db_calibrated)

    session = _hgc_sessions(gen_db_calibrated)[3]
    groups = _ordered_groups(session)

    assert [group.order_index for group in groups] == [1, 2, 3]
    assert [group.group_type for group in groups] == [
        GroupType.STRAIGHT,
        GroupType.STRAIGHT,
        GroupType.STRAIGHT,
    ]
    assert [group.rounds for group in groups] == [1, 1, 1]
    assert not any(group.group_type == GroupType.GIANT_SET for group in groups)
    assert [_group_movement_names(gen_db_calibrated, group) for group in groups] == [
        ["Kickstand RDL [DB]"],
        ["Better Fly Kickback [FT]"],
        ["Reverse Nordic Curl [GHR]"],
    ]


def test_hgc_condensed_week_is_idempotent(gen_db_calibrated):
    apply(gen_db_calibrated)
    sessions1 = _hgc_sessions(gen_db_calibrated)
    assert len(sessions1) == 11
    
    apply(gen_db_calibrated)
    sessions2 = _hgc_sessions(gen_db_calibrated)
    assert len(sessions2) == 11
