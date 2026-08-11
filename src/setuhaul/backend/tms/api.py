"""FastAPI routes and error contracts for the TMS backend."""

from __future__ import annotations

from fastapi import APIRouter, Depends, FastAPI, Query, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from setuhaul.backend.tms.exceptions import TMSError
from setuhaul.backend.tms.models import (
    DriverContextResponse,
    DriverCreate,
    DriverResponse,
    DriverUpdate,
    FacilityResponse,
    FacilityStaffRegisterRequest,
    FacilityStaffResponse,
    ShipmentContextResponse,
    ShipmentCreate,
    ShipmentReferenceData,
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

router = APIRouter(prefix="/tms", tags=["tms"])


@router.get("/health")
def health() -> dict[str, str]:
    """Return a lightweight status payload for TMS smoke checks.

    Example:
    `GET /tms/health`
    """
    return {"status": "ok", "system": "tms"}


def get_service(
    principal: Principal = Depends(require_reader),
) -> TMSService:
    """Create a caller-scoped service whose repository is protected by RLS."""
    client = create_caller_client(get_settings(), principal.access_token)
    return TMSService(TMSRepository(client))


@router.get("/drivers", response_model=list[DriverResponse])
def list_or_find_drivers(
    phone: str | None = Query(default=None, min_length=1),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: TMSService = Depends(get_service),
) -> list[DriverResponse]:
    if phone:
        return [service.get_driver_by_phone(phone)]
    return service.list_drivers(limit=limit, offset=offset)


@router.get("/drivers/{driver_id}", response_model=DriverResponse)
def get_driver(driver_id: str, service: TMSService = Depends(get_service)) -> DriverResponse:
    return service.get_driver(driver_id)


@router.get("/drivers/{driver_id}/shipments", response_model=list[ShipmentResponse])
def get_driver_shipments(
    driver_id: str, service: TMSService = Depends(get_service)
) -> list[ShipmentResponse]:
    return service.driver_shipments(driver_id)


@router.get("/drivers/{driver_id}/active-shipments", response_model=list[ShipmentResponse])
def get_driver_active_shipments(
    driver_id: str, service: TMSService = Depends(get_service)
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
    driver_id: str,
    request: DriverUpdate,
    _: Principal = Depends(require_admin),
    service: TMSService = Depends(get_service),
) -> DriverResponse:
    return service.update_driver(driver_id, request)


@router.get("/vehicles", response_model=list[VehicleResponse])
def list_vehicles(
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: TMSService = Depends(get_service),
) -> list[VehicleResponse]:
    return service.list_vehicles(limit=limit, offset=offset)


@router.get("/vehicles/{vehicle_id}", response_model=VehicleResponse)
def get_vehicle(vehicle_id: str, service: TMSService = Depends(get_service)) -> VehicleResponse:
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
    vehicle_id: str,
    request: VehicleUpdate,
    _: Principal = Depends(require_admin),
    service: TMSService = Depends(get_service),
) -> VehicleResponse:
    return service.update_vehicle(vehicle_id, request)


@router.get("/shipments", response_model=list[ShipmentResponse])
def list_shipments(
    driver_id: str | None = None,
    destination_facility_id: str | None = None,
    shipment_status: ShipmentStatus | None = Query(default=None, alias="status"),
    unassigned_only: bool = False,
    include_archived: bool = False,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: TMSService = Depends(get_service),
) -> list[ShipmentResponse]:
    return service.list_shipments(
        driver_id=driver_id,
        destination_facility_id=destination_facility_id,
        status=shipment_status,
        unassigned_only=unassigned_only,
        include_archived=include_archived,
        limit=limit,
        offset=offset,
    )


@router.get("/shipments/{shipment_id}", response_model=ShipmentResponse)
def get_shipment(
    shipment_id: str, service: TMSService = Depends(get_service)
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
    shipment_id: str,
    request: ShipmentUpdate,
    _: Principal = Depends(require_admin),
    service: TMSService = Depends(get_service),
) -> ShipmentResponse:
    return service.update_shipment(shipment_id, request)


