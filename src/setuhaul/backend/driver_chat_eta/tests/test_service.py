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


def test_feasible_slots_is_memoized_within_one_service_instance(service, principal, tables):
    # A single chat turn calls _feasible_slots multiple times (initial
    # snapshot, report_delay_or_eta_change's tool, book_next_available_dock_slot's
    # tool, the post-turn snapshot rebuild) with identical (shipment_id,
    # after, max_leave_at) whenever nothing that affects availability
    # happened in between. Count the underlying compatible_slots() calls
    # directly to prove repeated calls with the same args hit the cache
    # instead of recomputing.
    call_count = {"n": 0}
    real_compatible_slots = service.dock_scheduler.repository.compatible_slots

    def _counting_compatible_slots(shipment_id):
        call_count["n"] += 1
        return real_compatible_slots(shipment_id)

    service.dock_scheduler.repository.compatible_slots = _counting_compatible_slots

    shipment_row = tables["shipments"][0]
    first = service._feasible_slots(shipment_row=shipment_row, after=shipment_row["original_eta_ts"])
    second = service._feasible_slots(shipment_row=shipment_row, after=shipment_row["original_eta_ts"])
    third = service._feasible_slots(shipment_row=shipment_row, after=shipment_row["original_eta_ts"])

    assert call_count["n"] == 1
    assert [opt.slot_id for opt in first] == [opt.slot_id for opt in second] == [opt.slot_id for opt in third]

    # A genuinely different `after` must NOT be served from the cache --
    # proven by the underlying compatible_slots() call count going up.
    service._feasible_slots(shipment_row=shipment_row, after=(datetime.utcnow() + timedelta(hours=10)).isoformat())
    assert call_count["n"] == 2


def test_build_snapshot_uses_the_rpc_bundle_when_available(service, principal, tables):
    # When the driver_snapshot RPC succeeds, _build_snapshot must use it
    # instead of falling through to the sequential per-table calls --
    # proven here by making every sequential-path repository method raise,
    # so the test fails loudly if the sequential path is used unexpectedly.
    shipment_row = tables["shipments"][0]
    bundle = {
        "shipment": shipment_row,
        "vehicle": tables["vehicles"][0],
        "facility": tables["facilities"][0],
        "docks": [d for d in tables["docks"] if d["dock_status"] == "ACTIVE"],
        "appointment": None,
        "checkin": None,
        "exception": None,
        "chat_messages": [],
    }

    def _boom(*_args, **_kwargs):
        raise AssertionError("sequential snapshot path should not run when the RPC bundle succeeds")

    monkeypatch_targets = [
        "get_active_shipment_for_driver",
        "get_vehicle",
        "get_facility",
        "list_docks",
        "get_checkin_for_shipment",
        "get_active_exception_for_driver",
        "list_chat_messages",
    ]
    originals = {name: getattr(service.repository, name) for name in monkeypatch_targets}
    for name in monkeypatch_targets:
        setattr(service.repository, name, _boom)
    service.repository.get_driver_snapshot_bundle = lambda driver_id: bundle

    try:
        driver = service.get_my_profile(principal)
        snapshot = service._build_snapshot(principal, driver)
    finally:
        for name, fn in originals.items():
            setattr(service.repository, name, fn)

    assert snapshot.shipment is not None
    assert snapshot.shipment.shipment_id == SHIPMENT_ID
    assert snapshot.facility is not None
    assert len(snapshot.docks) == 2
    # slot_options still comes from the live dock_scheduler computation
    # (deliberately not part of the RPC bundle), so it's populated as usual.
    assert any(opt.slot_id == "SLOT-1" for opt in snapshot.slot_options)


def test_build_snapshot_falls_back_to_sequential_when_the_rpc_bundle_fails(service, principal, tables):
    # FakeSupabaseClient has no .rpc() support, so get_driver_snapshot_bundle
    # raises by default -- this is the path every other test in this file
    # already exercises implicitly. Assert it explicitly so a future change
    # to the fallback wiring itself gets caught here, not discovered as a
    # mysterious failure elsewhere.
    driver = service.get_my_profile(principal)
    snapshot = service._build_snapshot(principal, driver)

    assert snapshot.shipment is not None
    assert snapshot.shipment.shipment_id == SHIPMENT_ID
    assert snapshot.facility is not None
    assert len(snapshot.docks) == 2


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


