from __future__ import annotations

from setuhaul.backend._testing.fake_supabase import FakeSupabaseClient
from setuhaul.backend.dock_scheduler.models import SuggestionType
from setuhaul.backend.dock_scheduler.repository import DockSchedulerRepository
from setuhaul.backend.dock_scheduler.service import DockSchedulerService
from setuhaul.backend.dock_scheduler.tests.conftest import (
    FACILITY,
    SHP_NORMAL,
    SHP_OCCUPANT,
    SHP_REEFER,
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
    # 4 STANDARD slots seeded across D1/D2 in the fixture -- board is unranked
    # and unlimited, unlike suggest_slots(limit=...).
    assert len(board) == 3
    assert {s.dock_code for s in board} == {"D1", "D2"}
    assert all(s.availability_status == "AVAILABLE" for s in board)


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
