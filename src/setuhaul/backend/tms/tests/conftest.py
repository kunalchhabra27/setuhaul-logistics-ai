"""Reusable in-memory TMS fixtures for unit and API tests."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Iterable
from uuid import UUID

import pytest

from setuhaul.backend.tms.models import ACTIVE_CONTEXT_STATUSES, ShipmentStatus
from setuhaul.backend.tms.service import TMSService

CARRIER_A = UUID("10000000-0000-0000-0000-000000000001")
CARRIER_B = UUID("10000000-0000-0000-0000-000000000002")
DRIVER_ONE = UUID("30000000-0000-0000-0000-000000000001")
DRIVER_AMBIGUOUS = UUID("30000000-0000-0000-0000-000000000002")
DRIVER_INACTIVE = UUID("30000000-0000-0000-0000-000000000003")
DRIVER_EMPTY = UUID("30000000-0000-0000-0000-000000000004")
VEHICLE_ONE = UUID("40000000-0000-0000-0000-000000000001")
VEHICLE_TWO = UUID("40000000-0000-0000-0000-000000000002")
VEHICLE_MAINTENANCE = UUID("40000000-0000-0000-0000-000000000003")
SHIPMENT_ONE = UUID("50000000-0000-0000-0000-000000000001")
SHIPMENT_TWO = UUID("50000000-0000-0000-0000-000000000002")
SHIPMENT_THREE = UUID("50000000-0000-0000-0000-000000000003")
FACILITY = UUID("20000000-0000-0000-0000-000000000001")


def _timestamps() -> dict[str, str]:
    value = datetime(2026, 8, 8, tzinfo=timezone.utc).isoformat()
    return {"created_at": value, "updated_at": value}


class FakeRepository:
    """Small in-memory implementation of the repository service contract."""

    def __init__(self):
        self.drivers: dict[UUID, dict[str, Any]] = {
            DRIVER_ONE: {
                "driver_id": str(DRIVER_ONE), "carrier_id": str(CARRIER_A),
                "driver_code": "DRV-001", "name": "Ravi", "phone": "+91001",
                "email": None, "license_number": None, "license_expiry": None,
                "home_base": "Jaipur", "active_flag": True, "status": "active", **_timestamps(),
            },
            DRIVER_AMBIGUOUS: {
                "driver_id": str(DRIVER_AMBIGUOUS), "carrier_id": str(CARRIER_A),
                "driver_code": "DRV-002", "name": "Aman", "phone": "+91002",
                "email": None, "license_number": None, "license_expiry": None,
                "home_base": "Delhi", "active_flag": True, "status": "active", **_timestamps(),
            },
            DRIVER_INACTIVE: {
                "driver_id": str(DRIVER_INACTIVE), "carrier_id": str(CARRIER_A),
                "driver_code": "DRV-003", "name": "Inactive", "phone": "+91003",
                "email": None, "license_number": None, "license_expiry": None,
                "home_base": None, "active_flag": False, "status": "inactive", **_timestamps(),
            },
            DRIVER_EMPTY: {
                "driver_id": str(DRIVER_EMPTY), "carrier_id": str(CARRIER_B),
                "driver_code": "DRV-004", "name": "Available", "phone": "+91004",
                "email": None, "license_number": None, "license_expiry": None,
                "home_base": None, "active_flag": True, "status": "active", **_timestamps(),
            },
        }
        self.vehicles: dict[UUID, dict[str, Any]] = {
            VEHICLE_ONE: {
                "vehicle_id": str(VEHICLE_ONE), "carrier_id": str(CARRIER_A),
                "vehicle_number": "RJ01AA0001", "vehicle_type": "dry_van",
                "length_ft": 32, "capacity_weight_kg": 15000,
                "refrigeration_required": False, "active_flag": True, "status": "active", **_timestamps(),
            },
            VEHICLE_TWO: {
                "vehicle_id": str(VEHICLE_TWO), "carrier_id": str(CARRIER_A),
                "vehicle_number": "RJ01AA0002", "vehicle_type": "reefer",
                "length_ft": 32, "capacity_weight_kg": 15000,
                "refrigeration_required": True, "active_flag": True, "status": "active", **_timestamps(),
            },
            VEHICLE_MAINTENANCE: {
                "vehicle_id": str(VEHICLE_MAINTENANCE), "carrier_id": str(CARRIER_A),
                "vehicle_number": "RJ01AA0003", "vehicle_type": "dry_van",
                "length_ft": 32, "capacity_weight_kg": 15000,
                "refrigeration_required": False, "active_flag": False, "status": "maintenance", **_timestamps(),
            },
        }
        self.shipments: dict[UUID, dict[str, Any]] = {
            SHIPMENT_ONE: self._shipment(SHIPMENT_ONE, DRIVER_ONE, VEHICLE_ONE, "in_transit"),
            SHIPMENT_TWO: self._shipment(SHIPMENT_TWO, DRIVER_AMBIGUOUS, VEHICLE_ONE, "planned"),
            SHIPMENT_THREE: self._shipment(SHIPMENT_THREE, DRIVER_AMBIGUOUS, VEHICLE_TWO, "exception"),
        }

    @staticmethod
    def _shipment(shipment_id: UUID, driver_id: UUID, vehicle_id: UUID, status: str) -> dict[str, Any]:
        return {
            "shipment_id": str(shipment_id), "driver_id": str(driver_id),
            "vehicle_id": str(vehicle_id), "origin_id": None,
            "destination_id": str(FACILITY), "product_class": "dry_freight",
            "priority": 2, "planned_eta": "2026-08-08T12:00:00+00:00",
            "expected_unload_minutes": 40, "status": status, **_timestamps(),
        }

    def get_driver(self, driver_id: UUID):
        return deepcopy(self.drivers.get(driver_id))

    def get_driver_by_phone(self, phone: str):
        return next((deepcopy(row) for row in self.drivers.values() if row["phone"] == phone), None)

    def create_driver(self, payload):
        driver_id = UUID("30000000-0000-0000-0000-000000000099")
        row = {"driver_id": str(driver_id), **payload, **_timestamps()}
        self.drivers[driver_id] = row
        return deepcopy(row)

    def update_driver(self, driver_id, payload):
        if driver_id not in self.drivers:
            return None
        self.drivers[driver_id].update(payload)
        return deepcopy(self.drivers[driver_id])

    def get_vehicle(self, vehicle_id: UUID):
        return deepcopy(self.vehicles.get(vehicle_id))

    def get_vehicles(self, vehicle_ids: Iterable[UUID]):
        return {item: deepcopy(self.vehicles[item]) for item in vehicle_ids if item in self.vehicles}

    def create_vehicle(self, payload):
        vehicle_id = UUID("40000000-0000-0000-0000-000000000099")
        row = {"vehicle_id": str(vehicle_id), **payload, **_timestamps()}
        self.vehicles[vehicle_id] = row
        return deepcopy(row)

    def update_vehicle(self, vehicle_id, payload):
        if vehicle_id not in self.vehicles:
            return None
        self.vehicles[vehicle_id].update(payload)
        return deepcopy(self.vehicles[vehicle_id])

    def get_shipment(self, shipment_id: UUID):
        return deepcopy(self.shipments.get(shipment_id))

    def list_shipments(self, *, driver_id=None, destination_id=None, status=None, active_only=False, limit=100, offset=0):
        rows = list(self.shipments.values())
        if driver_id:
            rows = [row for row in rows if row["driver_id"] == str(driver_id)]
        if destination_id:
            rows = [row for row in rows if row["destination_id"] == str(destination_id)]
        if status:
            rows = [row for row in rows if row["status"] == status.value]
        if active_only:
            active = {item.value for item in ACTIVE_CONTEXT_STATUSES}
            rows = [row for row in rows if row["status"] in active]
        return deepcopy(rows[offset:offset + limit])

    def create_shipment(self, payload):
        shipment_id = UUID("50000000-0000-0000-0000-000000000099")
        row = {"shipment_id": str(shipment_id), **payload, **_timestamps()}
        self.shipments[shipment_id] = row
        return deepcopy(row)

    def update_shipment(self, shipment_id, payload):
        if shipment_id not in self.shipments:
            return None
        self.shipments[shipment_id].update(payload)
        return deepcopy(self.shipments[shipment_id])


@pytest.fixture()
def repository() -> FakeRepository:
    return FakeRepository()


@pytest.fixture()
def service(repository: FakeRepository) -> TMSService:
    return TMSService(repository)  # type: ignore[arg-type]
