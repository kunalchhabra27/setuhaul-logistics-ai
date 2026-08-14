"""Reusable in-memory TMS fixtures for unit and API tests."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable

import pytest

from setuhaul.backend.tms.models import ACTIVE_CONTEXT_STATUSES
from setuhaul.backend.tms.service import TMSService

CARRIER_A = "CAR001"
CARRIER_B = "CAR002"
DRIVER_ONE = "DRV001"
DRIVER_AMBIGUOUS = "DRV002"
DRIVER_INACTIVE = "DRV003"
DRIVER_EMPTY = "DRV004"
VEHICLE_ONE = "VEH001"
VEHICLE_TWO = "VEH002"
VEHICLE_MAINTENANCE = "VEH003"
SHIPMENT_ONE = "SHP001"
SHIPMENT_TWO = "SHP002"
SHIPMENT_THREE = "SHP003"
FACILITY = "FAC-JAI-01"


class FakeRepository:
    """Small in-memory implementation of the repository service contract."""

    def __init__(self):
        self.drivers: dict[str, dict[str, Any]] = {
            DRIVER_ONE: {
                "driver_id": DRIVER_ONE, "carrier_id": CARRIER_A,
                "driver_name": "Ravi", "phone": "+91001",
                "licence_number": None, "home_base_city": "Jaipur", "driver_status": "ACTIVE",
            },
            DRIVER_AMBIGUOUS: {
                "driver_id": DRIVER_AMBIGUOUS, "carrier_id": CARRIER_A,
                "driver_name": "Aman", "phone": "+91002",
                "licence_number": None, "home_base_city": "Delhi", "driver_status": "ACTIVE",
            },
            DRIVER_INACTIVE: {
                "driver_id": DRIVER_INACTIVE, "carrier_id": CARRIER_A,
                "driver_name": "Inactive", "phone": "+91003",
                "licence_number": None, "home_base_city": None, "driver_status": "INACTIVE",
            },
            DRIVER_EMPTY: {
                "driver_id": DRIVER_EMPTY, "carrier_id": CARRIER_B,
                "driver_name": "Available", "phone": "+91004",
                "licence_number": None, "home_base_city": None, "driver_status": "ACTIVE",
            },
        }
        self.vehicles: dict[str, dict[str, Any]] = {
            VEHICLE_ONE: {
                "vehicle_id": VEHICLE_ONE, "carrier_id": CARRIER_A,
                "registration_number": "RJ01AA0001", "vehicle_type_code": "dry_van",
                "capacity_kg": 15000, "refrigeration_capable": False, "active_flag": True,
            },
            VEHICLE_TWO: {
                "vehicle_id": VEHICLE_TWO, "carrier_id": CARRIER_A,
                "registration_number": "RJ01AA0002", "vehicle_type_code": "reefer",
                "capacity_kg": 15000, "refrigeration_capable": True, "active_flag": True,
            },
            VEHICLE_MAINTENANCE: {
                "vehicle_id": VEHICLE_MAINTENANCE, "carrier_id": CARRIER_A,
                "registration_number": "RJ01AA0003", "vehicle_type_code": "dry_van",
                "capacity_kg": 15000, "refrigeration_capable": False, "active_flag": False,
            },
        }
        self.shipments: dict[str, dict[str, Any]] = {
            SHIPMENT_ONE: self._shipment(SHIPMENT_ONE, DRIVER_ONE, VEHICLE_ONE, "IN_TRANSIT"),
            SHIPMENT_TWO: self._shipment(SHIPMENT_TWO, DRIVER_AMBIGUOUS, VEHICLE_ONE, "PLANNED"),
            SHIPMENT_THREE: self._shipment(SHIPMENT_THREE, DRIVER_AMBIGUOUS, VEHICLE_TWO, "WAITING"),
        }
        # WMS/check-in trace data, keyed by shipment_id -- empty by default,
        # tests that care about tracing populate these directly.
        self.appointments: dict[str, dict[str, Any]] = {}
        self.checkins: dict[str, dict[str, Any]] = {}
        self.facilities: dict[str, dict[str, Any]] = {
            FACILITY: {"facility_id": FACILITY, "facility_name": "Jaipur DC", "city": "Jaipur", "state": "Rajasthan"},
        }
        self.staff_facility_assignments: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _shipment(shipment_id: str, driver_id: str, vehicle_id: str, status: str) -> dict[str, Any]:
        return {
            "shipment_id": shipment_id, "driver_id": driver_id,
            "vehicle_id": vehicle_id, "destination_facility_id": FACILITY,
            "product_category": "dry_freight", "priority_code": "NORMAL",
            "original_eta_ts": "2026-08-08T12:00:00+00:00",
            "expected_unload_min": 40, "current_status": status,
            "created_at": "2026-08-08T00:00:00+00:00", "updated_at": "2026-08-08T00:00:00+00:00",
        }

    def get_driver(self, driver_id: str):
        return deepcopy(self.drivers.get(driver_id))

    def get_driver_by_phone(self, phone: str):
        return next((deepcopy(row) for row in self.drivers.values() if row["phone"] == phone), None)

    def list_drivers(self, *, limit: int = 200, offset: int = 0):
        rows = list(self.drivers.values())
        return deepcopy(rows[offset:offset + limit])

    def create_driver(self, payload):
        driver_id = "DRV099"
        row = {"driver_id": driver_id, **payload}
        self.drivers[driver_id] = row
        return deepcopy(row)

    def update_driver(self, driver_id, payload):
        if driver_id not in self.drivers:
            return None
        self.drivers[driver_id].update(payload)
        return deepcopy(self.drivers[driver_id])

    def get_vehicle(self, vehicle_id: str):
        return deepcopy(self.vehicles.get(vehicle_id))

    def get_vehicles(self, vehicle_ids: Iterable[str]):
        return {item: deepcopy(self.vehicles[item]) for item in vehicle_ids if item in self.vehicles}

    def list_vehicles(self, *, limit: int = 200, offset: int = 0):
        rows = list(self.vehicles.values())
        return deepcopy(rows[offset:offset + limit])

    def create_vehicle(self, payload):
        vehicle_id = "VEH099"
        row = {"vehicle_id": vehicle_id, **payload}
        self.vehicles[vehicle_id] = row
        return deepcopy(row)

    def update_vehicle(self, vehicle_id, payload):
        if vehicle_id not in self.vehicles:
            return None
        self.vehicles[vehicle_id].update(payload)
        return deepcopy(self.vehicles[vehicle_id])

    def get_shipment(self, shipment_id: str):
        return deepcopy(self.shipments.get(shipment_id))

    def list_shipments(
        self, *, driver_id=None, destination_facility_id=None, status=None,
        active_only=False, unassigned_only=False, include_archived=False, limit=100, offset=0,
    ):
        rows = list(self.shipments.values())
        if driver_id:
            rows = [row for row in rows if row["driver_id"] == driver_id]
        if destination_facility_id:
            rows = [row for row in rows if row["destination_facility_id"] == destination_facility_id]
        if status:
            rows = [row for row in rows if row["current_status"] == status.value]
        if active_only:
            active = {item.value for item in ACTIVE_CONTEXT_STATUSES}
            rows = [row for row in rows if row["current_status"] in active]
        if unassigned_only:
            rows = [row for row in rows if not row.get("driver_id")]
        if not include_archived:
            rows = [row for row in rows if not row.get("archived_flag")]
        return deepcopy(rows[offset:offset + limit])

    def generate_shipment_id(self) -> str:
        return "SHP099"

    def create_shipment(self, payload):
        shipment_id = payload.get("shipment_id") or "SHP099"
        row = {"shipment_id": shipment_id, **payload}
        self.shipments[shipment_id] = row
        return deepcopy(row)

    def update_shipment(self, shipment_id, payload):
        if shipment_id not in self.shipments:
            return None
        self.shipments[shipment_id].update(payload)
        return deepcopy(self.shipments[shipment_id])

    def current_appointment_for_shipment(self, shipment_id: str):
        return deepcopy(self.appointments.get(shipment_id))

    def cancel_current_appointment(self, shipment_id: str, now_iso: str) -> None:
        appointment = self.appointments.get(shipment_id)
        if appointment is not None:
            appointment["appointment_status"] = "CANCELLED"
            appointment["is_current"] = 0
            appointment["cancelled_at"] = now_iso

    def checkin_for_shipment(self, shipment_id: str):
        return deepcopy(self.checkins.get(shipment_id))

    def list_shipment_reference_data(self):
        seen = set()
        origins = []
        categories = set()
        for row in self.shipments.values():
            name, city = row.get("origin_name"), row.get("origin_city")
            if name and (name, city) not in seen:
                seen.add((name, city))
                origins.append({"origin_name": name, "origin_city": city})
            category = row.get("product_category")
            if category:
                categories.add(category)
        return {"origins": origins, "product_categories": sorted(categories)}

    def get_facility(self, facility_id: str):
        return deepcopy(self.facilities.get(facility_id))

    def get_staff_facility(self, staff_user_id: str):
        return deepcopy(self.staff_facility_assignments.get(staff_user_id))

    def register_staff_facility(self, staff_user_id: str, facility_id: str):
        row = {"staff_user_id": staff_user_id, "facility_id": facility_id}
        self.staff_facility_assignments[staff_user_id] = row
        return deepcopy(row)


@pytest.fixture()
def repository() -> FakeRepository:
    return FakeRepository()


@pytest.fixture()
def service(repository: FakeRepository) -> TMSService:
    return TMSService(repository)  # type: ignore[arg-type]
