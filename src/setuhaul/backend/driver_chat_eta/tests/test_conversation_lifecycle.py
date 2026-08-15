"""Conversation/thread lifecycle tests -- written after a live investigation
(real Supabase, real Redis Cloud, real Gemini) proved the primary reported
bug ("every prompt creates a new thread") does not reproduce: conversation_id
stays stable across turns, history accumulates correctly, and isolation
holds. These tests lock in that behavior as regressions, plus the one real
gap the investigation's code review turned up: no guard against two
near-simultaneous first messages both creating a thread.
"""

from __future__ import annotations

import threading

import pytest

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


class TestSequentialLifecycle:
    """Regression coverage for what the live investigation actually proved:
    conversation_id is stable across turns and history accumulates. The
    core logic is already covered by
    test_service.py::test_chat_reuses_explicit_conversation_thread_across_turns
    and ::test_new_conversation_explicitly_starts_a_separate_thread; these
    add the isolation angle those didn't cover."""

    def test_two_different_drivers_never_share_a_thread(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import copy

        from setuhaul.backend._testing.fake_supabase import FakeSupabaseClient
        from setuhaul.backend.dock_scheduler.repository import _FUTURE_SLOTS_LAST_CHECKED
        from setuhaul.backend.driver_chat_eta.auth import DriverPrincipal
        from setuhaul.backend.driver_chat_eta.llm import agent as llm_agent
        from setuhaul.backend.driver_chat_eta.repository import DriverChatRepository
        from setuhaul.backend.driver_chat_eta.service import DriverChatService
        from setuhaul.backend.driver_chat_eta.tests.conftest import _base_tables

        monkeypatch.setattr(llm_agent, "is_configured", lambda: False)
        _FUTURE_SLOTS_LAST_CHECKED.clear()

        tables_a = _base_tables()
        tables_b = copy.deepcopy(_base_tables())
        tables_b["drivers"][0]["driver_id"] = "DRV002"
        tables_b["shipments"][0]["driver_id"] = "DRV002"
        tables_b["shipments"][0]["shipment_id"] = "SHP002"

        service_a = DriverChatService(DriverChatRepository(FakeSupabaseClient(tables_a)))
        service_b = DriverChatService(DriverChatRepository(FakeSupabaseClient(tables_b)))
        principal_a = DriverPrincipal(user_id="DRV001", email="a@example.com", access_token="tok-a")
        principal_b = DriverPrincipal(user_id="DRV002", email="b@example.com", access_token="tok-b")

        result_a = service_a.handle_chat_message(principal_a, "I am delayed")
        result_b = service_b.handle_chat_message(principal_b, "I am delayed")

        assert result_a.conversation_id != result_b.conversation_id
        assert {row["driver_id"] for row in tables_a["chat_threads"]} == {"DRV001"}
        assert {row["driver_id"] for row in tables_b["chat_threads"]} == {"DRV002"}


class TestConcurrentFirstMessageThreadCreation:
    """The one real (code-review-identified, not live-reproduced) gap this
    investigation surfaced: get_open_thread_for_driver() -> create_thread()
    is a read-then-write with no atomicity of its own. Two near-
    simultaneous first messages from the same driver could both see "no
    open thread" and both create one. Fixed with a best-effort Redis
    advisory lock (session_store.acquire_thread_creation_lock) around the
    read-check-create sequence, with a re-check after acquiring (double-
    checked locking) so the second caller finds the first caller's thread
    instead of creating a duplicate."""

    def test_concurrent_first_messages_result_in_exactly_one_open_thread(
        self, service, principal, tables, fake_redis: FakeRedis, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from setuhaul.backend.driver_chat_eta.llm import agent as llm_agent

        monkeypatch.setattr(llm_agent, "is_configured", lambda: False)

        from setuhaul.backend.driver_chat_eta import service as service_module

        real_get_open = service.repository.get_open_thread_for_driver
        barrier = threading.Barrier(2)
        barrier_used = threading.local()

        def _synchronized_get_open_thread_for_driver(driver_id: str):
            # Only synchronize each thread's FIRST call (the outer,
            # pre-lock check) -- the lock's own double-checked re-check
            # (see agent.py/service.py) calls this again from inside the
            # lock, one thread at a time by construction, and must not
            # wait on a partner that's already moved on.
            if not getattr(barrier_used, "done", False):
                barrier_used.done = True
                barrier.wait(timeout=2)
            return real_get_open(driver_id)

        monkeypatch.setattr(
            service.repository, "get_open_thread_for_driver", _synchronized_get_open_thread_for_driver
        )

        results: list = []
        errors: list[Exception] = []

        def _send():
            try:
                results.append(service.handle_chat_message(principal, "I am delayed"))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=_send), threading.Thread(target=_send)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors
        assert len(results) == 2
        # The guard's whole point: despite both requests racing past the
        # first "no open thread" read together, exactly one thread exists.
        assert len(tables["chat_threads"]) == 1
        assert results[0].conversation_id == results[1].conversation_id

    def test_lock_unavailable_falls_through_without_blocking_the_message(
        self, service, principal, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Redis outage during thread resolution must degrade to the
        pre-guard behavior (best-effort, no hard failure) -- never block or
        error out a driver's message just because the lock couldn't be
        acquired."""
        from setuhaul.backend.driver_chat_eta.llm import agent as llm_agent

        monkeypatch.setattr(llm_agent, "is_configured", lambda: False)
        monkeypatch.setattr(redis_client, "get_client", lambda: None)

        result = service.handle_chat_message(principal, "I am delayed")

        assert result.conversation_id


class TestThreadCreationLock:
    """Unit-level coverage for the lock primitive itself."""

    def test_second_concurrent_acquire_fails_until_released(self, fake_redis: FakeRedis) -> None:
        token1 = session_store.acquire_thread_creation_lock("DRV1")
        assert token1 is not None

        token2 = session_store.acquire_thread_creation_lock("DRV1")
        assert token2 is None  # already held

        session_store.release_thread_creation_lock("DRV1", token1)
        token3 = session_store.acquire_thread_creation_lock("DRV1")
        assert token3 is not None

    def test_different_drivers_do_not_contend_for_the_same_lock(self, fake_redis: FakeRedis) -> None:
        token_a = session_store.acquire_thread_creation_lock("DRV-A")
        token_b = session_store.acquire_thread_creation_lock("DRV-B")
        assert token_a is not None
        assert token_b is not None

    def test_release_with_the_wrong_token_does_not_release_someone_elses_lock(self, fake_redis: FakeRedis) -> None:
        real_token = session_store.acquire_thread_creation_lock("DRV1")
        session_store.release_thread_creation_lock("DRV1", "not-the-real-token")
        # Lock must still be held -- a stale/foreign release must not clear it.
        assert session_store.acquire_thread_creation_lock("DRV1") is None
        session_store.release_thread_creation_lock("DRV1", real_token)

    def test_redis_unavailable_returns_none_never_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(redis_client, "get_client", lambda: None)
        assert session_store.acquire_thread_creation_lock("DRV1") is None
        session_store.release_thread_creation_lock("DRV1", "any-token")  # must not raise
