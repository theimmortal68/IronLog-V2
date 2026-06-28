"""proposer.py — the selections-only contract (Fork 1) + the proposer port (Fork 2)."""
from dataclasses import dataclass, field
from typing import List, Optional, Protocol, runtime_checkable

# The §6 response_json_schema. Gemini structured output is constrained to this
# shape (Task 11), so "no numeric prescription fields" holds at the API boundary.
SELECTIONS_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "ordering": {"type": "array", "items": {"type": "string"}},
        "slots": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "slot_id": {"type": "string"},
                    "movement_id": {"type": "integer"},
                    "variant": {"type": "string", "nullable": True},
                    "technique_tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["slot_id", "movement_id"],
            },
        },
        "rationale": {"type": "string"},
    },
    "required": ["ordering", "slots", "rationale"],
}

@dataclass
class SlotSelection:
    slot_id: str
    movement_id: int
    variant: Optional[str] = None
    technique_tags: List[str] = field(default_factory=list)

@dataclass
class Selections:
    ordering: List[str]
    slots: List[SlotSelection]
    rationale: str

def selections_from_dict(d: dict) -> Selections:
    return Selections(
        ordering=list(d.get("ordering", [])),
        slots=[SlotSelection(
            slot_id=s["slot_id"], movement_id=int(s["movement_id"]),
            variant=s.get("variant"), technique_tags=list(s.get("technique_tags", [])),
        ) for s in d.get("slots", [])],
        rationale=d.get("rationale", ""),
    )

@runtime_checkable
class Proposer(Protocol):
    def propose(self, payload: dict) -> Selections: ...

class StubProposer:
    """Deterministic test/offline proposer: returns canned selections verbatim."""
    def __init__(self, canned: Selections):
        self._canned = canned
    def propose(self, payload: dict) -> Selections:
        return self._canned
