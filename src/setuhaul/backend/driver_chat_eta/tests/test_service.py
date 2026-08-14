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


def test_auto_book_upgrades_to_a_newly_available_earlier_slot_even_when_the_existing_one_still_fits(
    service, principal, tables
):
    # Behavior change (explicitly requested): every time auto-booking runs,
    # if a slot earlier than the one currently booked is open and still
    # compatible with the driver's ETA, move the appointment there instead
    # of leaving the shipment parked on a later-than-necessary slot just
    # because the old one still technically fits. Simulates a shipment
    # already confirmed on SLOT-2 (the later of conftest's two STANDARD
    # slots) while SLOT-1 (earlier) sits open and compatible -- the
    # assistant should proactively upgrade it onto SLOT-1.
    tables["appointments"].append(
        {
            "appointment_id": "APT-PRESET",
            "shipment_id": SHIPMENT_ID,
            "slot_id": "SLOT-2",
            "appointment_status": "CONFIRMED",
            "is_current": 1,
            "booked_at": "2026-08-01T00:00:00",
            "confirmed_at": "2026-08-01T00:00:00",
        }
    )

    result = service.auto_book_earliest_feasible_slot(principal)

    assert result["status"] == "booked"
    assert result["slot_id"] == "SLOT-1"

    appointments_by_status = {
        (a["slot_id"], a["appointment_status"]) for a in tables["appointments"] if a["shipment_id"] == SHIPMENT_ID
    }
    assert ("SLOT-1", "CONFIRMED") in appointments_by_status
    assert ("SLOT-2", "CANCELLED") in appointments_by_status
    confirmed = [a for a in tables["appointments"] if a["appointment_status"] == "CONFIRMED"]
    assert len(confirmed) == 1


def test_regex_fallback_auto_books_a_slot_instead_of_just_listing_options(service, principal, tables):
    # Regression test: the regex fallback (used when GOOGLE_API_KEY isn't
    # configured, or the LLM path fails at runtime -- e.g. Google's Aug 2026
    # AQ.-key rollout breaking langchain_google_genai) used to only ever
    # compose a "here are your options, reply to hold one" message and never
    # actually book anything, because the Hold/Confirm chat buttons that
    # message depended on were removed from ChatPanel.tsx when auto-booking
    # replaced them. A driver whose turn landed on this fallback path could
    # therefore never get a booked slot out of the chatbot at all.
    # A small delay (not 45+ min) so SLOT-1 (starting 2h from now in
    # conftest's fixtures) stays inside _feasible_slots' 15-minute grace
    # window and remains the earliest compatible slot -- keeps this test's
    # assertions aligned with the other auto-book tests above.
    response = service._handle_chat_message_regex(principal, "I have a tyre issue, 5 minutes late")

    assert "Booked dock slot" in response.agent_message.message_text
    assert "Reply to hold" not in response.agent_message.message_text

    confirmed = [a for a in tables["appointments"] if a["appointment_status"] == "CONFIRMED"]
    assert len(confirmed) == 1
    assert confirmed[0]["slot_id"] == "SLOT-1"
    assert confirmed[0]["shipment_id"] == SHIPMENT_ID

    exceptions = tables["driver_exceptions"]
    assert exceptions and exceptions[-1]["exception_status"] == "RESOLVED"


def test_regex_fallback_escalates_and_says_so_when_nothing_is_compatible(service, principal, tables):
    for dock in tables["docks"]:
        dock["max_vehicle_weight_kg"] = 100

    response = service._handle_chat_message_regex(principal, "I will be 45 minutes late")

    assert "escalat" in response.agent_message.message_text.lower()
    confirmed = [a for a in tables["appointments"] if a["appointment_status"] == "CONFIRMED"]
    assert confirmed == []
    exceptions = tables["driver_exceptions"]
    assert exceptions and exceptions[-1]["exception_status"] == "ESCALATED"


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


def test_auto_book_auto_executes_a_swap_upgrade_over_the_direct_slot_when_earlier(service, principal, tables):
    # SHP001 (mutated to HIGH priority here) genuinely outranks a LOW-priority
    # occupant on SLOT-1 (the earlier of the two STANDARD slots) -- the swap
    # is now auto-approved and executed immediately (the chatbot acts with
    # WMS's delegated approval authority), not left pending. SHP001 should
    # end up on SLOT-1 (the better slot), and SHP-LOW should be moved onto
    # SLOT-2 as its replacement.
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

    assert result["status"] == "booked_via_swap"
    assert result["slot_id"] == "SLOT-1"
    assert result["displaced_shipment_id"] == "SHP-LOW"
    assert "swap_change_request_id" in result

    confirmed = [a for a in tables["appointments"] if a["shipment_id"] == SHIPMENT_ID and a["appointment_status"] == "CONFIRMED"]
    assert len(confirmed) == 1 and confirmed[0]["slot_id"] == "SLOT-1"

    # dock_slot_change_requests isn't in the base fixture's `tables` dict --
    # FakeSupabaseClient lazily creates it on first `.table(...)` access, so
    # read it back through the same client via the service layer rather than
    # reaching into `tables` directly.
    change_requests = service.dock_scheduler.list_change_requests()
    assert len(change_requests) == 1
    assert change_requests[0]["requested_slot_id"] == "SLOT-1"
    assert change_requests[0]["displaced_shipment_id"] == "SHP-LOW"
    assert change_requests[0]["request_status"] == "APPROVED"
    assert change_requests[0]["decided_by_user_id"] == "DISPATCH-ASSISTANT"

    # SHP-LOW must have actually been moved to its replacement slot, not
    # left in place -- the swap executes immediately now, no human click.
    low_appt = next(a for a in tables["appointments"] if a["shipment_id"] == "SHP-LOW" and a["appointment_status"] == "CONFIRMED")
    assert low_appt["slot_id"] == "SLOT-2"


