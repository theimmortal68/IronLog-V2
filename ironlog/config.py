"""Application-wide configuration helpers.

NO from __future__ import annotations (project-wide constraint).
"""
from datetime import datetime
from zoneinfo import ZoneInfo


TRAINING_TIMEZONE = "America/New_York"


def local_today():
    """Return today's date on the training calendar."""
    return datetime.now(ZoneInfo(TRAINING_TIMEZONE)).date()
