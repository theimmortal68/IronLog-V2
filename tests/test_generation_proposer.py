from ironlog.generation.proposer import (
    SELECTIONS_JSON_SCHEMA, Selections, SlotSelection, StubProposer,
    selections_from_dict,
)

def _props(schema):
    return schema["properties"]

def test_schema_has_no_numeric_prescription_fields():
    # Fork 1: the only integer is movement_id (a reference). No load/rep/rpe.
    slot_props = _props(SELECTIONS_JSON_SCHEMA)["slots"]["items"]["properties"]
    assert set(slot_props) == {"slot_id", "movement_id", "variant", "technique_tags"}
    for banned in ("target_load", "load", "reps", "rpe", "scheme"):
        assert banned not in slot_props
    assert slot_props["movement_id"]["type"] == "integer"

def test_stub_returns_canned_selections():
    canned = Selections(ordering=["s1"],
                        slots=[SlotSelection("s1", 42, None, [])],
                        rationale="ok")
    assert StubProposer(canned).propose({"anything": True}) is canned

def test_selections_from_dict_roundtrips():
    d = {"ordering": ["s1"],
         "slots": [{"slot_id": "s1", "movement_id": 42,
                    "variant": None, "technique_tags": ["myo"]}],
         "rationale": "r"}
    sel = selections_from_dict(d)
    assert sel.slots[0].movement_id == 42 and sel.slots[0].technique_tags == ["myo"]
