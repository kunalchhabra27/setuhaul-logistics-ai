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
