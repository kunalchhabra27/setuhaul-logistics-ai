"""Amazon Bedrock AgentCore adapter for the existing SetuHaul Driver Chat."""

from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.security import HTTPAuthorizationCredentials

from setuhaul.backend.driver_chat_eta.api import get_service
from setuhaul.backend.driver_chat_eta.auth import get_current_driver
from setuhaul.infrastructure.logging import configure_logging
from setuhaul.infrastructure.telemetry import initialize_telemetry, observe_operation

load_dotenv()
configure_logging(os.getenv("LOG_LEVEL", "INFO"))

# Reuse SetuHaul's telemetry setup rather than introducing AgentCore-specific
# tracing. The FastAPI host is only the initialization target for that helper.
_telemetry_host = FastAPI()
initialize_telemetry(_telemetry_host)

app = BedrockAgentCoreApp()
log = app.logger

_REFRESH_WINDOW_SECONDS = 90


@dataclass
class _DriverSession:
    access_token: str
    refresh_token: str
    expires_at: float


_session: _DriverSession | None = None


def _jwt_expiration(token: str) -> float | None:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        exp = json.loads(base64.urlsafe_b64decode(payload)).get("exp")
        return float(exp) if exp is not None else None
    except (IndexError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _driver_credentials() -> tuple[str, str] | None:
    email = os.getenv("LOAD_TEST_DRIVER_EMAIL") or os.getenv("LOAD_TEST_EMAIL")
    password = os.getenv("LOAD_TEST_DRIVER_PASSWORD") or os.getenv("LOAD_TEST_PASSWORD")
    return (email, password) if email and password else None


def _supabase_token_request(payload: dict[str, str], *, action: str) -> _DriverSession:
    url = os.getenv("SUPABASE_URL", "").rstrip("/")
    publishable_key = os.getenv("SUPABASE_PUBLISHABLE_KEY", "")
    if not url or not publishable_key:
        raise ValueError(
            "Automatic driver token renewal requires SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY."
        )
    grant_type = "password" if action == "login" else "refresh_token"
    response = httpx.post(
        f"{url}/auth/v1/token?grant_type={grant_type}",
        headers={"apikey": publishable_key, "Content-Type": "application/json"},
        json=payload,
        timeout=10.0,
    )
    if not response.is_success:
        raise ValueError(f"Supabase driver token {action} failed with HTTP {response.status_code}.")
    body = response.json()
    access_token = body.get("access_token")
    refresh_token = body.get("refresh_token")
    if not access_token or not refresh_token:
        raise ValueError(f"Supabase driver token {action} response was missing token fields.")
    expires_at = body.get("expires_at") or time.time() + float(body.get("expires_in", 3600))
    return _DriverSession(access_token=access_token, refresh_token=refresh_token, expires_at=float(expires_at))


def _access_token() -> str:
    """Return a valid driver bearer token, renewing it automatically when possible.

    Falls back to a static TEST_DRIVER_ACCESS_TOKEN / TEST_ACCESS_TOKEN when no
    LOAD_TEST_DRIVER_* (or LOAD_TEST_*) credentials are configured, preserving
    the original manual-token behavior for setups that don't need renewal.
    """
    global _session

    credentials = _driver_credentials()
    if credentials is None:
        token = os.getenv("TEST_DRIVER_ACCESS_TOKEN") or os.getenv("TEST_ACCESS_TOKEN")
        if not token:
            raise ValueError(
                "A driver bearer token is required in TEST_DRIVER_ACCESS_TOKEN or TEST_ACCESS_TOKEN, "
                "or set LOAD_TEST_DRIVER_EMAIL/LOAD_TEST_DRIVER_PASSWORD for automatic renewal."
            )
        return token

    if _session is not None and _session.expires_at > time.time() + _REFRESH_WINDOW_SECONDS:
        return _session.access_token

    if _session is not None:
        try:
            _session = _supabase_token_request({"refresh_token": _session.refresh_token}, action="refresh")
            return _session.access_token
        except ValueError:
            log.warning("Driver token refresh failed; retrying with a fresh login.")

    email, password = credentials
    _session = _supabase_token_request({"email": email, "password": password}, action="login")
    return _session.access_token


def _run_existing_driver_chat(prompt: str):
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=_access_token())
    principal = get_current_driver(credentials)
    service = get_service(principal)
    return observe_operation(
        "driver_chat.request",
        {"operation": "chat_request"},
        lambda: service.handle_chat_message(principal, prompt),
    )


@app.entrypoint
def invoke(payload: dict[str, Any], context: Any) -> dict[str, Any]:
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("The AgentCore payload must include a non-empty prompt.")

    response = _run_existing_driver_chat(prompt.strip())
    shipment = response.snapshot.shipment
    log.info("SetuHaul Driver Chat request completed through AgentCore.")
    return {
        "result": response.agent_message.message_text or "",
        "shipment_id": shipment.shipment_id if shipment else None,
        "thread_id": response.agent_message.thread_id,
    }


if __name__ == "__main__":
    app.run()
