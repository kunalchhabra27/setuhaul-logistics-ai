"""Scoped, versioned Redis cache-aside infrastructure.

Redis is a best-effort performance cache. Supabase remains authoritative.
Cache keys contain only SHA-256 digests of authorization boundaries, entity
boundaries, and canonical query parameters. Generation tokens invalidate whole
families without KEYS/SCAN and prevent a slow pre-write reader from publishing
into the current generation after a write has committed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping
from uuid import uuid4

from setuhaul.infrastructure import redis_client

logger = logging.getLogger(__name__)

NAMESPACE = "setuhaul:v1"
LOCK_TTL_SECONDS = 5
LOCK_WAIT_SECONDS = 2.0
LOCK_POLL_INTERVAL_SECONDS = 0.05
TTL_HOT = 8
TTL_MODERATE = 20
TTL_SNAPSHOT_PART = 15
TTL_REFERENCE = 300
_JITTER_FRACTION = 0.10
_jitter_rng = random.Random()


@dataclass(frozen=True)
class CacheScope:
    """The narrowest authorization boundary for a response."""

    boundary: str

    @classmethod
    def role(cls, role: str) -> "CacheScope":
        return cls(f"role:{role}")

    @classmethod
    def user(cls, user_id: str) -> "CacheScope":
        return cls(f"user:{user_id}")

    @classmethod
    def facility(cls, facility_id: str, role: str) -> "CacheScope":
        return cls(f"facility:{facility_id}:role:{role}")


PUBLIC_SCOPE = CacheScope("public")


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:24]


def canonical_query_hash(values: Mapping[str, Any] | None = None, **kwargs: Any) -> str:
    """Hash normalized response-affecting query parameters deterministically."""
    source = dict(values or {})
    source.update(kwargs)

    def normalize(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {str(k): normalize(value[k]) for k in sorted(value)}
        if isinstance(value, (list, tuple)):
            return [normalize(item) for item in value]
        if isinstance(value, set):
            return sorted((normalize(item) for item in value), key=str)
        if hasattr(value, "value"):
            return normalize(value.value)
        if value is None or isinstance(value, bool):
            return value
        return str(value).strip()

    return _digest(normalize(source))


def _jittered_ttl(ttl_seconds: int) -> int:
    jitter = _jitter_rng.uniform(-_JITTER_FRACTION, _JITTER_FRACTION)
    return max(1, int(round(ttl_seconds * (1 + jitter))))


def _scope_digest(scope: CacheScope | str) -> str:
    return _digest(scope.boundary if isinstance(scope, CacheScope) else scope)


def _boundary_digest(boundary: Any) -> str:
    return _digest(boundary)


def _generation_key(family: str, boundary: Any) -> str:
    return f"{NAMESPACE}:generation:{_boundary_digest(family)}:{_boundary_digest(boundary)}"


def _data_key(scope: CacheScope | str, family: str, boundary: Any, generation: str, query_hash: str) -> str:
    scope_digest = _scope_digest(scope)
    return f"{NAMESPACE}:{{{scope_digest}}}:data:{_boundary_digest(family)}:{_boundary_digest(boundary)}:{generation}:{query_hash}"


def _lock_key_for(key: str) -> str:
    start = key.index("{")
    end = key.index("}", start)
    return f"{NAMESPACE}:{{{key[start + 1:end]}}}:lock:{key[end + 1:]}"


def _handle_redis_exception(exc: Exception, operation: str, **extra: Any) -> None:
    import redis

    fields = {"operation": operation, "error_type": type(exc).__name__, **extra}
    transient = (
        redis.exceptions.ConnectionError,
        redis.exceptions.TimeoutError,
        redis.exceptions.ClusterDownError,
        OSError,
    )
    if isinstance(exc, transient):
        logger.warning("cache_error", extra={"structured_fields": fields})
    else:
        logger.error("cache_error", extra={"structured_fields": fields}, exc_info=True)


def get_json(key: str) -> Any | None:
    client = redis_client.get_client()
    if client is None:
        logger.info("cache_fallback", extra={"structured_fields": {"reason": "redis_unavailable"}})
        return None
    try:
        stored = client.get(key)
    except Exception as exc:  # noqa: BLE001
        _handle_redis_exception(exc, "get", key=key)
        return None
    if not stored:
        logger.debug("cache_miss", extra={"structured_fields": {"key": key}})
        return None
    try:
        value = json.loads(stored)
    except Exception:  # noqa: BLE001
        logger.warning("cache_error", extra={"structured_fields": {"operation": "deserialize"}})
        return None
    logger.debug("cache_hit", extra={"structured_fields": {"key": key}})
    return value


def set_json(key: str, value: Any, ttl_seconds: int) -> None:
    client = redis_client.get_client()
    if client is None:
        return
    try:
        client.set(key, json.dumps(value, default=str), ex=_jittered_ttl(ttl_seconds))
        logger.debug("cache_set", extra={"structured_fields": {"key": key}})
    except Exception as exc:  # noqa: BLE001
        _handle_redis_exception(exc, "set", key=key)


def _generation(client: Any, family: str, boundary: Any) -> str:
    try:
        value = client.get(_generation_key(family, boundary))
        return str(value or "0")
    except Exception as exc:  # noqa: BLE001
        _handle_redis_exception(exc, "generation_get", family=family)
        return "0"


def _bump_generation(family: str, boundary: Any) -> None:
    client = redis_client.get_client()
    if client is None:
        return
    try:
        client.incr(_generation_key(family, boundary))
        logger.info("cache_generation_bump", extra={"structured_fields": {"family": family}})
    except Exception as exc:  # noqa: BLE001
        _handle_redis_exception(exc, "generation_bump", family=family)


_RELEASE_LOCK_SCRIPT = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
  return redis.call("DEL", KEYS[1])
else
  return 0
end
"""

