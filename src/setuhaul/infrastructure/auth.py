"""FastAPI authentication and TMS role dependencies."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from setuhaul.backend.tms.exceptions import AuthenticationError, AuthorizationError
from setuhaul.backend.tms.models import TMSRole
from setuhaul.infrastructure.settings import get_settings
from setuhaul.infrastructure.supabase_client import create_public_client

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
    """Verify a bearer token with Supabase Auth and read secure app metadata."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthenticationError("A valid bearer token is required.")

    try:
        response = create_public_client(get_settings()).auth.get_user(credentials.credentials)
        user = response.user
    except Exception as exc:
        raise AuthenticationError("The bearer token is invalid or expired.") from exc

    if user is None:
        raise AuthenticationError("The bearer token is invalid or expired.")

    role_value = (user.app_metadata or {}).get("tms_role")
    try:
        role = TMSRole(role_value)
    except (TypeError, ValueError) as exc:
        raise AuthorizationError("The authenticated user has no TMS role.") from exc

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
