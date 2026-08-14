"""Deterministic TMS business validation and context resolution."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from setuhaul.backend.tms.exceptions import (
    BusinessValidationError,
    DriverNotFoundError,
    FacilityAssignmentNotFoundError,
    ShipmentNotFoundError,
    VehicleNotFoundError,
)
from setuhaul.backend.tms.models import (
    ACTIVE_CONTEXT_STATUSES,
    CheckinTraceSummary,
    ContextResolution,
    DockTraceSummary,
    DriverContextResponse,
    DriverCreate,
    DriverResponse,
    DriverStatus,
    DriverSummary,
    DriverUpdate,
    FacilityResponse,
    FacilityStaffResponse,
    ShipmentCandidate,
    ShipmentContextResponse,
    ShipmentCreate,
    ShipmentReferenceData,
    ShipmentResponse,
    ShipmentStatus,
    ShipmentUpdate,
    VehicleContext,
    VehicleCreate,
    VehicleResponse,
    VehicleUpdate,
)
from setuhaul.backend.tms.repository import TMSRepository
from setuhaul.infrastructure import cache
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
        cache.invalidate_driver(driver_id)
        return DriverResponse.model_validate(row)

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
        fingerprint = "|".join(
            str(part)
            for part in (
                driver_id,
                status.value if status else None,
                active_only,
                unassigned_only,
                include_archived,
                limit,
                offset,
            )
        )
        cache_key = cache.shipments_list_key(destination_facility_id or "all", fingerprint)
        rows = cache.get_or_set(
            cache_key,
            cache.TTL_MODERATE,
            lambda: self.repository.list_shipments(
                driver_id=driver_id,
                destination_facility_id=destination_facility_id,
                status=status,
                active_only=active_only,
                unassigned_only=unassigned_only,
                include_archived=include_archived,
                limit=limit,
                offset=offset,
            ),
        )
        return [ShipmentResponse.model_validate(row) for row in rows]

    def archive_shipment(self, shipment_id: str) -> ShipmentResponse:
        current = self.get_shipment(shipment_id)
        if current.current_status is not ShipmentStatus.COMPLETED:
            raise BusinessValidationError("Only completed shipments can be archived.")
        row = self.repository.update_shipment(shipment_id, {"archived_flag": True})
        if row is None:
            raise ShipmentNotFoundError(f"Shipment {shipment_id} was not found.")
        self._invalidate_shipment_caches(shipment_id)
        return ShipmentResponse.model_validate(row)

    def unarchive_shipment(self, shipment_id: str) -> ShipmentResponse:
        row = self.repository.update_shipment(shipment_id, {"archived_flag": False})
        if row is None:
            raise ShipmentNotFoundError(f"Shipment {shipment_id} was not found.")
        self._invalidate_shipment_caches(shipment_id)
        return ShipmentResponse.model_validate(row)

    @staticmethod
    def _invalidate_shipment_caches(shipment_id: str) -> None:
        """Bust this shipment's own cached views plus every cached list page
        (facility-scoped and global) -- shared with dock_scheduler/
        checkin_portal/driver_chat_eta since they read the same tables."""
        cache.invalidate_shipment(shipment_id)
        cache.invalidate_shipments_lists()

    def cancel_shipment(self, shipment_id: str, reason: str | None = None) -> ShipmentResponse:
        current = self.get_shipment(shipment_id)
        if current.current_status in (ShipmentStatus.COMPLETED, ShipmentStatus.CANCELLED):
            raise BusinessValidationError(
                f"A shipment that is already {current.current_status.value.lower()} cannot be cancelled."
            )
        now = datetime.now(timezone.utc).isoformat()
        # Free the dock slot this shipment was holding, if any -- otherwise
        # WMS would keep showing the slot occupied for a load that's no
        # longer coming, and no future shipment could ever book it.
        self.repository.cancel_current_appointment(shipment_id, now)
        row = self.repository.update_shipment(shipment_id, {"current_status": ShipmentStatus.CANCELLED.value})
        if row is None:
            raise ShipmentNotFoundError(f"Shipment {shipment_id} was not found.")
        self._invalidate_shipment_caches(shipment_id)
        if current.destination_facility_id:
            # cancel_current_appointment above frees a dock slot -- WMS's
            # cached board for that facility must not keep showing it occupied.
            cache.invalidate_facility_board(current.destination_facility_id)
        shipment = ShipmentResponse.model_validate(row)
        if current.driver_id:
            self._notify_shipment_cancelled(current.driver_id, shipment, reason)
        return shipment

    def _notify_shipment_cancelled(self, driver_id: str, shipment: ShipmentResponse, reason: str | None) -> None:
        """Best-effort SMS to the assigned driver -- mirrors
        _notify_driver_assignment's never-block-the-request stance."""
        try:
            driver = self.repository.get_driver(driver_id)
            phone = driver.get("phone") if driver else None
            if not phone:
                logger.info("No phone on file for driver %s; skipping cancellation SMS.", driver_id)
                return
            reason_suffix = f" Reason: {reason}" if reason else ""
            send_sms(
                phone,
                f"SetuHaul: shipment {shipment.order_reference or shipment.shipment_id} has been "
                f"cancelled by dispatch.{reason_suffix}",
            )
        except Exception:  # noqa: BLE001 - notifications must never break cancellation
            logger.exception("Unable to send cancellation SMS for shipment %s", shipment.shipment_id)

    def create_shipment(self, request: ShipmentCreate) -> ShipmentResponse:
        if request.driver_id and request.vehicle_id:
            self._validate_active_assignment(request.driver_id, request.vehicle_id, request.current_status)
        payload = request.model_dump(mode="json")
        payload["shipment_id"] = payload.get("shipment_id") or self.repository.generate_shipment_id()
        now = datetime.now(timezone.utc).isoformat()
        payload["created_at"] = now
        payload["updated_at"] = now
        row = self.repository.create_shipment(payload)
        shipment = ShipmentResponse.model_validate(row)
        cache.invalidate_shipments_lists()
        cache.invalidate_reference("shipment-options")
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
        payload = request.model_dump(mode="json", exclude_unset=True)
        if request.current_status is ShipmentStatus.COMPLETED and "archived_flag" not in payload:
            # A shipment reaching COMPLETED through any path -- including a
            # direct staff edit here, not just the driver-side checkin flow
            # -- auto-archives rather than waiting on a manual Archive click.
            payload["archived_flag"] = True
        row = self.repository.update_shipment(shipment_id, payload)
        if row is None:
            raise ShipmentNotFoundError(f"Shipment {shipment_id} was not found.")
        self._invalidate_shipment_caches(shipment_id)
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
        self._invalidate_shipment_caches(shipment_id)
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
        cache_key = cache.reference_key(f"facilities:{limit}:{offset}")
        rows = cache.get_or_set(
            cache_key, cache.TTL_REFERENCE, lambda: self.repository.list_facilities(limit=limit, offset=offset)
        )
        return [FacilityResponse.model_validate(row) for row in rows]

    def shipment_reference_data(self) -> ShipmentReferenceData:
        cache_key = cache.reference_key("shipment-options")
        data = cache.get_or_set(cache_key, cache.TTL_REFERENCE, self.repository.list_shipment_reference_data)
        return ShipmentReferenceData.model_validate(data)

    # -- staff facility assignments (WMS/Check-in facility scoping) -----

    def register_staff_facility(self, staff_user_id: str, facility_id: str) -> FacilityStaffResponse:
        facility = self.repository.get_facility(facility_id)
        if facility is None:
            raise BusinessValidationError(f"Facility {facility_id} was not found.")
        row = self.repository.register_staff_facility(staff_user_id, facility_id)
        return FacilityStaffResponse(
            staff_user_id=row["staff_user_id"],
            facility_id=row["facility_id"],
            facility_name=facility.get("facility_name"),
        )

    def get_staff_facility(self, staff_user_id: str) -> FacilityStaffResponse | None:
        row = self.repository.get_staff_facility(staff_user_id)
        if row is None:
            return None
        facility = self.repository.get_facility(row["facility_id"])
        return FacilityStaffResponse(
            staff_user_id=row["staff_user_id"],
            facility_id=row["facility_id"],
            facility_name=facility.get("facility_name") if facility else None,
        )

    def require_staff_facility(self, staff_user_id: str) -> FacilityStaffResponse:
        assignment = self.get_staff_facility(staff_user_id)
        if assignment is None:
            raise FacilityAssignmentNotFoundError(
                "No warehouse facility is registered for this account yet. Complete facility setup to continue."
            )
        return assignment

    def list_shipments_for_staff_facility(
        self,
        staff_user_id: str,
        *,
        status: ShipmentStatus | None = None,
        active_only: bool = False,
        unassigned_only: bool = False,
        include_archived: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ShipmentResponse]:
        """Shipments scoped to ONLY the caller's own registered facility.

        facility_id is always resolved server-side from staff_facility_assignments
        via the caller's own verified user id -- never accepted as a
        client-supplied query parameter -- so one facility's staff cannot see
        shipments allotted to another facility by passing a different id.
        """
        assignment = self.require_staff_facility(staff_user_id)
        return self.list_shipments(
            destination_facility_id=assignment.facility_id,
            status=status,
            active_only=active_only,
            unassigned_only=unassigned_only,
            include_archived=include_archived,
            limit=limit,
            offset=offset,
        )

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
        def _fetch() -> dict:
            shipment = self.get_shipment(shipment_id)
            if not shipment.driver_id or not shipment.vehicle_id:
                raise BusinessValidationError(f"Shipment {shipment_id} has no driver/vehicle assigned yet.")
            driver = self.get_driver(shipment.driver_id)
            vehicle = self.get_vehicle(shipment.vehicle_id)

            appointment_row = self.repository.current_appointment_for_shipment(shipment_id)
            dock = DockTraceSummary.model_validate(appointment_row) if appointment_row else None
            checkin_row = self.repository.checkin_for_shipment(shipment_id)
            checkin = CheckinTraceSummary.model_validate(checkin_row) if checkin_row else None

            context = ShipmentContextResponse(
                driver=self._driver_summary(driver),
                vehicle=self._vehicle_context(vehicle),
                shipment=shipment,
                dock=dock,
                checkin=checkin,
            )
            return context.model_dump(mode="json")

        # Not found / not-yet-assigned raises out of _fetch before get_or_set
        # ever writes to the cache, so a missing shipment is never cached as
        # if it were a valid (empty) result.
        cached = cache.get_or_set(cache.shipment_context_key(shipment_id), cache.TTL_MODERATE, _fetch)
        return ShipmentContextResponse.model_validate(cached)

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
