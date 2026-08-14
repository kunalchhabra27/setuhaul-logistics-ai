from __future__ import annotations

import pytest

from datetime import datetime, timedelta

from setuhaul.backend.dock_scheduler.models import SlotLifecycleStage, SlotSuggestion, SuggestionType
from setuhaul.backend.dock_scheduler.repository import _FUTURE_SLOTS_LAST_CHECKED
from setuhaul.backend.driver_chat_eta.exceptions import DriverChatError, PersistenceError, SlotConflictError
from setuhaul.backend.driver_chat_eta.tests.conftest import FACILITY, SHIPMENT_ID


def test_handle_chat_message_propagates_driver_chat_errors_from_the_llm_path(service, principal, monkeypatch):
    # Regression test: service.py's handle_chat_message does
    #   try: ... return llm_agent.run_chat_turn(...)
    #   except DriverChatError: raise
    #   except Exception: <fall back to the regex parser>
    # `DriverChatError` was never imported into this module, so as soon as
    # the LLM path raised one (e.g. PersistenceError from a real RLS
    # failure writing to chat_threads), evaluating `except DriverChatError`
    # itself raised `NameError: name 'DriverChatError' is not defined`
    # instead of doing either intended thing -- the driver got a raw 500
    # with no fallback, on every single message, which is what made the
    # chatbot look completely dead.
    from setuhaul.backend.driver_chat_eta.llm import agent as llm_agent

    monkeypatch.setattr(llm_agent, "is_configured", lambda: True)

    def _boom(*_args, **_kwargs):
        raise PersistenceError("new row violates row-level security policy for table \"chat_threads\"")

    monkeypatch.setattr(llm_agent, "run_chat_turn", _boom)

    with pytest.raises(DriverChatError):
        service.handle_chat_message(principal, "hi")


def test_get_current_feasible_slots_returns_open_compatible_slot(service, principal):
    options = service.get_current_feasible_slots(principal)
    slot_ids = {opt.slot_id for opt in options}
    # SLOT-1/SLOT-2 are STANDARD docks matching the shipment's required_dock_type;
    # SLOT-REEFER is a REEFER dock and must not appear for a STANDARD shipment.
    assert "SLOT-1" in slot_ids
    assert "SLOT-REEFER" not in slot_ids
    assert all(opt.is_compatible for opt in options if opt.slot_id in {"SLOT-1", "SLOT-2"})


def test_hold_slot_creates_a_hold_via_dock_scheduler(service, principal, tables):
    result = service.hold_slot(principal, "SLOT-1")
    assert result.slot.slot_id == "SLOT-1"
    holds = [h for h in tables["slot_holds"] if h["hold_status"] == "HELD"]
    assert len(holds) == 1
    assert holds[0]["shipment_id"] == SHIPMENT_ID
    assert holds[0]["slot_id"] == "SLOT-1"


def test_hold_slot_releases_previous_hold_on_a_different_slot(service, principal, tables):
    service.hold_slot(principal, "SLOT-1")
    service.hold_slot(principal, "SLOT-2")

    held = [h for h in tables["slot_holds"] if h["hold_status"] == "HELD"]
    released = [h for h in tables["slot_holds"] if h["hold_status"] == "RELEASED"]
    assert len(held) == 1
    assert held[0]["slot_id"] == "SLOT-2"
    assert len(released) == 1
    assert released[0]["slot_id"] == "SLOT-1"


def test_hold_slot_rejects_incompatible_dock_type(service, principal):
    # SHP001 requires a STANDARD dock; SLOT-REEFER sits on a REEFER dock, so
    # dock_scheduler.compatible_slots() should exclude it and hold_slot must
    # reject it -- this re-validation is new versus the old chatbot-only
    # implementation, which never re-checked compatibility at hold time.
    with pytest.raises(SlotConflictError):
        service.hold_slot(principal, "SLOT-REEFER")


def test_confirm_slot_requires_an_active_hold(service, principal):
    with pytest.raises(SlotConflictError, match="not currently held"):
        service.confirm_slot(principal, "SLOT-1")


def test_confirm_slot_books_a_confirmed_appointment(service, principal, tables):
    service.hold_slot(principal, "SLOT-1")
    result = service.confirm_slot(principal, "SLOT-1")

    assert result.appointment.slot_id == "SLOT-1"
    assert result.appointment.appointment_status.value == "CONFIRMED"

    confirmed = [a for a in tables["appointments"] if a["appointment_status"] == "CONFIRMED"]
    assert len(confirmed) == 1
    assert confirmed[0]["shipment_id"] == SHIPMENT_ID
    assert confirmed[0]["slot_id"] == "SLOT-1"

    converted_holds = [h for h in tables["slot_holds"] if h["hold_status"] == "CONVERTED"]
    assert len(converted_holds) == 1


