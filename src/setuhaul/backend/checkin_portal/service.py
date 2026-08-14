"""Deterministic business rules for facility check-in transitions."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
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
from setuhaul.infrastructure import cache
from setuhaul.infrastructure.sms import send_sms

logger = logging.getLogger(__name__)

# How close to the booked slot start counts as "on time" -- outside this
# window in either direction, arrival is genuinely early or late relative to
# the appointment, not just noise from clocks/traffic.
ON_TIME_TOLERANCE_MINUTES = 15


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
        def _fetch() -> dict | None:
            record = self.repository.get_by_shipment(shipment_id)
            if record is None:
                return None
            contact = self.repository.get_driver_contact_for_shipment(shipment_id)
            dock = self.repository.get_confirmed_dock_for_shipment(shipment_id)
            return {
                **record,
                "driver_name": contact.get("driver_name") if contact else None,
                "driver_phone": contact.get("phone") if contact else None,
                "staff_approved": bool(record.get("staff_approved_flag")),
                "timing_status": self._compute_timing_status(
                    record.get("gate_in_ts"), dock.get("slot_start_ts") if dock else None
                ),
            }

        # get_or_set can't distinguish "cached None" from "no cache entry" --
        # a genuinely-absent check-in record is never cached and always
        # re-fetched, same as before this change.
        return cache.get_or_set(cache.checkin_status_key(shipment_id), cache.TTL_MODERATE, _fetch)

    @staticmethod
    def _invalidate_checkin_caches(shipment_id: str) -> None:
        """A check-in write also updates shipments.current_status (see
        repository.update_shipment_status), so TMS's cached shipment/context/
        list views of this shipment go stale too -- bust all of them, not
        just this module's own status cache."""
        cache.invalidate_shipment(shipment_id)
        cache.invalidate_shipments_lists()

    @staticmethod
    def _compute_timing_status(gate_in_ts: str | None, slot_start_ts: str | None) -> str | None:
        """Real early/on-time/late classification of the truck's actual gate
        arrival against its booked slot -- separate from arrival_status
        (which despite the DB column's EARLY/ON_TIME/LATE/NO_SHOW naming is
        actually used here as a check-in *stage* label: GATE_IN/WAITING/
        DOCKED/COMPLETED, see _to_domain_arrival_status). Renaming that
        existing stage machinery to free up its real meaning would be a
        bigger, riskier change than this feature needs, so this is computed
        fresh instead of trying to reuse that column.

        gate_in_ts (facility_checkins) and slot_start_ts (appointment_slots)
        are not guaranteed to share a timezone convention -- real seed/prod
        data stores slot_start_ts with a +05:30 offset while gate_in_ts can
        come through naive, and datetime.fromisoformat preserves whatever
        offset (or lack of one) each string happens to carry. Subtracting
        mismatched aware/naive datetimes raises TypeError, so both are
        normalized to naive before the delta is computed -- same pattern
        used in dock_scheduler.repository.ensure_future_slots.
        """
        if not gate_in_ts or not slot_start_ts:
            return None
        try:
            arrived = datetime.fromisoformat(gate_in_ts).replace(tzinfo=None)
            scheduled = datetime.fromisoformat(slot_start_ts).replace(tzinfo=None)
        except ValueError:
            return None
        delta_minutes = (arrived - scheduled).total_seconds() / 60
        if delta_minutes < -ON_TIME_TOLERANCE_MINUTES:
            return "EARLY"
        if delta_minutes > ON_TIME_TOLERANCE_MINUTES:
            return "LATE"
        return "ON_TIME"

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
        self._invalidate_checkin_caches(request.shipment_id)
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

    def approve_gate_checkin(self, shipment_id: str) -> dict:
        """Staff sign-off on a driver-reported gate arrival.

        A driver marking themselves as arrived (via the driver chat's own
        check-in action, which writes the same facility_checkins row this
        portal reads) is that driver's own claim, not a verified fact -- it
        must not silently become the shipment's fleet-wide "checked in"
        status on TMS/WMS until someone at the gate actually confirms it.
        Only after this call does the shipment's current_status move to
        AT_GATE, which is what makes it visible everywhere else.
        """
        record = self.repository.get_by_shipment(shipment_id)
        if not record:
            raise InvalidCheckInTransition("Truck has not checked in yet.")
        if record.get("staff_approved_flag"):
            raise InvalidCheckInTransition("This gate check-in was already approved.")
        self.repository.approve_gate_checkin(shipment_id)
        self.repository.update_shipment_status(shipment_id, "AT_GATE")
        self._invalidate_checkin_caches(shipment_id)
        return self.get_status(shipment_id)

    def update_queue(self, request: QueueUpdateRequest) -> dict:
        """Update the queue state for an active check-in."""
        record = self.repository.get_by_shipment(request.shipment_id)
        if not record:
            raise InvalidCheckInTransition("Truck must check in first.")
        validate_transition(record["arrival_status"], "WAITING")
        self.repository.update_queue(request.shipment_id, request.queue_status.value)
        # WAITING_DOCK_UNAVAILABLE/WAITING_EARLY/WAITING_LATE all mean the
        # truck is sitting in the yard, not moving yet -- WAITING is the one
        # TMS-facing status that covers all three without over-specifying.
        self.repository.update_shipment_status(request.shipment_id, "WAITING")
        self._invalidate_checkin_caches(request.shipment_id)
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
        self.repository.update_shipment_status(request.shipment_id, "IN_DOCK")
        self._invalidate_checkin_caches(request.shipment_id)
        return self.repository.get_by_shipment(request.shipment_id)

    def complete(self, request: CompleteRequest) -> dict:
        """Mark an active shipment as completed."""
        record = self.repository.get_by_shipment(request.shipment_id)
        if not record:
            raise InvalidCheckInTransition("Shipment has not checked in.")
        validate_transition(record["arrival_status"], "COMPLETED")
        self.repository.mark_completed(request.shipment_id, request.completed_at.isoformat())
        # Auto-archive on completion, same as driver_chat_eta's own
        # completed-checkin path -- a shipment shouldn't need a separate
        # manual Archive click just because check-in staff closed it out
        # instead of the driver.
        self.repository.update_shipment_status(request.shipment_id, "COMPLETED", archive=True)
        self._invalidate_checkin_caches(request.shipment_id)
        return self.repository.get_by_shipment(request.shipment_id)

    def _require_record(self, shipment_id: str) -> dict:
        """Return the current record or raise if it does not exist."""
        record = self.repository.get_by_shipment(shipment_id)
        if record is None:
            raise InvalidCheckInTransition("No check-in record exists for this shipment.")
        return record
