"""Pydantic models for the check-in portal domain."""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel


class ArrivalStatus(str, Enum):
    """Supported arrival states for a facility check-in."""

    GATE_IN = "GATE_IN"
    WAITING = "WAITING"
    DOCKED = "DOCKED"
    COMPLETED = "COMPLETED"


class QueueStatus(str, Enum):
    """Supported queue states while a shipment waits for dock access."""

    NONE = "NONE"
    GATE_QUEUE = "GATE_QUEUE"
    YARD_QUEUE = "YARD_QUEUE"
    CALLED_TO_DOCK = "CALLED_TO_DOCK"


class GateCheckInRequest(BaseModel):
    """Request payload for creating an initial gate-in check-in."""

    shipment_id: str
    facility_id: str
    gate_in_at: datetime


class QueueUpdateRequest(BaseModel):
    """Request payload for updating the queue status of a check-in."""

    shipment_id: str
    queue_status: QueueStatus


class DockInRequest(BaseModel):
    """Request payload for marking a shipment as docked."""

    shipment_id: str
    dock_in_at: datetime


class CompleteRequest(BaseModel):
    """Request payload for marking a shipment as completed."""

    shipment_id: str
    completed_at: datetime


class CheckInRecord(BaseModel):
    """Persisted representation of a facility check-in."""

    checkin_id: str
    shipment_id: str
    facility_id: str

    gate_in_at: Optional[datetime]
    arrival_status: ArrivalStatus
    queue_status: QueueStatus

    dock_in_at: Optional[datetime]
    completed_at: Optional[datetime]


class CheckInShipmentSummary(BaseModel):
    """Backend-owned read model for the Check-in portal shipment picker."""

    shipment_id: str
    order_reference: Optional[str] = None
    carrier_id: Optional[str] = None
    driver_id: Optional[str] = None
    driver_name: Optional[str] = None
    vehicle_id: Optional[str] = None
    registration_number: Optional[str] = None
    origin_name: Optional[str] = None
    origin_city: Optional[str] = None
    destination_facility_id: Optional[str] = None
    destination_facility_name: Optional[str] = None
    destination_facility_city: Optional[str] = None
    customer_name: Optional[str] = None
    product_category: Optional[str] = None
    load_weight_kg: Optional[int] = None
    required_dock_type: Optional[str] = None
    temperature_control_required: Optional[bool] = None
    priority_code: Optional[str] = None
    planned_departure_ts: Optional[str] = None
    original_eta_ts: Optional[str] = None
    latest_eta_ts: Optional[str] = None
    expected_unload_min: Optional[int] = None
    current_status: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    checkin: Optional[CheckInRecord] = None
    can_gate_in: bool
    can_queue: bool
    can_dock: bool
    can_complete: bool


class CheckInFacilityOption(BaseModel):
    """Active facility choice for Check-in registration and display."""

    facility_id: str
    facility_name: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    active_flag: Optional[bool] = None


class BackendSystem(str, Enum):
    """Backend systems that interact with the check-in portal."""

    TMS = "TMS"
    DOCK_SCHEDULER = "DOCK_SCHEDULER"
    DRIVER_CHAT_ETA = "DRIVER_CHAT_ETA"


class BackendRelationship(BaseModel):
    """High-level relationship between the check-in portal and another backend."""

    system: BackendSystem
    owns: list[str]
    consumes: list[str]
    notes: str
