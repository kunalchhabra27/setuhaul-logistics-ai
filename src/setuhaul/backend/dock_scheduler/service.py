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
from setuhaul.infrastructure import cache


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class DockSchedulerService:
    """Public service for proposing, holding, and confirming dock appointments."""

    def __init__(self, repository: DockSchedulerRepository, cache_scope: cache.CacheScope | None = None):
        self.repository = repository
        self.cache_scope = cache_scope or cache.PUBLIC_SCOPE
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
        rows = cache.get_or_set_scoped(
            self.cache_scope, "dock-board", shipment_id, {}, cache.TTL_HOT,
            lambda: [slot.model_dump(mode="json") for slot in self._build_dock_board(shipment_id)],
        )
        return [DockSlot.model_validate(row) for row in rows]

    def facility_availability_cached(self, facility_id: str) -> list[dict]:
        """Cached read-through wrapper around
        ``repository.facility_dock_availability`` -- the shipment-agnostic
        (which docks, which slots, who occupies/holds each one) dataset
        shared by every shipment shown on the same facility's board.

        Measured: ~9 sequential Supabase round trips (~3.5s) per call before
        this cache existed -- and since a WMS board shows *every* shipment
        at a facility, each with its own ``compatible_slots_cached()`` call
        below, that cost was paid **once per shipment shown**, not once per
        facility, even though the underlying data barely differs between
        them (only the compatibility filter is shipment-specific). Caching
        it here, keyed by facility_id, means only the *first* shipment
        viewed at a facility within TTL_HOT pays this cost -- every other
        shipment at the same facility gets a Redis hit instead.
        """
        return cache.get_or_set_scoped(
            self.cache_scope, "facility-dock-availability", facility_id, {}, cache.TTL_HOT,
            lambda: self.repository.facility_dock_availability(facility_id),
        )

    def compatible_slots_cached(self, shipment_id: str) -> list[dict]:
        """Cached read-through equivalent of ``repository.compatible_slots``.

        Composes two independently-cached layers: the shipment's own
        operational state (uncached -- a handful of round trips, invalidated
        via ``cache.invalidate_shipment`` on mutation) determines its
        compatibility requirements, then ``facility_availability_cached``
        above supplies the (heavily shared, facility-scoped) availability
        dataset those requirements filter cheaply in-memory. The whole
        composed result is itself cached per-shipment too, so a repeat view
        of the *same* shipment within TTL_HOT needs no Supabase calls at
        all, not even the operational-state lookup.

        Its only prior "cache" was ``DockSchedulerRepository.
        _compatible_slots_cache``, a plain per-instance dict -- useless
        across requests since a fresh repository is constructed on every
        HTTP call, so every caller of the raw method paid the full cost on
        every single call. ``dock_board()`` above avoids this for its own
        (differently-shaped, filtered/joined) output; this does the
        equivalent for the raw slot rows, so any other read-only caller
        (e.g. driver_chat_eta's feasibility check, polled every 8s) gets the
        same cross-request speedup instead of silently bypassing Redis.
        Mutation-path callers (hold_slot, rebook_slot, create_change_request)
        must keep calling ``repository.compatible_slots`` directly -- they
        need the true current state to avoid double-booking, and they're
        user-action triggered, not part of the polling hot path.
        """

        def _fetch() -> list[dict]:
            target = self.repository.operational_state(shipment_id)
            availability = self.facility_availability_cached(target["destination_facility_id"])
            return self.repository.filter_compatible_slots(target, availability)

        return cache.get_or_set_scoped(self.cache_scope, "dock-board-slots", shipment_id, {}, cache.TTL_HOT, _fetch)

    def current_appointment_cached(self, shipment_id: str) -> dict | None:
        """Cached read-through wrapper around ``repository.current_appointment``.

        Three sequential round trips (~580ms measured) for data that only
        changes on a booking mutation -- and every such mutation already
        calls ``_invalidate_booking_caches`` -> ``cache.invalidate_shipment``,
        which busts this key too (see that function). Safe to cache with no
        new invalidation call sites needed.
        """
        return cache.get_or_set_scoped(
            self.cache_scope, "current-appointment", shipment_id, {}, cache.TTL_HOT,
            lambda: self.repository.current_appointment(shipment_id),
        )

    def _build_dock_board(self, shipment_id: str) -> list[DockSlot]:
        self.repository.ensure_future_slots_for_shipment(shipment_id)
        slots = self.compatible_slots_cached(shipment_id)

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

    def hold_slot(self, shipment_id: str, slot_id: str, ttl_minutes: int = 15) -> HoldResult:
        """Reserve a slot temporarily while the driver considers the option."""
        compatible = self.repository.compatible_slots(shipment_id)
        if not any(slot["slot_id"] == slot_id for slot in compatible):
            raise InvalidBookingError("Slot is not a feasible option for this shipment")
        result = self.repository.create_hold(shipment_id, slot_id, ttl_minutes)
        self._invalidate_booking_caches(shipment_id)
        return result

    def request_confirmation(self, shipment_id: str, slot_id: str) -> str:
        """Move from HELD to PENDING_CONFIRMATION."""
        hold = self.repository.active_hold_for_shipment(shipment_id, slot_id)
        if hold is None:
            self.repository.create_hold(shipment_id, slot_id, ttl_minutes=15)
        appointment_id = self.repository.create_pending_appointment(shipment_id, slot_id)
        self._invalidate_booking_caches(shipment_id)
        return appointment_id

    def confirm_booking(self, shipment_id: str, slot_id: str, accepted: bool = True) -> str:
        """Confirm a held or pending slot after explicit driver acceptance."""
        appointment_id = self.repository.book_after_acceptance(shipment_id, slot_id, accepted)
        self._invalidate_booking_caches(shipment_id)
        return appointment_id

    def cancel_hold(self, hold_id: str) -> None:
        """Release a temporary slot hold."""
        self.repository.release_hold(hold_id)
        # release_hold only takes hold_id, not shipment_id -- but a hold
        # changes availability for every shipment that could have used the
        # slot anyway (see invalidate_dock_boards' docstring), so a
        # shipment-scoped invalidation isn't needed here.
        cache.invalidate_dock_boards()

    def cancel_pending(self, shipment_id: str, slot_id: str) -> None:
        """Cancel a pending booking and release any active hold."""
        self.repository.cancel_pending(shipment_id, slot_id)
        self._invalidate_booking_caches(shipment_id)

    def _invalidate_booking_caches(self, shipment_id: str) -> None:
        """A booking mutation for one shipment changes slot availability for
        every other shipment that could have used the same slot, and this
        shipment's own cached shipment/context/checkin/snapshot views are
        now stale too (dock_scheduler shares its tables directly with
        tms/checkin_portal/driver_chat_eta)."""
        cache.invalidate_dock_boards()
        cache.invalidate_shipment(shipment_id)
        # Correctly facility-scoped (unlike invalidate_dock_boards' own
        # "global" sweep, which no per-facility key actually checks -- see
        # that function's docstring) -- this is what makes the shared
        # facility-availability cache other shipments at the same facility
        # rely on (see DockSchedulerService.facility_availability_cached)
        # actually bust promptly instead of only via its own TTL_HOT expiry.
        facility_id = self.repository.facility_id_for_shipment(shipment_id)
        if facility_id:
            cache.invalidate_facility_board(facility_id)

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
        appointment_id = self.repository.book_after_acceptance(shipment_id, new_slot_id, accepted=True)
        self._invalidate_booking_caches(shipment_id)
        return appointment_id

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
        row = self.repository.create_change_request(payload)
        cache.invalidate_facility_board(self.repository.facility_id_for_shipment(shipment_id))
        return row

    def list_change_requests(self, status: str | None = None) -> list[dict]:
        return cache.get_or_set_scoped(
            self.cache_scope, "change-requests", "global", {"status": status}, cache.TTL_HOT,
            lambda: self.repository.list_change_requests(status),
        )

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
        cache.invalidate_facility_board(self.repository.facility_id_for_shipment(request["shipment_id"]))
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
