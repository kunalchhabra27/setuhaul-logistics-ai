"""Orchestration layer for dock scheduling and slot lifecycle management."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from setuhaul.backend.dock_scheduler.constraints import FacilityConstraintEvaluator
from setuhaul.backend.dock_scheduler.exceptions import (
    ChangeRequestAlreadyDecidedError,
    ChangeRequestNotFoundError,
    DockSchedulerError,
    InvalidBookingError,
)
from setuhaul.backend.dock_scheduler.models import (
    ChangeRequestRole,
    DockSlot,
    DriverConstraints,
    HoldResult,
    SlotLifecycleStage,
    SlotSuggestion,
)
from setuhaul.backend.dock_scheduler.repository import DockSchedulerRepository, parse_ts
from setuhaul.backend.dock_scheduler.scheduler import DeterministicReschedulingEngine


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


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
        self.repository.ensure_future_slots_for_shipment(shipment_id)
        slots = self.repository.compatible_slots(shipment_id)

        # compatible_slots() has no notion of the facility's own operating
        # hours -- suggest_slots() already filtered by them via
        # FacilityConstraintEvaluator, but this board (what WMS/TMS/driver
        # actually see and click) never did, so a slot the seed data
        # generated outside the facility's declared open_time/close_time
        # (e.g. a 21:00-22:00 slot at a facility that closes at 21:00) was
        # showing up as a normal, bookable option. Filter it out here too.
        facility_id = self.repository.facility_id_for_shipment(shipment_id)
        if facility_id:
            facility = self.repository.facility(facility_id)
            evaluator = FacilityConstraintEvaluator(dict(facility), [])
            slots = [
                slot
                for slot in slots
                if evaluator.slot_within_operating_hours(parse_ts(slot["slot_start_ts"]), parse_ts(slot["slot_end_ts"]))
            ]

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

    def dock_board_unavailable_reason(self, shipment_id: str) -> str | None:
        """Explain why dock_board() came back empty for this shipment.

        The frontend only calls this when the board is already known to be
        empty, so it re-derives the answer by walking the exact same filter
        chain dock_board()/compatible_slots() apply -- docks at the facility
        -> ACTIVE docks -> dock-type match -> refrigeration match -> weight
        capacity match -> operating-hours overlap -- and returns a message
        for the FIRST stage that eliminates every candidate. That's the
        actual blocker; later stages are moot once an earlier one has
        already zeroed out the list. Returns None only if nothing here can
        explain it (e.g. a transient data issue) -- callers should fall back
        to a generic "no slots right now" message in that case.
        """
        try:
            target = self.repository.shipment(shipment_id)
        except DockSchedulerError:
            return None

        facility_id = target.get("destination_facility_id")
        if not facility_id:
            return "This shipment has no destination facility assigned yet, so no dock board can be shown."

        try:
            facility = self.repository.facility(facility_id)
        except DockSchedulerError:
            facility = None

        all_docks = self.repository.docks_for_facility(facility_id)
        if not all_docks:
            return "This facility has no docks configured yet."

        active_docks = [d for d in all_docks if d.get("dock_status") == "ACTIVE"]
        if not active_docks:
            return "Every dock at this facility is currently inactive or out of service."

        required_dock_type = target.get("required_dock_type")
        type_matches = [
            d for d in active_docks if required_dock_type == "ANY" or d.get("dock_type") == required_dock_type
        ]
        if not type_matches:
            available_types = sorted({d.get("dock_type") for d in active_docks if d.get("dock_type")})
            return (
                f"This shipment requires a {required_dock_type} dock, but no active dock at this "
                f"facility is that type -- active dock types here: {', '.join(available_types) or 'none'}."
            )

        refrigeration_matches = type_matches
        if target.get("temperature_control_required"):
            refrigeration_matches = [d for d in type_matches if d.get("supports_refrigerated")]
            if not refrigeration_matches:
                return (
                    f"This shipment needs a refrigerated dock, but no active {required_dock_type} dock "
                    "at this facility supports refrigeration."
                )

        load_weight_kg = target.get("load_weight_kg")
        weight_matches = refrigeration_matches
        if load_weight_kg is not None:
            weight_matches = [
                d
                for d in refrigeration_matches
                if d.get("max_vehicle_weight_kg") is None or d.get("max_vehicle_weight_kg") >= load_weight_kg
            ]
            if not weight_matches:
                heaviest = max((d.get("max_vehicle_weight_kg") or 0) for d in refrigeration_matches)
                return (
                    f"This shipment's load ({load_weight_kg:,} kg) exceeds the weight capacity of "
                    f"every matching dock at this facility (highest capacity here: {heaviest:,} kg)."
                )

        hours_note = ""
        if facility and facility.get("open_time") and facility.get("close_time"):
            hours_note = f" ({facility['open_time']}–{facility['close_time']})"
        dock_word = "dock" if len(weight_matches) == 1 else "docks"
        return (
            f"{len(weight_matches)} matching {dock_word} at this facility, but none currently have an "
            f"open appointment slot within the facility's operating hours{hours_note} right now -- "
            "try again shortly, or ask WMS to open more slots."
        )

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

    def rebook_slot(self, shipment_id: str, new_slot_id: str) -> str:
        """Directly move a shipment's appointment to a different slot.

        Unlike the driver self-service hold -> confirm flow (which needs a
        temporary hold to prevent racing a concurrent driver browsing the
        same board), this is used after a human has already made the
        availability decision -- a WMS-approved dock-slot-change request --
        so it creates a short hold and immediately confirms it in one call.
        book_after_acceptance already knows how to move a shipment off a
        CONFIRMED appointment onto a new slot (it cancels the old one and
        books the new one), which is exactly what a "change slot" means.

        Only a strictly AVAILABLE slot is accepted here -- create_hold()
        below unconditionally requires AVAILABLE and raises otherwise, so
        this used to also "accept" HELD only to immediately blow up one
        line later whenever the requested slot happened to be held by
        someone else's concurrent browsing (a real, previously-silent
        failure mode for WMS approving a pending change request).
        """
        compatible = self.repository.compatible_slots(shipment_id)
        if not any(slot["slot_id"] == new_slot_id for slot in compatible):
            raise InvalidBookingError("Slot is not a feasible option for this shipment")
        availability = self.repository.slot_availability(new_slot_id)
        if availability and availability["availability_status"] != "AVAILABLE":
            raise InvalidBookingError(
                "Selected slot is no longer available "
                f"(currently {availability['availability_status'].lower()})"
            )
        self.repository.create_hold(shipment_id, new_slot_id, ttl_minutes=5)
        return self.repository.book_after_acceptance(shipment_id, new_slot_id, accepted=True)

    # -- dock-slot change requests (TMS/driver requested, WMS approved) -----

    def create_change_request(
        self,
        shipment_id: str,
        requested_slot_id: str,
        requested_by_role: ChangeRequestRole,
        requested_by_user_id: str,
        reason: str | None,
        displaced_shipment_id: str | None = None,
        displaced_to_slot_id: str | None = None,
    ) -> dict:
        # Validate the requested slot is at least feasible for this shipment
        # up front, so an obviously-bad request doesn't sit in WMS's queue
        # only to fail validation again at approval time. Feasibility here
        # is about dock type/refrigeration/weight/time window, not current
        # occupancy -- a priority-swap request's requested_slot_id is
        # expected to be OCCUPIED right now (that's the whole point), so
        # this deliberately doesn't also require it to be AVAILABLE.
        compatible = self.repository.compatible_slots(shipment_id)
        if not any(slot["slot_id"] == requested_slot_id for slot in compatible):
            raise InvalidBookingError("Slot is not a feasible option for this shipment")

        current = self.repository.current_appointment(shipment_id)
        payload = {
            "change_request_id": f"CHG-{uuid4().hex[:10].upper()}",
            "shipment_id": shipment_id,
            "current_appointment_id": current["appointment_id"] if current else None,
            "requested_slot_id": requested_slot_id,
            "requested_by_role": requested_by_role.value,
            "requested_by_user_id": requested_by_user_id,
            "reason": reason,
            "request_status": "PENDING",
            "created_at": _now_iso(),
            "displaced_shipment_id": displaced_shipment_id,
            "displaced_to_slot_id": displaced_to_slot_id,
        }
        return self.repository.create_change_request(payload)

    def list_change_requests(self, status: str | None = None) -> list[dict]:
        return self.repository.list_change_requests(status)

    def decide_change_request(self, change_request_id: str, approve: bool, decided_by_user_id: str, note: str | None) -> dict:
        request = self.repository.get_change_request(change_request_id)
        if request is None:
            raise ChangeRequestNotFoundError(f"Unknown change request: {change_request_id}")
        if request["request_status"] != "PENDING":
            raise ChangeRequestAlreadyDecidedError(
                f"This request was already {request['request_status'].lower()}."
            )

        if approve:
            # A priority-swap request (displaced_shipment_id set) needs the
            # occupant moved off requested_slot_id before the original
            # requester can take it -- rebook_slot() requires a strictly
            # AVAILABLE target, and requested_slot_id is occupied by
            # definition for a swap. Move the displaced shipment first.
            #
            # This is two separate rebook_slot() calls, not one DB
            # transaction (this codebase's Supabase client has no
            # cross-call transaction primitive -- see book_after_acceptance
            # and its own callers for the same limitation). If the first
            # call succeeds and the second then fails (e.g. someone else
            # grabbed requested_slot_id in between), the displaced shipment
            # has already moved but the original requester hasn't -- the
            # request is left PENDING so WMS sees it needs another look,
            # and the error message says explicitly what already happened
            # rather than leaving that silent.
            if request.get("displaced_shipment_id") and request.get("displaced_to_slot_id"):
                try:
                    self.rebook_slot(request["displaced_shipment_id"], request["displaced_to_slot_id"])
                except DockSchedulerError as exc:
                    raise InvalidBookingError(
                        f"Could not move the displaced shipment {request['displaced_shipment_id']} to its "
                        f"replacement slot first: {exc}. No changes were made."
                    ) from exc
                try:
                    self.rebook_slot(request["shipment_id"], request["requested_slot_id"])
                except DockSchedulerError as exc:
                    raise InvalidBookingError(
                        f"Displaced shipment {request['displaced_shipment_id']} was already moved to "
                        f"{request['displaced_to_slot_id']}, but rebooking {request['shipment_id']} onto "
                        f"{request['requested_slot_id']} then failed: {exc}. Please resolve manually."
                    ) from exc
            else:
                # Perform the actual rebook first -- if the slot became
                # unavailable while the request sat pending, this raises and
                # the request is left PENDING rather than being marked
                # APPROVED against a change that never actually happened.
                self.rebook_slot(request["shipment_id"], request["requested_slot_id"])

        updated = self.repository.update_change_request(
            change_request_id,
            {
                "request_status": "APPROVED" if approve else "DECLINED",
                "decided_at": _now_iso(),
                "decided_by_user_id": decided_by_user_id,
                "decision_note": note,
            },
        )
        return updated or request

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
