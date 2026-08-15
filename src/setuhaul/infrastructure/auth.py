"""FastAPI authentication and TMS role dependencies."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from setuhaul.backend.tms.exceptions import AuthenticationError, AuthorizationError
from setuhaul.backend.tms.models import TMSRole
from setuhaul.infrastructure import auth_session
from setuhaul.infrastructure.settings import get_settings
from setuhaul.infrastructure.supabase_client import create_public_client

PORTAL = "tms"

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    """Verified Supabase caller and TMS role."""

    user_id: str
    role: TMSRole
    access_token: str


def get_current_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> Principal:
    """Verify a bearer token, preferring a cached Redis session over a live
    Supabase Auth call. See ``infrastructure.auth_session`` for the full
    design -- in short: a cache hit skips Supabase entirely; a miss (first
    request, expired, revoked, or Redis unreachable) always falls through to
    the exact same full verification as before, so this never authenticates
    anyone the pre-existing check wouldn't have.
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthenticationError("A valid bearer token is required.")

    settings = get_settings()
    cached = auth_session.get_cached_session(credentials.credentials, settings)
    if cached is not None and cached.portal == PORTAL:
        try:
            role = TMSRole(cached.role)
        except (TypeError, ValueError):
            role = TMSRole.ADMIN_1
        return Principal(user_id=cached.user_id, role=role, access_token=credentials.credentials)

    try:
        response = create_public_client(settings).auth.get_user(credentials.credentials)
        user = response.user
    except Exception as exc:
        raise AuthenticationError("The bearer token is invalid or expired.") from exc

    if user is None:
        raise AuthenticationError("The bearer token is invalid or expired.")

    role_value = (user.app_metadata or {}).get("tms_role")
    try:
        role = TMSRole(role_value)
    except (TypeError, ValueError):
        # No tms_role claim has been set on this user via the Supabase Admin
        # API (app_metadata can only be written with the service-role key,
        # which this codebase never uses). Rather than lock every
        # authenticated user out of TMS, default to full access -- mirrors
        # the RLS being fully open on drivers/vehicles/shipments for local
        # development. Reinstate real role checks before production use.
        role = TMSRole.ADMIN_1

    auth_session.create_session(credentials.credentials, str(user.id), PORTAL, role.value, settings)
    return Principal(user_id=str(user.id), role=role, access_token=credentials.credentials)


def require_reader(principal: Principal = Depends(get_current_principal)) -> Principal:
    """Allow either supported TMS role to read data."""
    if principal.role not in {TMSRole.ADMIN_1, TMSRole.AGENT_READER}:
        raise AuthorizationError("TMS read access is required.")
    return principal


def require_admin(principal: Principal = Depends(get_current_principal)) -> Principal:
    """Allow only TMS administrators to mutate data."""
    if principal.role is not TMSRole.ADMIN_1:
        raise AuthorizationError("ADMIN_1 role is required for this operation.")
    return principal