def test_auto_book_files_a_pending_change_request_for_the_earliest_compatible_slot(service, principal, tables):
    # Behavior change (explicitly requested): the assistant never books on
    # its own -- it only files a PENDING change request for WMS to approve.
    # SLOT-1 starts before SLOT-2 in conftest's fixtures, so it must be the
    # one proposed.
    result = service.auto_book_earliest_feasible_slot(principal)

    assert result["status"] == "request_submitted"
    assert result["slot_id"] == "SLOT-1"
    assert result["via_swap"] is False

    # Nothing is actually booked yet -- it's still just a request.
    confirmed = [a for a in tables["appointments"] if a["appointment_status"] == "CONFIRMED"]
    assert confirmed == []
    held = [h for h in tables["slot_holds"] if h["hold_status"] == "HELD"]
    assert held == []

    change_requests = service.dock_scheduler.list_change_requests()
    assert len(change_requests) == 1
    assert change_requests[0]["requested_slot_id"] == "SLOT-1"
    assert change_requests[0]["shipment_id"] == SHIPMENT_ID
    assert change_requests[0]["request_status"] == "PENDING"
    assert change_requests[0].get("decided_by_user_id") is None


def test_auto_book_is_idempotent_once_a_confirmed_appointment_exists(service, principal, tables):
    # Seed a CONFIRMED appointment directly -- auto_book itself can no
    # longer produce one on its own (it only files requests), so this
    # simulates a slot WMS has already approved.
    tables["appointments"].append(
        {
            "appointment_id": "APT-PRESET",
            "shipment_id": SHIPMENT_ID,
            "slot_id": "SLOT-1",
            "appointment_status": "CONFIRMED",
            "is_current": 1,
            "booked_at": "2026-08-01T00:00:00",
            "confirmed_at": "2026-08-01T00:00:00",
        }
    )

    result = service.auto_book_earliest_feasible_slot(principal)

    assert result["status"] == "already_booked"
    assert result["slot_id"] == "SLOT-1"
    # Still exactly one confirmed appointment, and no request filed -- a
    # slot that already fits needs no proposal.
    confirmed = [a for a in tables["appointments"] if a["appointment_status"] == "CONFIRMED"]
    assert len(confirmed) == 1
    assert service.dock_scheduler.list_change_requests() == []


def test_auto_book_requests_a_newly_available_earlier_slot_even_when_the_existing_one_still_fits(
    service, principal, tables
):
    # Behavior change (explicitly requested): every time auto-booking runs,
    # if a slot earlier than the one currently booked is open and still
    # compatible with the driver's ETA, propose moving there instead of
    # leaving the shipment parked on a later-than-necessary slot just
    # because the old one still technically fits -- as a PENDING request,
    # not an automatic move. Simulates a shipment already confirmed on
    # SLOT-2 (the later of conftest's two STANDARD slots) while SLOT-1
    # (earlier) sits open and compatible.
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

    assert result["status"] == "request_submitted"
    assert result["slot_id"] == "SLOT-1"

    # The existing SLOT-2 appointment is untouched -- only WMS approving the
    # new request can move it, this call never does that itself.
    appointments_by_status = {
        (a["slot_id"], a["appointment_status"]) for a in tables["appointments"] if a["shipment_id"] == SHIPMENT_ID
    }
    assert ("SLOT-2", "CONFIRMED") in appointments_by_status
    confirmed = [a for a in tables["appointments"] if a["appointment_status"] == "CONFIRMED"]
    assert len(confirmed) == 1

    change_requests = service.dock_scheduler.list_change_requests()
    assert len(change_requests) == 1
    assert change_requests[0]["requested_slot_id"] == "SLOT-1"
    assert change_requests[0]["request_status"] == "PENDING"


