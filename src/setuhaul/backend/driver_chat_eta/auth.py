"""Driver-scoped Supabase authentication for the driver chat & ETA backend.

Unlike ``setuhaul.infrastructure.auth`` (which gates TMS staff endpoints on an
``app_metadata.tms_role`` claim), any driver who has verified their email with
Supabase Auth is a valid caller here -- the driver portal has its own auth
boundary ("Driver + stakeholders" in the system architecture diagram), not the
TMS admin boundary.

Driver identity resolution
---------------------------
A driver's ``drivers`` row is linked to their Supabase Auth account by
stamping ``driver_id`` onto that account's own ``user_metadata`` (see
``link_driver_to_auth_account`` below) the moment onboarding completes.
Every subsequent bearer token then carries that ``driver_id`` straight in the
verified user payload, so ``get_my_profile``/``snapshot``/etc. can resolve it
with zero extra database round-trips and with no dependency on any
``drivers`` table migration having been applied -- ``user_metadata`` is
managed entirely by Supabase Auth itself. (``drivers.auth_user_id`` is also
written on a best-effort basis for forward compatibility once that column
exists, but ``user_metadata`` is the primary, always-available mechanism.)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from setuhaul.backend.driver_chat_eta.exceptions import AuthenticationError
from setuhaul.infrastructure.settings import get_settings
from setuhaul.infrastructure.supabase_client import create_public_client

bearer_scheme = HTTPBearer(auto_error=False)
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DriverPrincipal:
    """A verified Supabase caller, scoped to the driver portal."""

    user_id: str
    email: str | None
    access_token: str
    driver_id: str | None = None


def get_current_driver(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> DriverPrincipal:
    """Verify a bearer token with Supabase Auth. Any confirmed user may pass."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthenticationError("A valid bearer token is required.")

    try:
        response = create_public_client(get_settings()).auth.get_user(credentials.credentials)
        user = response.user
    except Exception as exc:  # noqa: BLE001 - Supabase raises its own error types
        raise AuthenticationError("The bearer token is invalid or expired.") from exc

    if user is None:
        raise AuthenticationError("The bearer token is invalid or expired.")

    metadata = getattr(user, "user_metadata", None) or {}
    linked_driver_id = metadata.get("driver_id")
    if not isinstance(linked_driver_id, str) or not linked_driver_id:
        linked_driver_id = None

    return DriverPrincipal(
        user_id=str(user.id),
        email=user.email,
        access_token=credentials.credentials,
        driver_id=linked_driver_id,
    )


def link_driver_to_auth_account(access_token: str, driver_id: str) -> None:
    """Best-effort: stamp ``driver_id`` onto the caller's own Supabase Auth user_metadata.

    This is a self-service update (the caller's own bearer token authorizes
    it, no service-role key needed) and is what lets ``get_current_driver``
    resolve the same driver profile on every future login without the driver
    ever seeing the onboarding form again. Failure here must never fail
    profile completion -- the profile itself is already saved; at worst the
    driver would see onboarding once more next time and get re-linked then.
    """
    settings = get_settings()
    url = f"{settings.supabase_url}/auth/v1/user"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "apikey": settings.supabase_publishable_key,
        "Content-Type": "application/json",
    }
    try:
        response = httpx.put(url, headers=headers, json={"data": {"driver_id": driver_id}}, timeout=10.0)
        response.raise_for_status()
    except Exception:  # noqa: BLE001 - a link failure must not fail profile completion
        logger.exception("driver_chat_eta: failed to link driver_id=%s to the caller's auth account", driver_id)
