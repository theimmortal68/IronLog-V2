"""test_stall_record.py — Task 4: typed/severity/limiter stall record (gap D).

Tests verify:
- build_weak_point_hints returns the new record shape (stall_type, failed_count,
  e1rm_window, limiter) instead of the old generic hint string.
- should_invoke_llm still fires on the *presence* of a record for a stalled movement
  (checks mid in ctx.weak_point_hints — key lookup unchanged by value-shape change).

Fixture: stalled_session_db (conftest.py) — consecutive_failed_progressions=2 on
Pendlay Row - Narrow [OB] (D1 d1_t2a, semi) → detect_stall fires → movement
in weak_point_hints → slot_has_deviation_signal True → should_invoke_llm True.

NO from __future__ import annotations (project-wide constraint).
"""


def test_failed_stall_record_shape(stalled_session_db):
    """build_weak_point_hints returns the typed record dict, not a string."""
    from ironlog.generation.context import build_weak_point_hints

    rec = build_weak_point_hints(stalled_session_db)
    assert rec, "expected at least one stalled movement"
    mid, r = next(iter(rec.items()))
    assert r["stall_type"] in ("failed", "trend", "both")
    assert isinstance(r["failed_count"], int)
    assert set(r["e1rm_window"]) == {"sessions", "peak", "latest"}
    assert set(r["limiter"]) == {"primary_muscle", "secondary_muscles"}


def test_should_invoke_llm_still_fires_on_record_presence(stalled_session_db):
    """should_invoke_llm fires on record *presence* — value shape change is transparent."""
    from ironlog.generation.context import resolve_context, should_invoke_llm
    from ironlog.generation.skeleton import lay_skeleton

    wk = lambda d: (d.isocalendar()[0], d.isocalendar()[1])  # noqa: E731
    sk = lay_skeleton("D1 Upper Push", stalled_session_db)
    ctx = resolve_context("D1 Upper Push", sk, stalled_session_db, wk)
    assert should_invoke_llm(sk, ctx) is True
