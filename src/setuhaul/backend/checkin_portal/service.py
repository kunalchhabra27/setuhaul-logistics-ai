"""Deterministic business rules for facility check-in transitions."""

from __future__ import annotations

import logging
from uuid import uuid4

from setuhaul.backend.checkin_portal.exceptions import InvalidCheckInTransition
from setuhaul.backend.checkin_portal.models import (
    ArrivalStatus,
    CompleteRequest,
    DockInRequest,
    GateCheckInRequest,
    QueueUpdateRequest,
)
from setuhaul.backend.checkin_portal.repository import CheckInRepository
from setuhaul.backend.checkin_portal.state_machine import validate_transition
from setuhaul.infrastructure.sms import send_sms

logger = logging.getLogger(__name__)


class CheckInService:
    """Enforce valid state transitions for facility check-ins."""

    def __init__(self, repository: CheckInRepository):
        """Initialize the service with its persistence boundary."""
        self.repository = repository

    def get_status(self, shipment_id: str) -> dict | None:
        """Return the current check-in record for a shipment, enriched with
        the assigned driver's name/phone so gate staff can verify the right
        person and vehicle showed up -- not just that a shipment_id matches.
        """
        record = self.repository.get_by_shipment(shipment_id)
        if record is None:
            return None
        contact = self.repository.get_driver_contact_for_shipment(shipment_id)
        return {
            **record,
            "driver_name": contact.get("driver_name") if contact else None,
            "driver_phone": contact.get("phone") if contact else None,
        }

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
        """Create the initial check-in record for a shipment.

        Verifies the shipment actually exists in TMS before recording a
        gate-in -- previously any shipment_id, including a typo or a
        cancelled/unknown load, would silently create a check-in record.
        Deliberately does NOT require a confirmed WMS appointment here:
        trucks can and do arrive at the gate before dispatch/WMS has
        finished booking a dock, so gate-in stays permissive. The stricter
        check lives on mark_docked below, where a real dock assignment is
        actually required.
        """
        if not self.repository.shipment_exists(request.shipment_id):
            raise InvalidCheckInTransition(f"Unknown shipment: {request.shipment_id}")
        existing = self.repository.get_by_shipment(request.shipment_id)
        if existing:
            raise InvalidCheckInTransition("Shipment has already checked in.")

        checkin_id = f"CHK-{uuid4().hex[:8].upper()}"
        self.repository.create_gate_checkin(
            checkin_id=checkin_id,
            shipment_id=request.shipment_id,
            facility_id=request.facility_id,
            gate_in_at=request.gate_in_at.isoformat(),
        )
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
        return self.repository.get_by_shipment(request.shipment_id)

    def mark_docked(self, request: DockInRequest) -> dict:
        """Mark an active shipment as docked.

        Requires a CONFIRMED WMS appointment for this shipment -- unlike
        gate check-in, actually assigning a truck to a physical dock without
        a real booking would let staff dock a truck WMS never allocated
        capacity for. The confirmed appointment's dock is also used to
        auto-fill actual_dock_id, mirroring what driver_chat_eta's own
        check-in flow already does for driver-initiated dock-in.
        """
        record = self.repository.get_by_shipment(request.shipment_id)
        if not record:
            raise InvalidCheckInTransition("Truck has not checked in.")
        validate_transition(record["arrival_status"], "DOCKED")
        dock = self.repository.get_confirmed_dock_for_shipment(request.shipment_id)
        if dock is None:
            raise InvalidCheckInTransition(
                "No confirmed WMS appointment exists for this shipment -- book a dock slot "
                "(via WMS or the driver chat) before marking it docked."
            )
        self.repository.mark_docked(
            request.shipment_id, request.dock_in_at.isoformat(), actual_dock_id=dock["dock_id"]
        )
        return self.repository.get_by_shipment(request.shipment_id)

    def complete(self, request: CompleteRequest) -> dict:
        """Mark an active shipment as completed."""
        record = self.repository.get_by_shipment(request.shipment_id)
        if not record:
            raise InvalidCheckInTransition("Shipment has not checked in.")
        validate_transition(record["arrival_status"], "COMPLETED")
        self.repository.mark_completed(request.shipment_id, request.completed_at.isoformat())
        return self.repository.get_by_shipment(request.shipment_id)

    def _require_record(self, shipment_id: str) -> dict:
        """Return the current record or raise if it does not exist."""
        record = self.repository.get_by_shipment(shipment_id)
        if record is None:
            raise InvalidCheckInTransition("No check-in record exists for this shipment.")
        return record
