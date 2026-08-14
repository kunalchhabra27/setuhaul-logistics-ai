"""Cache-aside behavior for TMS's list_shipments/shipment_context/list_facilities.

TMS's fixtures use a hand-written FakeRepository (see conftest.py), not
FakeSupabaseClient, so there's no query-count to assert on directly. Instead
these tests mutate the fake repository's backing dict directly (bypassing
the service layer entirely) between two service calls: if the second call
still returns the pre-mutation value, that proves a cache -- not a fresh
repository call -- served it. The invalidation tests then perform the
mutation through the real service method and confirm the next read is fresh.
"""

from __future__ import annotations

import pytest

from setuhaul.backend._testing.fake_redis import FakeRedis
from setuhaul.backend.tms.models import ShipmentStatus
from setuhaul.backend.tms.service import TMSService
from setuhaul.backend.tms.tests.conftest import FACILITY, SHIPMENT_ONE, FakeRepository
from setuhaul.infrastructure import cache, redis_client


@pytest.fixture()
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    redis = FakeRedis()
    monkeypatch.setattr(redis_client, "get_client", lambda: redis)
    return redis


def test_list_shipments_second_call_is_served_from_cache(
    service: TMSService, repository: FakeRepository, fake_redis: FakeRedis
) -> None:
    first = service.list_shipments()
    # Mutate the backing store directly, bypassing the service/cache layer --
    # a fresh (uncached) call would see this; a cache hit would not.
    repository.shipments[SHIPMENT_ONE]["current_status"] = "COMPLETED"

    second = service.list_shipments()

    assert second == first, "a cache hit must not reflect a change made behind the cache's back"


def test_archive_shipment_invalidates_the_cached_list(
    service: TMSService, repository: FakeRepository, fake_redis: FakeRedis
) -> None:
    repository.shipments[SHIPMENT_ONE]["current_status"] = "COMPLETED"
    before = service.list_shipments(status=ShipmentStatus.COMPLETED)
    assert any(s.shipment_id == SHIPMENT_ONE for s in before)

    service.archive_shipment(SHIPMENT_ONE)

    after = service.list_shipments(status=ShipmentStatus.COMPLETED, include_archived=False)
    assert not any(s.shipment_id == SHIPMENT_ONE for s in after), (
        "archiving must invalidate the cached list so the archived shipment stops showing up immediately"
    )


def test_list_facilities_second_call_is_served_from_cache(
    service: TMSService, repository: FakeRepository, fake_redis: FakeRedis
) -> None:
    first = service.list_facilities()
    repository.facilities[FACILITY]["facility_name"] = "Renamed DC"

    second = service.list_facilities()

    assert second == first


def test_list_facilities_works_correctly_with_no_redis_configured(
    service: TMSService, monkeypatch
) -> None:
    monkeypatch.setattr(redis_client, "get_client", lambda: None)
    facilities = service.list_facilities()
    assert any(f.facility_id == FACILITY for f in facilities)


def test_shipment_context_second_call_is_served_from_cache(
    service: TMSService, repository: FakeRepository, fake_redis: FakeRedis
) -> None:
    first = service.shipment_context(SHIPMENT_ONE)
    repository.drivers[first.driver.driver_id]["driver_name"] = "Renamed Driver"

    second = service.shipment_context(SHIPMENT_ONE)

    assert second == first


def test_update_shipment_invalidates_the_cached_context(
    service: TMSService, repository: FakeRepository, fake_redis: FakeRedis
) -> None:
    from setuhaul.backend.tms.models import ShipmentUpdate

    service.shipment_context(SHIPMENT_ONE)  # warm the cache

    service.update_shipment(SHIPMENT_ONE, ShipmentUpdate(customer_name="New Customer"))

    refreshed = service.shipment_context(SHIPMENT_ONE)
    assert refreshed.shipment.customer_name == "New Customer"