_COMMIT_AND_RELEASE_SCRIPT = """
if redis.call("GET", KEYS[2]) == ARGV[1] then
  redis.call("SET", KEYS[1], ARGV[2], "EX", ARGV[3])
  redis.call("DEL", KEYS[2])
  return 1
else
  return 0
end
"""


def _try_acquire_lock(client: Any, lock_key: str, token: str) -> bool:
    try:
        return bool(client.set(lock_key, token, nx=True, ex=LOCK_TTL_SECONDS))
    except Exception as exc:  # noqa: BLE001
        _handle_redis_exception(exc, "lock_acquire")
        return False


def _release_lock(client: Any, lock_key: str, token: str) -> None:
    try:
        client.eval(_RELEASE_LOCK_SCRIPT, 1, lock_key, token)
    except Exception as exc:  # noqa: BLE001
        _handle_redis_exception(exc, "lock_release")


def _commit_and_release(client: Any, key: str, lock_key: str, token: str, value: Any, ttl_seconds: int) -> bool:
    try:
        result = client.eval(
            _COMMIT_AND_RELEASE_SCRIPT,
            2,
            key,
            lock_key,
            token,
            json.dumps(value, default=str),
            _jittered_ttl(ttl_seconds),
        )
        return bool(result)
    except Exception as exc:  # noqa: BLE001
        _handle_redis_exception(exc, "lock_commit")
        return False


def get_or_set_scoped(
    scope: CacheScope | str,
    family: str,
    boundary: Any,
    query: Mapping[str, Any] | None,
    ttl_seconds: int,
    fetch: Callable[[], Any],
) -> Any:
    """Read/write a generation-qualified key with stampede protection."""
    client = redis_client.get_client()
    if client is None:
        logger.info("cache_fallback", extra={"structured_fields": {"family": family}})
        return fetch()

    query_hash = canonical_query_hash(query)
    generation = _generation(client, family, boundary)
    key = _data_key(scope, family, boundary, generation, query_hash)
    lookup_started = time.perf_counter()
    cached = get_json(key)
    logger.debug(
        "cache_lookup",
        extra={"structured_fields": {"family": family, "duration_ms": round((time.perf_counter() - lookup_started) * 1000, 3)}},
    )
    if cached is not None:
        return cached

    lock_key = _lock_key_for(key)
    token = uuid4().hex
    if _try_acquire_lock(client, lock_key, token):
        try:
            fetch_started = time.perf_counter()
            result = fetch()
            logger.debug(
                "cache_fetch",
                extra={"structured_fields": {"family": family, "duration_ms": round((time.perf_counter() - fetch_started) * 1000, 3)}},
            )
        except Exception:
            _release_lock(client, lock_key, token)
            raise
        # A concurrent write bumps the generation, making this key
        # unreachable by all future readers even if this fetch finishes late.
        if result is None:
            _release_lock(client, lock_key, token)
            return None
        if _commit_and_release(client, key, lock_key, token, result, ttl_seconds):
            return result
        _release_lock(client, lock_key, token)
        current_key = _data_key(scope, family, boundary, _generation(client, family, boundary), query_hash)
        current = get_json(current_key)
        if current is not None:
            logger.info("cache_publish_rejected", extra={"structured_fields": {"family": family}})
            return current
        return fetch()

    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while time.monotonic() < deadline:
        time.sleep(LOCK_POLL_INTERVAL_SECONDS)
        current_generation = _generation(client, family, boundary)
        current_key = _data_key(scope, family, boundary, current_generation, query_hash)
        cached = get_json(current_key)
        if cached is not None:
            return cached
    logger.info("cache_fallback", extra={"structured_fields": {"reason": "lock_wait_timeout", "family": family}})
    return fetch()


