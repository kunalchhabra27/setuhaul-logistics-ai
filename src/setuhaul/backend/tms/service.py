"""Deterministic TMS business validation and context resolution."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from setuhaul.backend.tms.exceptions import (
    BusinessValidationError,
    DriverNotFoundError,
    ShipmentNotFoundError,
    VehicleNotFoundError,
)
from setuhaul.backend.tms.models import (
    ACTIVE_CONTEXT_STATUSES,
    ContextResolution,
    DriverContextResponse,
    DriverCreate,
    DriverResponse,
    DriverStatus,
    DriverSummary,
    DriverUpdate,
    FacilityResponse,
    ShipmentCandidate,
    ShipmentContextResponse,
    ShipmentCreate,
    ShipmentResponse,
    ShipmentStatus,
    ShipmentUpdate,
    VehicleContext,
    VehicleCreate,
    VehicleResponse,
    VehicleUpdate,
)
from setuhaul.backend.tms.repository import TMSRepository
from setuhaul.infrastructure.sms import send_sms

logger = logging.getLogger(__name__)


class TMSService:
    """Coordinate TMS persistence while preserving domain boundaries."""

    def __init__(self, repository: TMSRepository):
        self.repository = repository

    # -- drivers --------------------------------------------------------

    def get_driver(self, driver_id: str) -> DriverResponse:
        row = self.repository.get_driver(driver_id)
        if row is None:
            raise DriverNotFoundError(f"Driver {driver_id} was not found.")
        return DriverResponse.model_validate(row)

    def get_driver_by_phone(self, phone: str) -> DriverResponse:
        row = self.repository.get_driver_by_phone(phone)
        if row is None:
            raise DriverNotFoundError(f"No driver was found for phone {phone}.")
        return DriverResponse.model_validate(row)

    def list_drivers(self, *, limit: int = 200, offset: int = 0) -> list[DriverResponse]:
        return [DriverResponse.model_validate(row) for row in self.repository.list_drivers(limit=limit, offset=offset)]

    def create_driver(self, request: DriverCreate) -> DriverResponse:
        row = self.repository.create_driver(request.model_dump(mode="json"))
        return DriverResponse.model_validate(row)

    def update_driver(self, driver_id: str, request: DriverUpdate) -> DriverResponse:
        self.get_driver(driver_id)
        row = self.repository.update_driver(driver_id, request.model_dump(mode="json", exclude_unset=True))
        if row is None:
            raise DriverNotFoundError(f"Driver {driver_id} was not found.")
        return DriverResponse.model_validate(row)

    def delete_driver(self, driver_id: str) -> DriverResponse:
        current = self.get_driver(driver_id)
        row = self.repository.update_driver(driver_id, {"driver_status": DriverStatus.INACTIVE.value})
        if row is None:
            raise DriverNotFoundError(f"Driver {driver_id} was not found.")
        # Preserve the historical row so driver_id is never reused.
        return DriverResponse.model_validate(row if row else current.model_dump())

    # -- vehicles ---------------------------------------------------------

    def get_vehicle(self, vehicle_id: str) -> VehicleResponse:
        row = self.repository.get_vehicle(vehicle_id)
        if row is None:
            raise VehicleNotFoundError(f"Vehicle {vehicle_id} was not found.")
        return VehicleResponse.model_validate(row)

    def list_vehicles(self, *, limit: int = 200, offset: int = 0) -> list[VehicleResponse]:
        return [VehicleResponse.model_validate(row) for row in self.repository.list_vehicles(limit=limit, offset=offset)]

    def create_vehicle(self, request: VehicleCreate) -> VehicleResponse:
        row = self.repository.create_vehicle(request.model_dump(mode="json"))
        return VehicleResponse.model_validate(row)

    def update_vehicle(self, vehicle_id: str, request: VehicleUpdate) -> VehicleResponse:
        self.get_vehicle(vehicle_id)
        row = self.repository.update_vehicle(vehicle_id, request.model_dump(mode="json", exclude_unset=True))
        if row is None:
            raise VehicleNotFoundError(f"Vehicle {vehicle_id} was not found.")
        return VehicleResponse.model_validate(row)

    # -- shipments ----------------------------------------------------------

    def get_shipment(self, shipment_id: str) -> ShipmentResponse:
        row = self.repository.get_shipment(shipment_id)
        if row is None:
            raise ShipmentNotFoundError(f"Shipment {shipment_id} was not found.")
        return ShipmentResponse.model_validate(row)

    def list_shipments(
        self,
        *,
        driver_id: str | None = None,
        destination_facility_id: str | None = None,
        status: ShipmentStatus | None = None,
        active_only: bool = False,
        unassigned_only: bool = False,
        include_archived: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ShipmentResponse]:
        return [
            ShipmentResponse.model_validate(row)
            for row in self.repository.list_shipments(
                driver_id=driver_id,
                destination_facility_id=destination_facility_id,
                status=status,
                active_only=active_only,
                unassigned_only=unassigned_only,
                include_archived=include_archived,
                limit=limit,
                offset=offset,
            )
        ]

    def archive_shipment(self, shipment_id: str) -> ShipmentResponse:
        current = self.get_shipment(shipment_id)
        if current.current_status is not ShipmentStatus.COMPLETED:
            raise BusinessValidationError("Only completed shipments can be archived.")
        row = self.repository.update_shipment(shipment_id, {"archived_flag": True})
        if row is None:
            raise ShipmentNotFoundError(f"Shipment {shipment_id} was not found.")
        return ShipmentResponse.model_validate(row)

    def unarchive_shipment(self, shipment_id: str) -> ShipmentResponse:
        row = self.repository.update_shipment(shipment_id, {"archived_flag": False})
        if row is None:
            raise ShipmentNotFoundError(f"Shipment {shipment_id} was not found.")
        return ShipmentResponse.model_validate(row)

    def create_shipment(self, request: ShipmentCreate) -> ShipmentResponse:
        if request.driver_id and request.vehicle_id:
            self._validate_active_assignment(request.driver_id, request.vehicle_id, request.current_status)
        payload = request.model_dump(mode="json")
        payload["shipment_id"] = payload.get("shipment_id") or f"SHP-{uuid.uuid4().hex[:8].upper()}"
        now = datetime.now(timezone.utc).isoformat()
        payload["created_at"] = now
        payload["updated_at"] = now
        row = self.repository.create_shipment(payload)
        shipment = ShipmentResponse.model_validate(row)
        if shipment.driver_id:
            self._notify_driver_assignment(shipment.driver_id, shipment)
        return shipment

    def update_shipment(self, shipment_id: str, request: ShipmentUpdate) -> ShipmentResponse:
        current = self.get_shipment(shipment_id)
        driver_id = request.driver_id if "driver_id" in request.model_fields_set else current.driver_id
        vehicle_id = request.vehicle_id if "vehicle_id" in request.model_fields_set else current.vehicle_id
        status = request.current_status or current.current_status
        if driver_id and vehicle_id:
            self._validate_active_assignment(driver_id, vehicle_id, status)
        row = self.repository.update_shipment(shipment_id, request.model_dump(mode="json", exclude_unset=True))
        if row is None:
            raise ShipmentNotFoundError(f"Shipment {shipment_id} was not found.")
        return ShipmentResponse.model_validate(row)

    def assign_shipment(self, shipment_id: str, *, driver_id: str, vehicle_id: str | None = None) -> ShipmentResponse:
        """Assign (or reassign) a driver -- and optionally a vehicle -- to an existing shipment."""
        current = self.get_shipment(shipment_id)
        resolved_vehicle_id = vehicle_id or current.vehicle_id
        if not resolved_vehicle_id:
            raise BusinessValidationError("A vehicle must be assigned along with the driver.")
        status = current.current_status or ShipmentStatus.PLANNED
        self._validate_active_assignment(driver_id, resolved_vehicle_id, status)
        payload: dict[str, str | None] = {"driver_id": driver_id, "vehicle_id": resolved_vehicle_id}
        if current.current_status == ShipmentStatus.PLANNED:
            payload["current_status"] = ShipmentStatus.ASSIGNED.value
        row = self.repository.update_shipment(shipment_id, payload)
        if row is None:
            raise ShipmentNotFoundError(f"Shipment {shipment_id} was not found.")
        shipment = ShipmentResponse.model_validate(row)
        self._notify_driver_assignment(driver_id, shipment)
        return shipment

    def _notify_driver_assignment(self, driver_id: str, shipment: ShipmentResponse) -> None:
        """Best-effort SMS to a driver confirming a shipment assignment.
        Never lets a notification failure surface as an assignment failure --
        the assignment is already committed to the database by the time this
        runs.
        """
        try:
            driver = self.repository.get_driver(driver_id)
            phone = driver.get("phone") if driver else None
            if not phone:
                logger.info("No phone on file for driver %s; skipping assignment SMS.", driver_id)
                return
            reference = shipment.order_reference or shipment.shipment_id
            destination = shipment.destination_facility_id or "your destination facility"
            send_sms(
                phone,
                f"SetuHaul: you've been assigned shipment {reference} to {destination}. "
                f"Check the driver app for pickup and appointment details.",
            )
        except Exception:  # noqa: BLE001 - notifications must never break assignment
            logger.exception("Unable to send driver assignment SMS for shipment %s", shipment.shipment_id)

    # -- facilities -----------------------------------------------------

    def list_facilities(self, *, limit: int = 200, offset: int = 0) -> list[FacilityResponse]:
        return [
            FacilityResponse.model_validate(row)
            for row in self.repository.list_facilities(limit=limit, offset=offset)
        ]

    def driver_shipments(self, driver_id: str, *, active_only: bool = False) -> list[ShipmentResponse]:
        self.get_driver(driver_id)
        return self.list_shipments(driver_id=driver_id, active_only=active_only)

    def driver_context(self, driver_id: str) -> DriverContextResponse:
        driver = self.get_driver(driver_id)
        summary = self._driver_summary(driver)
        if driver.driver_status is not DriverStatus.ACTIVE:
            return DriverContextResponse(
                resolution=ContextResolution.NOT_FOUND,
                requires_disambiguation=False,
                driver=summary,
                active_shipments=[],
            )

        shipments = self.list_shipments(driver_id=driver_id, active_only=True, limit=500)
        vehicles = self.repository.get_vehicles(item.vehicle_id for item in shipments if item.vehicle_id)
        candidates: list[ShipmentCandidate] = []
        for shipment in shipments:
            vehicle_row = vehicles.get(shipment.vehicle_id) if shipment.vehicle_id else None
            if vehicle_row is None:
                raise BusinessValidationError(
                    f"Shipment {shipment.shipment_id} references an unknown vehicle."
                )
            vehicle = self._vehicle_context(VehicleResponse.model_validate(vehicle_row))
            candidates.append(
                ShipmentCandidate(
                    vehicle=vehicle,
                    **shipment.model_dump(exclude={"driver_id", "vehicle_id", "created_at", "updated_at"}),
                )
            )

        count = len(candidates)
        resolution = (
            ContextResolution.NOT_FOUND
            if count == 0
            else ContextResolution.RESOLVED
            if count == 1
            else ContextResolution.AMBIGUOUS
        )
        return DriverContextResponse(
            resolution=resolution,
            requires_disambiguation=count > 1,
            driver=summary,
            active_shipments=candidates,
        )

    def shipment_context(self, shipment_id: str) -> ShipmentContextResponse:
        shipment = self.get_shipment(shipment_id)
        if not shipment.driver_id or not shipment.vehicle_id:
            raise BusinessValidationError(f"Shipment {shipment_id} has no driver/vehicle assigned yet.")
        driver = self.get_driver(shipment.driver_id)
        vehicle = self.get_vehicle(shipment.vehicle_id)
        return ShipmentContextResponse(
            driver=self._driver_summary(driver),
            vehicle=self._vehicle_context(vehicle),
            shipment=shipment,
        )

    @staticmethod
    def _driver_summary(driver: DriverResponse) -> DriverSummary:
        return DriverSummary(
            driver_id=driver.driver_id,
            driver_name=driver.driver_name,
            carrier_id=driver.carrier_id,
            driver_status=driver.driver_status,
        )

    @staticmethod
    def _vehicle_context(vehicle: VehicleResponse) -> VehicleContext:
        return VehicleContext(
            vehicle_id=vehicle.vehicle_id,
            registration_number=vehicle.registration_number,
            vehicle_type_code=vehicle.vehicle_type_code,
            refrigeration_capable=vehicle.refrigeration_capable,
            active_flag=vehicle.active_flag,
        )

    def _validate_active_assignment(self, driver_id: str, vehicle_id: str, status: ShipmentStatus) -> None:
        driver = self.get_driver(driver_id)
        vehicle = self.get_vehicle(vehicle_id)
        if driver.carrier_id != vehicle.carrier_id:
            raise BusinessValidationError("Assigned driver and vehicle must belong to the same carrier.")
        if status in ACTIVE_CONTEXT_STATUSES:
            if driver.driver_status is not DriverStatus.ACTIVE:
                raise BusinessValidationError("An inactive or suspended driver cannot receive an active shipment.")
            if not vehicle.active_flag:
                raise BusinessValidationError("An inactive vehicle cannot receive an active shipment.")
