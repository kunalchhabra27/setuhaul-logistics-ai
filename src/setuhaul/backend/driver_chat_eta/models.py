"""Pydantic domain and API models for the driver chat & ETA backend.

Field names, enums, and key types mirror the ACTUAL Supabase schema the user
supplied (a live `information_schema` dump), not the earlier local-dev
reconstruction migration -- that file said outright it "is not an
authoritative snapshot." Notable shape differences from a typical Postgres
app schema:

- Every primary/foreign key is a plain ``text`` column, not ``uuid``, and
  none of them have a DB-side default -- IDs are app-generated (see
  ``_new_id`` in service.py), matching the human-readable style used
  throughout the challenge ("DRV-027", "SHP-1042", "EXC-778", ...).
- Enum-like columns are plain ``text`` with a CHECK constraint, and the
  allowed values are UPPER_SNAKE_CASE.
- Boolean-ish columns (``active_flag``, ``refrigeration_capable``, ...) are
  ``integer`` restricted to 0/1, not a real ``boolean`` type.
- ``drivers`` has no ``email`` column and no column linking it to a
  Supabase Auth user -- this backend uses the driver's own Supabase Auth
  user id (a UUID) as the ``driver_id`` text value, giving a natural 1:1
  mapping without needing a new column.
- Timestamps are stored as ``text`` (ISO-8601 strings), not ``timestamptz``.
- Slot availability is tracked via a separate ``slot_holds`` table, not a
  ``held`` value on ``appointment_slots.slot_status`` (which only ever
  takes OPEN/BLOCKED/CLOSED).
- ``facility_checkins`` tracks discrete stage timestamps
  (gate_in_ts/yard_queue_enter_ts/dock_in_ts/unload_start_ts/unload_end_ts/
  gate_out_ts) rather than a single "arrival_status" field.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class APIModel(BaseModel):
    model_config = ConfigDict(extra="ignore", from_attributes=True)


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


class SlotStatus(str, Enum):
    OPEN = "OPEN"
    BLOCKED = "BLOCKED"
    CLOSED = "CLOSED"


class SlotHoldStatus(str, Enum):
    HELD = "HELD"
    EXPIRED = "EXPIRED"
    CONVERTED = "CONVERTED"
    RELEASED = "RELEASED"


class AppointmentStatus(str, Enum):
    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"
    CONFIRMED = "CONFIRMED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    NO_SHOW = "NO_SHOW"
    REJECTED = "REJECTED"


class BookingSource(str, Enum):
    PLANNER = "PLANNER"
    DRIVER_CHAT = "DRIVER_CHAT"
    WAREHOUSE = "WAREHOUSE"
    SCHEDULING_TOOL = "SCHEDULING_TOOL"
    MANUAL_OVERRIDE = "MANUAL_OVERRIDE"


class ArrivalState(str, Enum):
    EARLY = "EARLY"
    ON_TIME = "ON_TIME"
    LATE = "LATE"
    NO_SHOW = "NO_SHOW"


class QueueState(str, Enum):
    NOT_QUEUED = "NOT_QUEUED"
    WAITING_EARLY = "WAITING_EARLY"
    WAITING_LATE = "WAITING_LATE"
    WAITING_DOCK_UNAVAILABLE = "WAITING_DOCK_UNAVAILABLE"
    CALLED_TO_DOCK = "CALLED_TO_DOCK"
    IN_DOCK = "IN_DOCK"
    COMPLETED = "COMPLETED"


class ThreadStatus(str, Enum):
    OPEN = "OPEN"
    WAITING_FOR_DRIVER = "WAITING_FOR_DRIVER"
    WAITING_FOR_WAREHOUSE = "WAITING_FOR_WAREHOUSE"
    RESOLVED = "RESOLVED"
    ESCALATED = "ESCALATED"
    CLOSED = "CLOSED"


class ThreadIntent(str, Enum):
    REPORT_DELAY = "REPORT_DELAY"
    ASK_SLOT_OPTIONS = "ASK_SLOT_OPTIONS"
    CHECK_STATUS = "CHECK_STATUS"
    EARLY_ARRIVAL = "EARLY_ARRIVAL"
    GENERAL_QUESTION = "GENERAL_QUESTION"
    UNKNOWN = "UNKNOWN"


class MessageSenderType(str, Enum):
    DRIVER = "DRIVER"
    AGENT = "AGENT"
    OPERATIONS = "OPERATIONS"
    WAREHOUSE = "WAREHOUSE"
    SYSTEM = "SYSTEM"


class ExceptionType(str, Enum):
    DELAY = "DELAY"
    BREAKDOWN = "BREAKDOWN"
    TRAFFIC = "TRAFFIC"
    WEATHER = "WEATHER"
    EARLY_ARRIVAL = "EARLY_ARRIVAL"
    DOCK_UNAVAILABLE = "DOCK_UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


class SeverityCode(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ExceptionStatus(str, Enum):
    OPEN = "OPEN"
    NEEDS_INFORMATION = "NEEDS_INFORMATION"
    SLOT_OPTIONS_SHARED = "SLOT_OPTIONS_SHARED"
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    RESOLVED = "RESOLVED"
    ESCALATED = "ESCALATED"
    DUPLICATE = "DUPLICATE"
    CANCELLED = "CANCELLED"


# --------------------------------------------------------------------------
# Driver profile (self-service, scoped to the authenticated Supabase user)
# --------------------------------------------------------------------------


class ProfileCompleteRequest(BaseModel):
    """Payload submitted once a driver's email is verified via Supabase Auth.

    carrier_id is picked from a dropdown of existing carriers (see
    CarrierSummary / GET /driver-chat-eta/carriers) rather than typed as free
    text -- a driver can no longer silently create a brand-new carrier record
    with a random id just by mistyping a name.
    """

    driver_name: str = Field(min_length=1)
    phone: str = Field(min_length=1)
    licence_number: str = Field(min_length=1)
    home_base_city: str = Field(min_length=1)
    carrier_id: str = Field(min_length=1)


class CarrierSummary(APIModel):
    carrier_id: str
    carrier_name: str | None = None
    has_active_vehicle: bool = True


class DriverProfile(APIModel):
    driver_id: str
    carrier_id: str | None = None
    driver_name: str | None = None
    phone: str | None = None
    licence_number: str | None = None
    home_base_city: str | None = None
    driver_status: DriverStatus | None = None


# --------------------------------------------------------------------------
# Reference / context data
# --------------------------------------------------------------------------


class VehicleSummary(APIModel):
    vehicle_id: str
    carrier_id: str | None = None
    vehicle_type_code: str | None = None
    registration_number: str | None = None
    capacity_kg: int | None = None
    refrigeration_capable: bool | None = None
    active_flag: bool | None = None


class FacilitySummary(APIModel):
    facility_id: str
    facility_name: str | None = None
    city: str | None = None
    state: str | None = None
    timezone: str | None = None
    open_time: str | None = None
    close_time: str | None = None
    checkin_grace_min: int | None = None
    default_unload_min: int | None = None


class DockSummary(APIModel):
    dock_id: str
    facility_id: str
    dock_code: str | None = None
    dock_type: str | None = None
    supports_refrigerated: bool | None = None
    max_vehicle_weight_kg: int | None = None
    dock_status: str | None = None


class ShipmentSummary(APIModel):
    shipment_id: str
    order_reference: str | None = None
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


class AppointmentSlotSummary(APIModel):
    slot_id: str
    facility_id: str | None = None
    dock_id: str | None = None
    slot_start_ts: str | None = None
    slot_end_ts: str | None = None
    slot_status: SlotStatus | None = None


class AppointmentSummary(APIModel):
    appointment_id: str
    shipment_id: str | None = None
    slot_id: str | None = None
    appointment_status: AppointmentStatus | None = None
    booking_source: BookingSource | None = None
    booked_at: str | None = None
    confirmed_at: str | None = None
    cancelled_at: str | None = None
    # Sourced from DockSchedulerRepository.current_appointment()'s slot/dock
    # join (not present on the raw appointments row) so the driver UI can
    # show a real "confirmed at Dock D1, 11:00-12:00" banner instead of just
    # an opaque slot id.
    dock_code: str | None = None
    slot_start_ts: str | None = None
    slot_end_ts: str | None = None


class FacilityCheckinSummary(APIModel):
    checkin_id: str
    shipment_id: str | None = None
    facility_id: str | None = None
    gate_in_ts: str | None = None
    yard_queue_enter_ts: str | None = None
    dock_in_ts: str | None = None
    unload_start_ts: str | None = None
    unload_end_ts: str | None = None
    gate_out_ts: str | None = None
    arrival_state: ArrivalState | None = None
    queue_state: QueueState | None = None
    actual_dock_id: str | None = None
    notes: str | None = None


class DriverExceptionSummary(APIModel):
    exception_id: str
    shipment_id: str | None = None
    thread_id: str | None = None
    exception_type: ExceptionType | None = None
    reported_at: str | None = None
    reported_delay_min: int | None = None
    declared_eta_ts: str | None = None
    earliest_acceptable_ts: str | None = None
    latest_acceptable_ts: str | None = None
    severity_code: SeverityCode | None = None
    exception_status: ExceptionStatus | None = None
    description: str | None = None


class ChatMessageSummary(APIModel):
    chat_message_id: str
    thread_id: str | None = None
    sender_type: MessageSenderType | None = None
    sender_reference: str | None = None
    message_text: str | None = None
    message_ts: str | None = None


class SlotOption(BaseModel):
    """A dock slot candidate surfaced by the deterministic feasibility check."""

    slot_id: str
    dock_id: str
    dock_code: str | None = None
    start_time: datetime
    end_time: datetime
    is_compatible: bool
    compatibility_reason: str
    estimated_wait_minutes: int
    is_held: bool = False
    is_booked_by_me: bool = False
    # Purely "does this slot's time window fit the driver's currently
    # declared ETA/must-leave-by constraint" -- independent of
    # is_compatible, which also folds in whether the slot is *bookable*
    # right now (an already-booked-by-me slot is never "bookable" again,
    # so is_compatible is always False for it regardless of timing; this
    # field is what auto_book_earliest_feasible_slot actually needs to
    # decide whether an existing appointment is still usable).
    fits_declared_eta: bool = True


# --------------------------------------------------------------------------
# Requests
# --------------------------------------------------------------------------


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    conversation_id: str | None = None
    new_conversation: bool = False


class VoiceChatRequest(BaseModel):
    """A recorded voice note from the driver, to be transcribed then handled
    exactly like a typed chat message (see service.handle_voice_chat_message).
    """

    audio_base64: str = Field(min_length=1, max_length=20_000_000, description="Base64-encoded audio clip (no data: URL prefix).")
    mime_type: str = Field(default="audio/webm", description="MIME type of the clip, e.g. audio/webm, audio/mp4, audio/ogg.")
    conversation_id: str | None = None
    new_conversation: bool = False


class HoldSlotRequest(BaseModel):
    slot_id: str


class ConfirmSlotRequest(BaseModel):
    slot_id: str


class ArrivalUpdateChoice(str, Enum):
    ARRIVED_GATE = "arrived_gate"
    WAITING_YARD = "waiting_yard"
    DOCKED = "docked"
    COMPLETED = "completed"


class CheckinUpdateRequest(BaseModel):
    arrival_status: ArrivalUpdateChoice


class EscalateRequest(BaseModel):
    reason: str = Field(min_length=1, default="Driver requested manual coordinator support")


# --------------------------------------------------------------------------
# Responses
# --------------------------------------------------------------------------


class DriverSnapshot(BaseModel):
    """Everything the driver portal UI needs for the logged-in driver."""

    driver: DriverProfile
    vehicle: VehicleSummary | None = None
    shipment: ShipmentSummary | None = None
    facility: FacilitySummary | None = None
    docks: list[DockSummary] = Field(default_factory=list)
    appointment: AppointmentSummary | None = None
    checkin: FacilityCheckinSummary | None = None
    exception: DriverExceptionSummary | None = None
    slot_options: list[SlotOption] = Field(default_factory=list)
    chat_messages: list[ChatMessageSummary] = Field(default_factory=list)
    conversation_id: str | None = None


class ChatResponse(BaseModel):
    conversation_id: str | None = None
    agent_message: ChatMessageSummary
    suggested_options: list[SlotOption] = Field(default_factory=list)
    exception: DriverExceptionSummary | None = None
    snapshot: DriverSnapshot


class SlotActionResponse(BaseModel):
    slot: AppointmentSlotSummary
    snapshot: DriverSnapshot
    message: str


class ConfirmSlotResponse(BaseModel):
    appointment: AppointmentSummary
    snapshot: DriverSnapshot
    message: str


class CheckinResponse(BaseModel):
    checkin: FacilityCheckinSummary
    snapshot: DriverSnapshot


class EscalateResponse(BaseModel):
    exception: DriverExceptionSummary | None = None
    snapshot: DriverSnapshot
