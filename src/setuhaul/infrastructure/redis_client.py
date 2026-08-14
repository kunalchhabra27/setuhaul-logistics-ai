"""Shared Redis/Valkey connection factory used by every direct Redis
consumer in this app -- currently ``infrastructure.cache`` (the cache-aside
layer) and ``backend.driver_chat_eta.llm.session_store`` (the LLM agent's
hot session-history cache). One factory means one place that decides how to
connect (standalone vs. cluster-mode), one connection pool per process, and
one exception-classification policy -- neither consumer talks to ``redis``/
``redis.cluster`` directly.

Two client modes, selected by ``settings.cache_cluster_mode`` (explicit, not
inferred from a hostname -- guessing wrong silently is worse than requiring
one flag):

- Standalone (default, ``CACHE_CLUSTER_MODE=false``): today's setup --
  local Valkey, Redis Cloud, or a non-cluster ElastiCache node. Builds a
  plain ``redis.Redis``.
- Cluster mode (``CACHE_CLUSTER_MODE=true``): AWS ElastiCache Serverless's
  single configuration endpoint speaks the Redis Cluster protocol (``MOVED``/
  ``ASK`` redirects, hash-slot sharding) -- a plain ``redis.Redis`` client
  cannot follow that. Builds ``redis.cluster.RedisCluster`` instead.

Everything else about how a caller uses the returned client is identical
between the two modes for the single-key operations both consumers perform
(``GET``/``SET``/``DEL``/``EVAL`` against one hash-tagged key pair) -- see
``infrastructure.cache``'s module docstring for how it keeps multi-key
operations cluster-safe.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from setuhaul.infrastructure.settings import get_settings

logger = logging.getLogger(__name__)

# Bounds how long a single connection *attempt* (construction + PING) is
# allowed to take before giving up -- see get_client()'s module docstring
# note on latency for the full reasoning. Short and conservative: this app
# is a cache-aside layer, never worth blocking a Supabase-backed request on.
SOCKET_CONNECT_TIMEOUT_SECONDS = 0.2
SOCKET_TIMEOUT_SECONDS = 0.5
# 2 retries capped at 0.5s backoff each -- small, bounded, not the 3-retry/
# 1s-cap budget from the first draft (too generous for a fail-open cache).
RETRY_ATTEMPTS = 2
RETRY_BACKOFF_CAP_SECONDS = 0.5
RETRY_BACKOFF_BASE_SECONDS = 0.05

# How long get_client() waits after a failed connection attempt before
# trying again -- see the module-level docstring on get_client() for the
# full reasoning (this is what keeps repeated calls during an outage cheap
# instead of each re-paying the connect-timeout cost).
FAILURE_COOLDOWN_SECONDS = 5.0

# --- process-local, per-worker state -----------------------------------
# Deliberately plain module globals, not a class -- there is exactly one of
# these per Python process. Under a multi-worker deployment (multiple
# uvicorn/gunicorn worker processes), each worker has its OWN copy of this
# state: the cooldown/singleton guarantee below is "one connection probe per
# process per cooldown window," never a single rate limit shared across a
# horizontally-scaled deployment. Documented explicitly so it's never
# mistaken for a cluster-wide guarantee.
_client_lock = threading.Lock()
_client_singleton: Any | None = None
_last_failure_at = 0.0


def reset_client_cache() -> None:
    """Test-only reset of the process-local singleton/cooldown state.

    Replaces the old ``cache._client.cache_clear()`` (an ``lru_cache`` API)
    now that construction failures are intentionally *not* memoized via
    ``lru_cache`` -- see get_client()'s docstring for why.
    """
    global _client_singleton, _last_failure_at
    with _client_lock:
        _client_singleton = None
        _last_failure_at = 0.0


def _key_slot(key: str) -> int:
    """Redis Cluster hash slot for ``key``, via redis-py's own CRC16
    implementation (the real algorithm ElastiCache/Valkey Cluster uses).

    ``redis.crc.key_slot`` takes ``bytes``, not ``str`` (verified against
    the installed redis-py's source) -- centralized here as the one place
    that encodes, so every slot computation in this app (CROSSSLOT-safe
    deletion in cache.py, and the hash-tag-affinity tests) agrees on the
    same encoding (UTF-8, matching every other str<->bytes boundary in this
    codebase, e.g. ``decode_responses=True``'s implicit UTF-8).
    """
    from redis.crc import key_slot

    return key_slot(key.encode("utf-8"))


def _build_client(settings) -> Any:
    """Construct (but do not yet verify connectivity for) a client from
    settings. Raises on bad construction; callers classify exceptions."""
    import redis
    from redis.backoff import ExponentialBackoff
    from redis.cluster import RedisCluster
    from redis.retry import Retry

    retry = Retry(ExponentialBackoff(cap=RETRY_BACKOFF_CAP_SECONDS, base=RETRY_BACKOFF_BASE_SECONDS), RETRY_ATTEMPTS)
    common_kwargs: dict[str, Any] = {
        # Both this app's Redis consumers (cache.py's JSON blobs,
        # session_store.py's LangChain message JSON) assume str responses,
        # not bytes -- explicit here in both branches below, never relying
        # on redis-py's own default (which is bytes/False).
        "decode_responses": True,
        "socket_connect_timeout": SOCKET_CONNECT_TIMEOUT_SECONDS,
        "socket_timeout": SOCKET_TIMEOUT_SECONDS,
        "retry": retry,
        "retry_on_error": [redis.exceptions.ConnectionError, redis.exceptions.TimeoutError],
    }
    if settings.cache_username:
        common_kwargs["username"] = settings.cache_username
    if settings.cache_auth_token:
        common_kwargs["password"] = settings.cache_auth_token

    if settings.cache_cluster_mode:
        if settings.cache_host:
            return RedisCluster(
                host=settings.cache_host,
                port=settings.cache_port or 6379,
                ssl=bool(settings.cache_tls),
                **common_kwargs,
            )
        return RedisCluster(url=settings.redis_url, **common_kwargs)

    if settings.cache_host:
        return redis.Redis(
            host=settings.cache_host,
            port=settings.cache_port or 6379,
            ssl=bool(settings.cache_tls),
            **common_kwargs,
        )
    return redis.Redis.from_url(settings.redis_url, **common_kwargs)


def get_client() -> Any | None:
    """Return the process-wide Redis/Valkey client, or None if unavailable.

    Only a *successful* construction is memoized. A failed attempt is
    remembered just long enough (``FAILURE_COOLDOWN_SECONDS``) to avoid
    hammering a dead endpoint -- never permanently, unlike an ``lru_cache``
    would (which would memoize a `None` forever the first time Redis is
    unreachable, even after it recovers). Concurrency-safe: FastAPI runs
    this app's sync route handlers in a threadpool (no ``async def`` routes
    anywhere), so real OS-thread races through this function are possible;
    a lock with double-checked locking ensures only one thread ever builds
    (or ever probes a failing endpoint for) the shared client at a time.

    Latency bound: one connection *attempt* costs at most roughly
    ``SOCKET_CONNECT_TIMEOUT_SECONDS + (SOCKET_TIMEOUT_SECONDS + backoff) *
    RETRY_ATTEMPTS`` (~1.5s with the constants above) -- and that cost is
    paid **at most once per FAILURE_COOLDOWN_SECONDS window per process**,
    not once per Redis operation. Every other call within that window
    (including a stampede-lock waiter's repeated polling, see cache.py)
    hits the cooldown short-circuit and returns None in microseconds
    instead of re-attempting a connection.
    """
    global _client_singleton, _last_failure_at

    if _client_singleton is not None:
        return _client_singleton

    with _client_lock:
        if _client_singleton is not None:
            return _client_singleton

        settings = get_settings()
        if not settings.redis_url and not settings.cache_host:
            return None

        if time.monotonic() - _last_failure_at < FAILURE_COOLDOWN_SECONDS:
            return None

        import redis

        try:
            client = _build_client(settings)
            client.ping()
        except (
            redis.exceptions.AuthenticationError,
            redis.exceptions.AuthorizationError,
            redis.exceptions.ExternalAuthProviderError,
        ) as exc:
            # Credential/config problem -- not transient. Checked before the
            # broader ConnectionError clause below since these are its
            # subclasses (redis.exceptions: AuthenticationError,
            # AuthorizationError < ConnectionError), and Python matches the
            # first applicable except in source order.
            _last_failure_at = time.monotonic()
            logger.error(
                "infrastructure.redis_client: authentication/authorization failure -- "
                "check CACHE_USERNAME/CACHE_AUTH_TOKEN, this is not a transient outage.",
                extra={"structured_fields": {"error_type": type(exc).__name__}},
                exc_info=True,
            )
            return None
        except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError, redis.exceptions.ClusterDownError, OSError) as exc:
            # Genuinely transient: network blip, connect/read timeout, or the
            # cluster is temporarily unavailable (e.g. mid-resharding --
            # MasterDownError is a ClusterDownError subclass). Fail open,
            # log quietly -- this is normal, expected, already-handled
            # behavior, not something worth paging anyone over.
            _last_failure_at = time.monotonic()
            logger.warning(
                "infrastructure.redis_client: Redis/ElastiCache unavailable, continuing without cache.",
                extra={"structured_fields": {"error_type": type(exc).__name__}},
            )
            return None
        except Exception as exc:  # noqa: BLE001 - see docstring: unexpected bugs must still fail open, but loudly
            # Everything else: a programming/config bug in _build_client, a
            # bad settings value, etc. -- not an ordinary Redis-availability
            # issue. Still fails open for the calling request (the
            # invariant never changes), but logged loudly so it actually
            # gets noticed and fixed instead of masquerading as "Redis is
            # just down."
            _last_failure_at = time.monotonic()
            logger.error(
                "infrastructure.redis_client: unexpected error constructing the client -- "
                "this looks like a bug, not a transient outage.",
                extra={"structured_fields": {"error_type": type(exc).__name__}},
                exc_info=True,
            )
            return None

        _client_singleton = client
        return client
