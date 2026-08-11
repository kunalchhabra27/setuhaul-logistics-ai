from __future__ import annotations

from datetime import datetime

import pytest

from setuhaul.backend._testing.fake_supabase import FakeSupabaseClient
from setuhaul.backend.checkin_portal.exceptions import InvalidCheckInTransition
from setuhaul.backend.checkin_portal.models import (
    CompleteRequest,
    DockInRequest,
    GateCheckInRequest,
    QueueStatus,
    QueueUpdateRequest,
)
from setuhaul.backend.checkin_portal.repository import CheckInRepository
from setuhaul.backend.checkin_portal.service import CheckInService

DEFAULT_SHIPMENT = {
    "shipment_id": "SHP1006",
    "order_reference": "ORD-1",
    "carrier_id": "CAR001",
    "driver_id": "DRV001",
    "vehicle_id": "VEH001",
    "origin_name": "Jaipur Depot",
    "origin_city": "Jaipur",
    "destination_facility_id": "FAC-1",
    "customer_name": "Retail Hub",
    "product_category": "General",
    "load_weight_kg": 1000,
    "required_dock_type": "STANDARD",
    "temperature_control_required": 0,
    "priority_code": "NORMAL",
    "planned_departure_ts": "2026-08-08T08:00:00",
    "original_eta_ts": "2026-08-08T10:00:00",
    "latest_eta_ts": None,
    "expected_unload_min": 45,
    "current_status": "ASSIGNED",
    "created_at": "2026-08-08T07:00:00",
    "updated_at": "2026-08-08T07:00:00",
}


def _service(extra_tables: dict | None = None) -> CheckInService:
    tables = {
        "facility_checkins": [],
        "shipments": [dict(DEFAULT_SHIPMENT)],
        "drivers": [{"driver_id": "DRV001", "driver_name": "Rajesh Kumar", "phone": "+91-9000010001"}],
        "vehicles": [{"vehicle_id": "VEH001", "registration_number": "MH02LD8342"}],
        "facilities": [{"facility_id": "FAC-1", "facility_name": "Jaipur DC", "city": "Jaipur"}],
    }
    tables.update(extra_tables or {})
    return CheckInService(CheckInRepository(FakeSupabaseClient(tables)))


def _gate_request(shipment_id: str = "SHP1006") -> GateCheckInRequest:
    return GateCheckInRequest(
        shipment_id=shipment_id,
        facility_id="FAC-1",
        gate_in_at=datetime.fromisoformat("2026-08-08T10:00:00"),
    )


def test_gate_checkin_success() -> None:
    service = _service()
    record = service.gate_check_in(_gate_request())
    assert record["arrival_status"] == "GATE_IN"
    assert record["queue_status"] == "GATE_QUEUE"


def test_duplicate_gate_checkin() -> None:
    service = _service()
    service.gate_check_in(_gate_request())
    with pytest.raises(InvalidCheckInTransition, match="already checked in"):
        service.gate_check_in(_gate_request())


def test_queue_before_gate() -> None:
    service = _service()
    with pytest.raises(InvalidCheckInTransition, match="Truck must check in first"):
        service.update_queue(
            QueueUpdateRequest(shipment_id="SHP1006", queue_status=QueueStatus.YARD_QUEUE)
        )


def test_queue_success() -> None:
    service = _service()
    service.gate_check_in(_gate_request())
    record = service.update_queue(
        QueueUpdateRequest(shipment_id="SHP1006", queue_status=QueueStatus.YARD_QUEUE)
    )
    assert record["arrival_status"] == "WAITING"
    assert record["queue_status"] == "YARD_QUEUE"
    assert service.repository.get_shipment("SHP1006")["current_status"] == "WAITING"


def test_dock_before_checkin() -> None:
    service = _service()
    with pytest.raises(InvalidCheckInTransition, match="Truck has not checked in"):
        service.mark_docked(
            DockInRequest(
                shipment_id="SHP1006",
                dock_in_at=datetime.fromisoformat("2026-08-08T11:00:00"),
            )
        )


def test_dock_success() -> None:
    service = _service()
    service.gate_check_in(_gate_request())
    service.update_queue(
        QueueUpdateRequest(shipment_id="SHP1006", queue_status=QueueStatus.YARD_QUEUE)
    )
    record = service.mark_docked(
        DockInRequest(
            shipment_id="SHP1006",
            dock_in_at=datetime.fromisoformat("2026-08-08T11:00:00"),
        )
    )
    assert record["arrival_status"] == "DOCKED"
    assert record["dock_in_at"] == "2026-08-08T11:00:00"
    assert service.repository.get_shipment("SHP1006")["current_status"] == "IN_DOCK"


