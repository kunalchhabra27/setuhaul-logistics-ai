"""Cache-aside behavior for dock_board/list_change_requests -- cache hits
avoid re-querying Supabase, mutations invalidate correctly, and the service
still returns correct data with no Redis configured."""

from __future__ import annotations

import pytest

from setuhaul.backend._testing.fake_redis import FakeRedis
from setuhaul.backend.dock_scheduler.service import DockSchedulerService
from setuhaul.backend.dock_scheduler.tests.conftest import SHP_NORMAL
from setuhaul.infrastructure import cache, redis_client


@pytest.fixture()
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    redis = FakeRedis()
    monkeypatch.setattr(redis_client, "get_client", lambda: redis)
    return redis


def test_dock_board_second_call_is_a_cache_hit(service: DockSchedulerService, fake_redis: FakeRedis) -> None:
    first = service.dock_board(SHP_NORMAL)
    assert first  # sanity: fixture data actually produced slots
    calls_after_first = service.repository.backend.execute_count

    second = service.dock_board(SHP_NORMAL)

    assert service.repository.backend.execute_count == calls_after_first, (
        "a cached dock_board read must not issue any further Supabase queries"
    )
    assert [s.slot_id for s in second] == [s.slot_id for s in first]


def test_hold_slot_invalidates_the_cached_board(service: DockSchedulerService, fake_redis: FakeRedis) -> None:
    before = service.dock_board(SHP_NORMAL)
    held_slot = next(s for s in before if s.availability_status == "AVAILABLE")

    service.hold_slot(SHP_NORMAL, held_slot.slot_id)

    after = service.dock_board(SHP_NORMAL)
    updated = next(s for s in after if s.slot_id == held_slot.slot_id)
    assert updated.availability_status == "HELD", "invalidation must force a fresh read reflecting the new hold"


def test_dock_board_works_correctly_with_no_redis_configured(service: DockSchedulerService, monkeypatch) -> None:
    monkeypatch.setattr(redis_client, "get_client", lambda: None)
    board = service.dock_board(SHP_NORMAL)
    assert any(s.dock_code for s in board)


def test_list_change_requests_second_call_is_a_cache_hit(service: DockSchedulerService, fake_redis: FakeRedis) -> None:
    service.list_change_requests(None)
    calls_after_first = service.repository.backend.execute_count

    service.list_change_requests(None)

    assert service.repository.backend.execute_count == calls_after_first
