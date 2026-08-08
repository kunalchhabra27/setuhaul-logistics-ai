"""Orchestration layer for dock scheduling and slot lifecycle management."""

from __future__ import annotations

from setuhaul.backend.dock_scheduler.exceptions import InvalidBookingError
from setuhaul.backend.dock_scheduler.models import (
    DriverConstraints,
    HoldResult,
    SlotLifecycleStage,
    SlotSuggestion,
)
from setuhaul.backend.dock_scheduler.repository import DockSchedulerRepository
from setuhaul.backend.dock_scheduler.scheduler import DeterministicReschedulingEngine


class DockSchedulerService:
    """Public service for proposing, holding, and confirming dock appointments."""

    def __init__(self, repository: DockSchedulerRepository):
        self.repository = repository
        self.engine = DeterministicReschedulingEngine(repository)

    def suggest_slots(
        self,
        shipment_id: str,
        constraints: DriverConstraints | None = None,
        limit: int = 3,
    ) -> list[SlotSuggestion]:
        """Return ranked slot options without mutating capacity."""
        return self.engine.suggest(shipment_id, constraints, limit)

    def hold_slot(self, shipment_id: str, slot_id: str, ttl_minutes: int = 15) -> HoldResult:
        """Reserve a slot temporarily while the driver considers the option."""
        suggestions = self.engine.suggest(shipment_id, limit=20)
        allowed = {item.slot_id for item in suggestions if item.suggestion_type.value != "PRIORITY_SWAP"}
        if slot_id not in allowed:
            raise InvalidBookingError("Slot is not a feasible option for this shipment")
        return self.repository.create_hold(shipment_id, slot_id, ttl_minutes)

    def request_confirmation(self, shipment_id: str, slot_id: str) -> str:
        """Move from HELD to PENDING_CONFIRMATION."""
        hold = self.repository.active_hold_for_shipment(shipment_id, slot_id)
        if hold is None:
            self.repository.create_hold(shipment_id, slot_id, ttl_minutes=15)
        return self.repository.create_pending_appointment(shipment_id, slot_id)

    def confirm_booking(self, shipment_id: str, slot_id: str, accepted: bool = True) -> str:
        """Confirm a held or pending slot after explicit driver acceptance."""
        return self.repository.book_after_acceptance(shipment_id, slot_id, accepted)

    def cancel_hold(self, hold_id: str) -> None:
        """Release a temporary slot hold."""
        self.repository.release_hold(hold_id)

    def cancel_pending(self, shipment_id: str, slot_id: str) -> None:
        """Cancel a pending booking and release any active hold."""
        self.repository.cancel_pending(shipment_id, slot_id)

    @staticmethod
    def lifecycle_stage_for_slot(slot_id: str, shipment_id: str, repository: DockSchedulerRepository) -> SlotLifecycleStage:
        hold = repository.active_hold_for_shipment(shipment_id, slot_id)
        if hold:
            pending = repository.connection.execute(
                """
                SELECT appointment_status
                FROM appointments
                WHERE shipment_id = ? AND slot_id = ? AND is_current = 1
                ORDER BY booked_at DESC
                LIMIT 1
                """,
                (shipment_id, slot_id),
            ).fetchone()
            if pending and pending["appointment_status"] == "PENDING_CONFIRMATION":
                return SlotLifecycleStage.PENDING_CONFIRMATION
            if pending and pending["appointment_status"] == "CONFIRMED":
                return SlotLifecycleStage.CONFIRMED
            return SlotLifecycleStage.HELD
        return SlotLifecycleStage.PROPOSED
