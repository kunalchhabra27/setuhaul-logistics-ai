"""Pydantic domain and API models for the Transportation Management System.

Field names mirror the real Supabase columns on public.drivers / public.vehicles /
public.shipments (verified live against the project on 2026-08-10) rather than the
earlier UUID/`planned_eta`-style schema this module was originally written against.
IDs on these tables are plain text (e.g. "DRV001", "SHP1001"), not UUIDs.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

try:
    from typing import Self
except ImportError:  # pragma: no cover - Python < 3.11 compatibility shim
    from typing_extensions import Self


class TMSRole(str, Enum):
    ADMIN_1 = "ADMIN_1"
    AGENT_READER = "AGENT_READER"


class DriverStatus(str, Enum):
    ACTIVE = "ACTIVE"
    OFF_DUTY = "OFF_DUTY"
    SUSPENDED = "SUSPENDED"
    INACTIVE = "INACTIVE"


class ShipmentStatus(str, Enum):
    PLANNED = "PLANNED"
    ASSIGNED = "ASSIGNED"
    IN_TRANSIT = "IN_TRANSIT"
    AT_GATE = "AT_GATE"
    WAITING = "WAITING"
    IN_DOCK = "IN_DOCK"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


ACTIVE_CONTEXT_STATUSES = frozenset(
    {
        ShipmentStatus.PLANNED,
        ShipmentStatus.ASSIGNED,
        ShipmentStatus.IN_TRANSIT,
        ShipmentStatus.AT_GATE,
        ShipmentStatus.WAITING,
        ShipmentStatus.IN_DOCK,
    }
)


class APIModel(BaseModel):
    """Base for request models -- rejects unknown fields."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class DBModel(BaseModel):
    """Base for models parsed straight from a Supabase row.

    Uses extra="ignore" rather than "forbid": repository queries select an
    explicit, known column list, but staying lenient here means an
    unexpected/extra column from Supabase degrades gracefully instead of
    500-ing every request.
    """

    model_config = ConfigDict(extra="ignore", from_attributes=True)


