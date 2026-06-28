"""Tests for GenerationLog model (Task 8 — Fork 7d provenance table)."""
from ironlog.models import GenerationLog


def test_generation_log_fields():
    g = GenerationLog(session_id=1, prompt_json={"k": 1}, selections_json={"slots": []},
                      clamps_json=[], repairs_json=[], approval_mode="auto",
                      fallback_used=False)
    assert g.approval_mode == "auto" and g.fallback_used is False
