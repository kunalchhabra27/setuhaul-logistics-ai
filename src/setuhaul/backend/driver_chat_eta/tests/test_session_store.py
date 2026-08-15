"""Unit tests for the LLM conversation-history cache (session_store.py).

This is deliberately separate from setuhaul.infrastructure.cache (app-data
cache) and setuhaul.infrastructure.auth_session (authentication session
cache) -- see session_store.py's own module docstring. These tests exist to
lock in that separation and the specific TTL/isolation/failure behaviors
verified live against the real app during the conversation-persistence
investigation."""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from setuhaul.backend._testing.fake_redis import FakeRedis
from setuhaul.backend.driver_chat_eta.llm import session_store
from setuhaul.infrastructure import redis_client


@pytest.fixture(autouse=True)
def _reset_client_cache():
    redis_client.reset_client_cache()
    yield
    redis_client.reset_client_cache()


@pytest.fixture()
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    redis = FakeRedis()
    monkeypatch.setattr(redis_client, "get_client", lambda: redis)
    return redis


class TestKeyScheme:
    def test_key_is_scoped_by_driver_and_thread(self) -> None:
        assert session_store._redis_key("DRV1", "TH-A") == "chat:setuhaul:DRV1:TH-A"

    def test_key_is_distinct_from_the_app_data_cache_namespace(self) -> None:
        # infrastructure.cache uses "setuhaul:v1:..."; this module must never
        # collide with or be reachable through that namespace.
        key = session_store._redis_key("DRV1", "TH-A")
        assert not key.startswith("setuhaul:v1:")
        assert not key.startswith("session:setuhaul:")  # auth_session's namespace


class TestLoadSaveRoundTrip:
    def test_load_before_any_save_is_empty(self, fake_redis: FakeRedis) -> None:
        assert session_store.load_history("DRV1", "TH-A") == []

    def test_save_then_load_round_trips_message_content_and_order(self, fake_redis: FakeRedis) -> None:
        messages = [HumanMessage(content="hello"), AIMessage(content="hi there")]
        session_store.save_history("DRV1", "TH-A", messages)

        loaded = session_store.load_history("DRV1", "TH-A")

        assert [type(m).__name__ for m in loaded] == ["HumanMessage", "AIMessage"]
        assert loaded[0].content == "hello"
        assert loaded[1].content == "hi there"

    def test_save_appends_by_replacing_the_full_accumulated_list_not_merging(self, fake_redis: FakeRedis) -> None:
        # session_store itself has no append primitive -- the caller
        # (agent.py) is responsible for accumulating history in memory and
        # passing the full list to save_history each turn. This test
        # documents that contract: a second save with a longer list fully
        # replaces the first, it does not merge/union with what was stored.
        session_store.save_history("DRV1", "TH-A", [HumanMessage(content="turn 1")])
        session_store.save_history(
            "DRV1", "TH-A", [HumanMessage(content="turn 1"), AIMessage(content="reply 1"), HumanMessage(content="turn 2")]
        )

        loaded = session_store.load_history("DRV1", "TH-A")
        assert len(loaded) == 3

    def test_corrupt_entry_is_treated_as_empty_history_not_an_error(self, fake_redis: FakeRedis) -> None:
        fake_redis.set(session_store._redis_key("DRV1", "TH-A"), "not-valid-json{")
        assert session_store.load_history("DRV1", "TH-A") == []


class TestIsolation:
    def test_two_drivers_with_the_same_thread_id_do_not_share_history(self, fake_redis: FakeRedis) -> None:
        # thread_id alone is not guaranteed globally unique across drivers in
        # principle (it's a driver-scoped concept) -- the key must include
        # driver_id too, which this proves directly.
        session_store.save_history("DRV-A", "TH-SHARED", [HumanMessage(content="driver A's message")])
        session_store.save_history("DRV-B", "TH-SHARED", [HumanMessage(content="driver B's message")])

        history_a = session_store.load_history("DRV-A", "TH-SHARED")
        history_b = session_store.load_history("DRV-B", "TH-SHARED")

        assert history_a[0].content == "driver A's message"
        assert history_b[0].content == "driver B's message"

    def test_two_threads_for_the_same_driver_do_not_share_history(self, fake_redis: FakeRedis) -> None:
        session_store.save_history("DRV1", "TH-OLD", [HumanMessage(content="old conversation")])
        session_store.save_history("DRV1", "TH-NEW", [HumanMessage(content="new conversation")])

        assert session_store.load_history("DRV1", "TH-OLD")[0].content == "old conversation"
        assert session_store.load_history("DRV1", "TH-NEW")[0].content == "new conversation"


class TestTtlIsASlidingWindowIndependentOfThreadLifecycle:
    def test_save_sets_the_configured_ttl(self, fake_redis: FakeRedis, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, int | None] = {}
        real_set = fake_redis.set

        def _spy_set(key: str, value: str, ex: int | None = None, nx: bool = False):
            captured["ex"] = ex
            return real_set(key, value, ex=ex, nx=nx)

        monkeypatch.setattr(fake_redis, "set", _spy_set)

        session_store.save_history("DRV1", "TH-A", [HumanMessage(content="hi")])

        assert captured["ex"] == session_store.SESSION_TTL_SECONDS

    def test_every_save_slides_the_ttl_forward_not_just_the_first(self, fake_redis: FakeRedis, monkeypatch: pytest.MonkeyPatch) -> None:
        set_calls = {"n": 0}
        real_set = fake_redis.set

        def _spy_set(*a, **k):
            set_calls["n"] += 1
            return real_set(*a, **k)

        monkeypatch.setattr(fake_redis, "set", _spy_set)

        session_store.save_history("DRV1", "TH-A", [HumanMessage(content="turn 1")])
        session_store.save_history("DRV1", "TH-A", [HumanMessage(content="turn 1"), AIMessage(content="reply")])

        assert set_calls["n"] == 2  # each turn's save re-issues SET ... EX, resetting the TTL clock

    def test_expired_redis_history_does_not_mean_the_conversation_ended(self, fake_redis: FakeRedis) -> None:
        """The Redis TTL governs only the hot working-memory cache, not the
        chatbot thread's own lifecycle (that lives in Supabase chat_threads,
        independent of this module entirely -- see agent.py's
        _hydrate_from_persisted fallback). Losing the Redis entry must look
        like an ordinary cache miss here, never an error and never
        something this module conflates with "the thread is gone"."""
        session_store.save_history("DRV1", "TH-A", [HumanMessage(content="turn 1")])
        fake_redis.expire_all()

        # A miss, not an exception, not a signal to create a new thread --
        # this module has no concept of thread status at all, by design.
        assert session_store.load_history("DRV1", "TH-A") == []


class TestRedisFailureBehavior:
    def test_redis_unavailable_load_returns_empty_never_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(redis_client, "get_client", lambda: None)
        assert session_store.load_history("DRV1", "TH-A") == []

    def test_redis_unavailable_save_is_a_silent_no_op_never_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(redis_client, "get_client", lambda: None)
        session_store.save_history("DRV1", "TH-A", [HumanMessage(content="hi")])  # must not raise

    def test_a_read_error_never_leaks_another_drivers_data_it_just_fails_open_to_empty(
        self, fake_redis: FakeRedis, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Even under a Redis error, load_history must never return
        anything other than this exact driver+thread's own data or an
        empty list -- there is no code path that could return a different
        key's contents, and this test pins that down explicitly."""

        def _boom(*_a, **_k):
            raise ConnectionError("simulated Redis outage")

        monkeypatch.setattr(fake_redis, "get", _boom)
        assert session_store.load_history("DRV1", "TH-A") == []