def test_complete_before_dock() -> None:
    service = _service()
    service.gate_check_in(_gate_request())
    with pytest.raises(InvalidCheckInTransition, match="Invalid transition"):
        service.complete(
            CompleteRequest(
                shipment_id="SHP1006",
                completed_at=datetime.fromisoformat("2026-08-08T12:00:00"),
            )
        )


def test_complete_success() -> None:
    service = _service()
    service.gate_check_in(_gate_request())
    service.mark_docked(
        DockInRequest(
            shipment_id="SHP1006",
            dock_in_at=datetime.fromisoformat("2026-08-08T11:00:00"),
        )
    )
    record = service.complete(
        CompleteRequest(
            shipment_id="SHP1006",
            completed_at=datetime.fromisoformat("2026-08-08T12:00:00"),
        )
    )
    assert record["arrival_status"] == "COMPLETED"
    assert record["completed_at"] == "2026-08-08T12:00:00"
    assert service.repository.get_shipment("SHP1006")["current_status"] == "COMPLETED"


def test_completed_cannot_return_to_queue() -> None:
    service = _service()
    service.gate_check_in(_gate_request())
    service.mark_docked(
        DockInRequest(
            shipment_id="SHP1006",
            dock_in_at=datetime.fromisoformat("2026-08-08T11:00:00"),
        )
    )
    service.complete(
        CompleteRequest(
            shipment_id="SHP1006",
            completed_at=datetime.fromisoformat("2026-08-08T12:00:00"),
        )
    )
    with pytest.raises(InvalidCheckInTransition, match="Invalid transition"):
        service.update_queue(
            QueueUpdateRequest(shipment_id="SHP1006", queue_status=QueueStatus.GATE_QUEUE)
        )


def test_gate_checkin_sends_sms_to_assigned_driver(monkeypatch) -> None:
    import setuhaul.backend.checkin_portal.service as checkin_service_module

    captured: dict = {}
    monkeypatch.setattr(
        checkin_service_module,
        "send_sms",
        lambda to, body, **_kwargs: captured.update(to=to, body=body) or "SM_FAKE",
    )

    service = _service()
    service.gate_check_in(_gate_request())

    assert captured["to"] == "+91-9000010001"
    assert "ORD-1" in captured["body"]


def test_gate_checkin_succeeds_even_if_sms_lookup_fails(monkeypatch) -> None:
    import setuhaul.backend.checkin_portal.service as checkin_service_module

    def _boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(checkin_service_module, "send_sms", _boom)

    service = _service()
    # Must not raise even though the SMS lookup/send blows up.
    record = service.gate_check_in(_gate_request())
    assert record["arrival_status"] == "GATE_IN"


def test_gate_checkin_without_driver_skips_sms_silently(monkeypatch) -> None:
    import setuhaul.backend.checkin_portal.service as checkin_service_module

    calls = []
    monkeypatch.setattr(checkin_service_module, "send_sms", lambda *a, **k: calls.append((a, k)))

    service = _service({"shipments": [{"shipment_id": "SHP1006", "destination_facility_id": "FAC-1", "current_status": "ASSIGNED", "driver_id": None}]})
    service.gate_check_in(_gate_request())

    assert calls == []


def test_gate_checkin_rejects_unknown_shipment() -> None:
    service = _service({"shipments": []})
    with pytest.raises(InvalidCheckInTransition, match="does not exist"):
        service.gate_check_in(_gate_request())


def test_gate_checkin_rejects_wrong_facility() -> None:
    service = _service()
    with pytest.raises(InvalidCheckInTransition, match="destination facility"):
        service.gate_check_in(
            GateCheckInRequest(
                shipment_id="SHP1006",
                facility_id="FAC-OTHER",
                gate_in_at=datetime.fromisoformat("2026-08-08T10:00:00"),
            )
        )


def test_gate_checkin_rejects_ineligible_status() -> None:
    service = _service({"shipments": [{**DEFAULT_SHIPMENT, "current_status": "PLANNED"}]})
    with pytest.raises(InvalidCheckInTransition, match="not eligible"):
        service.gate_check_in(_gate_request())


def test_gate_checkin_updates_shipment_status() -> None:
    service = _service()
    service.gate_check_in(_gate_request())
    assert service.repository.get_shipment("SHP1006")["current_status"] == "AT_GATE"