def test_auto_book_auto_executes_a_swap_without_a_direct_slot_available(service, principal, monkeypatch, tables):
    # Isolates the "swap exists, nothing direct" branch: no fixture geometry
    # can cleanly produce "zero direct options but a valid swap replacement"
    # (a replacement slot for the displaced occupant is, by construction,
    # also a direct option for the requesting shipment -- see the extensive
    # reasoning in this test file's history), so this monkeypatches the two
    # already-independently-tested building blocks (_feasible_slots,
    # _best_priority_swap) to exercise auto_book_earliest_feasible_slot's
    # own branching logic in isolation. dock_scheduler's own create/decide
    # change request calls still run for real against the fake Supabase
    # client, so the swap's actual execution (moving SHP-LOW onto SLOT-2,
    # SHP001 onto SLOT-1) is genuinely exercised here, not mocked.
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

    assert result["status"] == "booked_via_swap"
    assert result["slot_id"] == "SLOT-1"

    confirmed = [a for a in tables["appointments"] if a["shipment_id"] == SHIPMENT_ID and a["appointment_status"] == "CONFIRMED"]
    assert len(confirmed) == 1 and confirmed[0]["slot_id"] == "SLOT-1"
    change_requests = service.dock_scheduler.list_change_requests()
    assert len(change_requests) == 1
    assert change_requests[0]["request_status"] == "APPROVED"


def test_auto_book_rebooks_when_a_later_delay_invalidates_the_existing_appointment(service, principal, tables):
    # Regression test for the reported bug: a driver with an existing
    # confirmed appointment reports a NEW, larger delay that pushes their
    # ETA past that appointment's start time -- the bot used to reply
    # "This shipment already has a confirmed dock appointment ... no need to
    # book another" regardless of whether the appointment still made sense,
    # because the old `already_booked` short-circuit never re-checked the
    # slot's window against the driver's current declared ETA. It must
    # instead move the shipment onto a later slot that does fit.
    first = service.auto_book_earliest_feasible_slot(principal)
    assert first["status"] == "booked"
    assert first["slot_id"] == "SLOT-1"  # starts at now+2h in conftest's fixtures

    # Declare a new, larger delay: original_eta_ts is now+2h, so +70 minutes
    # pushes the declared ETA to now+3h10m -- past SLOT-1's start (now+2h,
    # even with the 15-minute grace window) but still within SLOT-2's start
    # (now+3h) and end (now+4h).
    service.report_exception(principal, delay_minutes=70, note="Delayed by over an hour")

    second = service.auto_book_earliest_feasible_slot(principal)

    assert second["status"] == "booked"
    assert second["slot_id"] == "SLOT-2"

    appointments_by_status = {
        (a["slot_id"], a["appointment_status"]) for a in tables["appointments"] if a["shipment_id"] == SHIPMENT_ID
    }
    assert ("SLOT-2", "CONFIRMED") in appointments_by_status
    assert ("SLOT-1", "CANCELLED") in appointments_by_status
    confirmed = [a for a in tables["appointments"] if a["appointment_status"] == "CONFIRMED"]
    assert len(confirmed) == 1


def test_auto_book_still_short_circuits_when_a_small_delay_still_fits_the_existing_slot(service, principal, tables):
    # Guard against over-correcting the bug above: a driver who reports a
    # small delay/changes their mind slightly, where the existing booked
    # slot's window still comfortably covers the new declared ETA, must
    # still get "already booked" rather than being needlessly rebooked.
    first = service.auto_book_earliest_feasible_slot(principal)
    assert first["slot_id"] == "SLOT-1"

    service.report_exception(principal, delay_minutes=5, note="Just a few minutes behind")

    second = service.auto_book_earliest_feasible_slot(principal)

    assert second["status"] == "already_booked"
    assert second["slot_id"] == "SLOT-1"
    confirmed = [a for a in tables["appointments"] if a["appointment_status"] == "CONFIRMED"]
    assert len(confirmed) == 1
    assert confirmed[0]["slot_id"] == "SLOT-1"


def test_auto_book_escalates_instead_of_already_booked_when_a_new_leave_by_constraint_invalidates_the_booking(
    service, principal, tables
):
    # "Add a constraint" conversation type from the challenge doc: a driver
    # who already has a confirmed appointment adds a must-leave-by time that
    # the existing slot no longer satisfies, and there is no later slot to
    # move to either (a tighter leave-by can only exclude slots, never admit
    # a later-ending one the earlier slot didn't already satisfy). This must
    # escalate to a human coordinator instead of incorrectly reporting
    # "already booked" -- and must not silently cancel the driver's only
    # existing appointment without a replacement lined up.
    first = service.auto_book_earliest_feasible_slot(principal)
    assert first["slot_id"] == "SLOT-1"  # now+2h to now+3h

    must_leave_by = (datetime.utcnow() + timedelta(hours=2, minutes=30)).isoformat()
    service.report_exception(principal, must_leave_by_iso=must_leave_by, note="Must leave by 2:30 from now")

    second = service.auto_book_earliest_feasible_slot(principal)

    assert second["status"] == "escalated"
    exceptions = tables["driver_exceptions"]
    assert exceptions and exceptions[-1]["exception_status"] == "ESCALATED"
    # The stale appointment is left alone (not cancelled) since nothing was
    # found to replace it with.
    confirmed = [a for a in tables["appointments"] if a["appointment_status"] == "CONFIRMED"]
    assert len(confirmed) == 1
    assert confirmed[0]["slot_id"] == "SLOT-1"


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
