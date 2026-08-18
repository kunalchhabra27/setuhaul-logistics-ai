"""Driver-scoped Supabase authentication for the driver chat & ETA backend.

Unlike ``setuhaul.infrastructure.auth`` (which gates TMS staff endpoints on an
``app_metadata.tms_role`` claim), any driver who has verified their email with
Supabase Auth is a valid caller here -- the driver portal has its own auth
boundary ("Driver + stakeholders" in the system architecture diagram), not the
TMS admin boundary.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from setuhaul.backend.driver_chat_eta.exceptions import AuthenticationError
from setuhaul.infrastructure.settings import get_settings
from setuhaul.infrastructure.supabase_client import create_public_client
from setuhaul.infrastructure.telemetry import operation_span

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class DriverPrincipal:
    """A verified Supabase caller, scoped to the driver portal."""

    user_id: str
    email: str | None
    access_token: str


def get_current_driver(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> DriverPrincipal:
    """Verify a bearer token with Supabase Auth. Any confirmed user may pass."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthenticationError("A valid bearer token is required.")

    try:
        with operation_span("driver_chat.authenticate", {"operation": "authenticate"}):
            response = create_public_client(get_settings()).auth.get_user(credentials.credentials)
        user = response.user
    except Exception as exc:  # noqa: BLE001 - Supabase raises its own error types
        raise AuthenticationError("The bearer token is invalid or expired.") from exc

    if user is None:
        raise AuthenticationError("The bearer token is invalid or expired.")

    return DriverPrincipal(user_id=str(user.id), email=user.email, access_token=credentials.credentials)
