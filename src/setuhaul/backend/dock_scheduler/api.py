"""FastAPI routes for the dock scheduler (WMS) backend."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from setuhaul.backend.dock_scheduler.exceptions import (
    DockSchedulerError,
    InvalidBookingError,
    SlotUnavailableError,
    UnknownShipmentError,
)
from setuhaul.backend.dock_scheduler.models import (
    CancelHoldRequest,
    ConfirmRequest,
    ConfirmResponse,
    DriverConstraints,
    HoldRequest,
    HoldResponse,
    SlotLifecycleStage,
    SlotSuggestionResponse,
    SuggestRequest,
)
from setuhaul.backend.dock_scheduler.service import DockSchedulerService

router = APIRouter(prefix="/dock-scheduler", tags=["dock-scheduler"])


def _service() -> DockSchedulerService:
    from setuhaul.db.connection import connect
    from pathlib import Path

    from setuhaul.backend.dock_scheduler.repository import DockSchedulerRepository

    root = Path(__file__).resolve().parents[4]
    db_path = root / "data" / "setuhaul_freight_operations.db"
    connection = connect(db_path)
    return DockSchedulerService(DockSchedulerRepository(connection))


def _handle_error(exc: DockSchedulerError) -> HTTPException:
    if isinstance(exc, UnknownShipmentError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, SlotUnavailableError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, InvalidBookingError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


@router.post("/suggest", response_model=list[SlotSuggestionResponse])
def suggest_slots(request: SuggestRequest) -> list[SlotSuggestionResponse]:
    service = _service()
    constraints = DriverConstraints(
        earliest_start=request.earliest_start,
        must_finish_by=request.must_finish_by,
    )
    try:
        suggestions = service.suggest_slots(request.shipment_id, constraints, request.limit)
    except DockSchedulerError as exc:
        raise _handle_error(exc) from exc
    return [SlotSuggestionResponse.from_suggestion(item) for item in suggestions]


@router.post("/hold", response_model=HoldResponse)
def hold_slot(request: HoldRequest) -> HoldResponse:
    service = _service()
    try:
        hold = service.hold_slot(request.shipment_id, request.slot_id, request.ttl_minutes)
    except DockSchedulerError as exc:
        raise _handle_error(exc) from exc
    return HoldResponse.from_hold(hold)


@router.post("/request-confirmation", response_model=HoldResponse)
def request_confirmation(request: HoldRequest) -> HoldResponse:
    from setuhaul.backend.dock_scheduler.models import HoldResult
    from setuhaul.backend.dock_scheduler.repository import parse_ts

    service = _service()
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
def confirm_booking(request: ConfirmRequest) -> ConfirmResponse:
    service = _service()
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
def cancel_hold(request: CancelHoldRequest) -> dict[str, str]:
    service = _service()
    service.cancel_hold(request.hold_id)
    return {"status": "released", "hold_id": request.hold_id}
