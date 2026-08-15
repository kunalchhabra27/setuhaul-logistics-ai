"""Tests for POST /api/v1/webhooks/supabase -- secret verification and the
table-to-invalidation dispatch."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from setuhaul.backend._testing.fake_redis import FakeRedis
from setuhaul.backend.webhooks import api as webhooks_api
from setuhaul.infrastructure import cache, redis_client
from setuhaul.main import app

WEBHOOK_SECRET = "test-webhook-secret"


@pytest.fixture()
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    redis = FakeRedis()
    monkeypatch.setattr(redis_client, "get_client", lambda: redis)
    return redis


@pytest.fixture()
def configured_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(webhooks_api, "get_settings", lambda: SimpleNamespace(webhook_secret=WEBHOOK_SECRET))


def _post(payload: dict, *, secret: str | None = WEBHOOK_SECRET):
    headers = {"X-Webhook-Secret": secret} if secret is not None else {}
    return TestClient(app).post("/api/v1/webhooks/supabase", json=payload, headers=headers)


def test_missing_secret_is_rejected(configured_secret: None) -> None:
    response = _post({"type": "UPDATE", "table": "shipments", "record": {"shipment_id": "SHP1"}}, secret=None)
    assert response.status_code == 401


def test_wrong_secret_is_rejected(configured_secret: None) -> None:
    response = _post({"type": "UPDATE", "table": "shipments", "record": {"shipment_id": "SHP1"}}, secret="wrong")
    assert response.status_code == 401


def test_secret_required_even_when_none_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unconfigured WEBHOOK_SECRET (the out-of-the-box local/dev state)
    must reject every call, not silently accept an empty/missing secret."""
    monkeypatch.setattr(webhooks_api, "get_settings", lambda: SimpleNamespace(webhook_secret=None))
    response = _post({"type": "UPDATE", "table": "shipments", "record": {"shipment_id": "SHP1"}}, secret="")
    assert response.status_code == 401


def test_valid_secret_with_shipments_update_invalidates_the_shipment(
    configured_secret: None, fake_redis: FakeRedis
) -> None:
    shipment_id = "SHP1006"
    cache.set_json(cache.shipment_key(shipment_id), {"stale": True}, 30)
    cache.set_json(cache.shipment_context_key(shipment_id), {"stale": True}, 30)

    response = _post(
        {
            "type": "UPDATE",
            "table": "shipments",
            "record": {"shipment_id": shipment_id, "current_status": "IN_TRANSIT"},
            "old_record": {"shipment_id": shipment_id, "current_status": "PLANNED"},
        }
    )

    assert response.status_code == 200
    assert response.json() == {"status": "invalidated", "table": "shipments"}
    assert fake_redis.get(cache._generation_key("shipment", shipment_id)) == "1"
    assert fake_redis.get(cache._generation_key("shipment-context", shipment_id)) == "1"


def test_delete_uses_old_record_since_record_is_null(configured_secret: None, fake_redis: FakeRedis) -> None:
    shipment_id = "SHP1006"
    cache.set_json(cache.shipment_key(shipment_id), {"stale": True}, 30)

    response = _post(
        {"type": "DELETE", "table": "shipments", "record": None, "old_record": {"shipment_id": shipment_id}}
    )

    assert response.status_code == 200
    assert fake_redis.get(cache._generation_key("shipment", shipment_id)) == "1"


def test_dock_state_tables_sweep_the_whole_dock_board(configured_secret: None, fake_redis: FakeRedis) -> None:
    cache.set_json(cache.dock_board_key("SHP1"), [{"a": 1}], 30)
    cache.set_json(cache.dock_board_key("SHP2"), [{"b": 1}], 30)

    response = _post({"type": "UPDATE", "table": "appointment_slots", "record": {"slot_id": "SLOT1"}})

    assert response.status_code == 200
    assert fake_redis.get(cache._generation_key("dock-board", "global")) == "1"


def test_unrecognized_table_is_ignored_not_errored(configured_secret: None, fake_redis: FakeRedis) -> None:
    response = _post({"type": "INSERT", "table": "eta_updates", "record": {"eta_update_id": "ETA1"}})
    assert response.status_code == 200
    assert response.json() == {"status": "ignored", "table": "eta_updates"}


def test_drivers_vehicles_facilities_docks_carriers_invalidate_their_own_entity(
    configured_secret: None, fake_redis: FakeRedis
) -> None:
    cache.set_json(cache.driver_profile_key("DRV1"), {"a": 1}, 30)
    cache.set_json(cache.vehicle_key("VEH1"), {"a": 1}, 30)
    cache.set_json(cache.facility_key("FAC1"), {"a": 1}, 30)
    cache.set_json(cache.docks_key("FAC1"), [{"a": 1}], 30)
    cache.set_json(cache.reference_key("carriers"), [{"a": 1}], 300)

    assert _post({"type": "UPDATE", "table": "drivers", "record": {"driver_id": "DRV1"}}).status_code == 200
    assert fake_redis.get(cache._generation_key("driver-profile", "DRV1")) == "1"

    assert _post({"type": "UPDATE", "table": "vehicles", "record": {"vehicle_id": "VEH1"}}).status_code == 200
    assert fake_redis.get(cache._generation_key("vehicle", "VEH1")) == "1"

    assert _post({"type": "UPDATE", "table": "facilities", "record": {"facility_id": "FAC1"}}).status_code == 200
    assert fake_redis.get(cache._generation_key("facility", "FAC1")) == "1"

    cache.set_json(cache.docks_key("FAC1"), [{"a": 1}], 30)
    assert _post({"type": "UPDATE", "table": "docks", "record": {"facility_id": "FAC1"}}).status_code == 200
    assert fake_redis.get(cache._generation_key("facility", "FAC1")) == "2"

    assert _post({"type": "UPDATE", "table": "carriers", "record": {"carrier_id": "CAR1"}}).status_code == 200
    assert fake_redis.get(cache._generation_key("reference", "carriers")) == "1"
