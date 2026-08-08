"""FastAPI routes and error contracts for the TMS backend."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Query, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from setuhaul.backend.tms.exceptions import TMSError
from setuhaul.backend.tms.models import (
    DriverContextResponse,
    DriverCreate,
    DriverResponse,
    DriverUpdate,
    ShipmentContextResponse,
    ShipmentCreate,
    ShipmentResponse,
    ShipmentStatus,
    ShipmentUpdate,
    VehicleCreate,
    VehicleResponse,
    VehicleUpdate,
)
from setuhaul.backend.tms.repository import TMSRepository
from setuhaul.backend.tms.service import TMSService
from setuhaul.infrastructure.auth import Principal, require_admin, require_reader
from setuhaul.infrastructure.settings import get_settings
from setuhaul.infrastructure.supabase_client import create_caller_client

router = APIRouter(prefix="/api/v1/tms", tags=["tms"])


def get_service(
    principal: Principal = Depends(require_reader),
) -> TMSService:
    """Create a caller-scoped service whose repository is protected by RLS."""
    client = create_caller_client(get_settings(), principal.access_token)
    return TMSService(TMSRepository(client))


@router.get("/drivers", response_model=DriverResponse)
def get_driver_by_phone(
    phone: str = Query(min_length=1), service: TMSService = Depends(get_service)
) -> DriverResponse:
    return service.get_driver_by_phone(phone)


@router.get("/drivers/{driver_id}", response_model=DriverResponse)
def get_driver(driver_id: UUID, service: TMSService = Depends(get_service)) -> DriverResponse:
    return service.get_driver(driver_id)


@router.get("/drivers/{driver_id}/shipments", response_model=list[ShipmentResponse])
def get_driver_shipments(
    driver_id: UUID, service: TMSService = Depends(get_service)
) -> list[ShipmentResponse]:
    return service.driver_shipments(driver_id)


@router.get("/drivers/{driver_id}/active-shipments", response_model=list[ShipmentResponse])
def get_driver_active_shipments(
    driver_id: UUID, service: TMSService = Depends(get_service)
) -> list[ShipmentResponse]:
    return service.driver_shipments(driver_id, active_only=True)


@router.post("/drivers", response_model=DriverResponse, status_code=status.HTTP_201_CREATED)
def create_driver(
    request: DriverCreate,
    _: Principal = Depends(require_admin),
    service: TMSService = Depends(get_service),
) -> DriverResponse:
    return service.create_driver(request)


@router.patch("/drivers/{driver_id}", response_model=DriverResponse)
def update_driver(
    driver_id: UUID,
    request: DriverUpdate,
    _: Principal = Depends(require_admin),
    service: TMSService = Depends(get_service),
) -> DriverResponse:
    return service.update_driver(driver_id, request)


@router.get("/vehicles/{vehicle_id}", response_model=VehicleResponse)
def get_vehicle(vehicle_id: UUID, service: TMSService = Depends(get_service)) -> VehicleResponse:
    return service.get_vehicle(vehicle_id)


@router.post("/vehicles", response_model=VehicleResponse, status_code=status.HTTP_201_CREATED)
def create_vehicle(
    request: VehicleCreate,
    _: Principal = Depends(require_admin),
    service: TMSService = Depends(get_service),
) -> VehicleResponse:
    return service.create_vehicle(request)


@router.patch("/vehicles/{vehicle_id}", response_model=VehicleResponse)
def update_vehicle(
    vehicle_id: UUID,
    request: VehicleUpdate,
    _: Principal = Depends(require_admin),
    service: TMSService = Depends(get_service),
) -> VehicleResponse:
    return service.update_vehicle(vehicle_id, request)


@router.get("/shipments", response_model=list[ShipmentResponse])
def list_shipments(
    driver_id: UUID | None = None,
    destination_id: UUID | None = None,
    shipment_status: ShipmentStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: TMSService = Depends(get_service),
) -> list[ShipmentResponse]:
    return service.list_shipments(
        driver_id=driver_id,
        destination_id=destination_id,
        status=shipment_status,
        limit=limit,
        offset=offset,
    )


@router.get("/shipments/{shipment_id}", response_model=ShipmentResponse)
def get_shipment(
    shipment_id: UUID, service: TMSService = Depends(get_service)
) -> ShipmentResponse:
    return service.get_shipment(shipment_id)


@router.post("/shipments", response_model=ShipmentResponse, status_code=status.HTTP_201_CREATED)
def create_shipment(
    request: ShipmentCreate,
    _: Principal = Depends(require_admin),
    service: TMSService = Depends(get_service),
) -> ShipmentResponse:
    return service.create_shipment(request)


@router.patch("/shipments/{shipment_id}", response_model=ShipmentResponse)
def update_shipment(
    shipment_id: UUID,
    request: ShipmentUpdate,
    _: Principal = Depends(require_admin),
    service: TMSService = Depends(get_service),
) -> ShipmentResponse:
    return service.update_shipment(shipment_id, request)


@router.get("/context/drivers/{driver_id}", response_model=DriverContextResponse)
def get_driver_context(
    driver_id: UUID, service: TMSService = Depends(get_service)
) -> DriverContextResponse:
    return service.driver_context(driver_id)


@router.get("/context/shipments/{shipment_id}", response_model=ShipmentContextResponse)
def get_shipment_context(
    shipment_id: UUID, service: TMSService = Depends(get_service)
) -> ShipmentContextResponse:
    return service.shipment_context(shipment_id)


def install_exception_handlers(app: FastAPI) -> None:
    """Install stable TMS and Pydantic error response envelopes."""

    @app.exception_handler(TMSError)
    async def handle_tms_error(_: Request, exc: TMSError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
        return JSONResponse(
            status_code=exc.status_code,
            headers=headers,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=jsonable_encoder(
                {
                    "error": {
                        "code": "REQUEST_VALIDATION_FAILED",
                        "message": "The request payload or parameters are invalid.",
                        "details": exc.errors(),
                    }
                }
            ),
        )
