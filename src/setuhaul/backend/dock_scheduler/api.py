"""FastAPI routes for the dock scheduler (WMS) backend."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from setuhaul.backend.dock_scheduler.exceptions import (
    ChangeRequestAlreadyDecidedError,
    ChangeRequestNotFoundError,
    DockSchedulerError,
    InvalidBookingError,
    SlotUnavailableError,
    UnknownShipmentError,
)
from setuhaul.backend.dock_scheduler.models import (
    CancelHoldRequest,
    ChangeRequestResponse,
    ConfirmRequest,
    ConfirmResponse,
    CreateChangeRequest,
    DecideChangeRequest,
    DockSlot,
    DriverConstraints,
    HoldRequest,
    HoldResponse,
    HoldResult,
    SlotLifecycleStage,
    SlotSuggestionResponse,
    SuggestRequest,
)
from setuhaul.backend.dock_scheduler.repository import DockSchedulerRepository, parse_ts
from setuhaul.backend.dock_scheduler.service import DockSchedulerService
from setuhaul.infrastructure.auth import Principal, require_admin, require_reader
from setuhaul.infrastructure import cache
from setuhaul.infrastructure.settings import get_settings
from setuhaul.infrastructure.supabase_client import create_caller_client

router = APIRouter(prefix="/dock-scheduler", tags=["dock-scheduler"])


def get_service(principal: Principal = Depends(require_reader)) -> DockSchedulerService:
    """Create a caller-scoped service whose repository is protected by RLS."""
    client = create_caller_client(get_settings(), principal.access_token)
    return DockSchedulerService(
        DockSchedulerRepository(client), cache_scope=cache.CacheScope.user(principal.user_id)
    )


def _handle_error(exc: DockSchedulerError) -> HTTPException:
    if isinstance(exc, (UnknownShipmentError, ChangeRequestNotFoundError)):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, (SlotUnavailableError, ChangeRequestAlreadyDecidedError)):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, InvalidBookingError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


@router.post("/suggest", response_model=list[SlotSuggestionResponse])
def suggest_slots(
    request: SuggestRequest, service: DockSchedulerService = Depends(get_service)
) -> list[SlotSuggestionResponse]:
    constraints = DriverConstraints(
        earliest_start=request.earliest_start,
        must_finish_by=request.must_finish_by,
    )
    try:
        suggestions = service.suggest_slots(request.shipment_id, constraints, request.limit)
    except DockSchedulerError as exc:
        raise _handle_error(exc) from exc
    return [SlotSuggestionResponse.from_suggestion(item) for item in suggestions]


@router.get("/board", response_model=list[DockSlot])
def dock_board(shipment_id: str, service: DockSchedulerService = Depends(get_service)) -> list[DockSlot]:
    """Every compatible slot for a shipment, grouped by dock on the frontend,
    for rendering the full visual dock board (not just the top-ranked
    suggestions from POST /suggest).

    Example:
    `GET /dock-scheduler/board?shipment_id=SHP1006`
    """
    try:
        return service.dock_board(shipment_id)
    except DockSchedulerError as exc:
        raise _handle_error(exc) from exc


@router.post("/hold", response_model=HoldResponse)
def hold_slot(
    request: HoldRequest,
    _: Principal = Depends(require_admin),
    service: DockSchedulerService = Depends(get_service),
) -> HoldResponse:
    try:
        hold = service.hold_slot(request.shipment_id, request.slot_id, request.ttl_minutes)
    except DockSchedulerError as exc:
        raise _handle_error(exc) from exc
    return HoldResponse.from_hold(hold)


@router.post("/request-confirmation", response_model=HoldResponse)
def request_confirmation(
    request: HoldRequest,
    _: Principal = Depends(require_admin),
    service: DockSchedulerService = Depends(get_service),
) -> HoldResponse:
    try:
        appointment_id = service.request_confirmation(request.shipment_id, request.slot_id)
        hold = service.repository.active_hold_for_shipment(request.shipment_id, request.slot_id)
        if hold is None:
            raise InvalidBookingError("Hold was not created for the selected slot")
        result = HoldResult(
            hold_id=hold["hold_id"],
            slot_id=hold["slot_id"],
            shipment_id=hold["shipment_id"],
            expires_at=parse_ts(hold["expires_at"]),
            lifecycle_stage=SlotLifecycleStage.PENDING_CONFIRMATION,
        )
    except DockSchedulerError as exc:
        raise _handle_error(exc) from exc
    return HoldResponse.from_hold(result, appointment_id=appointment_id)


@router.post("/confirm", response_model=ConfirmResponse)
def confirm_booking(
    request: ConfirmRequest,
    _: Principal = Depends(require_admin),
    service: DockSchedulerService = Depends(get_service),
) -> ConfirmResponse:
    try:
        appointment_id = service.confirm_booking(
            request.shipment_id, request.slot_id, request.accepted
        )
    except DockSchedulerError as exc:
        raise _handle_error(exc) from exc
    return ConfirmResponse(
        appointment_id=appointment_id,
        shipment_id=request.shipment_id,
        slot_id=request.slot_id,
        lifecycle_stage=SlotLifecycleStage.CONFIRMED,
    )


@router.post("/cancel-hold")
def cancel_hold(
    request: CancelHoldRequest,
    _: Principal = Depends(require_admin),
    service: DockSchedulerService = Depends(get_service),
) -> dict[str, str]:
    service.cancel_hold(request.hold_id)
    return {"status": "released", "hold_id": request.hold_id}


@router.post("/change-requests", response_model=ChangeRequestResponse)
def create_change_request(
    request: CreateChangeRequest,
    principal: Principal = Depends(require_reader),
    service: DockSchedulerService = Depends(get_service),
) -> ChangeRequestResponse:
    """A TMS dispatcher or a driver asks to move an already-booked shipment
    to a different dock slot. This does not move the appointment yet -- see
    POST /change-requests/{id}/decide, which WMS staff use to approve or
    decline it."""
    try:
        row = service.create_change_request(
            shipment_id=request.shipment_id,
            requested_slot_id=request.requested_slot_id,
            requested_by_role=request.requested_by_role,
            requested_by_user_id=principal.user_id,
            reason=request.reason,
        )
    except DockSchedulerError as exc:
        raise _handle_error(exc) from exc
    return ChangeRequestResponse(**row)


@router.get("/change-requests", response_model=list[ChangeRequestResponse])
def list_change_requests(
    status: str | None = None,
    _: Principal = Depends(require_reader),
    service: DockSchedulerService = Depends(get_service),
) -> list[ChangeRequestResponse]:
    """List dock-slot change requests, optionally filtered by status
    (PENDING/APPROVED/DECLINED). WMS filters client-side to requests for
    shipments at its own facility using its already-fetched shipment list,
    the same way the rest of the WMS board is facility-scoped.

    Example:
    `GET /dock-scheduler/change-requests?status=PENDING`
    """
    return [ChangeRequestResponse(**row) for row in service.list_change_requests(status)]


@router.post("/change-requests/{change_request_id}/decide", response_model=ChangeRequestResponse)
def decide_change_request(
    change_request_id: str,
    request: DecideChangeRequest,
    principal: Principal = Depends(require_admin),
    service: DockSchedulerService = Depends(get_service),
) -> ChangeRequestResponse:
    try:
        row = service.decide_change_request(
            change_request_id, approve=request.approve, decided_by_user_id=principal.user_id, note=request.note
        )
    except DockSchedulerError as exc:
        raise _handle_error(exc) from exc
    return ChangeRequestResponse(**row)
