"""Pydantic domain and API models for the Transportation Management System."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TMSRole(str, Enum):
    ADMIN_1 = "ADMIN_1"
    AGENT_READER = "AGENT_READER"


class DriverStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    SUSPENDED = "suspended"


class VehicleStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    MAINTENANCE = "maintenance"


class ShipmentStatus(str, Enum):
    PLANNED = "planned"
    IN_TRANSIT = "in_transit"
    ARRIVED = "arrived"
    WAITING = "waiting"
    UNLOADING = "unloading"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXCEPTION = "exception"


ACTIVE_CONTEXT_STATUSES = frozenset(
    {
        ShipmentStatus.PLANNED,
        ShipmentStatus.IN_TRANSIT,
        ShipmentStatus.ARRIVED,
        ShipmentStatus.WAITING,
        ShipmentStatus.UNLOADING,
        ShipmentStatus.EXCEPTION,
    }
)


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class NonEmptyPatch(APIModel):
    @model_validator(mode="after")
    def require_change(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("At least one field must be supplied.")
        return self


class DriverCreate(APIModel):
    carrier_id: UUID
    driver_code: str | None = Field(default=None, min_length=1)
    name: str = Field(min_length=1)
    phone: str = Field(min_length=1)
    email: str | None = None
    license_number: str | None = None
    license_expiry: date | None = None
    home_base: str | None = None
    active_flag: bool = True
    status: DriverStatus = DriverStatus.ACTIVE


class DriverUpdate(NonEmptyPatch):
    carrier_id: UUID | None = None
    driver_code: str | None = Field(default=None, min_length=1)
    name: str | None = Field(default=None, min_length=1)
    phone: str | None = Field(default=None, min_length=1)
    email: str | None = None
    license_number: str | None = None
    license_expiry: date | None = None
    home_base: str | None = None
    active_flag: bool | None = None
    status: DriverStatus | None = None


class DriverResponse(DriverCreate):
    driver_id: UUID
    created_at: datetime | None = None
    updated_at: datetime | None = None


class VehicleCreate(APIModel):
    carrier_id: UUID
    vehicle_number: str = Field(min_length=1)
    vehicle_type: str = Field(min_length=1)
    length_ft: float | None = Field(default=None, gt=0)
    capacity_weight_kg: float | None = Field(default=None, gt=0)
    refrigeration_required: bool = False
    active_flag: bool = True
    status: VehicleStatus = VehicleStatus.ACTIVE


class VehicleUpdate(NonEmptyPatch):
    carrier_id: UUID | None = None
    vehicle_number: str | None = Field(default=None, min_length=1)
    vehicle_type: str | None = Field(default=None, min_length=1)
    length_ft: float | None = Field(default=None, gt=0)
    capacity_weight_kg: float | None = Field(default=None, gt=0)
    refrigeration_required: bool | None = None
    active_flag: bool | None = None
    status: VehicleStatus | None = None


class VehicleResponse(VehicleCreate):
    vehicle_id: UUID
    created_at: datetime | None = None
    updated_at: datetime | None = None


def _validate_timezone(value: datetime | None) -> datetime | None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError("planned_eta must include a timezone offset")
    return value


class ShipmentCreate(APIModel):
    driver_id: UUID
    vehicle_id: UUID
    origin_id: UUID | None = None
    destination_id: UUID
    product_class: str = Field(min_length=1)
    priority: int = Field(gt=0)
    planned_eta: datetime | None = None
    expected_unload_minutes: int = Field(gt=0)
    status: ShipmentStatus = ShipmentStatus.PLANNED

    _timezone = field_validator("planned_eta")(_validate_timezone)


class ShipmentUpdate(NonEmptyPatch):
    driver_id: UUID | None = None
    vehicle_id: UUID | None = None
    origin_id: UUID | None = None
    destination_id: UUID | None = None
    product_class: str | None = Field(default=None, min_length=1)
    priority: int | None = Field(default=None, gt=0)
    planned_eta: datetime | None = None
    expected_unload_minutes: int | None = Field(default=None, gt=0)
    status: ShipmentStatus | None = None

    _timezone = field_validator("planned_eta")(_validate_timezone)


class ShipmentResponse(ShipmentCreate):
    shipment_id: UUID
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ContextResolution(str, Enum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"


class DriverSummary(APIModel):
    driver_id: UUID
    driver_code: str | None
    name: str
    carrier_id: UUID
    status: DriverStatus
    active_flag: bool


class VehicleContext(APIModel):
    vehicle_id: UUID
    vehicle_number: str
    vehicle_type: str
    length_ft: float | None
    refrigeration_required: bool
    status: VehicleStatus


class ShipmentCandidate(APIModel):
    shipment_id: UUID
    vehicle: VehicleContext
    origin_id: UUID | None
    destination_id: UUID
    product_class: str
    priority: int
    planned_eta: datetime | None
    expected_unload_minutes: int
    status: ShipmentStatus


class DriverContextResponse(APIModel):
    resolution: ContextResolution
    requires_disambiguation: bool
    driver: DriverSummary
    active_shipments: list[ShipmentCandidate]


class ShipmentContextResponse(APIModel):
    driver: DriverSummary
    vehicle: VehicleContext
    shipment: ShipmentResponse


class ErrorDetail(APIModel):
    code: str
    message: str
    details: list[dict[str, Any]] | None = None


class ErrorResponse(APIModel):
    error: ErrorDetail