def test_list_operational_shipments_includes_joined_context_and_action_flags() -> None:
    service = _service(
        {
            "shipments": [
                dict(DEFAULT_SHIPMENT),
                {
                    "shipment_id": "SHP2000",
                    "order_reference": "ORD-2",
                    "carrier_id": "CAR001",
                    "driver_id": "DRV001",
                    "vehicle_id": "VEH001",
                    "origin_name": "Delhi Depot",
                    "origin_city": "Delhi",
                    "destination_facility_id": "FAC-1",
                    "customer_name": "Retail Hub",
                    "product_category": "General",
                    "load_weight_kg": 1000,
                    "required_dock_type": "STANDARD",
                    "temperature_control_required": 0,
                    "priority_code": "NORMAL",
                    "planned_departure_ts": "2026-08-08T08:00:00",
                    "original_eta_ts": "2026-08-08T11:00:00",
                    "latest_eta_ts": None,
                    "expected_unload_min": 45,
                    "current_status": "PLANNED",
                    "created_at": "2026-08-08T07:00:00",
                    "updated_at": "2026-08-08T07:00:00",
                },
            ],
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
                    "queue_position": 1,
                    "actual_dock_id": None,
                    "notes": None,
                    "updated_at": "2026-08-08T10:00:00",
                }
            ],
        }
    )

    items = service.list_operational_shipments("FAC-1")
    assert [item.shipment_id for item in items] == ["SHP1006"]
    assert items[0].driver_name == "Rajesh Kumar"
    assert items[0].registration_number == "MH02LD8342"
    assert items[0].destination_facility_name == "Jaipur DC"
    assert items[0].can_gate_in is False
    assert items[0].can_queue is True


def test_list_operational_shipments_excludes_completed_and_cancelled_from_first_checkin() -> None:
    service = _service(
        {
            "shipments": [
                {**DEFAULT_SHIPMENT, "shipment_id": "SHP2000", "current_status": "COMPLETED"},
                {**DEFAULT_SHIPMENT, "shipment_id": "SHP3000", "current_status": "CANCELLED"},
                {**DEFAULT_SHIPMENT, "shipment_id": "SHP4000", "current_status": "IN_TRANSIT"},
            ]
        }
    )
    items = service.list_operational_shipments("FAC-1")
    assert [item.shipment_id for item in items] == ["SHP4000"]
    assert items[0].can_gate_in is True


def test_list_operational_shipments_filters_by_assigned_facility() -> None:
    service = _service(
        {
            "shipments": [
                {**DEFAULT_SHIPMENT, "shipment_id": "SHP-JAI", "destination_facility_id": "FAC-JAI-01"},
                {**DEFAULT_SHIPMENT, "shipment_id": "SHP-GGN", "destination_facility_id": "FAC-GGN-01"},
            ],
            "facilities": [
                {"facility_id": "FAC-JAI-01", "facility_name": "Jaipur DC", "city": "Jaipur"},
                {"facility_id": "FAC-GGN-01", "facility_name": "Gurugram Cross-Dock", "city": "Gurugram"},
            ],
        }
    )

    jaipur_items = service.list_operational_shipments("FAC-JAI-01")
    gurugram_items = service.list_operational_shipments("FAC-GGN-01")

    assert [item.shipment_id for item in jaipur_items] == ["SHP-JAI"]
    assert [item.shipment_id for item in gurugram_items] == ["SHP-GGN"]


def test_list_operational_shipments_returns_empty_without_assigned_facility() -> None:
    service = _service()
    assert service.list_operational_shipments(None) == []


def test_list_active_facilities_returns_ui_options() -> None:
    service = _service(
        {
            "facilities": [
                {"facility_id": "FAC-JAI-01", "facility_name": "Jaipur DC", "city": "Jaipur", "state": "Rajasthan", "active_flag": 1},
                {"facility_id": "FAC-GGN-01", "facility_name": "Gurugram Cross-Dock", "city": "Gurugram", "state": "Haryana", "active_flag": 1},
                {"facility_id": "FAC-OLD-01", "facility_name": "Old DC", "city": "Old", "state": "Old", "active_flag": 0},
            ]
        }
    )

    items = service.list_active_facilities()

    assert [item.facility_id for item in items] == ["FAC-GGN-01", "FAC-JAI-01"]
    assert items[0].active_flag is True


def test_locked_for_rescheduling() -> None:
    service = _service()
    assert service.is_locked_for_rescheduling("SHP1006") is False
    service.gate_check_in(_gate_request())
    assert service.is_locked_for_rescheduling("SHP1006") is False
    service.mark_docked(
        DockInRequest(
            shipment_id="SHP1006",
            dock_in_at=datetime.fromisoformat("2026-08-08T11:00:00"),
        )
    )
    assert service.is_locked_for_rescheduling("SHP1006") is True
