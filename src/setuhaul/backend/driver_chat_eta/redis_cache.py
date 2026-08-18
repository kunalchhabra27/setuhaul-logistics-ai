"""Process-wide Redis client + small get/set-JSON-with-TTL helpers.

Used by `repository.py` to cache slow-changing, read-heavy reference data
(facility and dock rows) so the ~7-9 sequential Supabase calls
`DriverChatService._build_snapshot` makes per driver turn don't all have to
hit Supabase every single time -- this is the concrete "use the REDIS_URL
that's already in .env to cache" requirement for the 100-concurrent-driver
chatbot load test. Deliberately independent from `llm/session_store.py`'s
own Redis client (which caches something conceptually different -- the LLM
tool-call conversational scratchpad, not database rows) rather than sharing
its connection, to avoid coupling this reference-data cache's lifecycle to
the LLM session cache's.

Only ever used for data that's safe to serve slightly stale: facility rows
(name/hours/timezone) and dock rows (type/capacity/status) used for the
driver-facing snapshot display. Nothing booking-critical is cached here --
appointment_slots/holds/appointments (the actual availability data that
`dock_scheduler` reads to decide what can be booked) are deliberately never
touched by this module, so caching this can never cause a stale-availability
double-booking; at worst a driver's dock-list display lags a cache TTL
behind a rare facility/dock edit.

Best-effort throughout: every function here degrades to a cache miss (return
None / no-op) rather than raising, on any Redis failure -- REDIS_URL being
unset, Redis being unreachable, or a corrupt cache entry must never break a
request. Redis is always a cache here, never the source of truth; the
Supabase tables it backs remain the source of truth as always.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Any

from setuhaul.infrastructure.settings import get_settings

logger = logging.getLogger(__name__)


@lru_cache
def client():
    """Return a process-wide Redis client, or None if unavailable/unconfigured."""
    settings = get_settings()
    if not settings.redis_url:
        return None
    try:
        import redis

        c = redis.from_url(settings.redis_url, decode_responses=True)
        c.ping()
        return c
    except Exception:  # noqa: BLE001 - best-effort cache, never fatal
        logger.warning(
            "driver_chat_eta: could not connect to REDIS_URL, continuing without reference-data cache.",
            exc_info=True,
        )
        return None


def get_json(key: str) -> Any | None:
    c = client()
    if c is None:
        return None
    try:
        stored = c.get(key)
    except Exception:  # noqa: BLE001
        logger.warning("driver_chat_eta: Redis read failed for %s, continuing without cache.", key, exc_info=True)
        return None
    if not stored:
        return None
    try:
        return json.loads(stored)
    except Exception:  # noqa: BLE001 - corrupt/incompatible cache entry
        logger.warning("driver_chat_eta: discarding unreadable cache entry %s.", key, exc_info=True)
        return None


def set_json(key: str, value: Any, ttl_seconds: int) -> None:
    c = client()
    if c is None:
        return
    try:
        c.set(key, json.dumps(value, default=str), ex=ttl_seconds)
    except Exception:  # noqa: BLE001
        logger.warning("driver_chat_eta: Redis write failed for %s, cache not updated.", key, exc_info=True)
