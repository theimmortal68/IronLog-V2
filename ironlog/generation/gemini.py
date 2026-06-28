"""gemini.py — GeminiProposer: runtime Gemini adapter implementing the Proposer port.

Uses Gemini's structured-output feature (responseMimeType + responseJsonSchema) to
enforce the Fork-1 selections-only shape at the API boundary (NAMED GATE a).

httpx is imported lazily — only when constructing the default real client — so the
module imports and mocked tests run without httpx installed.
"""
import json
import os

from ironlog.generation.proposer import (
    SELECTIONS_JSON_SCHEMA,
    Selections,
    selections_from_dict,
)

_GEMINI_V1BETA = "https://generativelanguage.googleapis.com/v1beta/models"
_REQUIRED_KEYS = {"ordering", "slots", "rationale"}


class ProposerError(Exception):
    """Raised when the Gemini response does not conform to the selections contract."""


def _default_http_client():
    """Construct a real httpx.Client. Imported lazily so tests don't need httpx."""
    import httpx  # noqa: PLC0415
    return httpx.Client()


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

    def __init__(self, api_key: str = None, model: str = "gemini-3.1-flash-lite", http=None):
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
        http = self._http if self._http is not None else _default_http_client()

        url = f"{_GEMINI_V1BETA}/{self._model}:generateContent"
        body = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": json.dumps(payload)}],
                }
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseJsonSchema": SELECTIONS_JSON_SCHEMA,
                "thinkingConfig": {"thinkingBudget": 0},
            },
        }
        headers = {"x-goog-api-key": self._api_key}

        resp = http.post(url, json=body, headers=headers)
        resp.raise_for_status()
        raw = resp.json()

        return self._parse(raw)

    def _parse(self, raw: dict) -> Selections:
        try:
            text = raw["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProposerError(
                f"Unexpected Gemini response structure: {exc!r}"
            ) from exc

        try:
            obj = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProposerError(
                f"Gemini returned non-JSON text: {exc!r}"
            ) from exc

        missing = _REQUIRED_KEYS - obj.keys()
        if missing:
            raise ProposerError(
                f"Gemini response missing required keys: {missing!r}"
            )

        return selections_from_dict(obj)
