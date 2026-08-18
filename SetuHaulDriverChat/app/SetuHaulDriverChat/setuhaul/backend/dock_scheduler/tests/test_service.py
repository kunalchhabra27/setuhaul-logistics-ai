from __future__ import annotations

import pytest

from setuhaul.backend._testing.fake_supabase import FakeSupabaseClient
from setuhaul.backend.dock_scheduler.exceptions import ChangeRequestAlreadyDecidedError, InvalidBookingError
from setuhaul.backend.dock_scheduler.models import ChangeRequestRole, SuggestionType
from setuhaul.backend.dock_scheduler.repository import DockSchedulerRepository
from setuhaul.backend.dock_scheduler.service import DockSchedulerService
from setuhaul.backend.dock_scheduler.tests.conftest import (
    DOCK_REEFER,
    DOCK_STANDARD_1,
    FACILITY,
    SHP_NORMAL,
    SHP_OCCUPANT,
    SHP_REEFER,
    seed_ts,
)


def test_suggest_returns_only_standard_docks_for_standard_shipment(service):
    suggestions = service.suggest_slots(SHP_NORMAL, limit=10)
    assert suggestions
    assert all(s.suggestion_type is SuggestionType.ASSIGN_AVAILABLE for s in suggestions)
    dock_codes = {s.dock_code for s in suggestions}
    assert dock_codes == {"D1", "D2"}


def test_suggest_returns_only_reefer_dock_for_temperature_controlled_shipment(service):
    suggestions = service.suggest_slots(SHP_REEFER, limit=10)
    assert suggestions
    assert {s.dock_code for s in suggestions} == {"D5"}


def test_suggestions_sorted_by_start_then_dock(service):
    suggestions = service.suggest_slots(SHP_NORMAL, limit=10)
    starts = [(s.start, s.dock_code) for s in suggestions]
    assert starts == sorted(starts)


def test_hold_request_confirm_flow(service):
    suggestions = service.suggest_slots(SHP_NORMAL, limit=10)
    slot_id = suggestions[0].slot_id

    hold = service.hold_slot(SHP_NORMAL, slot_id)
    assert hold.slot_id == slot_id
    assert hold.shipment_id == SHP_NORMAL

    appointment_id = service.request_confirmation(SHP_NORMAL, slot_id)
    assert appointment_id

    confirmed_id = service.confirm_booking(SHP_NORMAL, slot_id, accepted=True)
    assert confirmed_id == appointment_id

    current = service.repository.current_appointment(SHP_NORMAL)
    assert current is not None
    assert current["slot_id"] == slot_id
    assert current["appointment_status"] == "CONFIRMED"

    # The now-occupied slot should no longer be offered as an available option.
    remaining = service.suggest_slots(SHP_NORMAL, limit=10)
    assert slot_id not in {s.slot_id for s in remaining if s.suggestion_type is SuggestionType.ASSIGN_AVAILABLE}


def test_cancel_hold_frees_the_slot(service):
    suggestions = service.suggest_slots(SHP_NORMAL, limit=10)
    slot_id = suggestions[0].slot_id

    hold = service.hold_slot(SHP_NORMAL, slot_id)
    service.cancel_hold(hold.hold_id)

    slot = service.repository.slot_availability(slot_id)
    assert slot["availability_status"] == "AVAILABLE"


