"""Best-effort Redis/Valkey cache-aside layer shared by the four backend
modules. Mirrors ``driver_chat_eta.llm.session_store``'s philosophy: this is
a read-through performance cache, never the source of truth. Every call is
wrapped so a missing/unreachable cache degrades silently to "no cache" --
never fatal, never raises into a request. See ``infrastructure.redis_client``
for the shared connection factory both this module and ``session_store``
build on (standalone Redis vs. Redis Cluster, singleton/cooldown/locking,
tiered exception handling).

Cache keys are namespaced by shared entity id (shipment_id, facility_id,
driver_id) rather than by backend module, because tms/dock_scheduler/
checkin_portal/driver_chat_eta all read and write the *same* underlying
Supabase tables directly. A single write in one module can make another
module's cached read stale, so invalidation is centralized here as a handful
of entity-scoped helpers instead of being reinvented per module.

## Cluster-mode key design (hash tags)

Every key wraps its identifying suffix in one Redis Cluster hash tag --
``cache:setuhaul:{shipment:SHP1}``, not ``cache:setuhaul:shipment:SHP1`` --
so a key and *its own* stampede-lock companion (``lock:{shipment:SHP1}``)
always hash to the same cluster slot (required for the atomic Lua scripts
below, which touch both in one call). Different *entities* are deliberately
on different slots (e.g. SHP1 and SHP2's keys do not share a tag) -- this is
fine for single-entity operations, but means any function that deletes
*multiple* keys at once (``delete()``, and the SCAN-based sweeps below) must
never send a multi-key Redis command spanning more than one slot. See
``delete()``'s own docstring for how that's handled. Hash-tag braces are
inert in standalone mode (local Valkey, Redis Cloud) -- this key format is
safe and behavior-preserving there too, no mode branching needed in the key
format itself.

**Migration note**: deploying this key format makes every *pre-existing*
(non-hash-tagged) cache entry permanently unreachable by the new code. This
is intentional and safe: old entries are simply never looked up again and
expire on their own TTL (at most a few minutes), causing a handful of
ordinary extra cache misses right after deploy -- never incorrect data.
**Do not flush Redis** to "clean this up"; that trades a gradual, harmless
trickle of misses for a synchronized mass-miss spike at the exact moment of
deploy. This note applies only to this module's own ``cache:setuhaul:...``
keys -- ``driver_chat_eta.llm.session_store``'s ``chat:setuhaul:...`` keys
are a separate, unaffected key scheme (single-key reads/writes only, never
part of the multi-key Lua locking that motivated hash tags here).

## Latency bound

A dead cache costs at most one connection-attempt's worth of latency
(~1.5s, see ``redis_client.get_client()``'s docstring) **once per
``redis_client.FAILURE_COOLDOWN_SECONDS`` window per process** -- not once
per Redis operation. See that module for the full reasoning; it's what
keeps a single request's several cache.py calls (or a stampede-lock
waiter's dozens of poll iterations) from compounding into unbounded added
latency when Redis/ElastiCache is down.
"""

from __future__ import annotations

import json
import logging
import random
import time
from typing import Any, Callable
from uuid import uuid4

from setuhaul.infrastructure import redis_client

logger = logging.getLogger(__name__)

# How long a stampede-guard lock is held before it's assumed abandoned (e.g.
# the holder crashed mid-fetch) and Redis expires it on its own -- see
# get_or_set(). Deliberately short: the fetches this guards (a handful of
# Supabase round trips) never legitimately take this long.
LOCK_TTL_SECONDS = 5
# How long a non-holder waits for the lock holder to populate the key before
# giving up and fetching directly itself. Bounded and short on purpose --
# this must never turn into an indefinite hang; "fetch it myself" is always
# the safe fallback.
LOCK_WAIT_SECONDS = 2.0
LOCK_POLL_INTERVAL_SECONDS = 0.05

# TTL categories, matching this app's actual usage shape.
# HOT: dock board / change-request reads, polled every 8-15s by the frontend.
TTL_HOT = 8
# NORMAL: shipment-shaped reads, fresh enough between polls, cheap backstop
# if an invalidation call is ever missed.
TTL_MODERATE = 20
# Slow-changing sub-fetches inside driver_chat_eta's snapshot (vehicle,
# facility, docks, driver profile) -- shared across every driver/snapshot
# poll that touches the same entity.
TTL_SNAPSHOT_PART = 15
# REFERENCE: data that changes only when someone edits a facility/carrier/etc.
TTL_REFERENCE = 300

