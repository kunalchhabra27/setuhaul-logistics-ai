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
    tables = {"facility_checkins": []}
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

    service = _service()  # no shipments/drivers tables -> no driver on file
    service.gate_check_in(_gate_request())

    assert calls == []


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
