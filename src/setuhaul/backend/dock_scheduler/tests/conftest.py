"""Reusable in-memory dock_scheduler fixtures backed by FakeSupabaseClient."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from setuhaul.backend._testing.fake_supabase import FakeSupabaseClient
from setuhaul.backend.dock_scheduler.repository import (
    _FUTURE_SLOTS_LAST_CHECKED,
    DockSchedulerRepository,
)
from setuhaul.backend.dock_scheduler.service import DockSchedulerService
from setuhaul.infrastructure import redis_client


@pytest.fixture(autouse=True)
def _no_real_redis_by_default(monkeypatch: pytest.MonkeyPatch):
    """See tms/tests/conftest.py's fixture of the same name -- REDIS_URL in
    .env points at a real shared instance; disable it by default so cached
    results keyed by shipment/facility id (SHP_NORMAL etc. are reused
    constants across many tests) can't leak between tests. test_caching.py's
    `fake_redis` fixture opts back in per-test."""
    redis_client.reset_client_cache()
    monkeypatch.setattr(redis_client, "get_client", lambda: None)
    yield
    redis_client.reset_client_cache()

FACILITY = "FAC-JAI-01"
DOCK_STANDARD_1 = "DOCK-D1"
DOCK_STANDARD_2 = "DOCK-D2"
DOCK_REEFER = "DOCK-D5"
SHP_NORMAL = "SHP-TEST1"
SHP_REEFER = "SHP-TEST2"
SHP_OCCUPANT = "SHP-TEST3"

# compatible_slots() bounds its appointment_slots read relative to wall-clock
# "now" (see repository.py), so fixture timestamps hardcoded to a fixed
# calendar date rot the instant real time moves past that date -- exactly
# the staleness bug ensure_future_slots() exists to fix in production, just
# showing up in the test suite instead. SEED_DAY floats forward with the
# real clock so these fixtures stay "today" no matter when the suite runs.
SEED_DAY = datetime.now(timezone.utc).date()


def seed_ts(hour: int, minute: int = 0, day_offset: int = 0) -> str:
    """An ISO timestamp on (SEED_DAY + day_offset), IST offset to match the
    real seed data / production convention (see dock_scheduler/repository.py's
    module docstring)."""
    day = SEED_DAY + timedelta(days=day_offset)
    return f"{day.isoformat()}T{hour:02d}:{minute:02d}:00+05:30"


def _shipment(shipment_id: str, **overrides) -> dict:
    base = {
        "shipment_id": shipment_id,
        "order_reference": f"ORD-{shipment_id}",
        "carrier_id": "CAR001",
        "driver_id": "DRV001",
        "vehicle_id": "VEH001",
        "origin_name": "Depot",
        "origin_city": "Jaipur",
        "destination_facility_id": FACILITY,
        "customer_name": "Test Customer",
        "product_category": "General",
        "load_weight_kg": 10000,
        "required_dock_type": "STANDARD",
        "temperature_control_required": 0,
        "priority_code": "NORMAL",
        "planned_departure_ts": seed_ts(4),
        "original_eta_ts": seed_ts(8),
        "latest_eta_ts": None,
        "expected_unload_min": 45,
        "current_status": "PLANNED",
        "created_at": seed_ts(12, day_offset=-3),
        "updated_at": seed_ts(12, day_offset=-3),
    }
    base.update(overrides)
    return base


@pytest.fixture()
def tables() -> dict[str, list[dict]]:
    return {
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
                "active_flag": 1,
            }
        ],
        "docks": [
            {
                "dock_id": DOCK_STANDARD_1,
                "facility_id": FACILITY,
                "dock_code": "D1",
                "dock_type": "STANDARD",
                "supports_refrigerated": 0,
                "max_vehicle_weight_kg": 20000,
                "dock_status": "ACTIVE",
            },
            {
                "dock_id": DOCK_STANDARD_2,
                "facility_id": FACILITY,
                "dock_code": "D2",
                "dock_type": "STANDARD",
                "supports_refrigerated": 0,
                "max_vehicle_weight_kg": 20000,
                "dock_status": "ACTIVE",
            },
            {
                "dock_id": DOCK_REEFER,
                "facility_id": FACILITY,
                "dock_code": "D5",
                "dock_type": "REEFER",
                "supports_refrigerated": 1,
                "max_vehicle_weight_kg": 20000,
                "dock_status": "ACTIVE",
            },
        ],
        "facility_rules": [
            {
                "rule_id": "RULE-REEFER",
                "facility_id": FACILITY,
                "rule_type": "REEFER_DOCK_REQUIRED",
                "rule_value": "TRUE",
                "description": "Temperature-controlled loads must use the reefer dock.",
                "active_flag": 1,
            },
            {
                "rule_id": "RULE-LAST-START",
                "facility_id": FACILITY,
                "rule_type": "LAST_NEW_START_TIME",
                "rule_value": "21:00",
                "description": "No new unload after 21:00.",
                "active_flag": 1,
            },
        ],
        "appointment_slots": [
            {
                "slot_id": "SLOT-D1-0800",
                "facility_id": FACILITY,
                "dock_id": DOCK_STANDARD_1,
                "slot_start_ts": seed_ts(8),
                "slot_end_ts": seed_ts(9),
                "slot_status": "OPEN",
                "block_reason": None,
                "created_at": seed_ts(12, day_offset=-3),
            },
            {
                "slot_id": "SLOT-D1-0900",
                "facility_id": FACILITY,
                "dock_id": DOCK_STANDARD_1,
                "slot_start_ts": seed_ts(9),
                "slot_end_ts": seed_ts(10),
                "slot_status": "OPEN",
                "block_reason": None,
                "created_at": seed_ts(12, day_offset=-3),
            },
            {
                "slot_id": "SLOT-D2-0800",
                "facility_id": FACILITY,
                "dock_id": DOCK_STANDARD_2,
                "slot_start_ts": seed_ts(8),
                "slot_end_ts": seed_ts(9),
                "slot_status": "OPEN",
                "block_reason": None,
                "created_at": seed_ts(12, day_offset=-3),
            },
            {
                "slot_id": "SLOT-D5-0800",
                "facility_id": FACILITY,
                "dock_id": DOCK_REEFER,
                "slot_start_ts": seed_ts(8),
                "slot_end_ts": seed_ts(9),
                "slot_status": "OPEN",
                "block_reason": None,
                "created_at": seed_ts(12, day_offset=-3),
            },
        ],
        "appointments": [],
        "slot_holds": [],
        "shipments": [
            _shipment(SHP_NORMAL),
            _shipment(
                SHP_REEFER,
                required_dock_type="REEFER",
                temperature_control_required=1,
                priority_code="HIGH",
            ),
            _shipment(SHP_OCCUPANT, priority_code="LOW", driver_id="DRV003"),
        ],
        "drivers": [
            {"driver_id": "DRV001", "driver_name": "Rajesh Kumar"},
            {"driver_id": "DRV003", "driver_name": "Mukesh Yadav"},
        ],
        "eta_updates": [],
    }


@pytest.fixture()
def repository(tables: dict[str, list[dict]]) -> DockSchedulerRepository:
    # ensure_future_slots() caches "already checked this facility" at
    # module scope (see its docstring) -- clear it so one test's cached
    # result can't hide a real bug in the next test's fresh fixture data.
    _FUTURE_SLOTS_LAST_CHECKED.clear()
    return DockSchedulerRepository(FakeSupabaseClient(tables))


@pytest.fixture()
def service(repository: DockSchedulerRepository) -> DockSchedulerService:
    return DockSchedulerService(repository)
