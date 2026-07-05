"""gemini.py — GeminiProposer: runtime Gemini adapter implementing the Proposer port.

Uses Gemini's structured-output feature (responseMimeType + responseJsonSchema) to
enforce the Fork-1 selections-only shape at the API boundary (NAMED GATE a).

httpx is imported lazily — only when constructing the default real client — so the
module imports and mocked tests run without httpx installed.
"""
import json
import os
from typing import Optional

from ironlog.generation.proposer import (
    PROPOSER_SYSTEM_INSTRUCTION,
    SELECTIONS_JSON_SCHEMA,
    Selections,
    selections_from_dict,
)

_GEMINI_V1BETA = "https://generativelanguage.googleapis.com/v1beta/models"
_REQUIRED_KEYS = {"ordering", "slots", "rationale"}


class ProposerError(Exception):
    """Raised when the Gemini response does not conform to the selections contract."""


def _default_http_client():
    """Construct a real httpx.Client. Imported lazily so tests don't need httpx.

    timeout=60.0 accommodates dynamic thinking responses (~7s typical, up to ~45s
    worst-case) while still bounding runaway calls.  The default httpx 5s timeout
    caused systematic ReadTimeout with thinkingBudget=-1.
    """
    import httpx  # noqa: PLC0415
    return httpx.Client(timeout=60.0)


def gemini_generate_json(api_key, model, system_instruction, user_text, response_schema, http) -> dict:
    """POST a structured-output request to Gemini and return the parsed JSON object.
    Raises ProposerError on unexpected response structure or non-JSON text."""
    url = f"{_GEMINI_V1BETA}/{model}:generateContent"
    body = {
        "systemInstruction": {"parts": [{"text": system_instruction}]},
        "contents": [{"role": "user", "parts": [{"text": user_text}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseJsonSchema": response_schema,
            "thinkingConfig": {"thinkingBudget": -1},
        },
    }
    headers = {"x-goog-api-key": api_key}
    resp = http.post(url, json=body, headers=headers)
    resp.raise_for_status()
    raw = resp.json()
    try:
        text = raw["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProposerError(f"Unexpected Gemini response structure: {exc!r}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProposerError(f"Gemini returned non-JSON text: {exc!r}") from exc


class GeminiProposer:
    """Live Gemini proposer adapter.

    Parameters
    ----------
    api_key:
        Gemini API key.  If omitted, read from the ``GEMINI_API_KEY`` env var.
    model:
        Gemini model name (default ``gemini-3.1-flash-lite``).
    http:
        Injectable HTTP client for testing.  Must expose ``.post(url, **kw)``.
        Defaults to a real ``httpx.Client`` constructed lazily on first use.
    """

    def __init__(self, api_key: Optional[str] = None, model: str = "gemini-3.1-flash-lite", http=None):
        if api_key is None:
            api_key = os.environ.get("GEMINI_API_KEY")
            if not api_key:
                raise ValueError(
                    "api_key not provided and GEMINI_API_KEY env var is not set"
                )
        self._api_key = api_key
        self._model = model
        self._http = http  # None → lazy-constructed on first propose()

    def propose(self, payload: dict) -> Selections:
        """POST *payload* to Gemini and return parsed ``Selections``."""
        # Cache the lazily-constructed default client so repeated proposes reuse one
        # httpx.Client (the lazy import keeps mocked/injected-client tests httpx-free).
        if self._http is None:
            self._http = _default_http_client()
        obj = gemini_generate_json(
            self._api_key, self._model, PROPOSER_SYSTEM_INSTRUCTION,
            json.dumps(payload), SELECTIONS_JSON_SCHEMA, self._http)
        missing = _REQUIRED_KEYS - obj.keys()
        if missing:
            raise ProposerError(f"Gemini response missing required keys: {missing!r}")
        return selections_from_dict(obj)
