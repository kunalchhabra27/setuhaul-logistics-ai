"""Reusable in-memory driver_chat_eta fixtures backed by FakeSupabaseClient.

Both DriverChatRepository and (since the dock-booking unification) the
DockSchedulerRepository it now delegates to read/write the same underlying
tables in this dict -- there is no separate "WMS database" to fake, exactly
as in the real Supabase project.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from setuhaul.backend._testing.fake_supabase import FakeSupabaseClient
from setuhaul.backend.dock_scheduler.repository import _FUTURE_SLOTS_LAST_CHECKED
from setuhaul.backend.driver_chat_eta.auth import DriverPrincipal
from setuhaul.backend.driver_chat_eta.repository import DriverChatRepository
from setuhaul.backend.driver_chat_eta.service import DriverChatService
from setuhaul.infrastructure import redis_client


@pytest.fixture(autouse=True)
def _no_real_redis_by_default(monkeypatch: pytest.MonkeyPatch):
    """See tms/tests/conftest.py's fixture of the same name -- REDIS_URL in
    .env points at a real shared instance; disable it by default so cached
    results keyed by driver/shipment id (DRIVER_ID/SHIPMENT_ID are reused
    constants across many tests) can't leak between tests. test_caching.py's
    `fake_redis` fixture opts back in per-test."""
    redis_client.reset_client_cache()
    monkeypatch.setattr(redis_client, "get_client", lambda: None)
    yield
    redis_client.reset_client_cache()

FACILITY = "FAC-1"
DOCK_STANDARD = "DOCK-1"
DOCK_REEFER = "DOCK-2"
DRIVER_ID = "DRV001"
SHIPMENT_ID = "SHP001"

# Slot/ETA timestamps are computed relative to "now" (not a hardcoded past
# date) so _feasible_slots's ETA-window filter (start >= now - 15min)
# passes regardless of when the test suite actually runs.
_NOW = datetime.utcnow()


def _iso(delta: timedelta) -> str:
    return (_NOW + delta).isoformat()


def _base_tables() -> dict:
    return {
        "drivers": [
            {
                "driver_id": DRIVER_ID,
                "carrier_id": "CAR001",
                "driver_name": "Rajesh Kumar",
                "phone": "+91-9000010001",
                "licence_number": "RJ14DL1001",
                "home_base_city": "Jaipur",
                "driver_status": "ACTIVE",
            }
        ],
        "vehicles": [
            {
                "vehicle_id": "VEH001",
                "carrier_id": "CAR001",
                "registration_number": "RJ14DL1001",
                "vehicle_type_code": "dry_van",
                "capacity_kg": 15000,
                "refrigeration_capable": 0,
                "active_flag": 1,
            }
        ],
        "facilities": [
            {
                "facility_id": FACILITY,
                "facility_name": "Jaipur DC",
                "city": "Jaipur",
                "state": "Rajasthan",
                "timezone": "Asia/Kolkata",
                "open_time": "06:00",
                "close_time": "22:00",
                "checkin_grace_min": 30,
                "default_unload_min": 60,
            }
        ],
        "docks": [
            {
                "dock_id": DOCK_STANDARD,
                "facility_id": FACILITY,
                "dock_code": "D1",
                "dock_type": "STANDARD",
                "supports_refrigerated": 0,
                "max_vehicle_weight_kg": 20000,
                "dock_status": "ACTIVE",
            },
            {
                "dock_id": DOCK_REEFER,
                "facility_id": FACILITY,
                "dock_code": "D2",
                "dock_type": "REEFER",
                "supports_refrigerated": 1,
                "max_vehicle_weight_kg": 20000,
                "dock_status": "ACTIVE",
            },
        ],
        "appointment_slots": [
            {
                "slot_id": "SLOT-1",
                "facility_id": FACILITY,
                "dock_id": DOCK_STANDARD,
                "slot_start_ts": _iso(timedelta(hours=2)),
                "slot_end_ts": _iso(timedelta(hours=3)),
                "slot_status": "OPEN",
            },
            {
                "slot_id": "SLOT-2",
                "facility_id": FACILITY,
                "dock_id": DOCK_STANDARD,
                "slot_start_ts": _iso(timedelta(hours=3)),
                "slot_end_ts": _iso(timedelta(hours=4)),
                "slot_status": "OPEN",
            },
            {
                "slot_id": "SLOT-REEFER",
                "facility_id": FACILITY,
                "dock_id": DOCK_REEFER,
                "slot_start_ts": _iso(timedelta(hours=2)),
                "slot_end_ts": _iso(timedelta(hours=3)),
                "slot_status": "OPEN",
            },
        ],
        "appointments": [],
        "slot_holds": [],
        "shipments": [
            {
                "shipment_id": SHIPMENT_ID,
                "order_reference": "ORD-1",
                "driver_id": DRIVER_ID,
                "vehicle_id": "VEH001",
                "origin_name": "Depot",
                "origin_city": "Jaipur",
                "destination_facility_id": FACILITY,
                "product_category": "General",
                "load_weight_kg": 5000,
                "required_dock_type": "STANDARD",
                "temperature_control_required": 0,
                "priority_code": "NORMAL",
                "original_eta_ts": _iso(timedelta(hours=2)),
                "latest_eta_ts": None,
                "expected_unload_min": 45,
                "current_status": "PLANNED",
            }
        ],
        "eta_updates": [],
        "driver_exceptions": [],
        "chat_threads": [],
        "chat_messages": [],
        "facility_checkins": [],
        "facility_rules": [],
        "carriers": [
            {"carrier_id": "CAR001", "carrier_name": "Rajasthan Freight Co", "active_flag": 1},
            {"carrier_id": "CAR002", "carrier_name": "Delhi Logistics Ltd", "active_flag": 1},
        ],
    }


@pytest.fixture()
def tables() -> dict:
    return _base_tables()


@pytest.fixture()
def service(tables: dict) -> DriverChatService:
    # See dock_scheduler/tests/conftest.py -- ensure_future_slots() caches
    # "already checked this facility" at module scope, which would
    # otherwise leak between tests that reuse the same FACILITY id.
    _FUTURE_SLOTS_LAST_CHECKED.clear()
    return DriverChatService(DriverChatRepository(FakeSupabaseClient(tables)))


@pytest.fixture()
def principal() -> DriverPrincipal:
    return DriverPrincipal(user_id=DRIVER_ID, email="driver@example.com", access_token="fake-token")
