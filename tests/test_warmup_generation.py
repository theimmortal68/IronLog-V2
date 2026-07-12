"""
test_warmup_generation.py — static per-day warmup seed/payload gate.

NO from __future__ import annotations (project-wide constraint).
"""
from pathlib import Path

import yaml
from sqlmodel import select

from ironlog.generation.assembler import build_warmup_payload
from ironlog.generation.live_seed_warmup import apply as apply_live_warmups
from ironlog.models.program import ProgramDay


WARMUP_DAY_KEYS = {
    1: "d1",
    2: "d2",
    4: "d4",
    5: "d5",
    6: "d6",
}


def _expected_warmups():
    path = Path(__file__).resolve().parents[1] / "docs/program/phase1-warmup-finisher-source.yaml"
    source = yaml.safe_load(path.read_text())
    return {
        day_index: source[yaml_key]["warmup"]
        for day_index, yaml_key in WARMUP_DAY_KEYS.items()
    }


def _days_by_index(db):
    return {
        day.day_index: day
        for day in db.exec(select(ProgramDay)).all()
    }


def test_build_warmup_payload_matches_phase1_source_yaml(gen_db):
    days = _days_by_index(gen_db)
    expected = _expected_warmups()

    for day_index, warmup in expected.items():
        assert build_warmup_payload(gen_db, days[day_index].id) == warmup


def test_build_warmup_payload_returns_none_for_rest_days(gen_db):
    days = _days_by_index(gen_db)

    assert build_warmup_payload(gen_db, days[3].id) is None
    assert build_warmup_payload(gen_db, days[7].id) is None


def test_live_seed_warmup_backfills_only_null_configs(gen_db):
    days = _days_by_index(gen_db)
    expected = _expected_warmups()
    already_set = {"already": "set"}

    days[1].warmup_config = already_set
    days[2].warmup_config = None
    gen_db.add(days[1])
    gen_db.add(days[2])
    gen_db.commit()

    apply_live_warmups(gen_db)
    gen_db.refresh(days[1])
    gen_db.refresh(days[2])

    assert days[1].warmup_config == already_set
    assert days[2].warmup_config == expected[2]