def test_regex_fallback_files_a_request_instead_of_just_listing_options(service, principal, tables):
    # Regression test: the regex fallback (used when HUGGINGFACEHUB_API_TOKEN
    # isn't configured, or the LLM path fails at runtime) used to only ever
    # compose a "here are your options, reply to hold one" message and never
    # actually propose anything, because the Hold/Confirm chat buttons that
    # message depended on were removed from ChatPanel.tsx when auto-booking
    # replaced them. A driver whose turn landed on this fallback path could
    # therefore never get a slot request out of the chatbot at all.
    # A small delay (not 45+ min) so SLOT-1 (starting 2h from now in
    # conftest's fixtures) stays inside _feasible_slots' 15-minute grace
    # window and remains the earliest compatible slot -- keeps this test's
    # assertions aligned with the other auto-book tests above.
    response = service._handle_chat_message_regex(principal, "I have a tyre issue, 5 minutes late")

    assert "Requested dock slot" in response.agent_message.message_text
    assert "Reply to hold" not in response.agent_message.message_text

    # Nothing is actually booked -- only a pending request was filed.
    confirmed = [a for a in tables["appointments"] if a["appointment_status"] == "CONFIRMED"]
    assert confirmed == []
    change_requests = service.dock_scheduler.list_change_requests()
    assert len(change_requests) == 1
    assert change_requests[0]["requested_slot_id"] == "SLOT-1"
    assert change_requests[0]["request_status"] == "PENDING"

    exceptions = tables["driver_exceptions"]
    assert exceptions and exceptions[-1]["exception_status"] == "WAITING_CONFIRMATION"


def test_regex_fallback_escalates_and_says_so_when_nothing_is_compatible(service, principal, tables):
    for dock in tables["docks"]:
        dock["max_vehicle_weight_kg"] = 100

    response = service._handle_chat_message_regex(principal, "I will be 45 minutes late")

    assert "escalat" in response.agent_message.message_text.lower()
    confirmed = [a for a in tables["appointments"] if a["appointment_status"] == "CONFIRMED"]
    assert confirmed == []
    exceptions = tables["driver_exceptions"]
    assert exceptions and exceptions[-1]["exception_status"] == "ESCALATED"


def test_regex_fallback_answers_name_question_without_touching_booking_state(service, principal, tables):
    # Regression test for the bug that made the chatbot look "completely
    # down" whenever the LLM path was unavailable (e.g. HF Inference
    # Providers quota exhausted -- HTTP 402): _handle_chat_message_regex
    # used to treat EVERY message as a delay report and unconditionally
    # auto-book, so a plain factual question got the exact same
    # already-booked/escalation reply as any other message, and silently
    # created a driver_exceptions row it had no business creating.
    response = service._handle_chat_message_regex(principal, "what is my name?")

    assert "Rajesh Kumar" in response.agent_message.message_text
    assert tables["driver_exceptions"] == []
    assert tables["eta_updates"] == []


def test_regex_fallback_answers_shipment_status_question(service, principal, tables):
    response = service._handle_chat_message_regex(principal, "what is the status of my shipment?")

    assert "Planned" in response.agent_message.message_text
    assert tables["driver_exceptions"] == []


def test_regex_fallback_greeting_does_not_create_an_exception(service, principal, tables):
    response = service._handle_chat_message_regex(principal, "hi")

    assert response.agent_message.message_text
    assert tables["driver_exceptions"] == []
    # The exchange is still persisted so it shows up in chat history.
    assert any(m["message_text"] == "hi" for m in tables["chat_messages"])


def test_regex_fallback_still_auto_books_when_message_has_a_delay_signal(service, principal, tables):
    # Same message shape as the pre-existing regression tests above --
    # confirms the new intent gate doesn't accidentally swallow real delay
    # reports into the Q&A branch.
    response = service._handle_chat_message_regex(principal, "I have a tyre issue, 5 minutes late")

    assert "Requested dock slot" in response.agent_message.message_text
    exceptions = tables["driver_exceptions"]
    assert exceptions and exceptions[-1]["exception_status"] == "WAITING_CONFIRMATION"


