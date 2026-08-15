"""FastAPI routes for the check-in portal backend."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from setuhaul.backend.checkin_portal.exceptions import InvalidCheckInTransition
from setuhaul.backend.checkin_portal.models import (
    ApproveGateCheckinRequest,
    CompleteRequest,
    DockInRequest,
    GateCheckInRequest,
    QueueUpdateRequest,
)
from setuhaul.backend.checkin_portal.repository import CheckInRepository
from setuhaul.backend.checkin_portal.service import CheckInService
from setuhaul.infrastructure.auth import Principal, require_admin, require_reader
from setuhaul.infrastructure.metrics import emit_domain_event, increment
from setuhaul.infrastructure.settings import get_settings
from setuhaul.infrastructure.supabase_client import create_caller_client
from setuhaul.infrastructure.telemetry import observe_operation

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
        result = observe_operation(
            "checkin.gate_in",
            {"operation": "gate_check_in", "shipment_id": request.shipment_id, "facility_id": request.facility_id},
            lambda: service.gate_check_in(request),
        )
        increment("setuhaul.checkin.gate_ins", {"shipment_id": request.shipment_id, "facility_id": request.facility_id})
        emit_domain_event("gate_checkin", shipment_id=request.shipment_id, facility_id=request.facility_id, result="success")
        return result
    except InvalidCheckInTransition as exc:
        increment("setuhaul.checkin.invalid_transitions", {"shipment_id": request.shipment_id})
        emit_domain_event("invalid_transition", shipment_id=request.shipment_id, result="rejected")
        raise _handle_transition_error(exc) from exc


@router.patch("/approve-gate")
def approve_gate_checkin(
    request: ApproveGateCheckinRequest,
    _: Principal = Depends(require_admin),
    service: CheckInService = Depends(get_service),
) -> dict:
    """Staff approves a driver-reported gate arrival -- only after this does
    the shipment's status become visible to TMS/WMS as checked in.

    Example request body:
    ```json
    { "shipment_id": "SHP1006" }
    ```
    """
    try:
        return observe_operation(
            "checkin.gate_approval",
            {"operation": "approve_gate_checkin", "shipment_id": request.shipment_id},
            lambda: service.approve_gate_checkin(request.shipment_id),
        )
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
        result = observe_operation(
            "checkin.queue_update",
            {"operation": "update_queue", "shipment_id": request.shipment_id},
            lambda: service.update_queue(request),
        )
        increment("setuhaul.checkin.queue_updates", {"shipment_id": request.shipment_id})
        emit_domain_event("queue_updated", shipment_id=request.shipment_id, result="success")
        return result
    except InvalidCheckInTransition as exc:
        increment("setuhaul.checkin.invalid_transitions", {"shipment_id": request.shipment_id})
        emit_domain_event("invalid_transition", shipment_id=request.shipment_id, result="rejected")
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
        result = observe_operation(
            "checkin.dock_in",
            {"operation": "mark_docked", "shipment_id": request.shipment_id},
            lambda: service.mark_docked(request),
        )
        increment("setuhaul.checkin.dock_ins", {"shipment_id": request.shipment_id})
        emit_domain_event("truck_docked", shipment_id=request.shipment_id, result="success")
        return result
    except InvalidCheckInTransition as exc:
        increment("setuhaul.checkin.invalid_transitions", {"shipment_id": request.shipment_id})
        emit_domain_event("invalid_transition", shipment_id=request.shipment_id, result="rejected")
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
        result = observe_operation(
            "checkin.complete",
            {"operation": "complete", "shipment_id": request.shipment_id},
            lambda: service.complete(request),
        )
        increment("setuhaul.checkin.completions", {"shipment_id": request.shipment_id})
        emit_domain_event("unload_completed", shipment_id=request.shipment_id, result="success")
        return result
    except InvalidCheckInTransition as exc:
        increment("setuhaul.checkin.invalid_transitions", {"shipment_id": request.shipment_id})
        emit_domain_event("invalid_transition", shipment_id=request.shipment_id, result="rejected")
        raise _handle_transition_error(exc) from exc