# TTL jitter -- a small +/- spread so many keys populated at the same instant
# (e.g. a batch of dock-board reads right after a shared invalidation) don't
# all expire in the same instant and re-stampede together. Deterministic RNG
# instance so tests can seed/mock it for exact-value assertions rather than
# statistical ones.
_JITTER_FRACTION = 0.10
_jitter_rng = random.Random()


def _jittered_ttl(ttl_seconds: int) -> int:
    jitter = _jitter_rng.uniform(-_JITTER_FRACTION, _JITTER_FRACTION)
    return max(1, int(round(ttl_seconds * (1 + jitter))))


def _key(*parts: str) -> str:
    return "cache:setuhaul:{" + ":".join(parts) + "}"


def _lock_key_for(key: str) -> str:
    """Derive a key's stampede-lock companion by reusing its own hash tag
    (not by naively prefixing the full key), so the pair always shares a
    Redis Cluster slot -- see the module docstring."""
    start = key.index("{")
    end = key.rindex("}")
    return f"lock:{{{key[start + 1:end]}}}"


def _handle_redis_exception(exc: Exception, operation: str, **extra: Any) -> None:
    """Central tiered classification for every Redis-touching function
    below: genuinely transient availability problems (connection/timeout/
    cluster-down) log quietly at WARNING and are expected; authentication/
    authorization problems and anything else (a real bug, a malformed
    command, a CROSSSLOT error that should never happen if our own
    slot-grouping is correct) log loudly at ERROR with a traceback so it
    actually gets noticed. Both tiers still fail open for the caller --
    only the log severity/diagnostic depth differs. Never logs credentials
    -- only the exception type and the (non-secret) key/operation context
    the caller passes in.
    """
    import redis

    fields = {"operation": operation, "error_type": type(exc).__name__, **extra}
    if isinstance(
        exc,
        (redis.exceptions.AuthenticationError, redis.exceptions.AuthorizationError, redis.exceptions.ExternalAuthProviderError),
    ):
        logger.error(
            "infrastructure.cache: %s failed -- authentication/authorization problem, not transient.",
            operation,
            extra={"structured_fields": fields},
            exc_info=True,
        )
    elif isinstance(exc, (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError, redis.exceptions.ClusterDownError, OSError)):
        logger.warning(
            "infrastructure.cache: %s failed -- Redis/ElastiCache unavailable, continuing without cache.",
            operation,
            extra={"structured_fields": fields},
        )
    else:
        logger.error(
            "infrastructure.cache: %s failed unexpectedly -- not an ordinary Redis-availability issue.",
            operation,
            extra={"structured_fields": fields},
            exc_info=True,
        )


def get_json(key: str) -> Any | None:
    """Return the cached value for ``key``, or None on a miss or any failure."""
    client = redis_client.get_client()
    if client is None:
        return None
    try:
        stored = client.get(key)
    except Exception as exc:  # noqa: BLE001 - see _handle_redis_exception
        _handle_redis_exception(exc, "get_json", key=key)
        return None
    if not stored:
        logger.debug("cache_miss", extra={"structured_fields": {"key": key}})
        return None
    try:
        value = json.loads(stored)
    except Exception:  # noqa: BLE001 - corrupt/incompatible cache entry, not a Redis-availability issue
        logger.warning("infrastructure.cache: discarding unreadable cache entry for %s.", key)
        return None
    logger.debug("cache_hit", extra={"structured_fields": {"key": key}})
    return value


def set_json(key: str, value: Any, ttl_seconds: int) -> None:
    """Best-effort write-through; a failure here must never break the caller."""
    client = redis_client.get_client()
    if client is None:
        return
    try:
        client.set(key, json.dumps(value, default=str), ex=_jittered_ttl(ttl_seconds))
    except Exception as exc:  # noqa: BLE001
        _handle_redis_exception(exc, "set_json", key=key)


def delete(*keys: str) -> None:
    """Best-effort invalidation of one or more keys.

    Redis Cluster requires every key in one multi-key ``DEL`` to share a
    hash slot. Different entities are deliberately on different slots (see
    module docstring), so keys passed here are grouped by slot first and
    one ``DEL`` is issued per group -- a no-op grouping (single group) in
    standalone mode, and the difference between working and a hard
    ``CROSSSLOT`` error against a real cluster.
    """
    client = redis_client.get_client()
    if client is None or not keys:
        return
    try:
        groups: dict[int, list[str]] = {}
        for k in keys:
            groups.setdefault(redis_client._key_slot(k), []).append(k)
        for group in groups.values():
            client.delete(*group)
    except Exception as exc:  # noqa: BLE001
        _handle_redis_exception(exc, "delete", keys=list(keys))


