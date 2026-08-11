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


def _service(extra_tables: dict | None = None) -> CheckInService:
    # SHP1006 exists (satisfies the shipment_exists check on gate_check_in)
    # but has no driver_id by default -- tests that need a driver or a
    # confirmed WMS appointment opt in via extra_tables/_confirmed_dock_tables.
    tables = {"facility_checkins": [], "shipments": [{"shipment_id": "SHP1006"}]}
    tables.update(extra_tables or {})
    return CheckInService(CheckInRepository(FakeSupabaseClient(tables)))


def _confirmed_dock_tables() -> dict:
    """Gives SHP1006 a CONFIRMED WMS appointment/dock -- mark_docked now
    requires this to exist before it will let staff dock a truck."""
    return {
        "shipments": [{"shipment_id": "SHP1006"}],
        "appointments": [
            {
                "appointment_id": "APT-SHP1006",
                "shipment_id": "SHP1006",
                "slot_id": "SLOT-1",
                "is_current": 1,
                "appointment_status": "CONFIRMED",
            }
        ],
        "appointment_slots": [{"slot_id": "SLOT-1", "dock_id": "DOCK-1"}],
        "docks": [{"dock_id": "DOCK-1", "dock_code": "D1"}],
    }


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
    service = _service(_confirmed_dock_tables())
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
    # actual_dock_id was auto-filled from the confirmed appointment's dock.
    assert record["actual_dock_id"] == "DOCK-1"


def test_dock_blocked_without_confirmed_wms_appointment() -> None:
    service = _service()  # no appointments/slots/docks -- nothing confirmed
    service.gate_check_in(_gate_request())
    with pytest.raises(InvalidCheckInTransition, match="No confirmed WMS appointment"):
        service.mark_docked(
            DockInRequest(
                shipment_id="SHP1006",
                dock_in_at=datetime.fromisoformat("2026-08-08T11:00:00"),
            )
        )


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
    service = _service(_confirmed_dock_tables())
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


def test_completed_cannot_return_to_queue() -> None:
    service = _service(_confirmed_dock_tables())
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

    service = _service(
        {
            "shipments": [{"shipment_id": "SHP1006", "driver_id": "DRV001", "order_reference": "ORD-1"}],
            "drivers": [{"driver_id": "DRV001", "driver_name": "Rajesh Kumar", "phone": "+91-9000010001"}],
        }
    )
    service.gate_check_in(_gate_request())

    assert captured["to"] == "+91-9000010001"
    assert "ORD-1" in captured["body"]


def test_gate_checkin_succeeds_even_if_sms_lookup_fails(monkeypatch) -> None:
    import setuhaul.backend.checkin_portal.service as checkin_service_module

    def _boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(checkin_service_module, "send_sms", _boom)

    service = _service(
        {
            "shipments": [{"shipment_id": "SHP1006", "driver_id": "DRV001", "order_reference": "ORD-1"}],
            "drivers": [{"driver_id": "DRV001", "driver_name": "Rajesh Kumar", "phone": "+91-9000010001"}],
        }
    )
    # Must not raise even though the SMS lookup/send blows up.
    record = service.gate_check_in(_gate_request())
    assert record["arrival_status"] == "GATE_IN"


def test_gate_checkin_without_driver_skips_sms_silently(monkeypatch) -> None:
    import setuhaul.backend.checkin_portal.service as checkin_service_module

    calls = []
    monkeypatch.setattr(checkin_service_module, "send_sms", lambda *a, **k: calls.append((a, k)))

    service = _service()  # SHP1006 exists but has no driver_id -> no driver on file
    service.gate_check_in(_gate_request())

    assert calls == []


def test_gate_checkin_rejects_unknown_shipment() -> None:
    service = _service()
    with pytest.raises(InvalidCheckInTransition, match="Unknown shipment"):
        service.gate_check_in(_gate_request(shipment_id="SHP-DOES-NOT-EXIST"))


def test_locked_for_rescheduling() -> None:
    service = _service(_confirmed_dock_tables())
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
