from __future__ import annotations

from setuhaul.backend.driver_chat_eta.tests.conftest import DOCK_STANDARD


def test_snapshot_shows_feasible_slots_without_any_reported_exception(service, principal):
    # Regression test: slot_options used to only be computed inside the
    # "driver has an active exception" branch, so a freshly assigned
    # shipment with no reported delay always showed an empty dock board
    # (matching the "No open slots right now" bug report) even though real
    # open slots existed. It must be populated as soon as a shipment with a
    # destination facility exists, exception or not.
    snapshot = service.snapshot(principal)

    assert snapshot.exception is None
    slot_ids = {opt.slot_id for opt in snapshot.slot_options}
    assert "SLOT-1" in slot_ids
    standard_slot = next(opt for opt in snapshot.slot_options if opt.dock_id == DOCK_STANDARD)
    assert standard_slot.is_compatible is True


def test_snapshot_slot_options_still_reflect_declared_eta_after_exception(service, principal, tables):
    # Once an exception exists, its declared ETA should still drive the
    # feasibility window (unchanged behavior from before this fix).
    tables["driver_exceptions"].append(
        {
            "exception_id": "EXC-1",
            "shipment_id": "SHP001",
            "driver_id": principal.user_id,
            "thread_id": "TH-1",
            "exception_type": "DELAY",
            "declared_eta_ts": "2099-01-01T00:00:00",
            "latest_acceptable_ts": None,
            "exception_status": "OPEN",
        }
    )
    snapshot = service.snapshot(principal)
    # A declared ETA far in the future should make the existing near-term
    # slots infeasible (they start before that declared ETA).
    assert all(not opt.is_compatible for opt in snapshot.slot_options)