# --- Stampede-lock primitives (Lua, atomic ownership checks) ---------------
# Both scripts operate on a (data key, lock key) pair that always share a
# hash tag (see _lock_key_for) -- required for cluster-mode correctness,
# since a single EVAL can only touch keys in one slot.

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
    except Exception as exc:  # noqa: BLE001 - fail open, never let lock machinery block a read
        _handle_redis_exception(exc, "lock_acquire", key=lock_key)
        return False


def _release_lock(client: Any, lock_key: str, token: str) -> None:
    try:
        client.eval(_RELEASE_LOCK_SCRIPT, 1, lock_key, token)
    except Exception as exc:  # noqa: BLE001
        _handle_redis_exception(exc, "lock_release", key=lock_key)


def _commit_and_release(client: Any, key: str, lock_key: str, token: str, value: Any, ttl_seconds: int) -> bool:
    """Atomically: if we still own the lock, write ``value`` and release it
    in one round trip. Returns False (without writing anything) if the lock
    was lost -- see get_or_set()'s handling of that case."""
    try:
        result = client.eval(
            _COMMIT_AND_RELEASE_SCRIPT, 2, key, lock_key, token, json.dumps(value, default=str), _jittered_ttl(ttl_seconds)
        )
        return bool(result)
    except Exception as exc:  # noqa: BLE001
        _handle_redis_exception(exc, "lock_commit", key=key)
        return False


def get_or_set(key: str, ttl_seconds: int, fetch: Callable[[], Any]) -> Any:
    """Cache-aside read with single-flight stampede protection.

    Every cached-read call site in the app goes through this instead of
    hand-rolling its own get_json/set_json pair, so there is exactly one
    place that decides what happens on a miss.

    On a hit: returns the cached value, ``fetch`` is never called.

    On a miss: the first caller acquires a short-lived lock and calls
    ``fetch()`` itself, then atomically writes the result and releases the
    lock in one Lua script -- only if it still owns the lock (guards
    against a stale/slow caller overwriting a value that changed and was
    invalidated while it was fetching; see the "lock lost" branch below).
    Any other caller that misses the same key while the lock is held polls
    briefly for the holder to populate the cache; if that wait times out,
    it gives up waiting and calls ``fetch()`` itself rather than blocking
    indefinitely -- a stuck/crashed holder must never turn into a hang for
    everyone else.

    **Lock lost mid-fetch** (another request invalidated this key -- which
    also clears its lock, see ``invalidate_*`` below -- between us
    acquiring the lock and finishing our fetch): our fetched value might be
    stale, so it is never written to the cache. We re-read the cache once
    (someone else may have already repopulated it correctly), and if still
    nothing, perform exactly one more, uncached Supabase read and return
    it. No retry loop, no second lock attempt -- the rare case simply
    accepts one warm-cache miss; the next ordinary request repopulates the
    cache correctly through this same fully-protected path.

    If Redis is unavailable at all, this degrades straight to ``fetch()``,
    identically to every other function in this module.
    """
    cached = get_json(key)
    if cached is not None:
        return cached

    client = redis_client.get_client()
    if client is None:
        return fetch()

    lock_key = _lock_key_for(key)
    token = uuid4().hex
    acquired = _try_acquire_lock(client, lock_key, token)

    if acquired:
        logger.debug("lock_acquired", extra={"structured_fields": {"key": key}})
        try:
            result = fetch()
        except Exception:
            _release_lock(client, lock_key, token)
            raise

        if _commit_and_release(client, key, lock_key, token, result, ttl_seconds):
            return result

        # Lock lost mid-fetch -- `result` is not safe to cache (see docstring).
        fresh = get_json(key)
        if fresh is not None:
            return fresh
        return fetch()  # one bounded, uncached re-fetch -- never written back

    logger.debug("lock_contention", extra={"structured_fields": {"key": key}})
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while time.monotonic() < deadline:
        time.sleep(LOCK_POLL_INTERVAL_SECONDS)
        cached = get_json(key)
        if cached is not None:
            return cached
    logger.info("lock_wait_timeout", extra={"structured_fields": {"key": key}})
    return fetch()


# --- Key builders -----------------------------------------------------------
# Kept centralized so every module spells a given entity's key identically.


def shipment_key(shipment_id: str) -> str:
    return _key("shipment", shipment_id)


def shipment_context_key(shipment_id: str) -> str:
    return _key("shipment-context", shipment_id)


def shipments_list_key(scope: str, filters_fingerprint: str) -> str:
    """``scope`` is a facility_id for facility-scoped lists, or "all"."""
    return _key("shipments", scope, filters_fingerprint)