def test_regex_fallback_booking_verb_without_delay_still_treated_as_actionable(service, principal, tables):
    # "book d1" has no delay/leave-by signal but is clearly a booking
    # request, not a factual question -- must still go through the real
    # pipeline rather than getting a generic Q&A non-answer.
    response = service._handle_chat_message_regex(principal, "please book a dock slot for me")

    exceptions = tables["driver_exceptions"]
    assert exceptions, "booking-verb message should have gone through the exception/auto-book pipeline"


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


def test_auto_book_files_a_pending_swap_request_over_the_direct_slot_when_earlier(service, principal, tables):
    # SHP001 (mutated to HIGH priority here) genuinely outranks a LOW-priority
    # occupant on SLOT-1 (the earlier of the two STANDARD slots) -- this
    # files a PENDING swap change request (SHP001 wants SLOT-1, displacing
    # SHP-LOW to SLOT-2) for WMS to approve. Nothing executes on its own:
    # SHP001 gets no appointment yet, and SHP-LOW stays exactly where it was.
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

    assert result["status"] == "request_submitted"
    assert result["slot_id"] == "SLOT-1"
    assert result["via_swap"] is True
    assert result["displaced_shipment_id"] == "SHP-LOW"
    assert "change_request_id" in result

    # Nothing executes on its own -- SHP001 has no appointment yet.
    confirmed = [a for a in tables["appointments"] if a["shipment_id"] == SHIPMENT_ID and a["appointment_status"] == "CONFIRMED"]
    assert confirmed == []

    # dock_slot_change_requests isn't in the base fixture's `tables` dict --
    # FakeSupabaseClient lazily creates it on first `.table(...)` access, so
    # read it back through the same client via the service layer rather than
    # reaching into `tables` directly.
    change_requests = service.dock_scheduler.list_change_requests()
    assert len(change_requests) == 1
    assert change_requests[0]["requested_slot_id"] == "SLOT-1"
    assert change_requests[0]["displaced_shipment_id"] == "SHP-LOW"
    assert change_requests[0]["request_status"] == "PENDING"
    assert change_requests[0].get("decided_by_user_id") is None

    # SHP-LOW must NOT have been moved -- the swap only executes once a
    # human WMS user approves the request via decide_change_request.
    low_appt = next(a for a in tables["appointments"] if a["shipment_id"] == "SHP-LOW" and a["appointment_status"] == "CONFIRMED")
    assert low_appt["slot_id"] == "SLOT-1"


def test_auto_book_files_a_pending_swap_request_without_a_direct_slot_available(service, principal, monkeypatch, tables):
    # Isolates the "swap exists, nothing direct" branch: no fixture geometry
    # can cleanly produce "zero direct options but a valid swap replacement"
    # (a replacement slot for the displaced occupant is, by construction,
    # also a direct option for the requesting shipment -- see the extensive
    # reasoning in this test file's history), so this monkeypatches the two
    # already-independently-tested building blocks (_feasible_slots,
    # _best_priority_swap) to exercise auto_book_earliest_feasible_slot's
    # own branching logic in isolation. dock_scheduler's own create_change_request
    # call still runs for real against the fake Supabase client, so filing
    # the request is genuinely exercised here, not mocked -- only its later
    # approval (a human WMS action, out of scope for this method) is not.
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

    assert result["status"] == "request_submitted"
    assert result["via_swap"] is True
    assert result["slot_id"] == "SLOT-1"

    confirmed = [a for a in tables["appointments"] if a["shipment_id"] == SHIPMENT_ID and a["appointment_status"] == "CONFIRMED"]
    assert confirmed == []
    change_requests = service.dock_scheduler.list_change_requests()
    assert len(change_requests) == 1
    assert change_requests[0]["request_status"] == "PENDING"


