from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest

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


def _connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute(
        """
        CREATE TABLE facility_checkins (
            checkin_id TEXT PRIMARY KEY,
            shipment_id TEXT NOT NULL UNIQUE,
            facility_id TEXT NOT NULL,
            gate_in_ts TEXT,
            yard_queue_enter_ts TEXT,
            dock_in_ts TEXT,
            unload_start_ts TEXT,
            unload_end_ts TEXT,
            gate_out_ts TEXT,
            arrival_state TEXT,
            queue_state TEXT,
            queue_position INTEGER,
            actual_dock_id TEXT,
            notes TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )
    return connection


def _service() -> CheckInService:
    return CheckInService(CheckInRepository(_connection()))


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
