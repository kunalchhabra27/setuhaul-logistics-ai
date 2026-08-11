"""Deterministic business rules for facility check-in transitions."""

from __future__ import annotations

import logging
from uuid import uuid4

from setuhaul.backend.checkin_portal.exceptions import InvalidCheckInTransition
from setuhaul.backend.checkin_portal.models import (
    ArrivalStatus,
    CheckInFacilityOption,
    CheckInRecord,
    CheckInShipmentSummary,
    CompleteRequest,
    DockInRequest,
    GateCheckInRequest,
    QueueUpdateRequest,
)
from setuhaul.backend.checkin_portal.repository import CheckInRepository
from setuhaul.backend.checkin_portal.state_machine import validate_transition
from setuhaul.infrastructure.sms import send_sms

logger = logging.getLogger(__name__)
INITIAL_CHECKIN_ELIGIBLE_STATUSES = {"ASSIGNED", "IN_TRANSIT", "AT_GATE"}
ACTIVE_OPERATIONAL_STATUSES = INITIAL_CHECKIN_ELIGIBLE_STATUSES | {"WAITING", "IN_DOCK"}


class CheckInService:
    """Enforce valid state transitions for facility check-ins."""

    def __init__(self, repository: CheckInRepository):
        """Initialize the service with its persistence boundary."""
        self.repository = repository

    def get_status(self, shipment_id: str) -> dict | None:
        """Return the current check-in record for a shipment."""
        return self.repository.get_by_shipment(shipment_id)

    def list_active_facilities(self) -> list[CheckInFacilityOption]:
        return [CheckInFacilityOption.model_validate(row) for row in self.repository.list_active_facilities()]

    def list_operational_shipments(self, facility_id: str | None) -> list[CheckInShipmentSummary]:
        if not facility_id:
            return []
        shipments = self.repository.list_shipments(facility_id)
        checkins_by_shipment = self.repository.list_checkins()
        drivers = self.repository.get_drivers([row.get("driver_id") for row in shipments if row.get("driver_id")])
        vehicles = self.repository.get_vehicles([row.get("vehicle_id") for row in shipments if row.get("vehicle_id")])
        facilities = self.repository.get_facilities(
            [row.get("destination_facility_id") for row in shipments if row.get("destination_facility_id")]
        )

        items: list[CheckInShipmentSummary] = []
        for shipment in shipments:
            record = checkins_by_shipment.get(shipment["shipment_id"])
            shipment_status = shipment.get("current_status")
            if not self._is_visible_for_checkin(shipment_status, record):
                continue

            driver = drivers.get(shipment.get("driver_id"))
            vehicle = vehicles.get(shipment.get("vehicle_id"))
            facility = facilities.get(shipment.get("destination_facility_id"))
            actions = self._action_flags(shipment_status, record)

            items.append(
                CheckInShipmentSummary(
                    shipment_id=shipment["shipment_id"],
                    order_reference=shipment.get("order_reference"),
                    carrier_id=shipment.get("carrier_id"),
                    driver_id=shipment.get("driver_id"),
                    driver_name=driver.get("driver_name") if driver else None,
                    vehicle_id=shipment.get("vehicle_id"),
                    registration_number=vehicle.get("registration_number") if vehicle else None,
                    origin_name=shipment.get("origin_name"),
                    origin_city=shipment.get("origin_city"),
                    destination_facility_id=shipment.get("destination_facility_id"),
                    destination_facility_name=facility.get("facility_name") if facility else None,
                    destination_facility_city=facility.get("city") if facility else None,
                    customer_name=shipment.get("customer_name"),
                    product_category=shipment.get("product_category"),
                    load_weight_kg=shipment.get("load_weight_kg"),
                    required_dock_type=shipment.get("required_dock_type"),
                    temperature_control_required=shipment.get("temperature_control_required"),
                    priority_code=shipment.get("priority_code"),
                    planned_departure_ts=shipment.get("planned_departure_ts"),
                    original_eta_ts=shipment.get("original_eta_ts"),
                    latest_eta_ts=shipment.get("latest_eta_ts"),
                    expected_unload_min=shipment.get("expected_unload_min"),
                    current_status=shipment_status,
                    created_at=shipment.get("created_at"),
                    updated_at=shipment.get("updated_at"),
                    checkin=CheckInRecord.model_validate(record) if record else None,
                    **actions,
                )
            )

        items.sort(key=lambda item: ((item.checkin is not None), item.original_eta_ts or "", item.shipment_id))
        return items

    def is_locked_for_rescheduling(self, shipment_id: str) -> bool:
        """Return whether a shipment can no longer be rescheduled."""
        record = self.repository.get_by_shipment(shipment_id)
        if record is None:
            return False
        return record["arrival_status"] in {
            ArrivalStatus.DOCKED.value,
            ArrivalStatus.COMPLETED.value,
        }

    def gate_check_in(self, request: GateCheckInRequest) -> dict:
        """Create the initial check-in record for a shipment."""
        existing = self.repository.get_by_shipment(request.shipment_id)
        if existing:
            raise InvalidCheckInTransition("Shipment has already checked in.")
        shipment = self.repository.get_shipment(request.shipment_id)
        if shipment is None:
            raise InvalidCheckInTransition("Shipment does not exist.")
        if shipment.get("current_status") not in INITIAL_CHECKIN_ELIGIBLE_STATUSES:
            raise InvalidCheckInTransition(
                f"Shipment {request.shipment_id} is not eligible for gate check-in from status "
                f"{shipment.get('current_status') or 'UNKNOWN'}."
            )
        if shipment.get("destination_facility_id") != request.facility_id:
            raise InvalidCheckInTransition("Shipment destination facility does not match this gate check-in.")

        checkin_id = f"CHK-{uuid4().hex[:8].upper()}"
        self.repository.create_gate_checkin(
            checkin_id=checkin_id,
            shipment_id=request.shipment_id,
            facility_id=request.facility_id,
            gate_in_at=request.gate_in_at.isoformat(),
        )
        self.repository.update_shipment_status(request.shipment_id, "AT_GATE")
        record = self._require_record(request.shipment_id)
        self._notify_gate_checkin(request.shipment_id)
        return record

    def _notify_gate_checkin(self, shipment_id: str) -> None:
        """Best-effort SMS to the assigned driver confirming their gate
        check-in was recorded. Never lets a notification failure surface as
        a check-in failure -- the check-in row is already committed by the
        time this runs.
        """
        try:
            contact = self.repository.get_driver_contact_for_shipment(shipment_id)
            if contact is None:
                logger.info("No driver phone on file for shipment %s; skipping check-in SMS.", shipment_id)
                return
            reference = contact.get("order_reference") or shipment_id
            send_sms(
                contact["phone"],
                f"SetuHaul: gate check-in recorded for {reference}. "
                f"Please proceed to the yard and await your dock call.",
            )
        except Exception:  # noqa: BLE001 - notifications must never break check-in
            logger.exception("Unable to send gate check-in SMS for shipment %s", shipment_id)

    def update_queue(self, request: QueueUpdateRequest) -> dict:
        """Update the queue state for an active check-in."""
        record = self.repository.get_by_shipment(request.shipment_id)
        if not record:
            raise InvalidCheckInTransition("Truck must check in first.")
        validate_transition(record["arrival_status"], "WAITING")
        self.repository.update_queue(request.shipment_id, request.queue_status.value)
        self.repository.update_shipment_status(request.shipment_id, "WAITING")
        return self.repository.get_by_shipment(request.shipment_id)

    def mark_docked(self, request: DockInRequest) -> dict:
        """Mark an active shipment as docked."""
        record = self.repository.get_by_shipment(request.shipment_id)
        if not record:
            raise InvalidCheckInTransition("Truck has not checked in.")
        validate_transition(record["arrival_status"], "DOCKED")
        self.repository.mark_docked(request.shipment_id, request.dock_in_at.isoformat())
        self.repository.update_shipment_status(request.shipment_id, "IN_DOCK")
        return self.repository.get_by_shipment(request.shipment_id)

    def complete(self, request: CompleteRequest) -> dict:
        """Mark an active shipment as completed."""
        record = self.repository.get_by_shipment(request.shipment_id)
        if not record:
            raise InvalidCheckInTransition("Shipment has not checked in.")
        validate_transition(record["arrival_status"], "COMPLETED")
        self.repository.mark_completed(request.shipment_id, request.completed_at.isoformat())
        self.repository.update_shipment_status(request.shipment_id, "COMPLETED")
        return self.repository.get_by_shipment(request.shipment_id)

    def _require_record(self, shipment_id: str) -> dict:
        """Return the current record or raise if it does not exist."""
        record = self.repository.get_by_shipment(shipment_id)
        if record is None:
            raise InvalidCheckInTransition("No check-in record exists for this shipment.")
        return record

    @staticmethod
    def _action_flags(shipment_status: str | None, record: dict | None) -> dict[str, bool]:
        if record is None:
            return {
                "can_gate_in": shipment_status in INITIAL_CHECKIN_ELIGIBLE_STATUSES,
                "can_queue": False,
                "can_dock": False,
                "can_complete": False,
            }

        arrival_status = record.get("arrival_status")
        return {
            "can_gate_in": False,
            "can_queue": arrival_status == "GATE_IN",
            "can_dock": arrival_status in {"GATE_IN", "WAITING"},
            "can_complete": arrival_status == "DOCKED",
        }

    @staticmethod
    def _is_visible_for_checkin(shipment_status: str | None, record: dict | None) -> bool:
        if shipment_status == "CANCELLED":
            return False
        if record is not None:
            return record.get("arrival_status") != "COMPLETED"
        return shipment_status in ACTIVE_OPERATIONAL_STATUSES