def test_auto_book_requests_a_new_slot_when_a_later_delay_invalidates_the_existing_appointment(service, principal, tables):
    # Regression test for the reported bug: a driver with an existing
    # confirmed appointment reports a NEW, larger delay that pushes their
    # ETA past that appointment's start time -- the bot used to reply
    # "This shipment already has a confirmed dock appointment ... no need to
    # book another" regardless of whether the appointment still made sense,
    # because the old `already_booked` short-circuit never re-checked the
    # slot's window against the driver's current declared ETA. It must
    # instead propose moving the shipment onto a later slot that does fit
    # (as a pending request -- WMS decides whether to actually move it).
    tables["appointments"].append(
        {
            "appointment_id": "APT-PRESET",
            "shipment_id": SHIPMENT_ID,
            "slot_id": "SLOT-1",  # starts at now+2h in conftest's fixtures
            "appointment_status": "CONFIRMED",
            "is_current": 1,
            "booked_at": "2026-08-01T00:00:00",
            "confirmed_at": "2026-08-01T00:00:00",
        }
    )

    # Declare a new, larger delay: original_eta_ts is now+2h, so +70 minutes
    # pushes the declared ETA to now+3h10m -- past SLOT-1's start (now+2h,
    # even with the 15-minute grace window) but still within SLOT-2's start
    # (now+3h) and end (now+4h).
    service.report_exception(principal, delay_minutes=70, note="Delayed by over an hour")

    result = service.auto_book_earliest_feasible_slot(principal)

    assert result["status"] == "request_submitted"
    assert result["slot_id"] == "SLOT-2"

    # The stale SLOT-1 appointment is left alone -- only WMS approving the
    # new request can move it.
    appointments_by_status = {
        (a["slot_id"], a["appointment_status"]) for a in tables["appointments"] if a["shipment_id"] == SHIPMENT_ID
    }
    assert ("SLOT-1", "CONFIRMED") in appointments_by_status
    confirmed = [a for a in tables["appointments"] if a["appointment_status"] == "CONFIRMED"]
    assert len(confirmed) == 1

    change_requests = service.dock_scheduler.list_change_requests()
    assert len(change_requests) == 1
    assert change_requests[0]["requested_slot_id"] == "SLOT-2"
    assert change_requests[0]["request_status"] == "PENDING"


def test_auto_book_still_short_circuits_when_a_small_delay_still_fits_the_existing_slot(service, principal, tables):
    # Guard against over-correcting the bug above: a driver who reports a
    # small delay/changes their mind slightly, where the existing booked
    # slot's window still comfortably covers the new declared ETA, must
    # still get "already booked" rather than getting a needless new request.
    tables["appointments"].append(
        {
            "appointment_id": "APT-PRESET",
            "shipment_id": SHIPMENT_ID,
            "slot_id": "SLOT-1",
            "appointment_status": "CONFIRMED",
            "is_current": 1,
            "booked_at": "2026-08-01T00:00:00",
            "confirmed_at": "2026-08-01T00:00:00",
        }
    )

    service.report_exception(principal, delay_minutes=5, note="Just a few minutes behind")

    result = service.auto_book_earliest_feasible_slot(principal)

    assert result["status"] == "already_booked"
    assert result["slot_id"] == "SLOT-1"
    confirmed = [a for a in tables["appointments"] if a["appointment_status"] == "CONFIRMED"]
    assert len(confirmed) == 1
    assert confirmed[0]["slot_id"] == "SLOT-1"
    assert service.dock_scheduler.list_change_requests() == []


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
    tables["appointments"].append(
        {
            "appointment_id": "APT-PRESET",
            "shipment_id": SHIPMENT_ID,
            "slot_id": "SLOT-1",  # now+2h to now+3h
            "appointment_status": "CONFIRMED",
            "is_current": 1,
            "booked_at": "2026-08-01T00:00:00",
            "confirmed_at": "2026-08-01T00:00:00",
        }
    )

    must_leave_by = (datetime.utcnow() + timedelta(hours=2, minutes=30)).isoformat()
    service.report_exception(principal, must_leave_by_iso=must_leave_by, note="Must leave by 2:30 from now")

    result = service.auto_book_earliest_feasible_slot(principal)

    assert result["status"] == "escalated"
    exceptions = tables["driver_exceptions"]
    assert exceptions and exceptions[-1]["exception_status"] == "ESCALATED"
    # The stale appointment is left alone (not cancelled) since nothing was
    # found to replace it with.
    confirmed = [a for a in tables["appointments"] if a["appointment_status"] == "CONFIRMED"]
    assert len(confirmed) == 1
    assert confirmed[0]["slot_id"] == "SLOT-1"
    assert service.dock_scheduler.list_change_requests() == []


