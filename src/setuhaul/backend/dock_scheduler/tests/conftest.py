"""Reusable in-memory dock_scheduler fixtures backed by FakeSupabaseClient."""

from __future__ import annotations

import pytest

from setuhaul.backend._testing.fake_supabase import FakeSupabaseClient
from setuhaul.backend.dock_scheduler.repository import DockSchedulerRepository
from setuhaul.backend.dock_scheduler.service import DockSchedulerService

FACILITY = "FAC-JAI-01"
DOCK_STANDARD_1 = "DOCK-D1"
DOCK_STANDARD_2 = "DOCK-D2"
DOCK_REEFER = "DOCK-D5"
SHP_NORMAL = "SHP-TEST1"
SHP_REEFER = "SHP-TEST2"
SHP_OCCUPANT = "SHP-TEST3"


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
        "planned_departure_ts": "2026-08-04T04:00:00+05:30",
        "original_eta_ts": "2026-08-04T08:00:00+05:30",
        "latest_eta_ts": None,
        "expected_unload_min": 45,
        "current_status": "PLANNED",
        "created_at": "2026-08-01T12:00:00+05:30",
        "updated_at": "2026-08-01T12:00:00+05:30",
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
                "slot_start_ts": "2026-08-04T08:00:00+05:30",
                "slot_end_ts": "2026-08-04T09:00:00+05:30",
                "slot_status": "OPEN",
                "block_reason": None,
                "created_at": "2026-08-01T12:00:00+05:30",
            },
            {
                "slot_id": "SLOT-D1-0900",
                "facility_id": FACILITY,
                "dock_id": DOCK_STANDARD_1,
                "slot_start_ts": "2026-08-04T09:00:00+05:30",
                "slot_end_ts": "2026-08-04T10:00:00+05:30",
                "slot_status": "OPEN",
                "block_reason": None,
                "created_at": "2026-08-01T12:00:00+05:30",
            },
            {
                "slot_id": "SLOT-D2-0800",
                "facility_id": FACILITY,
                "dock_id": DOCK_STANDARD_2,
                "slot_start_ts": "2026-08-04T08:00:00+05:30",
                "slot_end_ts": "2026-08-04T09:00:00+05:30",
                "slot_status": "OPEN",
                "block_reason": None,
                "created_at": "2026-08-01T12:00:00+05:30",
            },
            {
                "slot_id": "SLOT-D5-0800",
                "facility_id": FACILITY,
                "dock_id": DOCK_REEFER,
                "slot_start_ts": "2026-08-04T08:00:00+05:30",
                "slot_end_ts": "2026-08-04T09:00:00+05:30",
                "slot_status": "OPEN",
                "block_reason": None,
                "created_at": "2026-08-01T12:00:00+05:30",
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
            _shipment(SHP_OCCUPANT, priority_code="LOW"),
        ],
        "eta_updates": [],
    }


@pytest.fixture()
def repository(tables: dict[str, list[dict]]) -> DockSchedulerRepository:
    return DockSchedulerRepository(FakeSupabaseClient(tables))


@pytest.fixture()
def service(repository: DockSchedulerRepository) -> DockSchedulerService:
    return DockSchedulerService(repository)
