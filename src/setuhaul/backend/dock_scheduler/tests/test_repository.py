"""Tests for DockSchedulerRepository.ensure_future_slots().

Context: the seed data (data/setuhaul_schema_and_seed.sql) only ever
populated a single calendar day of appointment_slots per dock (2026-08-04).
compatible_slots() has no date filter, so once that seeded day is in the
past, every slot on every board -- WMS, TMS's slot-change picker, and the
driver's dock board -- looks permanently unavailable. ensure_future_slots()
is the fix: it clones each dock's most recent OPEN day forward until a
rolling horizon is covered, called from board/feasibility read paths.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from setuhaul.backend.dock_scheduler.repository import DockSchedulerRepository, parse_ts
from setuhaul.backend.dock_scheduler.tests.conftest import (
    DOCK_REEFER,
    DOCK_STANDARD_1,
    DOCK_STANDARD_2,
    FACILITY,
    SHP_NORMAL,
    seed_ts,
)


def test_ensure_future_slots_backfills_when_seeded_data_is_in_the_past(repository: DockSchedulerRepository):
    inserted = repository.ensure_future_slots(FACILITY)
    assert inserted > 0

    now = datetime.now(timezone.utc).astimezone()
    horizon = now + timedelta(days=repository.FUTURE_SLOT_HORIZON_DAYS)
    all_slots = repository._select("appointment_slots", facility_id=FACILITY)

    future_open = [
        row for row in all_slots if row["slot_status"] == "OPEN" and parse_ts(row["slot_start_ts"]) >= now
    ]
    assert future_open, "expected at least one newly backfilled OPEN slot dated in the future"

    # Every ACTIVE dock at the facility should have gained coverage up to
    # (approximately) the horizon -- allow one day of slack for the
    # day-boundary rounding in the backfill loop.
    for dock_id in (DOCK_STANDARD_1, DOCK_STANDARD_2, DOCK_REEFER):
        dock_rows = [row for row in all_slots if row["dock_id"] == dock_id]
        latest = max(parse_ts(row["slot_start_ts"]) for row in dock_rows)
        assert latest >= horizon - timedelta(days=1)


def test_ensure_future_slots_is_idempotent(repository: DockSchedulerRepository):
    first = repository.ensure_future_slots(FACILITY)
    assert first > 0

    second = repository.ensure_future_slots(FACILITY)
    assert second == 0, "a facility already topped up to the horizon should need no further inserts"


def test_ensure_future_slots_only_clones_open_slots_forward(repository: DockSchedulerRepository, tables: dict):
    # D1 had two OPEN slots (08:00, 09:00) on the seeded day -- add a
    # BLOCKED one at 10:00 and confirm it's never replicated onto any of
    # the newly generated future days.
    tables["appointment_slots"].append(
        {
            "slot_id": "SLOT-D1-BLOCKED",
            "facility_id": FACILITY,
            "dock_id": DOCK_STANDARD_1,
            "slot_start_ts": seed_ts(10),
            "slot_end_ts": seed_ts(11),
            "slot_status": "BLOCKED",
            "block_reason": "Maintenance",
            "created_at": seed_ts(12, day_offset=-3),
        }
    )

    repository.ensure_future_slots(FACILITY)

    seeded_ids = {"SLOT-D1-0800", "SLOT-D1-0900", "SLOT-D1-BLOCKED"}
    generated = [
        row
        for row in repository._select("appointment_slots", facility_id=FACILITY, dock_id=DOCK_STANDARD_1)
        if row["slot_id"] not in seeded_ids
    ]
    assert generated, "expected new backfilled slots for D1"
    assert all(row["slot_status"] == "OPEN" for row in generated)
    assert not any(row["slot_start_ts"].endswith("T10:00:00+05:30") for row in generated)


def test_ensure_future_slots_does_not_clone_slots_outside_operating_hours(
    repository: DockSchedulerRepository, tables: dict
):
    # The facility fixture closes at 22:00. A 22:00-23:00 template slot is
    # outside that window and must never get cloned forward onto future
    # days -- otherwise every backfilled day perpetuates a slot WMS/TMS/
    # drivers could book past the facility's own declared close_time.
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

    repository.ensure_future_slots(FACILITY)

    all_d1_slots = repository._select("appointment_slots", facility_id=FACILITY, dock_id=DOCK_STANDARD_1)
    # The original seeded 22:00 row is untouched (backfill never deletes
    # existing rows) -- what must NOT happen is that time being cloned
    # onto any newly generated future day.
    generated_at_2200 = [
        row
        for row in all_d1_slots
        if row["slot_id"] != "SLOT-D1-2200" and row["slot_start_ts"].endswith("T22:00:00+05:30")
    ]
    assert not generated_at_2200


def test_ensure_future_slots_for_shipment_resolves_facility_from_shipment(repository: DockSchedulerRepository):
    inserted = repository.ensure_future_slots_for_shipment(SHP_NORMAL)
    assert inserted > 0


def test_ensure_future_slots_for_unknown_facility_is_a_no_op(repository: DockSchedulerRepository):
    assert repository.ensure_future_slots("FAC-DOES-NOT-EXIST") == 0


def test_compatible_slots_excludes_rows_far_in_the_past(repository: DockSchedulerRepository, tables: dict):
    # Regression test: compatible_slots() used to fetch every appointment_slots
    # row for the facility's docks with no date bound at all. Because
    # ensure_future_slots() only ever inserts rows and never prunes old
    # ones, that unbounded read grew every day real time passed, and
    # _slot_rows_with_availability() turned the full row set into a single
    # `appointments`/`slot_holds` .in_("slot_id", [...]) filter -- once that
    # list reached hundreds of ids the resulting request URL failed
    # outright, which is what was crashing the driver chatbot with 500s.
    # A slot from a year ago must be excluded; today's still-relevant
    # seeded slots (see conftest's TODAY-relative fixtures) must remain.
    tables["appointment_slots"].append(
        {
            "slot_id": "SLOT-D1-ANCIENT",
            "facility_id": FACILITY,
            "dock_id": DOCK_STANDARD_1,
            "slot_start_ts": "2025-08-04T08:00:00+05:30",
            "slot_end_ts": "2025-08-04T09:00:00+05:30",
            "slot_status": "OPEN",
            "block_reason": None,
            "created_at": "2025-08-01T12:00:00+05:30",
        }
    )

    compatible = repository.compatible_slots(SHP_NORMAL)
    ids = {row["slot_id"] for row in compatible}
    assert "SLOT-D1-ANCIENT" not in ids
    assert "SLOT-D1-0800" in ids or "SLOT-D1-0900" in ids
