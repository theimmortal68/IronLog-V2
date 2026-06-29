"""Tests for compute_load_trust — the shared load-trustworthiness keystone.

Covers: per-mode load field, value resolution (present / derived-ratio / none),
IS-NULL-not-zero presence guard, recency via max(working SetLog, confirmed_at),
bodyweight always-fresh, and naive/aware datetime comparability.
"""
import pytest
from datetime import datetime, timedelta, timezone

from sqlmodel import Session as DbSession, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from ironlog.generation.load_trust import compute_load_trust, LoadTrust, load_field_for_mode
from ironlog.models.library import Movement, MovementState
from ironlog.models.session import SetLog
from ironlog.models.enums import ProgressionMode, FeedbackTap
import ironlog.models  # noqa: F401 — ensure all tables are registered

NOW = datetime(2026, 7, 1, tzinfo=timezone.utc)


def _db():
    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(e)
    return e


def _mv(s, **kw):
    m = Movement(name=kw.pop("name", "M"), base_name="M", progression_mode=kw.pop("mode", ProgressionMode.LADDER), **kw)
    s.add(m)
    s.commit()
    s.refresh(m)
    return m


def test_load_field_for_mode():
    assert load_field_for_mode(ProgressionMode.LADDER) == "current_load"
    assert load_field_for_mode(ProgressionMode.COMPOSITE) == "current_load"
    assert load_field_for_mode(ProgressionMode.ASSISTED) == "assist_level"
    assert load_field_for_mode(ProgressionMode.PROTOCOL) is None
    assert load_field_for_mode(ProgressionMode.CONDITIONING) is None
    assert load_field_for_mode(ProgressionMode.NONE) is None


def test_ladder_no_current_load_is_unknown():
    e = _db()
    with DbSession(e) as s:
        m = _mv(s)
        r = compute_load_trust(m, None, s, NOW)
        assert r.trust == LoadTrust.UNKNOWN and r.value is None and r.load_field == "current_load"


def test_ladder_present_recent_is_fresh():
    e = _db()
    with DbSession(e) as s:
        m = _mv(s)
        st = MovementState(movement_id=m.id, current_load=205.0, confirmed_at=NOW - timedelta(days=5))
        s.add(st)
        s.commit()
        r = compute_load_trust(m, st, s, NOW)
        assert r.trust == LoadTrust.FRESH and r.value == 205.0


def test_ladder_present_old_is_stale():
    e = _db()
    with DbSession(e) as s:
        m = _mv(s)
        st = MovementState(movement_id=m.id, current_load=205.0, confirmed_at=NOW - timedelta(days=40))
        s.add(st)
        s.commit()
        assert compute_load_trust(m, st, s, NOW).trust == LoadTrust.STALE


def test_recency_uses_last_working_setlog_not_just_confirmed():
    e = _db()
    with DbSession(e) as s:
        m = _mv(s)
        st = MovementState(movement_id=m.id, current_load=205.0, confirmed_at=NOW - timedelta(days=40))
        s.add(st)
        s.commit()
        s.add(SetLog(session_id=1, movement_id=m.id, set_index=0, is_warmup=False,
                     feedback_tap=FeedbackTap.ON_TARGET, performed_at=NOW - timedelta(days=3)))
        s.commit()
        # logged 3d ago → fresh despite confirmed 40d ago (max of the two)
        assert compute_load_trust(m, st, s, NOW).trust == LoadTrust.FRESH


def test_warmup_setlog_does_not_count_for_recency():
    e = _db()
    with DbSession(e) as s:
        m = _mv(s)
        st = MovementState(movement_id=m.id, current_load=205.0, confirmed_at=NOW - timedelta(days=40))
        s.add(st)
        s.commit()
        # only a warmup set logged recently — must NOT refresh (working sets only)
        s.add(SetLog(session_id=1, movement_id=m.id, set_index=0, is_warmup=True,
                     performed_at=NOW - timedelta(days=2)))
        s.commit()
        assert compute_load_trust(m, st, s, NOW).trust == LoadTrust.STALE


def test_bodyweight_protocol_always_fresh_never_calibration():
    e = _db()
    with DbSession(e) as s:
        m = _mv(s, mode=ProgressionMode.PROTOCOL)
        r = compute_load_trust(m, None, s, NOW)
        assert r.trust == LoadTrust.FRESH and r.load_field is None   # no load to set, never blocks


def test_assisted_null_is_unknown_but_zero_is_fresh():
    e = _db()
    with DbSession(e) as s:
        m = _mv(s, mode=ProgressionMode.ASSISTED)
        # assist_level IS NULL → unknown
        st_null = MovementState(movement_id=m.id, assist_level=None, confirmed_at=NOW)
        s.add(st_null)
        s.commit()
        assert compute_load_trust(m, st_null, s, NOW).trust == LoadTrust.UNKNOWN
        # assist_level == 0 (unassisted) → VALID fresh, NOT unknown
        st_null.assist_level = 0.0
        s.add(st_null)
        s.commit()
        r = compute_load_trust(m, st_null, s, NOW)
        assert r.trust == LoadTrust.FRESH and r.value == 0.0 and r.load_field == "assist_level"


def test_derived_ratio_resolves_value_not_unknown():
    e = _db()
    with DbSession(e) as s:
        anchor = _mv(s, name="Anchor")
        anchor_st = MovementState(movement_id=anchor.id, e1rm=300.0, confirmed_at=NOW - timedelta(days=5))
        s.add(anchor_st)
        s.commit()
        derived = _mv(s, name="Derived", start_ratio=0.8, derived_from_id=anchor.id)
        # derived has its own state but NO current_load — must resolve via anchor e1rm
        d_st = MovementState(movement_id=derived.id, current_load=None, confirmed_at=NOW - timedelta(days=5))
        s.add(d_st)
        s.commit()
        r = compute_load_trust(derived, d_st, s, NOW)
        assert r.value == pytest.approx(0.8 * 300.0)
        assert r.trust == LoadTrust.FRESH
        assert r.load_field == "current_load"


def test_derived_ratio_resolves_value_with_no_own_state():
    e = _db()
    with DbSession(e) as s:
        anchor = _mv(s, name="Anchor2")
        anchor_st = MovementState(movement_id=anchor.id, e1rm=300.0, confirmed_at=NOW - timedelta(days=5))
        s.add(anchor_st)
        s.commit()
        derived = _mv(s, name="Derived2", start_ratio=0.8, derived_from_id=anchor.id)
        # no own state at all — value still resolves (not UNKNOWN), but recency is None → STALE
        r = compute_load_trust(derived, None, s, NOW)
        assert r.value == pytest.approx(0.8 * 300.0)
        assert r.trust == LoadTrust.STALE  # value present but no recency anchor


def test_naive_stored_datetimes_are_comparable_with_aware_as_of():
    e = _db()
    with DbSession(e) as s:
        m = _mv(s)
        # project default is naive utcnow — store a naive confirmed_at
        st = MovementState(movement_id=m.id, current_load=205.0,
                           confirmed_at=datetime.utcnow() - timedelta(days=5))
        s.add(st)
        s.commit()
        # as_of is tz-aware — subtraction must not raise naive-vs-aware
        r = compute_load_trust(m, st, s, datetime.now(timezone.utc))
        assert r.trust == LoadTrust.FRESH
