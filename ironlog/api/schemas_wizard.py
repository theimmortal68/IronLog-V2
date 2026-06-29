"""Wizard-layer API contract (the server<->client crossing artifact — locked).

These shapes are mirrored field-for-field (snake_case) by the Android client's
Kotlin DTOs, same discipline as the capture contract. The wizard read surface is
just compute_load_trust rendered per program movement (spec §5).

NO from __future__ import annotations (project-wide constraint).
"""
from typing import List, Optional

from pydantic import BaseModel


class WizardMovement(BaseModel):
    movement_id: int
    movement_name: str
    load_field: str                      # "current_load" | "assist_level"
    trust: str                           # "UNKNOWN" | "STALE" | "FRESH"
    prefill_value: Optional[float] = None  # current value for STALE/FRESH; null for UNKNOWN
    unit_hint: Optional[str] = None        # "lb" (current_load) | "assist" (assist_level)


class WizardStateResponse(BaseModel):
    program_id: int
    program_name: str
    needs_attention_count: int           # UNKNOWN + STALE (the "N left")
    ready_to_start: bool                 # needs_attention_count == 0
    movements: List[WizardMovement]      # program movements that NEED a load (bodyweight excluded)
