"""Logout endpoint for the Redis auth-session cache.

Shared across every portal -- revocation only needs the bearer token itself
(hashed to a session_id, see ``infrastructure.auth_session``), not which
portal's principal type it belongs to. Deliberately does not attempt to
verify the token via Supabase first: a token that's already expired or
otherwise invalid at Supabase should still have its (possibly still-cached)
Redis session cleared, and revocation must not fail or block just because
the underlying Supabase session is already gone.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.responses import Response

from setuhaul.infrastructure import auth_session

router = APIRouter(prefix="/auth", tags=["auth"])
bearer_scheme = HTTPBearer(auto_error=False)


@router.post("/logout", status_code=204)
def logout(credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme)) -> Response:
    """Immediately revoke the caller's cached session, if any.

    Always 204, even with no/invalid credentials or nothing cached --
    logout is idempotent by design, and a client racing its own token
    expiry against this call should never see it as an error.
    """
    if credentials is not None and credentials.scheme.lower() == "bearer":
        auth_session.revoke_session(credentials.credentials)
    return Response(status_code=204)
