"""Task 1: progression-engine schema gate.

Confirms MovementState gains the new progression-state columns + the composite
(movement_id, day_id) unique key, and Movement gains the per-movement rule
config columns. Paired with tests/test_migrations.py::test_chain_matches_create_all
which asserts the migration chain converges with these same models.
"""
from sqlmodel import SQLModel, Session, create_engine, select

from ironlog.models.library import MovementState, Movement


def test_movementstate_has_new_progression_fields():
    ms = MovementState(movement_id=1, day_id="d2", consecutive_advance_count=0)
    for f in ("day_id", "consecutive_advance_count", "active_rule",
              "current_body_position", "stall_signal", "unassisted_max_rolling"):
        assert hasattr(ms, f), f
    mv = Movement(name="X", base_name="X")  # follow the model's real required args
    for f in ("progression_rule", "assist_ladder", "position_ladder", "rep_ladder"):
        assert hasattr(mv, f), f


def test_movementstate_composite_key_allows_same_movement_two_days():
    eng = create_engine("sqlite://")
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        s.add(MovementState(movement_id=1, day_id="d2"))
        s.add(MovementState(movement_id=1, day_id="d5"))  # same movement, different day → OK
        s.commit()
        assert len(s.exec(select(MovementState).where(MovementState.movement_id == 1)).all()) == 2