def dock_board_key(shipment_id: str) -> str:
    return _key("dockboard", shipment_id)


def change_requests_key(status: str | None) -> str:
    return _key("changerequests", status or "all")


def checkin_status_key(shipment_id: str) -> str:
    return _key("checkin", shipment_id)


def driver_profile_key(driver_id: str) -> str:
    return _key("driver-profile", driver_id)


def vehicle_key(vehicle_id: str) -> str:
    return _key("vehicle", vehicle_id)


def facility_key(facility_id: str) -> str:
    return _key("facility", facility_id)


def docks_key(facility_id: str) -> str:
    return _key("docks", facility_id)


def reference_key(name: str) -> str:
    return _key("ref", name)


# --- Invalidation helpers ----------------------------------------------------
# Called from each module's service.py after a successful mutation. Entity-
# scoped so a dock_scheduler write can correctly bust tms's and
# driver_chat_eta's cached views of the same shipment/facility/driver without
# each module needing to know the others' key schemes in detail. Every
# multi-key delete here goes through delete() above, which is already
# CROSSSLOT-safe -- never issue a raw client.delete(*keys) directly.


def invalidate_shipment(shipment_id: str) -> None:
    """Bust every cached view of one shipment across all four modules."""
    delete(
        shipment_key(shipment_id),
        shipment_context_key(shipment_id),
        dock_board_key(shipment_id),
        checkin_status_key(shipment_id),
    )


def _scan_and_delete(pattern: str, sweep_name: str) -> None:
    client = redis_client.get_client()
    if client is None:
        return
    try:
        keys = list(client.scan_iter(match=pattern, count=200))
    except Exception as exc:  # noqa: BLE001
        _handle_redis_exception(exc, sweep_name, pattern=pattern)
        return
    if keys:
        delete(*keys)


def invalidate_shipments_lists() -> None:
    """Bust every cached shipment-list page (TMS's global + facility-scoped lists).

    Shipment lists are keyed by (scope, filters_fingerprint), an unbounded key
    space -- rather than track every fingerprint ever cached, callers use a
    short TTL (TTL_MODERATE) as the real backstop and this is only invoked as
    a best-effort hint; the SCAN-based sweep below keeps it correct without
    needing an index of live keys.
    """
    _scan_and_delete(_key("shipments", "*"), "shipments-list sweep")


def invalidate_dock_boards() -> None:
    """Bust every cached dock-board / compatible-slots read.

    A single hold/confirm/cancel-hold/rebook can change
    ``availability_status`` for every shipment that could have used the
    affected slot, not just the one that triggered the mutation -- this
    mirrors ``DockSchedulerRepository``'s own in-request
    ``_compatible_slots_cache.clear()``, which clears its whole cache (not
    just one shipment_id's entry) for exactly this reason. There's no cheap
    index of which shipment_ids are currently cached in Redis, so this
    sweeps the whole ``dockboard:*`` keyspace via SCAN instead.
    """
    _scan_and_delete(_key("dockboard", "*"), "dock-board sweep")


def invalidate_change_requests() -> None:
    """Bust every cached change-request-queue page (all/PENDING/APPROVED/DECLINED)."""
    delete(
        change_requests_key(None),
        change_requests_key("PENDING"),
        change_requests_key("APPROVED"),
        change_requests_key("DECLINED"),
    )


def invalidate_facility_board(_facility_id: str) -> None:
    """Bust the shared change-request lists after any booking mutation.

    Board/slot invalidation itself is handled by ``invalidate_dock_boards``
    (called alongside this); this helper only covers the separate
    change-request-queue cache, which any booking mutation can also affect
    (e.g. approving a change request books a slot).
    """
    invalidate_change_requests()


def invalidate_driver(driver_id: str) -> None:
    """Bust a driver's own cached profile."""
    delete(driver_profile_key(driver_id))


def invalidate_vehicle(vehicle_id: str) -> None:
    delete(vehicle_key(vehicle_id))


def invalidate_facility(facility_id: str) -> None:
    """Bust a facility's own cached row, its dock list, and every cached
    TMS facility-list page (keyed per limit/offset, hence the SCAN sweep --
    same reasoning as invalidate_shipments_lists)."""
    delete(facility_key(facility_id), docks_key(facility_id))
    _scan_and_delete(reference_key("facilities:*"), "facilities-list sweep")


def invalidate_carriers() -> None:
    invalidate_reference("carriers")


def invalidate_reference(name: str) -> None:
    delete(reference_key(name))
