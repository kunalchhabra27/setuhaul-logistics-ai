"""Repository-level tests for the Redis reference-data cache added to
get_facility/list_docks (see redis_cache.py's module docstring for why only
these two, display-only lookups are cached).

The rest of this package's tests run with Redis forced off (conftest.py's
`_no_redis_cache` autouse fixture) so they stay fast, isolated, and don't
touch a real Redis instance. These tests deliberately override that with a
small in-memory fake so the caching behavior itself -- a second call being
served from cache instead of hitting Supabase again -- is actually exercised
somewhere.
"""

from __future__ import annotations

import json

from setuhaul.backend._testing.fake_supabase import FakeSupabaseClient
from setuhaul.backend.driver_chat_eta import redis_cache
from setuhaul.backend.driver_chat_eta.repository import DriverChatRepository
from setuhaul.backend.driver_chat_eta.tests.conftest import FACILITY


class _FakeRedis:
    """Minimal stand-in for a redis-py client -- only what get_json/set_json use."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self._store.get(key)

    def set(self, key: str, value: str, ex: int | None = None) -> None:  # noqa: ARG002 - ttl not modeled
        self._store[key] = value


def test_get_facility_is_served_from_cache_on_the_second_call(monkeypatch, tables):
    fake_redis = _FakeRedis()
    monkeypatch.setattr(redis_cache, "client", lambda: fake_redis)

    client = FakeSupabaseClient(tables)
    repo = DriverChatRepository(client)

    first = repo.get_facility(FACILITY)
    assert first is not None
    assert first["facility_name"] == "Jaipur DC"

    # Mutate the underlying table directly -- if the second call actually
    # hit Supabase again, it would see this change. It shouldn't: the cache
    # should serve the first read's copy instead.
    for row in tables["facilities"]:
        if row["facility_id"] == FACILITY:
            row["facility_name"] = "MUTATED -- should not be seen"

    second = repo.get_facility(FACILITY)
    assert second["facility_name"] == "Jaipur DC"

    # And the cache actually holds something real, not just an empty dict.
    cached_raw = fake_redis.get(f"driver_chat_eta:facility:{FACILITY}")
    assert cached_raw is not None
    assert json.loads(cached_raw)["facility_name"] == "Jaipur DC"


def test_list_docks_is_served_from_cache_on_the_second_call(monkeypatch, tables):
    fake_redis = _FakeRedis()
    monkeypatch.setattr(redis_cache, "client", lambda: fake_redis)

    client = FakeSupabaseClient(tables)
    repo = DriverChatRepository(client)

    first = repo.list_docks(FACILITY)
    assert len(first) == 2  # DOCK-1 (STANDARD) + DOCK-2 (REEFER), both ACTIVE in the base fixture

    # Deactivate a dock directly in the table -- a real re-query would drop
    # it from the ACTIVE-only filter; the cached copy should not.
    tables["docks"][0]["dock_status"] = "INACTIVE"

    second = repo.list_docks(FACILITY)
    assert len(second) == 2


def test_get_facility_falls_back_to_supabase_when_redis_is_unavailable(monkeypatch, tables):
    # Mirrors the rest of the suite's default (conftest's autouse fixture
    # already does this), asserted explicitly here since this file is about
    # the cache specifically -- a missing/unreachable Redis must never break
    # the read, just skip the cache.
    monkeypatch.setattr(redis_cache, "client", lambda: None)

    client = FakeSupabaseClient(tables)
    repo = DriverChatRepository(client)

    result = repo.get_facility(FACILITY)
    assert result is not None
    assert result["facility_name"] == "Jaipur DC"
