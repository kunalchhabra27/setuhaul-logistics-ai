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
from datetime import datetime, timedelta

import httpx
import pytest

from setuhaul.backend._testing.fake_supabase import FakeQuery, FakeSupabaseClient
from setuhaul.backend.driver_chat_eta import redis_cache
from setuhaul.backend.driver_chat_eta.exceptions import PersistenceError
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


def test_list_chat_messages_returns_the_newest_messages_not_the_oldest(tables):
    # Regression test: list_chat_messages used to sort ascending by
    # message_ts and then apply .limit(100) directly -- since PostgREST
    # applies ORDER BY before LIMIT, that returned the OLDEST 100 messages on
    # a thread, not the newest. Any thread that grew past 100 messages (easy
    # over a long testing/demo session reusing the same driver+shipment) got
    # permanently stuck showing only its first 100 messages forever: new
    # messages were still being written to Supabase correctly, they just
    # never appeared in the driver-facing snapshot or the LLM's hydrated
    # history, making the assistant look like it had silently stopped
    # responding.
    thread_id = "TH-OVERFLOW"
    base = datetime(2026, 1, 1)
    tables["chat_threads"] = [{"thread_id": thread_id, "driver_id": "DRV001", "thread_status": "OPEN"}]
    tables["chat_messages"] = [
        {
            "chat_message_id": f"MSG-{i:03d}",
            "thread_id": thread_id,
            "sender_type": "DRIVER" if i % 2 == 0 else "AGENT",
            "message_text": f"message {i}",
            "message_ts": (base + timedelta(minutes=i)).isoformat(),
        }
        for i in range(150)
    ]

    client = FakeSupabaseClient(tables)
    repo = DriverChatRepository(client)

    rows = repo.list_chat_messages(thread_id, limit=100)

    assert len(rows) == 100
    # Newest 100 (messages 50-149), still returned oldest-first.
    assert [r["chat_message_id"] for r in rows] == [f"MSG-{i:03d}" for i in range(50, 150)]
    assert rows[0]["message_text"] == "message 50"
    assert rows[-1]["message_text"] == "message 149"


def test_execute_retries_a_transient_network_error_then_succeeds(monkeypatch, tables):
    # Regression test for the live `httpx.ReadError: [WinError 10035] ...`
    # 500 reported on the chat endpoint. This repository's Supabase clients
    # are long-lived and reused across requests (infrastructure/
    # supabase_client.py), so a pooled/kept-alive HTTP connection can go
    # stale between requests and fail on its very next reuse -- a raw
    # httpx/httpcore connection-layer exception that used to propagate
    # straight out of `.execute()` uncaught (only postgrest's own
    # `APIError` was ever handled), crashing the whole chat turn with a 500
    # instead of just retrying once against a fresh connection.
    client = FakeSupabaseClient(tables)
    repo = DriverChatRepository(client)

    real_execute = FakeQuery.execute
    calls = {"n": 0}

    def flaky_execute(self):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ReadError("simulated stale pooled connection")
        return real_execute(self)

    monkeypatch.setattr(FakeQuery, "execute", flaky_execute)

    result = repo.get_facility(FACILITY)

    assert result is not None
    assert result["facility_name"] == "Jaipur DC"
    assert calls["n"] == 2  # first call failed, retry succeeded


def test_execute_gives_up_after_repeated_transient_network_errors(monkeypatch, tables):
    client = FakeSupabaseClient(tables)
    repo = DriverChatRepository(client)

    def always_flaky(self):
        raise httpx.ConnectError("simulated connection reset")

    monkeypatch.setattr(FakeQuery, "execute", always_flaky)
    monkeypatch.setattr("setuhaul.backend.driver_chat_eta.repository.time.sleep", lambda _seconds: None)

    with pytest.raises(PersistenceError):
        repo.get_facility(FACILITY)


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
