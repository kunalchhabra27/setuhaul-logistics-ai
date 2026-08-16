"""LangChain tool wrappers around ``DriverChatService``.

Every tool here is a thin adapter: it validates nothing on its own and
contains no business logic. It just calls the same RLS-scoped,
caller-authenticated ``DriverChatService`` methods that the REST endpoints
in ``api.py`` call, catches the same typed ``DriverChatError`` exceptions
those endpoints catch, and returns a JSON-serializable dict either way so
the LLM always gets a structured result to reason about (never a raw
traceback). No tool here ever uses a service-role key or bypasses the
caller's own Supabase JWT -- ``service`` was built by ``api.get_service``
using the driver's own token, exactly as it is for button-driven actions.

Tools are built per-request via ``build_tools(service, principal)`` rather
than defined at module scope, because each one must close over the
specific authenticated driver's service/principal for this turn -- there is
no shared, cross-driver tool instance.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from setuhaul.backend.driver_chat_eta.exceptions import DriverChatError
from setuhaul.backend.driver_chat_eta.llm.schemas import (
    AutoBookSlotInput,
    CancelPendingRequestInput,
    CheckRequestStatusInput,
    EscalateInput,
    FlagEmergencyInput,
    ListFeasibleSlotsInput,
    ReportExceptionInput,
    UpdateCheckinInput,
)

if TYPE_CHECKING:
    from setuhaul.backend.driver_chat_eta.auth import DriverPrincipal
    from setuhaul.backend.driver_chat_eta.service import DriverChatService


def _serialize_slot(opt: Any) -> dict:
    return {
        "slot_id": opt.slot_id,
        "dock_id": opt.dock_id,
        "dock_code": opt.dock_code,
        "start_time": opt.start_time.isoformat(),
        "end_time": opt.end_time.isoformat(),
        "is_compatible": opt.is_compatible,
        "reason": opt.compatibility_reason,
        "estimated_wait_minutes": opt.estimated_wait_minutes,
        "is_held": opt.is_held,
        "is_booked_by_me": opt.is_booked_by_me,
    }


def build_tools(service: "DriverChatService", principal: "DriverPrincipal") -> list:
    """Build the driver's tool set for one chat turn."""
    from langchain_core.tools import tool

    @tool(args_schema=ReportExceptionInput)
    def report_delay_or_eta_change(
        delay_minutes: int = 0,
        declared_eta_iso: str | None = None,
        must_leave_by_iso: str | None = None,
        note: str = "",
    ) -> dict:
        """Record a driver-reported delay, breakdown, or ETA change, and return the
        currently compatible dock slot options at the destination facility. Always
        call this FIRST when the driver reports being late, early, broken down, or
        gives a new arrival time or a hard leave-by deadline -- before offering or
        discussing any slots."""
        try:
            return service.report_exception(
                principal,
                delay_minutes=delay_minutes,
                declared_eta_iso=declared_eta_iso,
                must_leave_by_iso=must_leave_by_iso,
                note=note,
            )
        except DriverChatError as exc:
            return {"error": exc.code, "message": exc.message}

    @tool(args_schema=ListFeasibleSlotsInput)
    def list_feasible_dock_slots() -> dict:
        """List the currently compatible open dock slots at the driver's destination
        facility, given their latest declared ETA. Use this if the driver asks to see
        slots again, asks for other options, or asks a question about slot
        availability without reporting a new delay."""
        try:
            options = service.get_current_feasible_slots(principal)
            # Capped here at the LLM boundary, not in the service method
            # itself -- get_current_feasible_slots also backs the driver-facing
            # DockSlotBoard (via DriverSnapshot.slot_options), which wants the
            # full list. options is already sorted compatible-first.
            from setuhaul.backend.driver_chat_eta.service import LLM_SLOT_SUMMARY_LIMIT

            return {"feasible_slots": [_serialize_slot(opt) for opt in options[:LLM_SLOT_SUMMARY_LIMIT]]}
        except DriverChatError as exc:
            return {"error": exc.code, "message": exc.message}

    @tool(args_schema=AutoBookSlotInput)
    def book_next_available_dock_slot() -> dict:
        """Identify the earliest dock slot that is compatible with the shipment
        (dock type, refrigeration, weight) and fits the driver's latest declared
        ETA, and FILE it as a change request for WMS to review -- you never book
        or approve anything yourself, only propose. This also works for changing
        a slot the shipment already has booked (there's no separate "change my
        slot" tool -- calling this again re-evaluates and, if a better slot
        exists, files a request to move there). It also checks whether a
        genuinely lower-priority shipment is occupying a better (earlier) slot
        that could be freed up -- if so, it files that as a swap request instead
        (still pending WMS approval, never auto-executed). Call this immediately
        after report_delay_or_eta_change (or after list_feasible_dock_slots if
        the driver is just asking about slots with no new delay to report)
        instead of listing multiple options for the driver to choose from.
        Only call this when the driver is explicitly asking to book, change, or move
        a dock slot -- never to answer a question about an EXISTING request's status
        (use check_request_status for that) or as a default action for messages that
        aren't about booking at all.
        Returns one of these statuses: "already_booked" (the existing confirmed
        appointment still fits, nothing to request), "request_submitted" (a
        change request was filed and is now pending WMS approval -- check the
        "via_swap" field to know whether it also requires displacing another
        shipment), "request_already_pending" (a request for this exact slot was
        already filed and is still waiting on WMS -- nothing new was submitted,
        this is the same request as before, not a fresh one), "gated_in" (the
        shipment already checked in at the facility, so no further change is
        possible from chat -- tell the driver to speak to gate/WMS staff on
        site), or "escalated" (nothing compatible and no swap candidate existed,
        so this was handed to a human coordinator)."""
        try:
            return service.auto_book_earliest_feasible_slot(principal)
        except DriverChatError as exc:
            return {"error": exc.code, "message": exc.message}

    @tool(args_schema=CheckRequestStatusInput)
    def check_request_status() -> dict:
        """Look up whether WMS has decided on the driver's most recently filed dock
        slot request. Use this whenever the driver asks something like "is my
        request approved?", "did WMS decide?", "what happened to my booking?",
        or "is my slot confirmed?" -- NEVER call book_next_available_dock_slot to
        answer a question like that, since that tool files a NEW request instead
        of checking an existing one. Returns has_request: False if nothing has
        been filed yet; otherwise "status": "PENDING" (still waiting on WMS),
        "APPROVED" (the requested slot is now the shipment's confirmed
        appointment), or "DECLINED" (WMS rejected it)."""
        try:
            return service.get_latest_change_request_status(principal)
        except DriverChatError as exc:
            return {"error": exc.code, "message": exc.message}

    @tool(args_schema=CancelPendingRequestInput)
    def cancel_pending_dock_request(reason: str | None = None) -> dict:
        """Withdraw the driver's own dock-slot change request while it's still PENDING
        with WMS. Use this whenever the driver says something like "cancel my request",
        "never mind, don't book that", "withdraw my slot request", or "forget the change
        I asked for" -- NEVER call book_next_available_dock_slot for this, since that
        tool has no concept of cancelling anything and would just file ANOTHER request
        instead, the opposite of what the driver wants.
        Returns "cancelled" (the PENDING request was withdrawn -- tell the driver plainly
        that it's cancelled, nothing pending anymore), "no_pending_request" (there was
        nothing to cancel -- tell them that), or "already_decided" (WMS decided it just
        before it could be cancelled -- tell the driver the real outcome instead of
        pretending the cancel worked; they may need check_request_status next)."""
        try:
            return service.cancel_pending_request(principal, reason=reason)
        except DriverChatError as exc:
            return {"error": exc.code, "message": exc.message}

    @tool(args_schema=UpdateCheckinInput)
    def update_arrival_checkin(stage: str) -> dict:
        """Update the driver's physical arrival stage at the destination facility.
        Only call this when the driver explicitly says they've just reached that
        stage (gate, yard, dock, or finished/completed) -- never infer or guess it."""
        try:
            from setuhaul.backend.driver_chat_eta.models import ArrivalUpdateChoice, CheckinUpdateRequest

            service.update_checkin(principal, CheckinUpdateRequest(arrival_status=ArrivalUpdateChoice(stage)))
            return {"status": "updated", "stage": stage}
        except DriverChatError as exc:
            return {"error": exc.code, "message": exc.message}

    @tool(args_schema=EscalateInput)
    def escalate_to_human(reason: str) -> dict:
        """Hand the conversation off to a human dispatch coordinator. Use this when no
        feasible dock slot was found, the driver explicitly asks for a human, or the
        situation is outside what you can resolve yourself (e.g. accident, mechanical
        breakdown, dispute, or the driver rejecting every offered slot)."""
        try:
            service.escalate(principal, reason)
            return {"status": "escalated"}
        except DriverChatError as exc:
            return {"error": exc.code, "message": exc.message}

    @tool(args_schema=FlagEmergencyInput)
    def flag_emergency_situation(reason: str) -> dict:
        """Flag a SAFETY-CRITICAL emergency: accident, engine failure/breakdown
        that leaves the driver stranded or unsafe, medical emergency, or a
        hazmat spill. This is different from escalate_to_human (which is for
        ordinary "no slot found"/"driver wants a person" cases) -- use this one
        specifically for situations involving driver safety. This marks the
        thread escalated and makes an "Send Emergency Alert" button appear in
        the driver's app so THEY can choose to notify the emergency contact by
        SMS with their shipment/location details -- it does not send that SMS
        itself. Always also tell the driver, in your reply, to call emergency
        services directly first if there is any immediate danger -- you are not
        a substitute for that."""
        try:
            service.flag_emergency_situation(principal, reason)
            return {"status": "flagged", "message": "Emergency flagged. The driver can now send an alert from the app."}
        except DriverChatError as exc:
            return {"error": exc.code, "message": exc.message}

    return [
        report_delay_or_eta_change,
        list_feasible_dock_slots,
        book_next_available_dock_slot,
        cancel_pending_dock_request,
        check_request_status,
        update_arrival_checkin,
        escalate_to_human,
        flag_emergency_situation,
    ]
