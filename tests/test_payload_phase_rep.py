"""Task 5: phase_intent + per-slot rep_scheme in build_context_payload."""
import pytest


def test_payload_has_phase_intent_and_slot_rep_scheme(gen_db):
    from ironlog.generation.skeleton import lay_skeleton
    from ironlog.generation.context import resolve_context, build_context_payload
    sk = lay_skeleton("D1 Upper Push", gen_db)
    ctx = resolve_context("D1 Upper Push", sk, gen_db, lambda d: (d.isocalendar()[0], d.isocalendar()[1]))
    p = build_context_payload(ctx, sk)
    assert set(p["phase_intent"]) == {"objective", "rpe_band", "volume_posture"}
    assert len(p["phase_intent"]["rpe_band"]) == 2
    assert all("rep_scheme" in s for s in p["slots"])
