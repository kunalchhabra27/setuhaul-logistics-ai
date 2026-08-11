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
    EscalateInput,
    ListFeasibleSlotsInput,
    ReportExceptionInput,
    SlotIdInput,
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

    @tool(args_schema=SlotIdInput)
    def hold_dock_slot(slot_id: str) -> dict:
        """Place a short temporary hold on one dock slot so no one else can take it
        while the driver decides. This does NOT book the appointment yet -- the driver
        must still explicitly confirm before confirm_dock_slot is called."""
        try:
            result = service.hold_slot(principal, slot_id)
            return {"status": "held", "slot_id": slot_id, "message": result.message}
        except DriverChatError as exc:
            return {"error": exc.code, "message": exc.message}

    @tool(args_schema=SlotIdInput)
    def confirm_dock_slot(slot_id: str) -> dict:
        """Confirm and permanently book the dock slot the driver currently has held.
        Only call this after the driver has explicitly confirmed, in this
        conversation, that they want this specific slot -- never call it just because
        a slot was held, and never call it for a slot that was not held first."""
        try:
            result = service.confirm_slot(principal, slot_id)
            return {"status": "confirmed", "slot_id": slot_id, "message": result.message}
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

    return [
        report_delay_or_eta_change,
        list_feasible_dock_slots,
        hold_dock_slot,
        confirm_dock_slot,
        update_arrival_checkin,
        escalate_to_human,
    ]