def test_priority_swap_suggested_when_higher_priority_shipment_competes(tables):
    tables["appointments"].append(
        {
            "appointment_id": "APT-OCC",
            "shipment_id": SHP_OCCUPANT,
            "slot_id": "SLOT-D2-0800",
            "appointment_status": "CONFIRMED",
            "booking_source": "PLANNER",
            "is_current": 1,
            "booked_at": "2026-08-01T12:00:00+05:30",
            "confirmed_at": "2026-08-01T12:05:00+05:30",
            "cancelled_at": None,
            "cancellation_reason": None,
            "replaced_appointment_id": None,
            "warehouse_confirmation_ref": None,
            "updated_at": "2026-08-01T12:05:00+05:30",
        }
    )
    tables["shipments"].append(
        {
            "shipment_id": "SHP-HIGH",
            "order_reference": "ORD-HIGH",
            "carrier_id": "CAR001",
            "driver_id": "DRV001",
            "vehicle_id": "VEH001",
            "origin_name": "Depot",
            "origin_city": "Jaipur",
            "destination_facility_id": FACILITY,
            "customer_name": "Test Customer",
            "product_category": "General",
            "load_weight_kg": 10000,
            "required_dock_type": "STANDARD",
            "temperature_control_required": 0,
            "priority_code": "HIGH",
            "planned_departure_ts": "2026-08-04T04:00:00+05:30",
            "original_eta_ts": "2026-08-04T08:00:00+05:30",
            "latest_eta_ts": None,
            "expected_unload_min": 45,
            "current_status": "PLANNED",
            "created_at": "2026-08-01T12:00:00+05:30",
            "updated_at": "2026-08-01T12:00:00+05:30",
        }
    )
    repository = DockSchedulerRepository(FakeSupabaseClient(tables))
    service = DockSchedulerService(repository)

    suggestions = service.suggest_slots("SHP-HIGH", limit=10)
    swaps = [s for s in suggestions if s.suggestion_type is SuggestionType.PRIORITY_SWAP]
    assert swaps, "expected a priority-swap suggestion against the LOW-priority occupant"
    assert swaps[0].displaced_shipment_id == SHP_OCCUPANT
    assert swaps[0].dock_code in {"D1", "D2"}


def test_dock_board_returns_every_compatible_slot_not_just_top_ranked(service):
    board = service.dock_board(SHP_NORMAL)
    # 3 STANDARD slots seeded across D1/D2 in the fixture -- board is unranked
    # and unlimited, unlike suggest_slots(limit=...). The fixture's seeded
    # slots are all dated 2026-08-04, which dock_board() also auto-backfills
    # forward from (see ensure_future_slots), so the real-world board may
    # contain more than just the 3 original rows -- assert the originals are
    # still present rather than an exact total.
    seeded_ids = {"SLOT-D1-0800", "SLOT-D1-0900", "SLOT-D2-0800"}
    board_ids = {s.slot_id for s in board}
    assert seeded_ids <= board_ids
    assert {s.dock_code for s in board} == {"D1", "D2"}
    assert all(s.availability_status == "AVAILABLE" for s in board)


def test_dock_board_excludes_slots_outside_facility_operating_hours(service, tables):
    # The facility fixture closes at 22:00 -- a slot starting at 22:00 ends
    # at 23:00, past close_time, and must never show up as a normal
    # bookable option on the board (this was the actual bug: dock_board()
    # applied dock-type/weight compatibility but never facility hours, so
    # WMS/TMS/driver boards could show slots beyond the facility's own
    # declared open/close window).
    tables["appointment_slots"].append(
        {
            "slot_id": "SLOT-D1-2200",
            "facility_id": FACILITY,
            "dock_id": DOCK_STANDARD_1,
            "slot_start_ts": seed_ts(22),
            "slot_end_ts": seed_ts(23),
            "slot_status": "OPEN",
            "block_reason": None,
            "created_at": seed_ts(12, day_offset=-3),
        }
    )
    board = service.dock_board(SHP_NORMAL)
    assert "SLOT-D1-2200" not in {s.slot_id for s in board}


def test_dock_board_unavailable_reason_explains_dock_type_mismatch(service, tables):
    # Remove the only REEFER dock -- SHP_REEFER needs one, so the board is
    # empty, and the reason must name the actual blocker (dock type), not a
    # generic "nothing here". Mutated in place (slice assignment), not
    # reassigned -- FakeSupabaseClient captures a reference to this exact
    # list object at construction time, so `tables["docks"] = [...]` would
    # rebind the dict key without the fake client ever seeing the change.
    tables["docks"][:] = [d for d in tables["docks"] if d["dock_id"] != DOCK_REEFER]
    assert service.dock_board(SHP_REEFER) == []
    reason = service.dock_board_unavailable_reason(SHP_REEFER)
    assert reason is not None
    assert "REEFER" in reason
    assert "STANDARD" in reason  # still lists what IS available, for context


