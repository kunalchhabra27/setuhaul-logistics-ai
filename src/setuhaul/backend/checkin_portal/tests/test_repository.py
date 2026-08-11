from __future__ import annotations

from setuhaul.backend._testing.fake_supabase import FakeSupabaseClient
from setuhaul.backend.checkin_portal.repository import CheckInRepository


def _repository() -> CheckInRepository:
    return CheckInRepository(FakeSupabaseClient({"facility_checkins": []}))


def test_repository_crud_round_trip() -> None:
    repo = _repository()

    repo.create_gate_checkin("CHK-1", "SHP1006", "FAC-1", "2026-08-08T10:00:00")
    assert repo.get_by_shipment("SHP1006")["arrival_status"] == "GATE_IN"

    repo.update_queue("SHP1006", "YARD_QUEUE")
    assert repo.get_by_shipment("SHP1006")["queue_status"] == "YARD_QUEUE"

    repo.mark_docked("SHP1006", "2026-08-08T11:00:00")
    assert repo.get_by_shipment("SHP1006")["arrival_status"] == "DOCKED"

    repo.mark_completed("SHP1006", "2026-08-08T12:00:00")
    assert repo.get_by_shipment("SHP1006")["arrival_status"] == "COMPLETED"


def test_get_by_shipment_returns_none_when_missing() -> None:
    repo = _repository()
    assert repo.get_by_shipment("SHP-NOPE") is None


def test_get_driver_contact_for_shipment_returns_phone() -> None:
    repo = CheckInRepository(
        FakeSupabaseClient(
            {
                "facility_checkins": [],
                "shipments": [
                    {"shipment_id": "SHP1006", "driver_id": "DRV001", "order_reference": "ORD-1"},
                ],
                "drivers": [
                    {"driver_id": "DRV001", "driver_name": "Rajesh Kumar", "phone": "+91-9000010001"},
                ],
            }
        )
    )
    contact = repo.get_driver_contact_for_shipment("SHP1006")
    assert contact == {
        "driver_id": "DRV001",
        "driver_name": "Rajesh Kumar",
        "phone": "+91-9000010001",
        "order_reference": "ORD-1",
    }


def test_get_driver_contact_for_shipment_returns_none_without_driver() -> None:
    repo = CheckInRepository(
        FakeSupabaseClient(
            {
                "facility_checkins": [],
                "shipments": [{"shipment_id": "SHP1006", "driver_id": None, "order_reference": "ORD-1"}],
                "drivers": [],
            }
        )
    )
    assert repo.get_driver_contact_for_shipment("SHP1006") is None


def test_get_driver_contact_for_shipment_returns_none_when_shipment_missing() -> None:
    repo = _repository()
    assert repo.get_driver_contact_for_shipment("SHP-NOPE") is None


def test_update_shipment_status_updates_current_status() -> None:
    repo = CheckInRepository(
        FakeSupabaseClient(
            {
                "facility_checkins": [],
                "shipments": [{"shipment_id": "SHP1006", "current_status": "ASSIGNED"}],
            }
        )
    )
    repo.update_shipment_status("SHP1006", "AT_GATE")
    assert repo.get_shipment("SHP1006")["current_status"] == "AT_GATE"


def test_list_checkins_returns_domain_mapped_records() -> None:
    repo = CheckInRepository(
        FakeSupabaseClient(
            {
                "facility_checkins": [
                    {
                        "checkin_id": "CHK-1",
                        "shipment_id": "SHP1006",
                        "facility_id": "FAC-1",
                        "gate_in_ts": "2026-08-08T10:00:00",
                        "yard_queue_enter_ts": None,
                        "dock_in_ts": None,
                        "unload_start_ts": None,
                        "unload_end_ts": None,
                        "gate_out_ts": None,
                        "arrival_state": "EARLY",
                        "queue_state": "WAITING_EARLY",
                        "queue_position": None,
                        "actual_dock_id": None,
                        "notes": None,
                        "updated_at": "2026-08-08T10:00:00",
                    }
                ]
            }
        )
    )
    record = repo.list_checkins()["SHP1006"]
    assert record["arrival_status"] == "GATE_IN"
    assert record["queue_status"] == "GATE_QUEUE"


def test_list_active_facilities_returns_only_active_rows() -> None:
    repo = CheckInRepository(
        FakeSupabaseClient(
            {
                "facility_checkins": [],
                "facilities": [
                    {"facility_id": "FAC-JAI-01", "facility_name": "Jaipur", "city": "Jaipur", "state": "Rajasthan", "active_flag": 1},
                    {"facility_id": "FAC-GGN-01", "facility_name": "Gurugram", "city": "Gurugram", "state": "Haryana", "active_flag": 1},
                    {"facility_id": "FAC-OLD-01", "facility_name": "Old", "city": "Old", "state": "Old", "active_flag": 0},
                ],
            }
        )
    )
    rows = repo.list_active_facilities()
    assert [row["facility_id"] for row in rows] == ["FAC-GGN-01", "FAC-JAI-01"]


def test_list_shipments_filters_by_destination_facility() -> None:
    repo = CheckInRepository(
        FakeSupabaseClient(
            {
                "facility_checkins": [],
                "shipments": [
                    {"shipment_id": "SHP-JAI", "destination_facility_id": "FAC-JAI-01", "original_eta_ts": "2026-08-08T10:00:00"},
                    {"shipment_id": "SHP-GGN", "destination_facility_id": "FAC-GGN-01", "original_eta_ts": "2026-08-08T11:00:00"},
                ],
            }
        )
    )

    rows = repo.list_shipments("FAC-JAI-01")

    assert [row["shipment_id"] for row in rows] == ["SHP-JAI"]