def get_or_set(key: str, ttl_seconds: int, fetch: Callable[[], Any]) -> Any:
    """Compatibility helper for already-built keys."""
    cached = get_json(key)
    if cached is not None:
        return cached
    client = redis_client.get_client()
    if client is None:
        return fetch()
    # A caller-supplied legacy key may not carry a Cluster hash tag. Keep
    # this compatibility path simple rather than issuing a cross-slot Lua
    # command; all production portal paths use get_or_set_scoped().
    if "{" not in key:
        result = fetch()
        if result is not None:
            set_json(key, result, ttl_seconds)
        return result
    lock_key = _lock_key_for(key)
    token = uuid4().hex
    if _try_acquire_lock(client, lock_key, token):
        try:
            result = fetch()
        except Exception:
            _release_lock(client, lock_key, token)
            raise
        if result is None:
            _release_lock(client, lock_key, token)
            return None
        if _commit_and_release(client, key, lock_key, token, result, ttl_seconds):
            return result
        _release_lock(client, lock_key, token)
        current = get_json(key)
        return current if current is not None else fetch()
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while time.monotonic() < deadline:
        time.sleep(LOCK_POLL_INTERVAL_SECONDS)
        cached = get_json(key)
        if cached is not None:
            return cached
    return fetch()


def _legacy_key(family: str, boundary: Any = "global", query: Any = None) -> str:
    return _data_key(CacheScope(f"legacy:{family}:{boundary}"), family, boundary, "0", canonical_query_hash({"query": query}))


def shipment_key(shipment_id: str) -> str: return _legacy_key("shipment", shipment_id)
def shipment_context_key(shipment_id: str) -> str: return _legacy_key("shipment-context", shipment_id)
def shipments_list_key(scope: str, filters_fingerprint: str) -> str: return _legacy_key("shipments-list", scope, filters_fingerprint)
def dock_board_key(shipment_id: str) -> str: return _legacy_key("dock-board", shipment_id)
def change_requests_key(status: str | None) -> str: return _legacy_key("change-requests", "global", status or "all")
def checkin_status_key(shipment_id: str) -> str: return _legacy_key("checkin", shipment_id)
def driver_profile_key(driver_id: str) -> str: return _legacy_key("driver-profile", driver_id)
def vehicle_key(vehicle_id: str) -> str: return _legacy_key("vehicle", vehicle_id)
def facility_key(facility_id: str) -> str: return _legacy_key("facility", facility_id)
def docks_key(facility_id: str) -> str: return _legacy_key("docks", facility_id)
def reference_key(name: str) -> str: return _legacy_key("reference", name)


def delete(*keys: str) -> None:
    """Best-effort exact deletion grouped by Redis Cluster hash slot."""
    client = redis_client.get_client()
    if client is None or not keys:
        return
    try:
        groups: dict[int, list[str]] = {}
        for key in keys:
            groups.setdefault(redis_client._key_slot(key), []).append(key)
        for group in groups.values():
            client.delete(*group)
    except Exception as exc:  # noqa: BLE001
        _handle_redis_exception(exc, "delete")


def invalidate_shipment(shipment_id: str) -> None:
    for family in ("shipment", "shipment-context", "dock-board", "dock-board-slots", "current-appointment", "checkin"):
        _bump_generation(family, shipment_id)
    _bump_generation("driver-active-shipment", "global")


def invalidate_shipments_lists() -> None: _bump_generation("shipments-list", "global")
def invalidate_dock_boards() -> None:
    """Broad, unscoped sweep -- bumps the "global" boundary for each family,
    which no per-shipment/per-facility keyed entry actually reads (those are
    generation-keyed by their own shipment_id/facility_id, not "global").
    This is a known, pre-existing, bounded gap: real promptness for a given
    shipment/facility comes from invalidate_shipment()/
    invalidate_facility_board() below, which bump the correct per-entity
    boundary; this call is a best-effort fallback for the broader "some
    *other* shipment/facility might also be affected" case, with TTL_HOT
    (8s) as the actual backstop for that case -- consistent with this
    codebase's existing "TTL is the real backstop" pattern used elsewhere
    (see invalidate_shipments_lists)."""
    _bump_generation("dock-board", "global")
    _bump_generation("dock-board-slots", "global")
    _bump_generation("facility-dock-availability", "global")
def invalidate_change_requests() -> None: _bump_generation("change-requests", "global")
def invalidate_facility_board(facility_id: str) -> None:
    _bump_generation("dock-board", "global")
    _bump_generation("facility-dock-availability", facility_id)
    _bump_generation("change-requests", facility_id)
def invalidate_driver(driver_id: str) -> None:
    _bump_generation("driver-profile", driver_id)
    _bump_generation("driver-read", "global")
    _bump_generation("driver-list", "global")
def invalidate_vehicle(vehicle_id: str) -> None:
    _bump_generation("vehicle", vehicle_id)
    _bump_generation("vehicle-read", "global")
    _bump_generation("vehicle-list", "global")
def invalidate_facility(facility_id: str) -> None:
    for family in ("facility", "docks"):
        _bump_generation(family, facility_id)
    _bump_generation("facility-list", "global")
def invalidate_carriers() -> None: invalidate_reference("carriers")
def invalidate_reference(name: str) -> None: _bump_generation("reference", name)
