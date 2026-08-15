from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class SuggestionType(str, Enum):
    KEEP_ORIGINAL = "KEEP_ORIGINAL"
    ASSIGN_AVAILABLE = "ASSIGN_AVAILABLE"
    PRIORITY_SWAP = "PRIORITY_SWAP"


class SlotLifecycleStage(str, Enum):
    """Stages from viewing an option through confirmed booking."""

    PROPOSED = "PROPOSED"
    HELD = "HELD"
    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"
    CONFIRMED = "CONFIRMED"


@dataclass(frozen=True)
class DriverConstraints:
    earliest_start: datetime | None = None
    must_finish_by: datetime | None = None


@dataclass(frozen=True)
class SlotSuggestion:
    rank: int
    suggestion_type: SuggestionType
    slot_id: str
    dock_code: str
    start: datetime
    end: datetime
    reason: str
    lifecycle_stage: SlotLifecycleStage = SlotLifecycleStage.PROPOSED
    displaced_shipment_id: str | None = None
    displaced_to_slot_id: str | None = None


@dataclass(frozen=True)
class HoldResult:
    hold_id: str
    slot_id: str
    shipment_id: str
    expires_at: datetime
    lifecycle_stage: SlotLifecycleStage = SlotLifecycleStage.HELD


class SuggestRequest(BaseModel):
    shipment_id: str
    must_finish_by: datetime | None = None
    earliest_start: datetime | None = None
    limit: int = Field(default=3, ge=1, le=10)


class HoldRequest(BaseModel):
    shipment_id: str
    slot_id: str
    ttl_minutes: int = Field(default=15, ge=1, le=60)


class ConfirmRequest(BaseModel):
    shipment_id: str
    slot_id: str
    accepted: bool = True


class CancelHoldRequest(BaseModel):
    hold_id: str


class SlotSuggestionResponse(BaseModel):
    rank: int
    suggestion_type: SuggestionType
    slot_id: str
    dock_code: str
    start: datetime
    end: datetime
    reason: str
    lifecycle_stage: SlotLifecycleStage
    displaced_shipment_id: str | None = None
    displaced_to_slot_id: str | None = None

    @classmethod
    def from_suggestion(cls, item: SlotSuggestion) -> SlotSuggestionResponse:
        return cls(
            rank=item.rank,
            suggestion_type=item.suggestion_type,
            slot_id=item.slot_id,
            dock_code=item.dock_code,
            start=item.start,
            end=item.end,
            reason=item.reason,
            lifecycle_stage=item.lifecycle_stage,
            displaced_shipment_id=item.displaced_shipment_id,
            displaced_to_slot_id=item.displaced_to_slot_id,
        )


class HoldResponse(BaseModel):
    hold_id: str
    slot_id: str
    shipment_id: str
    expires_at: datetime
    lifecycle_stage: SlotLifecycleStage
    appointment_id: str | None = None

    @classmethod
    def from_hold(cls, item: HoldResult, appointment_id: str | None = None) -> HoldResponse:
        return cls(
            hold_id=item.hold_id,
            slot_id=item.slot_id,
            shipment_id=item.shipment_id,
            expires_at=item.expires_at,
            lifecycle_stage=item.lifecycle_stage,
            appointment_id=appointment_id,
        )


class ConfirmResponse(BaseModel):
    appointment_id: str
    shipment_id: str
    slot_id: str
    lifecycle_stage: SlotLifecycleStage


class ChangeRequestRole(str, Enum):
    TMS = "TMS"
    DRIVER = "DRIVER"


class ChangeRequestStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    DECLINED = "DECLINED"


class CreateChangeRequest(BaseModel):
    """Body for POST /change-requests -- the public TMS/driver-facing route.

    Deliberately has no displaced_shipment_id/displaced_to_slot_id field:
    those are only ever set internally, by DriverChatService.
    auto_book_earliest_feasible_slot calling DockSchedulerService.
    create_change_request(...) directly in Python using values computed by
    DeterministicReschedulingEngine's own priority-swap validation. Exposing
    them here would let any caller of this REST endpoint claim an arbitrary
    shipment should be displaced, with none of that validation.
    """

    shipment_id: str
    requested_slot_id: str
    requested_by_role: ChangeRequestRole
    reason: str | None = None


class DecideChangeRequest(BaseModel):
    approve: bool
    note: str | None = None


class ChangeRequestResponse(BaseModel):
    """A pending/decided request to move a shipment's dock appointment to a
    different slot. Enriched with the requested slot's dock/time so WMS
    staff can decide without a second lookup."""

    change_request_id: str
    shipment_id: str
    current_appointment_id: str | None = None
    requested_slot_id: str
    requested_by_role: str
    requested_by_user_id: str
    reason: str | None = None
    request_status: str
    created_at: str | None = None
    decided_at: str | None = None
    decided_by_user_id: str | None = None
    decision_note: str | None = None
    dock_code: str | None = None
    slot_start_ts: str | None = None
    slot_end_ts: str | None = None
    displaced_shipment_id: str | None = None
    displaced_to_slot_id: str | None = None


class DockSlot(BaseModel):
    """A single slot on the visual dock board -- every compatible slot for a
    shipment (not just the top-ranked ones from /suggest), for rendering a
    full per-dock schedule."""

    slot_id: str
    dock_code: str
    dock_type: str
    start: datetime
    end: datetime
    availability_status: str
    occupant_shipment_id: str | None = None
    occupant_driver_name: str | None = None


class DockBoardReasonResponse(BaseModel):
    """Why GET /dock-scheduler/board came back empty for a shipment -- see
    DockSchedulerService.dock_board_unavailable_reason. `reason` is None
    when nothing could be determined (caller should show a generic
    fallback message in that case)."""

    reason: str | None = None
