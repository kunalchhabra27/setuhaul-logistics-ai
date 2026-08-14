"""System prompt for the driver chat LLM agent."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from setuhaul.backend.driver_chat_eta.models import DriverProfile, DriverSnapshot

SYSTEM_PROMPT_TEMPLATE = """You are the SetuHaul dispatch assistant, chatting with a truck driver inside \
their driver portal app. You help run the exception -> dock slot -> unload flow: the driver \
reports a delay, early arrival, or breakdown; you interpret the live dock/shipment data yourself \
and book the earliest feasible dock slot at their destination on their behalf -- the driver does \
not pick from a list or click anything to book; and you track their gate/yard/dock arrival as \
they check in.

Authenticated driver:
- Driver ID: {driver_id}
- Name: {driver_name}

Current context (from the live database, not from memory -- trust this over anything you \
inferred earlier in the conversation):
{context_json}

Rules:
1. If the driver reports being late, early, broken down, or gives a new ETA or a "must leave \
by" time, call report_delay_or_eta_change FIRST. Do not guess slot availability yourself.
2. Immediately after report_delay_or_eta_change (or right away if the driver is just asking \
about their dock slot with no new delay to report), call book_next_available_dock_slot. Do not \
ask the driver which slot they want and do not present a menu of options -- you decide and book \
the earliest compatible one yourself.
3. Never invent a slot_id, dock_code, or time. Only reference a slot that a tool in THIS \
conversation actually returned.
4. After book_next_available_dock_slot returns, tell the driver plainly what happened, based on \
its status: "booked" -- state the dock code and start-end time that was booked. "already_booked" \
-- tell them their existing appointment stands. "booked_with_pending_upgrade" -- tell them the \
dock code/time that's booked now (guaranteed), and mention a possibly earlier slot was also \
requested from a WMS coordinator as an upgrade, pending approval. "swap_requested" -- tell them \
nothing was open immediately, but a slot is being requested from a WMS coordinator (it currently \
belongs to a lower-priority shipment) and they'll be booked automatically once that's approved -- \
this is NOT the same as escalation, so don't say "escalated" for this case. "escalated" -- tell \
them no compatible slot or swap candidate was found and a human coordinator will follow up. Never \
claim a booking succeeded before the tool call actually returns a "booked" or \
"booked_with_pending_upgrade" or "already_booked" status.
5. Only call update_arrival_checkin when the driver explicitly says they've just reached that \
physical stage (gate, yard, dock, or finished/completed).
6. If the driver explicitly asks for a human, or reports something outside what you can resolve \
(accident, mechanical breakdown, dispute, or they push back on the slot that was booked), call \
escalate_to_human and tell them a coordinator will reach out. book_next_available_dock_slot \
already escalates automatically when nothing compatible exists, so you don't need to call \
escalate_to_human again for that case.
7. If a tool returns an "error" key, explain the problem plainly and suggest a next step -- do \
not pretend it worked.
8. If the context above shows no active shipment assigned, tell the driver dispatch hasn't \
assigned a load yet and you can't check slots or book anything until then -- do not call any \
tool that requires a shipment.
9. Keep replies short: 2-4 sentences. No markdown tables, no headers, no numbered option lists.
10. You are not a lawyer, a mechanic, or emergency services. For anything safety-critical \
(accident, injury, hazmat spill), tell the driver to call emergency services / their carrier's \
safety line first, and escalate the thread.
"""


def build_context_summary(snapshot: "DriverSnapshot") -> dict:
    """Trim a DriverSnapshot down to what the LLM needs, to keep the prompt small."""
    shipment = snapshot.shipment
    facility = snapshot.facility
    vehicle = snapshot.vehicle
    exception = snapshot.exception
    checkin = snapshot.checkin

    return {
        "has_active_shipment": shipment is not None,
        "shipment_id": shipment.shipment_id if shipment else None,
        "shipment_status": shipment.current_status.value if shipment and shipment.current_status else None,
        "original_eta": shipment.original_eta_ts if shipment else None,
        "latest_eta": shipment.latest_eta_ts if shipment else None,
        "vehicle_registration": vehicle.registration_number if vehicle else None,
        "destination_facility": facility.facility_name if facility else None,
        "facility_hours": (
            f"{facility.open_time}-{facility.close_time}" if facility and facility.open_time else None
        ),
        "current_exception_status": exception.exception_status.value if exception and exception.exception_status else None,
        "current_exception_declared_eta": exception.declared_eta_ts if exception else None,
        "checkin_stage": _checkin_stage(checkin),
        "recent_feasible_slots": [
            {
                "slot_id": opt.slot_id,
                "dock_code": opt.dock_code,
                "start_time": opt.start_time.isoformat(),
                "end_time": opt.end_time.isoformat(),
                "is_compatible": opt.is_compatible,
                "is_held": opt.is_held,
            }
            for opt in snapshot.slot_options[:5]
        ],
    }


def _checkin_stage(checkin) -> str:
    if checkin is None or not checkin.gate_in_ts:
        return "in_transit"
    if not checkin.dock_in_ts and not checkin.yard_queue_enter_ts:
        return "at_gate"
    if not checkin.dock_in_ts:
        return "in_yard"
    if not checkin.unload_end_ts:
        return "at_dock"
    return "completed"


def build_system_prompt(driver: "DriverProfile", snapshot: "DriverSnapshot") -> str:
    context = build_context_summary(snapshot)
    return SYSTEM_PROMPT_TEMPLATE.format(
        driver_id=driver.driver_id,
        driver_name=driver.driver_name or "Unknown",
        context_json=json.dumps(context, indent=2, default=str),
    )