def test_dock_board_unavailable_reason_explains_no_active_docks(service, tables):
    for dock in tables["docks"]:
        dock["dock_status"] = "OUT_OF_SERVICE"
    assert service.dock_board(SHP_NORMAL) == []
    reason = service.dock_board_unavailable_reason(SHP_NORMAL)
    assert reason == "Every dock at this facility is currently inactive or out of service."


def test_dock_board_unavailable_reason_explains_weight_capacity(service, tables):
    for dock in tables["docks"]:
        dock["max_vehicle_weight_kg"] = 1000  # below every shipment's load_weight_kg (10000)
    assert service.dock_board(SHP_NORMAL) == []
    reason = service.dock_board_unavailable_reason(SHP_NORMAL)
    assert reason is not None
    assert "exceeds the weight capacity" in reason


def test_hold_slot_accepts_any_compatible_slot_not_just_top_ranked(service):
    board = service.dock_board(SHP_NORMAL)
    # Hold the chronologically-last compatible slot -- this used to be
    # rejected when hold_slot only checked the top-20 ranked suggestions.
    target = sorted(board, key=lambda s: s.start)[-1]
    hold = service.hold_slot(SHP_NORMAL, target.slot_id)
    assert hold.slot_id == target.slot_id


def test_dock_board_shows_occupant_driver_name(service, tables):
    tables["appointments"].append(
        {
            "appointment_id": "APT-OCC2",
            "shipment_id": SHP_OCCUPANT,
            "slot_id": "SLOT-D2-0800",
            "appointment_status": "CONFIRMED",
            "booking_source": "PLANNER",
            "is_current": 1,
            "booked_at": "2026-08-01T12:00:00+05:30",
            "confirmed_at": "2026-08-01T12:05:00+05:30",
            "cancelled_at": None,
            "cancellation_reason": None,
            "replaced_appointment_id": None,
            "warehouse_confirmation_ref": None,
            "updated_at": "2026-08-01T12:05:00+05:30",
        }
    )
    board = service.dock_board(SHP_NORMAL)
    occupied = next(slot for slot in board if slot.slot_id == "SLOT-D2-0800")
    assert occupied.availability_status == "OCCUPIED"
    assert occupied.occupant_shipment_id == SHP_OCCUPANT
    assert occupied.occupant_driver_name == "Mukesh Yadav"

    # Untouched slots have no occupant, so no driver name either.
    open_slot = next(slot for slot in board if slot.slot_id == "SLOT-D1-0800")
    assert open_slot.occupant_driver_name is None


def test_facility_and_docks_lookup(service):
    facility = service.repository.facility(FACILITY)
    assert facility["facility_id"] == FACILITY
    rules = service.repository.facility_rules(FACILITY)
    assert any(rule["rule_type"] == "REEFER_DOCK_REQUIRED" for rule in rules)


def _confirm_slot(service, shipment_id: str, slot_id: str) -> None:
    service.hold_slot(shipment_id, slot_id)
    service.request_confirmation(shipment_id, slot_id)
    service.confirm_booking(shipment_id, slot_id, accepted=True)


def test_rebook_slot_moves_a_confirmed_appointment_to_a_new_slot(service):
    _confirm_slot(service, SHP_NORMAL, "SLOT-D1-0800")
    service.rebook_slot(SHP_NORMAL, "SLOT-D1-0900")

    current = service.repository.current_appointment(SHP_NORMAL)
    assert current["slot_id"] == "SLOT-D1-0900"
    assert current["appointment_status"] == "CONFIRMED"

    old_slot = service.repository.slot_availability("SLOT-D1-0800")
    assert old_slot["availability_status"] == "AVAILABLE"


def test_rebook_slot_rejects_an_incompatible_slot(service):
    with pytest.raises(InvalidBookingError):
        service.rebook_slot(SHP_REEFER, "SLOT-D1-0800")


