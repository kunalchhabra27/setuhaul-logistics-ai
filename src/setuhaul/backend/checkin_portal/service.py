"""Deterministic business rules for facility check-in transitions."""

from __future__ import annotations

from uuid import uuid4

from setuhaul.backend.checkin_portal.exceptions import InvalidCheckInTransition
from setuhaul.backend.checkin_portal.models import CompleteRequest, DockInRequest, GateCheckInRequest, QueueUpdateRequest
from setuhaul.backend.checkin_portal.repository import CheckInRepository


class CheckInService:
    """Enforce valid state transitions for facility check-ins."""

    def __init__(self, repository: CheckInRepository):
        """Initialize the service with its persistence boundary."""
        self.repository = repository

    def get_status(self, shipment_id: str) -> dict | None:
        """Return the current check-in record for a shipment."""
        return self.repository.get_by_shipment(shipment_id)

    def gate_check_in(self, request: GateCheckInRequest) -> dict:
        """Create the initial check-in record for a shipment."""
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
        return self._require_record(request.shipment_id)

    def update_queue(self, request: QueueUpdateRequest) -> dict:
        """Update the queue state for an active check-in."""
        record = self.repository.get_by_shipment(request.shipment_id)
        if not record:
            raise InvalidCheckInTransition("Truck must check in first.")
        if record["arrival_status"] in {"DOCKED", "COMPLETED"}:
            raise InvalidCheckInTransition("Truck cannot return to the waiting queue.")
        self.repository.update_queue(request.shipment_id, request.queue_status.value)
        return self.repository.get_by_shipment(request.shipment_id)

    def mark_docked(self, request: DockInRequest) -> dict:
        """Mark an active shipment as docked."""
        record = self.repository.get_by_shipment(request.shipment_id)
        if not record:
            raise InvalidCheckInTransition("Truck has not checked in.")
        if record["arrival_status"] == "COMPLETED":
            raise InvalidCheckInTransition("Completed shipment cannot dock again.")
        self.repository.mark_docked(request.shipment_id, request.dock_in_at.isoformat())
        return self.repository.get_by_shipment(request.shipment_id)

    def complete(self, request: CompleteRequest) -> dict:
        """Mark an active shipment as completed."""
        record = self.repository.get_by_shipment(request.shipment_id)
        if not record:
            raise InvalidCheckInTransition("Shipment has not checked in.")
        if record["arrival_status"] != "DOCKED":
            raise InvalidCheckInTransition("Shipment must be docked before completion.")
        self.repository.mark_completed(request.shipment_id, request.completed_at.isoformat())
        return self.repository.get_by_shipment(request.shipment_id)
