"""FastAPI routes for the check-in portal backend."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from setuhaul.backend.checkin_portal.exceptions import InvalidCheckInTransition
from setuhaul.backend.checkin_portal.models import (
    CompleteRequest,
    DockInRequest,
    GateCheckInRequest,
    QueueUpdateRequest,
)
from setuhaul.backend.checkin_portal.repository import CheckInRepository
from setuhaul.backend.checkin_portal.service import CheckInService
from setuhaul.db.connection import connect

router = APIRouter(prefix="/checkins", tags=["checkin-portal"])


def _service() -> CheckInService:
    """Build a repository-backed service for local smoke testing."""
    root = Path(__file__).resolve().parents[4]
    db_path = root / "data" / "setuhaul_freight_operations.db"
    connection = connect(db_path)
    return CheckInService(CheckInRepository(connection))


def _handle_transition_error(exc: InvalidCheckInTransition) -> HTTPException:
    """Map domain validation errors to an HTTP response."""
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/{shipment_id}")
def get_status(shipment_id: str) -> dict:
    """Get the current check-in state for a shipment.

    Example:
    `GET /checkins/SHP1006`
    """
    record = _service().get_status(shipment_id)
    if record is None:
        raise HTTPException(status_code=404, detail="No check-in record exists for this shipment.")
    return record


@router.post("/gate")
def gate_check_in(request: GateCheckInRequest) -> dict:
    """Create the initial gate-in record for a shipment.

    Example request body:
    ```json
    {
      "shipment_id": "SHP1006",
      "facility_id": "FAC-JAI-01",
      "gate_in_at": "2026-08-08T18:03:00+05:30"
    }
    ```
    """
    try:
        return _service().gate_check_in(request)
    except InvalidCheckInTransition as exc:
        raise _handle_transition_error(exc) from exc


@router.patch("/queue")
def update_queue(request: QueueUpdateRequest) -> dict:
    """Update the queue state for a shipment.

    Example request body:
    ```json
    {
      "shipment_id": "SHP1006",
      "queue_status": "YARD_QUEUE"
    }
    ```
    """
    try:
        return _service().update_queue(request)
    except InvalidCheckInTransition as exc:
        raise _handle_transition_error(exc) from exc


@router.patch("/dock")
def mark_docked(request: DockInRequest) -> dict:
    """Mark a shipment as docked.

    Example request body:
    ```json
    {
      "shipment_id": "SHP1006",
      "dock_in_at": "2026-08-08T18:25:00+05:30"
    }
    ```
    """
    try:
        return _service().mark_docked(request)
    except InvalidCheckInTransition as exc:
        raise _handle_transition_error(exc) from exc


@router.patch("/complete")
def complete(request: CompleteRequest) -> dict:
    """Mark a shipment as completed.

    Example request body:
    ```json
    {
      "shipment_id": "SHP1006",
      "completed_at": "2026-08-08T19:05:00+05:30"
    }
    ```
    """
    try:
        return _service().complete(request)
    except InvalidCheckInTransition as exc:
        raise _handle_transition_error(exc) from exc
