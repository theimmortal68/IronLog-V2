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
    "Also classify the action itself into EXACTLY ONE action_type:\n"
    "- SWAP: replace the movement with a different one (e.g. 'switch flat bench to incline').\n"
    "- LOAD_INCREASE: the current load is too light and should go up (e.g. 'bump belt squat +10').\n"
    "- LOAD_DECREASE: the current load is too heavy and should come down (e.g. 'drop OHP weight').\n"
    "- REP_CHANGE: a different rep target/scheme is requested (e.g. 'drop OHP to 3x8').\n"
    "- REORDER: change where this movement falls in the day's exercise sequence relative to "
    "OTHER named movements (e.g. 'move knee raises between meadows rows and single arm rows').\n"
    "- OTHER: anything else, including any note that is not a CONFIG_CHANGE.\n"
    "proposed_change.movement stays the extracted subject movement regardless of action_type. "
    "For REORDER, extract proposed_change.before_movement and proposed_change.after_movement "
    "as the named neighboring movements: after_movement is the movement the subject should "
    "come after, before_movement is the movement the subject should come before. Use null "
    "for either if the note only specifies one neighbor; use null for both for non-REORDER actions. "
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
                "before_movement": {"type": ["string", "null"]},
                "after_movement": {"type": ["string", "null"]},
            },
        },
        "action_type": {
            "type": "string",
            "enum": ["SWAP", "LOAD_INCREASE", "LOAD_DECREASE", "REP_CHANGE", "REORDER", "OTHER"],
        },
        "confidence": {"type": "number"},
        "rationale": {"type": "string"},
    },
    "required": ["classification", "action_type", "confidence", "rationale"],
}

_ACTION_TYPES = {"SWAP", "LOAD_INCREASE", "LOAD_DECREASE", "REP_CHANGE", "REORDER", "OTHER"}


@dataclass
class NoteClassification:
    classification: NoteClass
    proposed_change: Optional[dict]
    confidence: float
    rationale: str
    action_type: str = "OTHER"


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
        action_type = obj.get("action_type")
        if action_type not in _ACTION_TYPES:
            action_type = "OTHER"
        return NoteClassification(
            classification=cls,
            proposed_change=obj.get("proposed_change"),
            confidence=float(obj.get("confidence", 0.0)),
            rationale=obj.get("rationale", ""),
            action_type=action_type,
        )


def classify_session_notes(session_id: int, classifier=None) -> None:
    """Background: classify every Note in a session via Gemini and persist the result.
    Opens its OWN DB session (the request session is closed post-response). Degrades to
    leaving a note as JOURNAL on any per-note failure or a missing key; never raises."""
    from sqlmodel import Session, select  # noqa: PLC0415
    from ..db import engine  # noqa: PLC0415
    from ..models.session import Note  # noqa: PLC0415

    if classifier is None:
        try:
            classifier = NoteClassifier()
        except ValueError:
            return  # no GEMINI_API_KEY -> leave notes JOURNAL

    with Session(engine) as db:
        notes = db.exec(select(Note).where(Note.session_id == session_id)).all()
        for note in notes:
            try:
                result = classifier.classify(note.text)
            except Exception:
                continue  # degrade: leave this note as-is (JOURNAL default)
            note.classification = result.classification
            note.classification_meta = {
                "proposed_change": result.proposed_change,
                "confidence": result.confidence,
                "rationale": result.rationale,
                "action_type": result.action_type,
            }
            db.add(note)
        db.commit()
