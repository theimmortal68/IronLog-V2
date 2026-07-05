"""classify.py — NoteClassifier: Gemini adapter that classifies an in-gym note
into the 4-way NoteClass + extracts a proposed change. Reuses gemini_generate_json.

NO from __future__ import annotations (project-wide constraint).
"""
import os
from dataclasses import dataclass
from typing import Optional

from ..generation.gemini import (
    ProposerError, _default_http_client, gemini_generate_json,
)
from ..models.enums import NoteClass

NOTE_SYSTEM_INSTRUCTION = (
    "You classify a strength-training athlete's in-gym note into EXACTLY ONE class:\n"
    "- CONFIG_CHANGE: proposes a specific, actionable change to a movement/load/scheme "
    "(e.g. 'switch flat bench to incline', 'drop OHP to 3x8', 'bump belt squat +10').\n"
    "- PROGRAMMING_REQUEST: a request about programming direction, not a single specific "
    "change (e.g. 'can we add more back work').\n"
    "- TRANSIENT_FLAG: a passing physical/readiness state (e.g. 'shoulder tweaked today').\n"
    "- JOURNAL: a log/observation with no request (e.g. 'felt strong').\n"
    "For CONFIG_CHANGE, extract the proposed change (movement, action, params). "
    "Return confidence 0..1 and a one-line rationale."
)

NOTE_CLASSIFICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "classification": {
            "type": "string",
            "enum": ["CONFIG_CHANGE", "PROGRAMMING_REQUEST", "TRANSIENT_FLAG", "JOURNAL"],
        },
        "proposed_change": {
            "type": ["object", "null"],
            "properties": {
                "movement": {"type": ["string", "null"]},
                "action": {"type": ["string", "null"]},
                "params": {"type": ["string", "null"]},
            },
        },
        "confidence": {"type": "number"},
        "rationale": {"type": "string"},
    },
    "required": ["classification", "confidence", "rationale"],
}


@dataclass
class NoteClassification:
    classification: NoteClass
    proposed_change: Optional[dict]
    confidence: float
    rationale: str


class NoteClassifier:
    def __init__(self, api_key: Optional[str] = None, model: str = "gemini-3.1-flash-lite", http=None):
        if api_key is None:
            api_key = os.environ.get("GEMINI_API_KEY")
            if not api_key:
                raise ValueError("api_key not provided and GEMINI_API_KEY env var is not set")
        self._api_key = api_key
        self._model = model
        self._http = http

    def classify(self, text: str) -> NoteClassification:
        if self._http is None:
            self._http = _default_http_client()
        obj = gemini_generate_json(
            self._api_key, self._model, NOTE_SYSTEM_INSTRUCTION, text,
            NOTE_CLASSIFICATION_SCHEMA, self._http)
        try:
            cls = NoteClass(obj["classification"])
        except (KeyError, ValueError) as exc:
            raise ProposerError(f"bad classification value: {exc!r}") from exc
        return NoteClassification(
            classification=cls,
            proposed_change=obj.get("proposed_change"),
            confidence=float(obj.get("confidence", 0.0)),
            rationale=obj.get("rationale", ""),
        )
