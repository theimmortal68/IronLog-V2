from urllib.parse import urlencode
from typing import Optional

import httpx


WITHINGS_AUTHORIZE_URL = "https://account.withings.com/oauth2_user/authorize2"
WITHINGS_TOKEN_URL = "https://wbsapi.withings.net/v2/oauth2"
_pending_oauth_state: Optional[str] = None


def set_pending_state(state: str) -> None:
    global _pending_oauth_state
    _pending_oauth_state = state


def consume_pending_state(state: str) -> bool:
    """Returns True and clears the stored state iff it matches. Always
    clears on a successful match (one-time use, prevents replay)."""
    global _pending_oauth_state
    if _pending_oauth_state is not None and _pending_oauth_state == state:
        _pending_oauth_state = None
        return True
    return False


def build_authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    """Constructs the Withings OAuth2 authorization URL, scope=user.metrics."""
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "scope": "user.metrics",
        }
    )
    return f"{WITHINGS_AUTHORIZE_URL}?{query}"


async def exchange_code_for_tokens(
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
) -> dict:
    """POSTs action=requesttoken to WITHINGS_TOKEN_URL."""
    return await _request_tokens(
        {
            "action": "requesttoken",
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
    )


async def refresh_access_token(
    client_id: str,
    client_secret: str,
    refresh_token: str,
) -> dict:
    """POSTs action=requesttoken with grant_type=refresh_token."""
    return await _request_tokens(
        {
            "action": "requesttoken",
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
    )


async def _request_tokens(data: dict) -> dict:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(WITHINGS_TOKEN_URL, data=data)
    if response.status_code >= 400:
        raise RuntimeError(
            f"Withings token request failed with HTTP {response.status_code}"
        )
    return _extract_token_body(response.json())


def _extract_token_body(payload: dict) -> dict:
    status = payload.get("status")
    if status != 0:
        message = _withings_error_message(payload)
        if message:
            raise RuntimeError(
                f"Withings token request failed with status {status}: {message}"
            )
        raise RuntimeError(f"Withings token request failed with status {status}")

    body = payload.get("body")
    if not isinstance(body, dict):
        raise RuntimeError("Withings token response missing body")

    required = ("access_token", "refresh_token", "expires_in")
    missing = [key for key in required if key not in body]
    if missing:
        raise RuntimeError(
            "Withings token response missing required field(s): "
            + ", ".join(missing)
        )
    return {key: body[key] for key in required}


def _withings_error_message(payload: dict) -> str:
    body = payload.get("body")
    if isinstance(body, dict):
        for key in ("error_description", "error", "message"):
            if body.get(key):
                return str(body[key])
    for key in ("error_description", "error", "message"):
        if payload.get(key):
            return str(payload[key])
    return ""
