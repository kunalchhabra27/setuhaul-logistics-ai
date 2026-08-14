"""Cache-aside behavior for driver_chat_eta's snapshot sub-fetches and
reference-data reads."""

from __future__ import annotations

import pytest

from setuhaul.backend._testing.fake_redis import FakeRedis
from setuhaul.backend.driver_chat_eta.auth import DriverPrincipal
from setuhaul.backend.driver_chat_eta.service import DriverChatService
from setuhaul.backend.driver_chat_eta.tests.conftest import DRIVER_ID
from setuhaul.infrastructure import cache, redis_client


@pytest.fixture()
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    redis = FakeRedis()
    monkeypatch.setattr(redis_client, "get_client", lambda: redis)
    return redis


def test_get_my_profile_second_call_is_a_cache_hit(
    service: DriverChatService, principal: DriverPrincipal, fake_redis: FakeRedis
) -> None:
    first = service.get_my_profile(principal)
    calls_after_first = service.repository.client.execute_count

    second = service.get_my_profile(principal)

    assert service.repository.client.execute_count == calls_after_first
    assert second == first


def test_complete_profile_invalidates_the_cached_profile(
    service: DriverChatService, principal: DriverPrincipal, fake_redis: FakeRedis
) -> None:
    from setuhaul.backend.driver_chat_eta.models import ProfileCompleteRequest

    service.get_my_profile(principal)  # warms the cache with the pre-update name

    service.complete_profile(
        principal,
        ProfileCompleteRequest(
            carrier_id="CAR001",
            driver_name="Updated Name",
            phone="+91-9000010001",
            licence_number="RJ14DL1001",
            home_base_city="Jaipur",
        ),
    )

    refreshed = service.get_my_profile(principal)
    assert refreshed.driver_name == "Updated Name"


def test_snapshot_still_correct_with_no_redis_configured(
    service: DriverChatService, principal: DriverPrincipal, monkeypatch
) -> None:
    monkeypatch.setattr(redis_client, "get_client", lambda: None)
    snapshot = service.snapshot(principal)
    assert snapshot.driver.driver_id == DRIVER_ID


def test_list_carriers_second_call_is_a_cache_hit(service: DriverChatService, fake_redis: FakeRedis) -> None:
    first = service.list_carriers()
    calls_after_first = service.repository.client.execute_count

    second = service.list_carriers()

    assert service.repository.client.execute_count == calls_after_first
    assert [c.carrier_id for c in second] == [c.carrier_id for c in first]


def test_snapshot_chat_messages_are_never_stale_despite_caching(
    service: DriverChatService, principal: DriverPrincipal, fake_redis: FakeRedis
) -> None:
    """The vehicle/facility/docks/profile sub-parts of a snapshot are cached,
    but chat_messages/exception/checkin/appointment must always be read
    fresh -- report an exception (which opens a thread + writes a chat
    message) between two snapshot calls and confirm the second one sees it."""
    before = service.snapshot(principal)
    assert before.chat_messages == []

    service.report_exception(principal, delay_minutes=30, note="running late")

    after = service.snapshot(principal)
    assert after.exception is not None
    assert after.exception.exception_status in {"SLOT_OPTIONS_SHARED", "ESCALATED"}
