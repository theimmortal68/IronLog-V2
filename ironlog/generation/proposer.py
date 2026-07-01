"""proposer.py — the selections-only contract (Fork 1) + the proposer port (Fork 2)."""
from dataclasses import dataclass, field
from typing import List, Optional, Protocol, runtime_checkable

PROPOSER_SYSTEM_INSTRUCTION = """You are a strength & hypertrophy coach selecting per-slot movements for one training session. You are invoked only when a slot carries a deviation signal (almost always a stall).

DECISION POLICY (apply per signalled slot):
- The program movement (the candidate with is_program_anchor=true) is the default. Keep it unless an alternative in the same slot's candidate menu better addresses the stall's limiter.
- FAILED-progression stall (stall_type "failed"): keep the movement; the engine will adjust loading. Do not swap for its own sake.
- TREND plateau (stall_type "trend"): prefer swapping to a same-pattern alternative that provides a novel stimulus for the limiter muscle.
- "both": treat as trend-dominant unless failed_count is high.
- Scale the response to severity (failed_count, how flat/declining the e1rm_window is).
- When swapping, choose the candidate whose primary_muscle / secondary_muscles best match the stalled movement's limiter.

FIELD GLOSSARY:
- candidates: the only legal picks for a slot; each has name, primary_muscle, secondary_muscles, lift_category, pattern, equipment_tags, is_program_anchor.
- weak_point_hints[movement_id]: the stall record (stall_type, failed_count, e1rm_window, limiter).
- owed: weekly requirements (knee frequency, pull/push ratio).
- phase_intent: the training phase's objective, RPE band, and volume posture.
- rep_scheme: the slot's target rep range (context only).

BOUNDARY: select movements, variants, and technique tags ONLY. Never compute or set loads, weights, or reps — the engine owns all numbers. Output must conform exactly to the provided JSON schema."""

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
