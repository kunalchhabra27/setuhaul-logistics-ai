from __future__ import annotations

import pytest

from setuhaul.backend.driver_chat_eta.exceptions import SlotConflictError
from setuhaul.backend.driver_chat_eta.tests.conftest import SHIPMENT_ID


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