class NonEmptyPatch(APIModel):
    @model_validator(mode="after")
    def require_change(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("At least one field must be supplied.")
        return self


# --------------------------------------------------------------------------
# Drivers (public.drivers)
# --------------------------------------------------------------------------


class DriverCreate(APIModel):
    carrier_id: str = Field(min_length=1)
    driver_name: str = Field(min_length=1)
    phone: str = Field(min_length=1)
    licence_number: str | None = None
    home_base_city: str | None = None
    driver_status: DriverStatus = DriverStatus.ACTIVE


class DriverUpdate(NonEmptyPatch):
    carrier_id: str | None = Field(default=None, min_length=1)
    driver_name: str | None = Field(default=None, min_length=1)
    phone: str | None = Field(default=None, min_length=1)
    licence_number: str | None = None
    home_base_city: str | None = None
    driver_status: DriverStatus | None = None


class DriverResponse(DBModel):
    driver_id: str
    carrier_id: str | None = None
    driver_name: str | None = None
    phone: str | None = None
    licence_number: str | None = None
    home_base_city: str | None = None
    driver_status: DriverStatus | None = None

    @property
    def is_active(self) -> bool:
        return self.driver_status is DriverStatus.ACTIVE


# --------------------------------------------------------------------------
# Vehicles (public.vehicles)
# --------------------------------------------------------------------------


class VehicleCreate(APIModel):
    carrier_id: str = Field(min_length=1)
    registration_number: str = Field(min_length=1)
    vehicle_type_code: str = Field(min_length=1)
    capacity_kg: float | None = Field(default=None, gt=0)
    refrigeration_capable: bool = False
    active_flag: bool = True


class VehicleUpdate(NonEmptyPatch):
    carrier_id: str | None = Field(default=None, min_length=1)
    registration_number: str | None = Field(default=None, min_length=1)
    vehicle_type_code: str | None = Field(default=None, min_length=1)
    capacity_kg: float | None = Field(default=None, gt=0)
    refrigeration_capable: bool | None = None
    active_flag: bool | None = None


class VehicleResponse(DBModel):
    vehicle_id: str
    carrier_id: str | None = None
    registration_number: str | None = None
    vehicle_type_code: str | None = None
    capacity_kg: float | None = None
    refrigeration_capable: bool | None = None
    active_flag: bool | None = None


# --------------------------------------------------------------------------
# Shipments (public.shipments)
# --------------------------------------------------------------------------


class ShipmentCreate(APIModel):
    # Required fields here mirror the real Supabase table's NOT NULL columns
    # (verified against data/setuhaul_schema_and_seed.sql) -- these used to
    # be optional on this model even though Postgres would reject a NULL for
    # them, which meant a missing field surfaced as an opaque "database
    # operation failed" error instead of a clear 422 at the API boundary.
    shipment_id: str | None = Field(default=None, min_length=1)
    order_reference: str = Field(min_length=1)
    carrier_id: str = Field(min_length=1)
    driver_id: str = Field(min_length=1)
    vehicle_id: str = Field(min_length=1)
    origin_name: str = Field(min_length=1)
    origin_city: str = Field(min_length=1)
    destination_facility_id: str = Field(min_length=1)
    customer_name: str = Field(min_length=1)
    product_category: str = Field(min_length=1)
    load_weight_kg: int = Field(gt=0)
    required_dock_type: str = Field(default="STANDARD", min_length=1)
    temperature_control_required: bool = False
    priority_code: str | None = None
    planned_departure_ts: str
    original_eta_ts: str
    latest_eta_ts: str | None = None
    expected_unload_min: int = Field(gt=0)
    current_status: ShipmentStatus = ShipmentStatus.PLANNED
    # Note: no archived_flag here -- a shipment is never created pre-archived,
    # that only happens later via archive_shipment(). Keeping it off this model
    # also avoids sending a column that may not exist yet (see the
    # 20260811100000_tms_shipments_archive.sql migration).


class ShipmentUpdate(NonEmptyPatch):
    carrier_id: str | None = Field(default=None, min_length=1)
    driver_id: str | None = None
    vehicle_id: str | None = None
    origin_name: str | None = None
    origin_city: str | None = None
    destination_facility_id: str | None = Field(default=None, min_length=1)
    customer_name: str | None = None
    product_category: str | None = None
    load_weight_kg: int | None = Field(default=None, gt=0)
    required_dock_type: str | None = None
    temperature_control_required: bool | None = None
    priority_code: str | None = None
    planned_departure_ts: str | None = None
    original_eta_ts: str | None = None
    latest_eta_ts: str | None = None
    expected_unload_min: int | None = Field(default=None, gt=0)
    current_status: ShipmentStatus | None = None
    archived_flag: bool | None = None


class ShipmentResponse(DBModel):
    shipment_id: str
    order_reference: str | None = None
    carrier_id: str | None = None
    driver_id: str | None = None
    vehicle_id: str | None = None
    origin_name: str | None = None
    origin_city: str | None = None
    destination_facility_id: str | None = None
    customer_name: str | None = None
    product_category: str | None = None
    load_weight_kg: int | None = None
    required_dock_type: str | None = None
    temperature_control_required: bool | None = None
    priority_code: str | None = None
    planned_departure_ts: str | None = None
    original_eta_ts: str | None = None
    latest_eta_ts: str | None = None
    expected_unload_min: int | None = None
    current_status: ShipmentStatus | None = None
    archived_flag: bool | None = None
    created_at: str | None = None
    updated_at: str | None = None


class FacilityResponse(DBModel):
    facility_id: str
    facility_name: str | None = None
    city: str | None = None
    state: str | None = None


class ContextResolution(str, Enum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"


class DriverSummary(DBModel):
    driver_id: str
    driver_name: str | None = None
    carrier_id: str | None = None
    driver_status: DriverStatus | None = None


class VehicleContext(DBModel):
    vehicle_id: str
    registration_number: str | None = None
    vehicle_type_code: str | None = None
    refrigeration_capable: bool | None = None
    active_flag: bool | None = None


class ShipmentCandidate(DBModel):
    shipment_id: str
    vehicle: VehicleContext
    destination_facility_id: str | None = None
    product_category: str | None = None
    priority_code: str | None = None
    original_eta_ts: str | None = None
    expected_unload_min: int | None = None
    current_status: ShipmentStatus | None = None


class DriverContextResponse(DBModel):
    resolution: ContextResolution
    requires_disambiguation: bool
    driver: DriverSummary
    active_shipments: list[ShipmentCandidate]


class DockTraceSummary(DBModel):
    """Read-only WMS dock/appointment status for a shipment, so a dispatcher
    can trace a shipment from TMS without switching to the WMS portal.
    Sourced by directly reading dock_scheduler-owned tables (appointments /
    appointment_slots / docks) through the same caller-scoped client --
    consistent with how driver_chat_eta already reads across these same
    tables, rather than calling another backend's HTTP API."""

    appointment_id: str | None = None
    appointment_status: str | None = None
    dock_code: str | None = None
    slot_start_ts: str | None = None
    slot_end_ts: str | None = None


class CheckinTraceSummary(DBModel):
    """Read-only check-in portal status for a shipment, for the same tracing
    purpose as DockTraceSummary."""

    arrival_state: str | None = None
    queue_state: str | None = None
    gate_in_ts: str | None = None
    dock_in_ts: str | None = None
    unload_end_ts: str | None = None


class ShipmentContextResponse(DBModel):
    driver: DriverSummary
    vehicle: VehicleContext
    shipment: ShipmentResponse
    dock: DockTraceSummary | None = None
    checkin: CheckinTraceSummary | None = None


class OriginSummary(DBModel):
    origin_name: str
    origin_city: str | None = None


class ShipmentReferenceData(DBModel):
    """Backs the shipment-creation form's origin/product-category dropdowns
    with real, already-used values instead of free text -- see
    TMSRepository.list_shipment_reference_data for why these two specifically
    have no fixed lookup table to draw from."""

    origins: list[OriginSummary]
    product_categories: list[str]


class CancelShipmentRequest(APIModel):
    reason: str | None = None


class FacilityStaffRegisterRequest(APIModel):
    facility_id: str = Field(min_length=1)


class FacilityStaffResponse(DBModel):
    staff_user_id: str
    facility_id: str
    facility_name: str | None = None


class ErrorDetail(APIModel):
    code: str
    message: str
    details: list[dict[str, Any]] | None = None


class ErrorResponse(APIModel):
    error: ErrorDetail
