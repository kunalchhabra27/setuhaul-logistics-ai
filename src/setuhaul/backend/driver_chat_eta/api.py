"""FastAPI routes for the driver chat & ETA backend."""

from __future__ import annotations

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from setuhaul.backend.driver_chat_eta.auth import DriverPrincipal, get_current_driver
from setuhaul.backend.driver_chat_eta.exceptions import DriverChatError
from setuhaul.backend.driver_chat_eta.models import (
    CarrierSummary,
    ChatRequest,
    ChatResponse,
    CheckinResponse,
    CheckinUpdateRequest,
    ConfirmSlotRequest,
    ConfirmSlotResponse,
    DriverProfile,
    DriverSnapshot,
    EscalateRequest,
    EscalateResponse,
    HoldSlotRequest,
    ProfileCompleteRequest,
    SlotActionResponse,
    VoiceChatRequest,
)
from setuhaul.backend.driver_chat_eta.repository import DriverChatRepository
from setuhaul.backend.driver_chat_eta.service import DriverChatService
from setuhaul.infrastructure.settings import get_settings
from setuhaul.infrastructure.metrics import emit_domain_event, increment
from setuhaul.infrastructure.supabase_client import create_caller_client
from setuhaul.infrastructure.telemetry import observe_operation

router = APIRouter(prefix="/driver-chat-eta", tags=["driver-chat-eta"])


@router.get("/health")
def health() -> dict[str, str]:
    """Return a lightweight status payload for driver-chat-eta smoke checks.

    Example:
    `GET /driver-chat-eta/health`
    """
    return {"status": "ok", "system": "driver-chat-eta"}


def get_service(principal: DriverPrincipal = Depends(get_current_driver)) -> DriverChatService:
    """Build a caller-scoped service whose repository is protected by RLS."""
    client = create_caller_client(get_settings(), principal.access_token)
    return DriverChatService(DriverChatRepository(client))


def _raise_http(exc: DriverChatError) -> None:
    raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message}) from exc


@router.get("/me", response_model=DriverProfile)
def get_my_profile(
    principal: DriverPrincipal = Depends(get_current_driver),
    service: DriverChatService = Depends(get_service),
) -> DriverProfile:
    try:
        return service.get_my_profile(principal)
    except DriverChatError as exc:
        _raise_http(exc)


@router.get("/carriers", response_model=list[CarrierSummary])
def list_carriers(
    principal: DriverPrincipal = Depends(get_current_driver),
    service: DriverChatService = Depends(get_service),
) -> list[CarrierSummary]:
    try:
        return service.list_carriers()
    except DriverChatError as exc:
        _raise_http(exc)


@router.get("/reference/home-base-cities", response_model=list[str])
def list_home_base_cities(
    principal: DriverPrincipal = Depends(get_current_driver),
    service: DriverChatService = Depends(get_service),
) -> list[str]:
    try:
        return service.list_home_base_cities()
    except DriverChatError as exc:
        _raise_http(exc)


@router.post("/profile/complete", response_model=DriverProfile)
def complete_profile(
    request: ProfileCompleteRequest,
    principal: DriverPrincipal = Depends(get_current_driver),
    service: DriverChatService = Depends(get_service),
) -> DriverProfile:
    try:
        return service.complete_profile(principal, request)
    except DriverChatError as exc:
        _raise_http(exc)


@router.get("/snapshot", response_model=DriverSnapshot)
def get_snapshot(
    principal: DriverPrincipal = Depends(get_current_driver),
    service: DriverChatService = Depends(get_service),
) -> DriverSnapshot:
    try:
        return service.snapshot(principal)
    except DriverChatError as exc:
        _raise_http(exc)


@router.post("/chat", response_model=ChatResponse)
def send_chat_message(
    request: ChatRequest,
    principal: DriverPrincipal = Depends(get_current_driver),
    service: DriverChatService = Depends(get_service),
) -> ChatResponse:
    try:
        return observe_operation(
            "driver_chat.request",
            {"operation": "chat_request"},
            lambda: service.handle_chat_message(principal, request.message),
        )
    except DriverChatError as exc:
        _raise_http(exc)


@router.post("/chat/voice", response_model=ChatResponse)
def send_voice_message(
    request: VoiceChatRequest,
    principal: DriverPrincipal = Depends(get_current_driver),
    service: DriverChatService = Depends(get_service),
) -> ChatResponse:
    try:
        return service.handle_voice_chat_message(principal, request.audio_base64, request.mime_type)
    except DriverChatError as exc:
        _raise_http(exc)


@router.post("/slots/hold", response_model=SlotActionResponse)
def hold_slot(
    request: HoldSlotRequest,
    principal: DriverPrincipal = Depends(get_current_driver),
    service: DriverChatService = Depends(get_service),
) -> SlotActionResponse:
    try:
        response = service.hold_slot(principal, request.slot_id)
        increment("setuhaul.driver.slot_requests")
        emit_domain_event("slot_change_requested", result="held")
        return response
    except DriverChatError as exc:
        _raise_http(exc)


@router.post("/slots/confirm", response_model=ConfirmSlotResponse)
def confirm_slot(
    request: ConfirmSlotRequest,
    principal: DriverPrincipal = Depends(get_current_driver),
    service: DriverChatService = Depends(get_service),
) -> ConfirmSlotResponse:
    try:
        response = service.confirm_slot(principal, request.slot_id)
        increment("setuhaul.driver.slot_requests")
        emit_domain_event("slot_confirmed", result="success")
        return response
    except DriverChatError as exc:
        _raise_http(exc)


@router.post("/checkin/update", response_model=CheckinResponse)
def update_checkin(
    request: CheckinUpdateRequest,
    principal: DriverPrincipal = Depends(get_current_driver),
    service: DriverChatService = Depends(get_service),
) -> CheckinResponse:
    try:
        return service.update_checkin(principal, request)
    except DriverChatError as exc:
        _raise_http(exc)


@router.post("/escalate", response_model=EscalateResponse)
def escalate(
    request: EscalateRequest,
    principal: DriverPrincipal = Depends(get_current_driver),
    service: DriverChatService = Depends(get_service),
) -> EscalateResponse:
    try:
        return service.escalate(principal, request.reason)
    except DriverChatError as exc:
        _raise_http(exc)


def install_exception_handlers(app: FastAPI) -> None:
    """Install a stable error envelope for DriverChatError.

    Route bodies already convert DriverChatError -> HTTPException via
    _raise_http, but that only runs for exceptions raised *inside* a route
    function. get_current_driver (used as a Depends() on nearly every route
    here) raises AuthenticationError during dependency resolution, before
    the route body -- and therefore its try/except -- ever runs. Without an
    app-level handler, an expired/invalid bearer token propagates unhandled
    to Starlette's default handler and surfaces as a generic 500 instead of
    a 401. Mirrors tms.api.install_exception_handlers's TMSError handler.
    """

    @app.exception_handler(DriverChatError)
    async def handle_driver_chat_error(_: Request, exc: DriverChatError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
        return JSONResponse(
            status_code=exc.status_code,
            headers=headers,
            content={"error": {"code": exc.code, "message": exc.message}},
        )
