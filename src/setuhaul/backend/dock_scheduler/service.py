"""Orchestration layer for dock scheduling and slot lifecycle management."""

from __future__ import annotations

from setuhaul.backend.dock_scheduler.exceptions import InvalidBookingError
from setuhaul.backend.dock_scheduler.models import (
    DockSlot,
    DriverConstraints,
    HoldResult,
    SlotLifecycleStage,
    SlotSuggestion,
)
from setuhaul.backend.dock_scheduler.repository import DockSchedulerRepository, parse_ts
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

    def dock_board(self, shipment_id: str) -> list[DockSlot]:
        """Return every compatible slot (not just the top-ranked ones) grouped
        by dock, for rendering a full visual dock board. Unlike suggest_slots
        this is not limited/ranked -- it's meant to show the whole day's
        capacity across every dock the shipment could use.
        """
        slots = self.repository.compatible_slots(shipment_id)
        driver_names = self.repository.driver_names(
            [row["occupied_driver_id"] for row in slots if row.get("occupied_driver_id")]
        )
        return [
            DockSlot(
                slot_id=row["slot_id"],
                dock_code=row["dock_code"],
                dock_type=row["dock_type"],
                start=parse_ts(row["slot_start_ts"]),
                end=parse_ts(row["slot_end_ts"]),
                availability_status=row["availability_status"],
                occupant_shipment_id=row.get("shipment_id"),
                occupant_driver_name=driver_names.get(row.get("occupied_driver_id")),
            )
            for row in slots
        ]

    def hold_slot(self, shipment_id: str, slot_id: str, ttl_minutes: int = 15) -> HoldResult:
        """Reserve a slot temporarily while the driver considers the option."""
        compatible = self.repository.compatible_slots(shipment_id)
        if not any(slot["slot_id"] == slot_id for slot in compatible):
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
            status = repository.current_appointment_status(shipment_id, slot_id)
            if status == "PENDING_CONFIRMATION":
                return SlotLifecycleStage.PENDING_CONFIRMATION
            if status == "CONFIRMED":
                return SlotLifecycleStage.CONFIRMED
            return SlotLifecycleStage.HELD
        return SlotLifecycleStage.PROPOSED