def test_change_request_approval_actually_moves_the_appointment(service):
    _confirm_slot(service, SHP_NORMAL, "SLOT-D1-0800")
    original = service.repository.current_appointment(SHP_NORMAL)

    request = service.create_change_request(
        shipment_id=SHP_NORMAL,
        requested_slot_id="SLOT-D1-0900",
        requested_by_role=ChangeRequestRole.TMS,
        requested_by_user_id="tms-user-1",
        reason="Dispatcher requested a later slot.",
    )
    assert request["request_status"] == "PENDING"
    assert request["current_appointment_id"] == original["appointment_id"]
    assert request["dock_code"] == "D1"

    decided = service.decide_change_request(
        request["change_request_id"], approve=True, decided_by_user_id="wms-staff-1", note="Looks fine."
    )
    assert decided["request_status"] == "APPROVED"
    assert decided["decided_by_user_id"] == "wms-staff-1"

    current = service.repository.current_appointment(SHP_NORMAL)
    assert current["slot_id"] == "SLOT-D1-0900"


def test_change_request_decline_leaves_the_appointment_untouched(service):
    _confirm_slot(service, SHP_NORMAL, "SLOT-D1-0800")

    request = service.create_change_request(
        shipment_id=SHP_NORMAL,
        requested_slot_id="SLOT-D1-0900",
        requested_by_role=ChangeRequestRole.DRIVER,
        requested_by_user_id="driver-1",
        reason=None,
    )
    decided = service.decide_change_request(
        request["change_request_id"], approve=False, decided_by_user_id="wms-staff-1", note="Not available."
    )
    assert decided["request_status"] == "DECLINED"

    current = service.repository.current_appointment(SHP_NORMAL)
    assert current["slot_id"] == "SLOT-D1-0800"


def test_change_request_cannot_be_decided_twice(service):
    _confirm_slot(service, SHP_NORMAL, "SLOT-D1-0800")
    request = service.create_change_request(
        shipment_id=SHP_NORMAL,
        requested_slot_id="SLOT-D1-0900",
        requested_by_role=ChangeRequestRole.TMS,
        requested_by_user_id="tms-user-1",
        reason=None,
    )
    service.decide_change_request(request["change_request_id"], approve=True, decided_by_user_id="wms-1", note=None)

    with pytest.raises(ChangeRequestAlreadyDecidedError):
        service.decide_change_request(request["change_request_id"], approve=True, decided_by_user_id="wms-1", note=None)


def test_withdraw_change_request_marks_it_declined_with_a_distinguishing_note(service):
    # Used by driver_chat_eta when a driver cancels a request they made, or
    # when the chatbot supersedes a stale request with a better one it just
    # found (see DriverChatService._reuse_or_supersede_pending_request).
    # Reuses the 'DECLINED' status (the DB check constraint has no separate
    # CANCELLED/WITHDRAWN value), so the note must make clear this wasn't an
    # actual WMS rejection.
    request = service.create_change_request(
        shipment_id=SHP_NORMAL,
        requested_slot_id="SLOT-D1-0900",
        requested_by_role=ChangeRequestRole.DRIVER,
        requested_by_user_id="driver-1",
        reason=None,
    )

    withdrawn = service.withdraw_change_request(request["change_request_id"], withdrawn_by_user_id="driver-1")

    assert withdrawn["request_status"] == "DECLINED"
    assert withdrawn["decided_by_user_id"] == "driver-1"
    assert "withdraw" in withdrawn["decision_note"].lower()

    # The appointment (if any) is untouched -- withdrawing a request never
    # moves anything, unlike an approval.
    current = service.repository.current_appointment(SHP_NORMAL)
    assert current is None


def test_withdraw_change_request_cannot_withdraw_an_already_decided_request(service):
    request = service.create_change_request(
        shipment_id=SHP_NORMAL,
        requested_slot_id="SLOT-D1-0900",
        requested_by_role=ChangeRequestRole.DRIVER,
        requested_by_user_id="driver-1",
        reason=None,
    )
    service.decide_change_request(request["change_request_id"], approve=False, decided_by_user_id="wms-1", note=None)

    with pytest.raises(ChangeRequestAlreadyDecidedError):
        service.withdraw_change_request(request["change_request_id"], withdrawn_by_user_id="driver-1")