def test_auto_book_refuses_once_the_shipment_has_gated_in(service, principal, tables):
    # Once the driver has physically checked in at the facility gate, the
    # chatbot must not file (or even evaluate) a new dock-slot change
    # request -- any further change has to go through gate/WMS staff on
    # site. This must short-circuit before any feasibility computation or
    # change-request filing happens.
    tables["facility_checkins"].append(
        {
            "checkin_id": "CHK-1",
            "shipment_id": SHIPMENT_ID,
            "facility_id": FACILITY,
            "gate_in_ts": "2026-08-01T00:00:00",
            "queue_state": "WAITING_EARLY",
        }
    )

    result = service.auto_book_earliest_feasible_slot(principal)

    assert result["status"] == "gated_in"
    assert service.dock_scheduler.list_change_requests() == []


def test_get_latest_change_request_status_reports_nothing_filed_yet(service, principal):
    result = service.get_latest_change_request_status(principal)
    assert result == {
        "has_request": False,
        "message": "No dock slot request has been filed for this shipment yet.",
    }


def test_get_latest_change_request_status_reports_pending_then_approved(service, principal, tables):
    booking = service.auto_book_earliest_feasible_slot(principal)
    assert booking["status"] == "request_submitted"

    pending = service.get_latest_change_request_status(principal)
    assert pending["has_request"] is True
    assert pending["status"] == "PENDING"
    assert pending["change_request_id"] == booking["change_request_id"]

    service.dock_scheduler.decide_change_request(
        booking["change_request_id"], approve=True, decided_by_user_id="WMS-1", note="Looks good"
    )

    decided = service.get_latest_change_request_status(principal)
    assert decided["status"] == "APPROVED"
    assert decided["decision_note"] == "Looks good"


def test_update_checkin_does_not_advance_current_status_before_staff_approval(service, principal, tables):
    # Task #84's design: a driver's own "I'm at the gate" claim is
    # unverified until check-in staff approve it (staff_approved_flag) --
    # see CheckInService.approve_gate_checkin's docstring. current_status
    # must stay untouched by the chatbot's own checkin flow until then.
    from setuhaul.backend.driver_chat_eta.models import ArrivalUpdateChoice, CheckinUpdateRequest

    service.update_checkin(principal, CheckinUpdateRequest(arrival_status=ArrivalUpdateChoice("arrived_gate")))
    shipment = next(s for s in tables["shipments"] if s["shipment_id"] == SHIPMENT_ID)
    assert shipment["current_status"] == "PLANNED"  # unchanged from the fixture default

    service.update_checkin(principal, CheckinUpdateRequest(arrival_status=ArrivalUpdateChoice("waiting_yard")))
    shipment = next(s for s in tables["shipments"] if s["shipment_id"] == SHIPMENT_ID)
    assert shipment["current_status"] == "PLANNED"


def test_update_checkin_advances_current_status_once_staff_approve_gate_checkin(service, principal, tables):
    from setuhaul.backend.driver_chat_eta.models import ArrivalUpdateChoice, CheckinUpdateRequest

    service.update_checkin(principal, CheckinUpdateRequest(arrival_status=ArrivalUpdateChoice("arrived_gate")))
    checkin_row = next(c for c in tables["facility_checkins"] if c["shipment_id"] == SHIPMENT_ID)
    checkin_row["staff_approved_flag"] = 1  # simulate CheckInService.approve_gate_checkin

    service.update_checkin(principal, CheckinUpdateRequest(arrival_status=ArrivalUpdateChoice("waiting_yard")))
    shipment = next(s for s in tables["shipments"] if s["shipment_id"] == SHIPMENT_ID)
    assert shipment["current_status"] == "WAITING"

    service.update_checkin(principal, CheckinUpdateRequest(arrival_status=ArrivalUpdateChoice("docked")))
    shipment = next(s for s in tables["shipments"] if s["shipment_id"] == SHIPMENT_ID)
    assert shipment["current_status"] == "IN_DOCK"


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
