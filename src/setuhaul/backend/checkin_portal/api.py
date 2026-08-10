"""FastAPI routes for the check-in portal backend."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from setuhaul.backend.checkin_portal.exceptions import InvalidCheckInTransition
from setuhaul.backend.checkin_portal.models import (
    CompleteRequest,
    DockInRequest,
    GateCheckInRequest,
    QueueUpdateRequest,
)
from setuhaul.backend.checkin_portal.repository import CheckInRepository
from setuhaul.backend.checkin_portal.service import CheckInService
from setuhaul.infrastructure.auth import Principal, require_admin, require_reader
from setuhaul.infrastructure.settings import get_settings
from setuhaul.infrastructure.supabase_client import create_caller_client

router = APIRouter(prefix="/checkins", tags=["checkin-portal"])


def get_service(principal: Principal = Depends(require_reader)) -> CheckInService:
    """Create a caller-scoped service whose repository is protected by RLS."""
    client = create_caller_client(get_settings(), principal.access_token)
    return CheckInService(CheckInRepository(client))


def _handle_transition_error(exc: InvalidCheckInTransition) -> HTTPException:
    """Map domain validation errors to an HTTP response."""
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/{shipment_id}")
def get_status(shipment_id: str, service: CheckInService = Depends(get_service)) -> dict:
    """Get the current check-in state for a shipment.

    Example:
    `GET /checkins/SHP1006`
    """
    record = service.get_status(shipment_id)
    if record is None:
        raise HTTPException(status_code=404, detail="No check-in record exists for this shipment.")
    return record


@router.post("/gate")
def gate_check_in(
    request: GateCheckInRequest,
    _: Principal = Depends(require_admin),
    service: CheckInService = Depends(get_service),
) -> dict:
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
        return service.gate_check_in(request)
    except InvalidCheckInTransition as exc:
        raise _handle_transition_error(exc) from exc


@router.patch("/queue")
def update_queue(
    request: QueueUpdateRequest,
    _: Principal = Depends(require_admin),
    service: CheckInService = Depends(get_service),
) -> dict:
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
        return service.update_queue(request)
    except InvalidCheckInTransition as exc:
        raise _handle_transition_error(exc) from exc


@router.patch("/dock")
def mark_docked(
    request: DockInRequest,
    _: Principal = Depends(require_admin),
    service: CheckInService = Depends(get_service),
) -> dict:
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
        return service.mark_docked(request)
    except InvalidCheckInTransition as exc:
        raise _handle_transition_error(exc) from exc


@router.patch("/complete")
def complete(
    request: CompleteRequest,
    _: Principal = Depends(require_admin),
    service: CheckInService = Depends(get_service),
) -> dict:
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
        return service.complete(request)
    except InvalidCheckInTransition as exc:
        raise _handle_transition_error(exc) from exc
