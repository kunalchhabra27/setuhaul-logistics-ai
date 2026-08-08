"""Deterministic TMS business validation and context resolution."""

from __future__ import annotations

from uuid import UUID

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
    ShipmentCandidate,
    ShipmentContextResponse,
    ShipmentCreate,
    ShipmentResponse,
    ShipmentStatus,
    ShipmentUpdate,
    VehicleContext,
    VehicleCreate,
    VehicleResponse,
    VehicleStatus,
    VehicleUpdate,
)
from setuhaul.backend.tms.repository import TMSRepository


class TMSService:
    """Coordinate TMS persistence while preserving domain boundaries."""

    def __init__(self, repository: TMSRepository):
        self.repository = repository

    def get_driver(self, driver_id: UUID) -> DriverResponse:
        row = self.repository.get_driver(driver_id)
        if row is None:
            raise DriverNotFoundError(f"Driver {driver_id} was not found.")
        return DriverResponse.model_validate(row)

    def get_driver_by_phone(self, phone: str) -> DriverResponse:
        row = self.repository.get_driver_by_phone(phone)
        if row is None:
            raise DriverNotFoundError(f"No driver was found for phone {phone}.")
        return DriverResponse.model_validate(row)

    def create_driver(self, request: DriverCreate) -> DriverResponse:
        row = self.repository.create_driver(request.model_dump(mode="json"))
        return DriverResponse.model_validate(row)

    def update_driver(self, driver_id: UUID, request: DriverUpdate) -> DriverResponse:
        self.get_driver(driver_id)
        row = self.repository.update_driver(
            driver_id, request.model_dump(mode="json", exclude_unset=True)
        )
        if row is None:
            raise DriverNotFoundError(f"Driver {driver_id} was not found.")
        return DriverResponse.model_validate(row)

    def get_vehicle(self, vehicle_id: UUID) -> VehicleResponse:
        row = self.repository.get_vehicle(vehicle_id)
        if row is None:
            raise VehicleNotFoundError(f"Vehicle {vehicle_id} was not found.")
        return VehicleResponse.model_validate(row)

    def create_vehicle(self, request: VehicleCreate) -> VehicleResponse:
        row = self.repository.create_vehicle(request.model_dump(mode="json"))
        return VehicleResponse.model_validate(row)

    def update_vehicle(self, vehicle_id: UUID, request: VehicleUpdate) -> VehicleResponse:
        self.get_vehicle(vehicle_id)
        row = self.repository.update_vehicle(
            vehicle_id, request.model_dump(mode="json", exclude_unset=True)
        )
        if row is None:
            raise VehicleNotFoundError(f"Vehicle {vehicle_id} was not found.")
        return VehicleResponse.model_validate(row)

    def get_shipment(self, shipment_id: UUID) -> ShipmentResponse:
        row = self.repository.get_shipment(shipment_id)
        if row is None:
            raise ShipmentNotFoundError(f"Shipment {shipment_id} was not found.")
        return ShipmentResponse.model_validate(row)

    def list_shipments(
        self,
        *,
        driver_id: UUID | None = None,
        destination_id: UUID | None = None,
        status: ShipmentStatus | None = None,
        active_only: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ShipmentResponse]:
        return [
            ShipmentResponse.model_validate(row)
            for row in self.repository.list_shipments(
                driver_id=driver_id,
                destination_id=destination_id,
                status=status,
                active_only=active_only,
                limit=limit,
                offset=offset,
            )
        ]

    def create_shipment(self, request: ShipmentCreate) -> ShipmentResponse:
        self._validate_active_assignment(
            request.driver_id, request.vehicle_id, request.status
        )
        row = self.repository.create_shipment(request.model_dump(mode="json"))
        return ShipmentResponse.model_validate(row)

    def update_shipment(self, shipment_id: UUID, request: ShipmentUpdate) -> ShipmentResponse:
        current = self.get_shipment(shipment_id)
        driver_id = request.driver_id or current.driver_id
        vehicle_id = request.vehicle_id or current.vehicle_id
        status = request.status or current.status
        self._validate_active_assignment(driver_id, vehicle_id, status)
        row = self.repository.update_shipment(
            shipment_id, request.model_dump(mode="json", exclude_unset=True)
        )
        if row is None:
            raise ShipmentNotFoundError(f"Shipment {shipment_id} was not found.")
        return ShipmentResponse.model_validate(row)

    def driver_shipments(
        self, driver_id: UUID, *, active_only: bool = False
    ) -> list[ShipmentResponse]:
        self.get_driver(driver_id)
        return self.list_shipments(driver_id=driver_id, active_only=active_only)

    def driver_context(self, driver_id: UUID) -> DriverContextResponse:
        driver = self.get_driver(driver_id)
        summary = self._driver_summary(driver)
        if not driver.active_flag or driver.status is not DriverStatus.ACTIVE:
            return DriverContextResponse(
                resolution=ContextResolution.NOT_FOUND,
                requires_disambiguation=False,
                driver=summary,
                active_shipments=[],
            )

        shipments = self.list_shipments(driver_id=driver_id, active_only=True, limit=500)
        vehicles = self.repository.get_vehicles(item.vehicle_id for item in shipments)
        candidates: list[ShipmentCandidate] = []
        for shipment in shipments:
            vehicle_row = vehicles.get(shipment.vehicle_id)
            if vehicle_row is None:
                raise BusinessValidationError(
                    f"Shipment {shipment.shipment_id} references an unknown vehicle."
                )
            vehicle = self._vehicle_context(VehicleResponse.model_validate(vehicle_row))
            candidates.append(
                ShipmentCandidate(vehicle=vehicle, **shipment.model_dump(exclude={"driver_id", "vehicle_id", "created_at", "updated_at"}))
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

    def shipment_context(self, shipment_id: UUID) -> ShipmentContextResponse:
        shipment = self.get_shipment(shipment_id)
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
            driver_code=driver.driver_code,
            name=driver.name,
            carrier_id=driver.carrier_id,
            status=driver.status,
            active_flag=driver.active_flag,
        )

    @staticmethod
    def _vehicle_context(vehicle: VehicleResponse) -> VehicleContext:
        return VehicleContext(
            vehicle_id=vehicle.vehicle_id,
            vehicle_number=vehicle.vehicle_number,
            vehicle_type=vehicle.vehicle_type,
            length_ft=vehicle.length_ft,
            refrigeration_required=vehicle.refrigeration_required,
            status=vehicle.status,
        )

    def _validate_active_assignment(
        self, driver_id: UUID, vehicle_id: UUID, status: ShipmentStatus
    ) -> None:
        driver = self.get_driver(driver_id)
        vehicle = self.get_vehicle(vehicle_id)
        if driver.carrier_id != vehicle.carrier_id:
            raise BusinessValidationError(
                "Assigned driver and vehicle must belong to the same carrier."
            )
        if status in ACTIVE_CONTEXT_STATUSES:
            if not driver.active_flag or driver.status is not DriverStatus.ACTIVE:
                raise BusinessValidationError(
                    "An inactive or suspended driver cannot receive an active shipment."
                )
            if not vehicle.active_flag or vehicle.status is not VehicleStatus.ACTIVE:
                raise BusinessValidationError(
                    "An inactive or maintenance vehicle cannot receive an active shipment."
                )