def test_auto_book_books_the_earliest_compatible_slot_without_a_manual_hold_confirm_step(service, principal, tables):
    # Replaces the old "list options -> driver holds -> driver confirms"
    # flow: the agent decides and books in one call. SLOT-1 starts before
    # SLOT-2 in conftest's fixtures, so it must be the one picked.
    result = service.auto_book_earliest_feasible_slot(principal)

    assert result["status"] == "booked"
    assert result["slot_id"] == "SLOT-1"

    confirmed = [a for a in tables["appointments"] if a["appointment_status"] == "CONFIRMED"]
    assert len(confirmed) == 1
    assert confirmed[0]["slot_id"] == "SLOT-1"
    assert confirmed[0]["shipment_id"] == SHIPMENT_ID

    # No leftover HELD row -- it must have been converted, not left dangling
    # the way a driver abandoning the old manual flow midway could leave one.
    held = [h for h in tables["slot_holds"] if h["hold_status"] == "HELD"]
    assert held == []


def test_auto_book_is_idempotent_once_a_confirmed_appointment_exists(service, principal, tables):
    service.auto_book_earliest_feasible_slot(principal)
    result = service.auto_book_earliest_feasible_slot(principal)

    assert result["status"] == "already_booked"
    assert result["slot_id"] == "SLOT-1"
    # Still exactly one confirmed appointment -- calling this twice (e.g. the
    # driver sends another message) must not create a second booking.
    confirmed = [a for a in tables["appointments"] if a["appointment_status"] == "CONFIRMED"]
    assert len(confirmed) == 1


def test_auto_book_escalates_when_nothing_is_compatible(service, principal, tables):
    # Make every slot incompatible by shrinking every dock's capacity below
    # the shipment's load weight -- mirrors the real SHP1027-style data
    # inconsistency this session flagged separately. Reports an exception
    # first (as the real flow does per the system prompt's rule 1/2) so
    # there's an active driver_exceptions/chat_threads pair for escalate()
    # to actually update.
    for dock in tables["docks"]:
        dock["max_vehicle_weight_kg"] = 100
    service.report_exception(principal, delay_minutes=30, note="Tyre delay")

    result = service.auto_book_earliest_feasible_slot(principal)

    assert result["status"] == "escalated"
    exceptions = tables["driver_exceptions"]
    assert exceptions and exceptions[-1]["exception_status"] == "ESCALATED"
    threads = tables["chat_threads"]
    assert threads and threads[-1]["thread_status"] == "ESCALATED"
    confirmed = [a for a in tables["appointments"] if a["appointment_status"] == "CONFIRMED"]
    assert confirmed == []


