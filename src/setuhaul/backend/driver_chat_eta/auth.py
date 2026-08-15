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
from setuhaul.infrastructure import auth_session
from setuhaul.infrastructure.settings import get_settings
from setuhaul.infrastructure.supabase_client import create_public_client

bearer_scheme = HTTPBearer(auto_error=False)

PORTAL = "driver"


@dataclass(frozen=True)
class DriverPrincipal:
    """A verified Supabase caller, scoped to the driver portal."""

    user_id: str
    email: str | None
    access_token: str


def get_current_driver(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> DriverPrincipal:
    """Verify a bearer token, preferring a cached Redis session over a live
    Supabase Auth call -- see ``infrastructure.auth_session`` for the design.
    A cache miss (first request, expired, revoked, or Redis unreachable)
    always falls through to the same full verification as before.

    Note: the cached session never stores ``email`` (not needed to
    reconstruct authorization for this portal -- any confirmed user passes
    regardless), so a cache hit returns ``email=None``. Call sites that
    actually need the driver's email (e.g. profile display) already read it
    from the driver's own Supabase row via ``get_my_profile``, not from this
    principal, so this is not a behavior change for them.
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthenticationError("A valid bearer token is required.")

    settings = get_settings()
    cached = auth_session.get_cached_session(credentials.credentials, settings)
    if cached is not None and cached.portal == PORTAL:
        return DriverPrincipal(user_id=cached.user_id, email=None, access_token=credentials.credentials)

    try:
        response = create_public_client(settings).auth.get_user(credentials.credentials)
        user = response.user
    except Exception as exc:  # noqa: BLE001 - Supabase raises its own error types
        raise AuthenticationError("The bearer token is invalid or expired.") from exc

    if user is None:
        raise AuthenticationError("The bearer token is invalid or expired.")

    auth_session.create_session(credentials.credentials, str(user.id), PORTAL, None, settings)
    return DriverPrincipal(user_id=str(user.id), email=user.email, access_token=credentials.credentials)
