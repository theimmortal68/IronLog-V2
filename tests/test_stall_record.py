"""test_stall_record.py — Task 4: typed/severity/limiter stall record (gap D).

Tests verify:
- build_weak_point_hints returns the new record shape (stall_type, failed_count,
  e1rm_window, limiter) instead of the old generic hint string.
- should_invoke_llm still fires on the *presence* of a record for a stalled movement
  (checks mid in ctx.weak_point_hints — key lookup unchanged by value-shape change).

Fixture: stalled_session_db (conftest.py) — consecutive_failed_progressions=2 on
Seated Cable Row [FT] (D1 d1_t4a, semi) → detect_stall fires → movement
in weak_point_hints → slot_has_deviation_signal True → should_invoke_llm True.

NO from __future__ import annotations (project-wide constraint).
"""


def test_failed_stall_record_shape(stalled_session_db):
    """build_weak_point_hints returns the typed record dict, not a string."""
    from ironlog.generation.context import build_weak_point_hints

    rec = build_weak_point_hints(stalled_session_db, "D1 Upper Push")
    assert rec, "expected at least one stalled movement"
    mid, r = next(iter(rec.items()))
    assert r["stall_type"] in ("failed", "trend", "both")
    assert isinstance(r["failed_count"], int)
    assert set(r["e1rm_window"]) == {"sessions", "peak", "latest"}
    assert set(r["limiter"]) == {"primary_muscle", "secondary_muscles"}


def test_weak_point_hints_resolve_exact_day_state(gen_db):
    """Shared movements must use the MovementState row for the generated day."""
    from sqlmodel import select

    from ironlog.generation.context import build_weak_point_hints
    from ironlog.models.library import Movement, MovementState

    mv = gen_db.exec(
        select(Movement).where(Movement.name == "Hip Thrust [HIP_THRUST]")
    ).one()
    gen_db.add(MovementState(
        movement_id=mv.id,
        day_id="D2 Lower A",
        consecutive_failed_progressions=2,
    ))
    gen_db.add(MovementState(
        movement_id=mv.id,
        day_id="D5 Lower B",
        consecutive_failed_progressions=4,
    ))
    gen_db.commit()

    d2_hints = build_weak_point_hints(gen_db, "D2 Lower A")
    d5_hints = build_weak_point_hints(gen_db, "D5 Lower B")

    assert d2_hints[mv.id]["failed_count"] == 2
    assert d5_hints[mv.id]["failed_count"] == 4


def test_weak_point_hints_fall_back_to_legacy_null_day_state(gen_db):
    """A legacy day_id=NULL MovementState row still applies read-only."""
    from sqlmodel import select

    from ironlog.generation.context import build_weak_point_hints
    from ironlog.models.library import Movement, MovementState

    mv = gen_db.exec(
        select(Movement).where(Movement.name == "Reverse Hyper [REV_HYPER]")
    ).one()
    legacy = MovementState(
        movement_id=mv.id,
        consecutive_failed_progressions=3,
    )
    gen_db.add(legacy)
    gen_db.commit()
    gen_db.refresh(legacy)

    d2_hints = build_weak_point_hints(gen_db, "D2 Lower A")
    d5_hints = build_weak_point_hints(gen_db, "D5 Lower B")

    assert d2_hints[mv.id]["failed_count"] == 3
    assert d5_hints[mv.id]["failed_count"] == 3
    assert legacy.day_id is None


def test_should_invoke_llm_still_fires_on_record_presence(stalled_session_db):
    """should_invoke_llm fires on record *presence* — value shape change is transparent."""
    from ironlog.generation.context import resolve_context, should_invoke_llm
    from ironlog.generation.skeleton import lay_skeleton

    wk = lambda d: (d.isocalendar()[0], d.isocalendar()[1])  # noqa: E731
    sk = lay_skeleton("D1 Upper Push", stalled_session_db)
    ctx = resolve_context("D1 Upper Push", sk, stalled_session_db, wk)
    assert should_invoke_llm(sk, ctx) is True