def test_auto_book_prefers_a_pending_swap_upgrade_over_the_direct_slot_when_earlier(service, principal, tables):
    # SHP001 (mutated to HIGH priority here) genuinely outranks a LOW-priority
    # occupant on SLOT-1 (the earlier of the two STANDARD slots) -- the swap
    # should be *requested* (not executed), while SLOT-2 (still directly
    # AVAILABLE) is booked immediately as a guaranteed fallback so the driver
    # is never left unbooked while the swap sits pending WMS approval.
    #
    # Pin the "already backfilled recently" cache for this facility so
    # _feasible_slots's call to ensure_future_slots_for_shipment is a no-op:
    # without this, it would insert a fresh batch of AVAILABLE slots
    # starting from real wall-clock "now" (earlier than this fixture's
    # frozen SLOT-1/SLOT-2 times), which would legitimately become the new
    # best_direct and defeat the scenario this test is actually checking --
    # not a bug, just not what this test is about.
    _FUTURE_SLOTS_LAST_CHECKED[FACILITY] = datetime.utcnow()
    tables["shipments"][0]["priority_code"] = "HIGH"
    tables["shipments"].append(
        {
            "shipment_id": "SHP-LOW",
            "order_reference": "ORD-LOW",
            "driver_id": "DRV-LOW",
            "vehicle_id": "VEH001",
            "origin_name": "Depot",
            "origin_city": "Jaipur",
            "destination_facility_id": FACILITY,
            "product_category": "General",
            "load_weight_kg": 5000,
            "required_dock_type": "STANDARD",
            "temperature_control_required": 0,
            "priority_code": "LOW",
            "original_eta_ts": tables["appointment_slots"][0]["slot_start_ts"],
            "latest_eta_ts": None,
            "expected_unload_min": 45,
            "current_status": "PLANNED",
        }
    )
    tables["appointments"].append(
        {
            "appointment_id": "APT-LOW",
            "shipment_id": "SHP-LOW",
            "slot_id": "SLOT-1",
            "appointment_status": "CONFIRMED",
            "is_current": 1,
            "booked_at": "2026-08-01T00:00:00",
            "confirmed_at": "2026-08-01T00:00:00",
        }
    )

    result = service.auto_book_earliest_feasible_slot(principal)

    assert result["status"] == "booked_with_pending_upgrade"
    assert result["slot_id"] == "SLOT-2"
    assert result["swap_slot_id"] == "SLOT-1"
    assert "swap_change_request_id" in result

    confirmed = [a for a in tables["appointments"] if a["shipment_id"] == SHIPMENT_ID and a["appointment_status"] == "CONFIRMED"]
    assert len(confirmed) == 1 and confirmed[0]["slot_id"] == "SLOT-2"

    # dock_slot_change_requests isn't in the base fixture's `tables` dict --
    # FakeSupabaseClient lazily creates it on first `.table(...)` access, so
    # read it back through the same client via the service layer rather than
    # reaching into `tables` directly.
    change_requests = service.dock_scheduler.list_change_requests()
    assert len(change_requests) == 1
    assert change_requests[0]["requested_slot_id"] == "SLOT-1"
    assert change_requests[0]["displaced_shipment_id"] == "SHP-LOW"
    assert change_requests[0]["request_status"] == "PENDING"

    # SHP-LOW's own appointment must be untouched -- the swap is only a
    # request until a WMS coordinator approves it, never executed here.
    low_appt = next(a for a in tables["appointments"] if a["shipment_id"] == "SHP-LOW")
    assert low_appt["slot_id"] == "SLOT-1"
    assert low_appt["appointment_status"] == "CONFIRMED"


def test_auto_book_requests_a_swap_without_booking_when_nothing_is_directly_available(service, principal, monkeypatch):
    # Isolates the "swap exists, nothing direct" branch: no fixture geometry
    # can cleanly produce "zero direct options but a valid swap replacement"
    # (a replacement slot for the displaced occupant is, by construction,
    # also a direct option for the requesting shipment -- see the extensive
    # reasoning in this test file's history), so this monkeypatches the two
    # already-independently-tested building blocks (_feasible_slots,
    # _best_priority_swap) to exercise auto_book_earliest_feasible_slot's
    # own branching logic in isolation.
    monkeypatch.setattr(service, "_feasible_slots", lambda **kwargs: [])

    swap = SlotSuggestion(
        rank=0,
        suggestion_type=SuggestionType.PRIORITY_SWAP,
        slot_id="SLOT-1",
        dock_code="D1",
        start=datetime.utcnow() + timedelta(hours=1),
        end=datetime.utcnow() + timedelta(hours=2),
        reason="Higher-priority shipment may use this slot.",
        lifecycle_stage=SlotLifecycleStage.PROPOSED,
        displaced_shipment_id="SHP-LOW",
        displaced_to_slot_id="SLOT-2",
    )
    monkeypatch.setattr(service, "_best_priority_swap", lambda **kwargs: swap)

    result = service.auto_book_earliest_feasible_slot(principal)

    assert result["status"] == "swap_requested"
    assert result["slot_id"] == "SLOT-1"
    assert "swap_change_request_id" in result

    # Nothing was booked for this shipment -- it's still pending approval.
    assert service.dock_scheduler.repository.current_appointment(SHIPMENT_ID) is None
    change_requests = service.dock_scheduler.list_change_requests()
    assert len(change_requests) == 1
    assert change_requests[0]["request_status"] == "PENDING"


def test_confirm_slot_for_a_slot_not_held_by_this_driver_is_rejected(service, principal, tables):
    # Someone else holds SLOT-2 -- confirming it must fail even though a
    # slot_holds row exists, because it isn't this shipment's hold.
    tables["slot_holds"].append(
        {
            "hold_id": "HLD-OTHER",
            "slot_id": "SLOT-2",
            "shipment_id": "SHP-OTHER",
            "hold_status": "HELD",
            "held_at": "2026-08-01T00:00:00",
            "expires_at": "2099-01-01T00:00:00",
        }
    )
    service.hold_slot(principal, "SLOT-1")
    with pytest.raises(SlotConflictError):
        service.confirm_slot(principal, "SLOT-2")