def test_change_request_approval_executes_a_priority_swap(tables):
    # Mirrors test_priority_swap_suggested_when_higher_priority_shipment_competes's
    # setup (SHP-HIGH outranks SHP_OCCUPANT on SLOT-D2-0800), but goes one
    # step further: files the swap as a change request (as
    # DriverChatService.auto_book_earliest_feasible_slot now does) and
    # approves it, and asserts BOTH shipments end up where the suggestion
    # said they would -- the displaced occupant moved off SLOT-D2-0800 onto
    # its own replacement slot, and the higher-priority shipment now holds
    # SLOT-D2-0800.
    tables["appointments"].append(
        {
            "appointment_id": "APT-OCC",
            "shipment_id": SHP_OCCUPANT,
            "slot_id": "SLOT-D2-0800",
            "appointment_status": "CONFIRMED",
            "booking_source": "PLANNER",
            "is_current": 1,
            "booked_at": "2026-08-01T12:00:00+05:30",
            "confirmed_at": "2026-08-01T12:05:00+05:30",
            "cancelled_at": None,
            "cancellation_reason": None,
            "replaced_appointment_id": None,
            "warehouse_confirmation_ref": None,
            "updated_at": "2026-08-01T12:05:00+05:30",
        }
    )
    tables["shipments"].append(
        {
            "shipment_id": "SHP-HIGH",
            "order_reference": "ORD-HIGH",
            "carrier_id": "CAR001",
            "driver_id": "DRV001",
            "vehicle_id": "VEH001",
            "origin_name": "Depot",
            "origin_city": "Jaipur",
            "destination_facility_id": FACILITY,
            "customer_name": "Test Customer",
            "product_category": "General",
            "load_weight_kg": 10000,
            "required_dock_type": "STANDARD",
            "temperature_control_required": 0,
            "priority_code": "HIGH",
            "planned_departure_ts": "2026-08-04T04:00:00+05:30",
            "original_eta_ts": "2026-08-04T08:00:00+05:30",
            "latest_eta_ts": None,
            "expected_unload_min": 45,
            "current_status": "PLANNED",
            "created_at": "2026-08-01T12:00:00+05:30",
            "updated_at": "2026-08-01T12:00:00+05:30",
        }
    )
    repository = DockSchedulerRepository(FakeSupabaseClient(tables))
    service = DockSchedulerService(repository)

    suggestions = service.suggest_slots("SHP-HIGH", limit=10)
    swap = next(s for s in suggestions if s.suggestion_type is SuggestionType.PRIORITY_SWAP)

    request = service.create_change_request(
        shipment_id="SHP-HIGH",
        requested_slot_id=swap.slot_id,
        requested_by_role=ChangeRequestRole.DRIVER,
        requested_by_user_id="DRV001",
        reason=swap.reason,
        displaced_shipment_id=swap.displaced_shipment_id,
        displaced_to_slot_id=swap.displaced_to_slot_id,
    )
    assert request["displaced_shipment_id"] == SHP_OCCUPANT
    assert request["displaced_to_slot_id"] == swap.displaced_to_slot_id

    decided = service.decide_change_request(
        request["change_request_id"], approve=True, decided_by_user_id="wms-staff-1", note="Priority swap approved."
    )
    assert decided["request_status"] == "APPROVED"

    high_appt = service.repository.current_appointment("SHP-HIGH")
    assert high_appt["slot_id"] == swap.slot_id

    occupant_appt = service.repository.current_appointment(SHP_OCCUPANT)
    assert occupant_appt["slot_id"] == swap.displaced_to_slot_id
    assert occupant_appt["slot_id"] != swap.slot_id


def test_list_change_requests_filters_by_status(service):
    service.create_change_request(
        shipment_id=SHP_NORMAL,
        requested_slot_id="SLOT-D1-0900",
        requested_by_role=ChangeRequestRole.TMS,
        requested_by_user_id="tms-user-1",
        reason=None,
    )
    pending = service.list_change_requests(status="PENDING")
    assert len(pending) == 1
    approved = service.list_change_requests(status="APPROVED")
    assert approved == []
