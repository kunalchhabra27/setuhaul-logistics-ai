"""Redis-backed cache for already-verified Supabase auth sessions.

**This is not a replacement for Supabase Auth.** Every bearer token this app
receives is still ultimately Supabase's to issue, refresh, and revoke; the
frontend's login/logout/token-refresh flow (``frontend/src/auth/*``) is
unchanged. What this module adds is a short-lived Redis cache in front of
``create_public_client(...).auth.get_user(token)`` -- a live network call to
Supabase Auth that costs ~250ms and previously ran on *every single*
authenticated request across all four portals. A cache hit here skips that
call entirely; a miss (first request, expired, or revoked) always falls
through to the same full verification as before, so Supabase remains the
sole source of truth throughout. See ``infrastructure.auth`` and
``backend.driver_chat_eta.auth`` for the call sites.

**Not the same thing as ``backend.driver_chat_eta.llm.session_store``.**
That module caches an LLM agent's tool-calling scratchpad/message history
per chat thread (``chat:setuhaul:{driver_id}:{thread_id}``) -- conversation
state, unrelated to authentication. Do not conflate the two.

**Not the same thing as ``infrastructure.cache``.** That module is a
disposable, fail-open application-data cache with generation-token
invalidation and stampede-lock Lua scripts -- appropriate for Supabase query
results, wrong for session lifecycle. A session is not disposable data: on a
Redis outage, ``cache.py``'s answer is "skip the cache, still serve the
request"; this module's answer is "skip the cache, still fully verify" --
never "assume valid" (see ``get_cached_session``'s docstring). Sessions also
need predictable expiry, so TTLs here are never jittered.

## Key design

``session:setuhaul:{session_id}``, where ``session_id`` is the hex SHA-256
digest of the caller's bearer token -- never the raw token itself, so a
Redis dump can never leak a live credential. Different tokens (including
multiple simultaneous logins/devices for the same user, which this product
already supports client-side) hash to different, cryptographically
unlinkable keys, so cross-session/cross-user access is structurally
impossible via this cache. Stored value is the minimum needed to
reconstruct a Principal without calling Supabase: user_id, portal, role,
created_at, last_activity, expires_at -- never the token, never PII beyond
what the JWT already carried.

## TTL and refresh policy

A session lives for ``settings.session_ttl_seconds`` (default 300s) from
last refresh. This bounds how stale a cached role/account-status can be
after a role change, password change, or forced sign-out -- a short,
explicit window, not an indefinite one. Every cache *hit* only rewrites
``last_activity``/extends the TTL if more than
``settings.session_refresh_threshold_seconds`` (default 60s) has elapsed
since the last refresh -- avoiding a Redis write on every request (the
driver portal alone polls every 8s).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from setuhaul.infrastructure import redis_client
from setuhaul.infrastructure.settings import Settings

logger = logging.getLogger(__name__)

NAMESPACE = "session:setuhaul"


@dataclass(frozen=True)
class CachedSession:
    user_id: str
    portal: str
    role: str | None
    created_at: float
    last_activity: float


def session_id_for(access_token: str) -> str:
    """One-way derivation -- never store or log the raw token itself."""
    return hashlib.sha256(access_token.encode("utf-8")).hexdigest()


def _key(session_id: str) -> str:
    return f"{NAMESPACE}:{session_id}"


def _handle_redis_exception(exc: Exception, operation: str) -> None:
    import redis

    fields = {"operation": operation, "error_type": type(exc).__name__}
    transient = (
        redis.exceptions.ConnectionError,
        redis.exceptions.TimeoutError,
        redis.exceptions.ClusterDownError,
        OSError,
    )
    if isinstance(exc, transient):
        logger.warning("auth_session_error", extra={"structured_fields": fields})
    else:
        logger.error("auth_session_error", extra={"structured_fields": fields}, exc_info=True)


def get_cached_session(access_token: str, settings: Settings) -> CachedSession | None:
    """Return a still-valid cached session, or None.

    None covers three cases the caller must treat identically: no session
    was ever cached, it expired, or Redis is unreachable right now. In every
    case the caller's only correct response is to fall through to a full
    Supabase ``auth.get_user()`` verification -- this function never
    authenticates anyone by itself, it only ever *skips redundant work* for
    an already-verified, still-fresh session. A Redis outage therefore never
    weakens auth; it only removes the fast path.
    """
    client = redis_client.get_client()
    if client is None:
        return None
    session_id = session_id_for(access_token)
    try:
        stored = client.get(_key(session_id))
    except Exception as exc:  # noqa: BLE001
        _handle_redis_exception(exc, "get")
        return None
    if not stored:
        return None
    try:
        data = json.loads(stored)
        session = CachedSession(
            user_id=data["user_id"],
            portal=data["portal"],
            role=data.get("role"),
            created_at=data["created_at"],
            last_activity=data["last_activity"],
        )
    except (ValueError, KeyError, TypeError):
        logger.warning("auth_session_error", extra={"structured_fields": {"operation": "deserialize"}})
        return None

    now = time.time()
    if now - session.last_activity >= settings.session_refresh_threshold_seconds:
        _touch(client, session_id, session, now, settings)
    return session


def create_session(access_token: str, user_id: str, portal: str, role: str | None, settings: Settings) -> None:
    """Cache a freshly-verified session. Best-effort: a write failure must
    never fail the request that just successfully authenticated via
    Supabase -- the next request simply misses the cache again and re-
    verifies, exactly as if this call had never happened."""
    client = redis_client.get_client()
    if client is None:
        return
    session_id = session_id_for(access_token)
    now = time.time()
    payload = {
        "user_id": user_id,
        "portal": portal,
        "role": role,
        "created_at": now,
        "last_activity": now,
    }
    try:
        client.set(_key(session_id), json.dumps(payload), ex=settings.session_ttl_seconds)
        logger.debug("auth_session_created", extra={"structured_fields": {"portal": portal}})
    except Exception as exc:  # noqa: BLE001
        _handle_redis_exception(exc, "create")


def _touch(client: Any, session_id: str, session: CachedSession, now: float, settings: Settings) -> None:
    """Sliding-window refresh: rewrite last_activity and extend the TTL.
    Only called when the refresh threshold has elapsed (see
    get_cached_session), so an actively-polling client does not generate a
    Redis write on every single request."""
    payload = {
        "user_id": session.user_id,
        "portal": session.portal,
        "role": session.role,
        "created_at": session.created_at,
        "last_activity": now,
    }
    try:
        client.set(_key(session_id), json.dumps(payload), ex=settings.session_ttl_seconds)
    except Exception as exc:  # noqa: BLE001
        # Best-effort refresh -- if this fails, the session just expires on
        # its original schedule instead of sliding forward. Never surfaced
        # to the caller, who already has a perfectly valid cached session
        # for *this* request regardless.
        _handle_redis_exception(exc, "touch")


def revoke_session(access_token: str) -> None:
    """Immediately invalidate a cached session -- called on logout. Idempotent
    and safe to call even if nothing was ever cached for this token (e.g.
    Redis was down when it was created, or it already expired)."""
    client = redis_client.get_client()
    if client is None:
        return
    session_id = session_id_for(access_token)
    try:
        client.delete(_key(session_id))
        logger.debug("auth_session_revoked")
    except Exception as exc:  # noqa: BLE001
        _handle_redis_exception(exc, "revoke")
