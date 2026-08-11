"""FastAPI routes for the driver chat & ETA backend."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from setuhaul.backend.driver_chat_eta.auth import (
    DriverPrincipal,
    get_current_driver,
    link_driver_to_auth_account,
)
from setuhaul.backend.driver_chat_eta.exceptions import DriverChatError
from setuhaul.backend.driver_chat_eta.models import (
    ChatRequest,
    ChatResponse,
    CheckinResponse,
    CheckinUpdateRequest,
    ConfirmSlotRequest,
    ConfirmSlotResponse,
    DriverProfile,
    DriverSnapshot,
    OnboardingOptions,
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
from setuhaul.infrastructure.supabase_client import create_caller_client, create_public_client

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


@router.post("/profile/complete", response_model=DriverProfile)
def complete_profile(
    request: ProfileCompleteRequest,
    principal: DriverPrincipal = Depends(get_current_driver),
    service: DriverChatService = Depends(get_service),
) -> DriverProfile:
    try:
        profile = service.complete_profile(principal, request)
    except DriverChatError as exc:
        _raise_http(exc)
    link_driver_to_auth_account(principal.access_token, profile.driver_id)
    return profile


@router.get("/profile/options", response_model=OnboardingOptions)
def get_profile_options() -> OnboardingOptions:
    try:
        client = create_public_client(get_settings())
        return DriverChatService(DriverChatRepository(client)).onboarding_options()
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
        return service.handle_chat_message(principal, request.message)
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
        return service.hold_slot(principal, request.slot_id)
    except DriverChatError as exc:
        _raise_http(exc)


@router.post("/slots/confirm", response_model=ConfirmSlotResponse)
def confirm_slot(
    request: ConfirmSlotRequest,
    principal: DriverPrincipal = Depends(get_current_driver),
    service: DriverChatService = Depends(get_service),
) -> ConfirmSlotResponse:
    try:
        return service.confirm_slot(principal, request.slot_id)
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