class AssignShipmentRequest(BaseModel):
    driver_id: str = Field(min_length=1)
    vehicle_id: str | None = None


@router.post("/shipments/{shipment_id}/assign", response_model=ShipmentResponse)
def assign_shipment(
    shipment_id: str,
    request: AssignShipmentRequest,
    _: Principal = Depends(require_admin),
    service: TMSService = Depends(get_service),
) -> ShipmentResponse:
    return service.assign_shipment(shipment_id, driver_id=request.driver_id, vehicle_id=request.vehicle_id)


@router.post("/shipments/{shipment_id}/archive", response_model=ShipmentResponse)
def archive_shipment(
    shipment_id: str,
    _: Principal = Depends(require_admin),
    service: TMSService = Depends(get_service),
) -> ShipmentResponse:
    return service.archive_shipment(shipment_id)


@router.post("/shipments/{shipment_id}/unarchive", response_model=ShipmentResponse)
def unarchive_shipment(
    shipment_id: str,
    _: Principal = Depends(require_admin),
    service: TMSService = Depends(get_service),
) -> ShipmentResponse:
    return service.unarchive_shipment(shipment_id)


@router.get("/facilities", response_model=list[FacilityResponse])
def list_facilities(
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: TMSService = Depends(get_service),
) -> list[FacilityResponse]:
    return service.list_facilities(limit=limit, offset=offset)


@router.get("/reference/shipment-options", response_model=ShipmentReferenceData)
def get_shipment_reference_data(service: TMSService = Depends(get_service)) -> ShipmentReferenceData:
    """Real, already-used origin/product-category values to back the
    shipment-creation dropdowns -- see ShipmentReferenceData's docstring."""
    return service.shipment_reference_data()


@router.post("/facility-staff/register", response_model=FacilityStaffResponse, status_code=status.HTTP_201_CREATED)
def register_facility_staff(
    request: FacilityStaffRegisterRequest,
    principal: Principal = Depends(require_reader),
    service: TMSService = Depends(get_service),
) -> FacilityStaffResponse:
    """Register (or update) the CALLER's own facility assignment.

    staff_user_id always comes from the verified bearer token
    (principal.user_id), never from the request body -- a caller can only
    ever register themselves, not another account.
    """
    return service.register_staff_facility(principal.user_id, request.facility_id)


@router.get("/facility-staff/me", response_model=FacilityStaffResponse)
def get_my_facility_staff(
    principal: Principal = Depends(require_reader),
    service: TMSService = Depends(get_service),
) -> FacilityStaffResponse:
    return service.require_staff_facility(principal.user_id)


@router.get("/facility-staff/shipments", response_model=list[ShipmentResponse])
def list_shipments_for_my_facility(
    shipment_status: ShipmentStatus | None = Query(default=None, alias="status"),
    unassigned_only: bool = False,
    include_archived: bool = False,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    principal: Principal = Depends(require_reader),
    service: TMSService = Depends(get_service),
) -> list[ShipmentResponse]:
    """Shipments scoped to the CALLER's own registered facility only --
    the facility filter is resolved server-side from staff_facility_assignments,
    never accepted as a client-supplied parameter here."""
    return service.list_shipments_for_staff_facility(
        principal.user_id,
        status=shipment_status,
        unassigned_only=unassigned_only,
        include_archived=include_archived,
        limit=limit,
        offset=offset,
    )


@router.get("/context/drivers/{driver_id}", response_model=DriverContextResponse)
def get_driver_context(
    driver_id: str, service: TMSService = Depends(get_service)
) -> DriverContextResponse:
    return service.driver_context(driver_id)


@router.get("/context/shipments/{shipment_id}", response_model=ShipmentContextResponse)
def get_shipment_context(
    shipment_id: str, service: TMSService = Depends(get_service)
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
