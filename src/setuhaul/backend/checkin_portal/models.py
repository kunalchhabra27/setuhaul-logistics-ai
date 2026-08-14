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


class ApproveGateCheckinRequest(BaseModel):
    """Request payload for staff approving a driver-reported gate arrival."""

    shipment_id: str


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
