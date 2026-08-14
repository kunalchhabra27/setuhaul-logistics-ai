"""Cache-aside behavior for checkin_portal's get_status."""

from __future__ import annotations

from datetime import datetime

import pytest

from setuhaul.backend._testing.fake_redis import FakeRedis
from setuhaul.backend._testing.fake_supabase import FakeSupabaseClient
from setuhaul.backend.checkin_portal.models import GateCheckInRequest
from setuhaul.backend.checkin_portal.repository import CheckInRepository
from setuhaul.backend.checkin_portal.service import CheckInService
from setuhaul.infrastructure import cache, redis_client


@pytest.fixture()
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    redis = FakeRedis()
    monkeypatch.setattr(redis_client, "get_client", lambda: redis)
    return redis


def _service() -> tuple[CheckInService, FakeSupabaseClient]:
    tables = {"facility_checkins": [], "shipments": [{"shipment_id": "SHP1006", "current_status": "IN_TRANSIT"}]}
    client = FakeSupabaseClient(tables)
    return CheckInService(CheckInRepository(client)), client


def test_get_status_second_call_is_a_cache_hit(fake_redis: FakeRedis) -> None:
    service, client = _service()
    service.gate_check_in(
        GateCheckInRequest(shipment_id="SHP1006", facility_id="FAC-1", gate_in_at=datetime(2026, 8, 8, 18, 0))
    )
    first = service.get_status("SHP1006")
    calls_after_first = client.execute_count

    second = service.get_status("SHP1006")

    assert client.execute_count == calls_after_first
    assert second == first


def test_approve_gate_checkin_invalidates_the_cached_status(fake_redis: FakeRedis) -> None:
    service, _client = _service()
    service.gate_check_in(
        GateCheckInRequest(shipment_id="SHP1006", facility_id="FAC-1", gate_in_at=datetime(2026, 8, 8, 18, 0))
    )
    service.get_status("SHP1006")  # warm the cache pre-approval

    service.approve_gate_checkin("SHP1006")

    refreshed = service.get_status("SHP1006")
    assert refreshed["staff_approved"] is True


def test_get_status_works_correctly_with_no_redis_configured(monkeypatch) -> None:
    monkeypatch.setattr(redis_client, "get_client", lambda: None)
    service, _client = _service()
    service.gate_check_in(
        GateCheckInRequest(shipment_id="SHP1006", facility_id="FAC-1", gate_in_at=datetime(2026, 8, 8, 18, 0))
    )
    status = service.get_status("SHP1006")
    assert status["shipment_id"] == "SHP1006"
