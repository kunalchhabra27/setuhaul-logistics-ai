"""Deterministic business logic for the driver exception -> dock slot -> unload flow.

This mirrors the worked example in the challenge diagrams:
1. Driver reports an exception in chat.
2. Agent identifies context (driver, shipment, vehicle, destination facility).
3. Agent checks whether the original appointment is still feasible.
4. If not, the agent queries the dock scheduler for compatible open slots
   (``appointment_slots`` that are OPEN and not actively held by someone
   else -- holds live in the separate ``slot_holds`` table).
5. The agent presents feasible options (never auto-books).
6. The driver picks an option; the agent places a short atomic hold.
7. The driver confirms; the agent converts the hold into a CONFIRMED
   appointment and cancels whatever appointment was previously current.
8. Gate / yard / dock / exit check-ins are tracked as discrete timestamp
   columns on ``facility_checkins`` as the driver arrives.
9. If nothing is feasible, the thread is escalated to a human coordinator.

``handle_chat_message`` is a thin dispatcher: when ``GOOGLE_API_KEY`` is
configured, free-text driver messages are handled by the Gemini
tool-calling agent in ``driver_chat_eta.llm`` (see that package's module
docstring), which calls back into ``report_exception``,
``get_current_feasible_slots``, ``hold_slot``, ``confirm_slot``,
``update_checkin``, and ``escalate`` below -- the exact same deterministic,
RLS-scoped methods used by the REST endpoints the frontend's buttons call.
The LLM never talks to Supabase directly and never bypasses these methods'
validation. When no API key is configured, ``_handle_chat_message_regex``
below is used instead: a small auditable regex/rule layer that extracts a
delay in minutes and an optional "must leave by" time from the raw text.

IDs are app-generated (every primary key in this schema is ``text`` with no
DB-side default) and timestamps are written as naive ISO-8601 strings (UTC),
matching the convention already used by the seed/example data -- mixing
naive and timezone-aware strings in the same ``text`` column would break
lexicographic ordering and comparisons.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from uuid import uuid4

from setuhaul.backend.driver_chat_eta.auth import DriverPrincipal
from setuhaul.backend.driver_chat_eta.exceptions import (
    BusinessValidationError,
    DriverProfileNotFoundError,
    ShipmentNotFoundError,
    SlotConflictError,
    SlotNotFoundError,
)
from setuhaul.backend.driver_chat_eta.models import (
    AppointmentSlotSummary,
    AppointmentSummary,
    ChatMessageSummary,
    ChatResponse,
    CheckinResponse,
    CheckinUpdateRequest,
    ConfirmSlotResponse,
    DockSummary,
    DriverExceptionSummary,
    DriverProfile,
    DriverSnapshot,
    EscalateResponse,
    FacilityCheckinSummary,
    FacilitySummary,
    ProfileCompleteRequest,
    ShipmentSummary,
    SlotActionResponse,
    SlotOption,
    VehicleSummary,
)
from setuhaul.backend.driver_chat_eta.repository import DriverChatRepository

HOLD_MINUTES = 5
_DELAY_PATTERN = re.compile(r"(\d{1,3})\s*(?:min(?:ute)?s?)", re.IGNORECASE)
_HOUR_DELAY_PATTERN = re.compile(r"(\d{1,2})\s*(?:hour|hr)s?", re.IGNORECASE)
_LEAVE_BEFORE_PATTERN = re.compile(
    r"(?:before|leave by|out by)\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", re.IGNORECASE
)


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:8].upper()}"


def _now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat()


def _parse_dt(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.replace(tzinfo=None)


class DriverChatService:
    def __init__(self, repository: DriverChatRepository):
        self.repository = repository

    # -- profile ----------------------------------------------------------

    def complete_profile(self, principal: DriverPrincipal, request: ProfileCompleteRequest) -> DriverProfile:
        carrier_id = self.repository.find_or_create_carrier(request.carrier_name)
        payload = {
            "carrier_id": carrier_id,
            "driver_name": request.driver_name,
            "phone": request.phone,
            "licence_number": request.licence_number,
            "home_base_city": request.home_base_city,
            "driver_status": "ACTIVE",
        }
        row = self.repository.upsert_driver(principal.user_id, payload)
        return DriverProfile.model_validate(row)

    def get_my_profile(self, principal: DriverPrincipal) -> DriverProfile:
        row = self.repository.get_driver(principal.user_id)
        if row is None:
            raise DriverProfileNotFoundError(
                "No driver profile exists yet for this account. Complete your profile to continue."
            )
        return DriverProfile.model_validate(row)

    # -- snapshot -----------------------------------------------------------

    def snapshot(self, principal: DriverPrincipal) -> DriverSnapshot:
        driver = self.get_my_profile(principal)
        return self._build_snapshot(principal, driver)

    def _build_snapshot(self, principal: DriverPrincipal, driver: DriverProfile) -> DriverSnapshot:
        shipment_row = self.repository.get_active_shipment_for_driver(principal.user_id)
        shipment = ShipmentSummary.model_validate(shipment_row) if shipment_row else None

        vehicle_row = None
        facility_row = None
        docks: list[DockSummary] = []
        appointment = None
        checkin = None

        if shipment_row:
            if shipment_row.get("vehicle_id"):
                vehicle_row = self.repository.get_vehicle(shipment_row["vehicle_id"])
            if shipment_row.get("destination_facility_id"):
                facility_row = self.repository.get_facility(shipment_row["destination_facility_id"])
                docks = [
                    DockSummary.model_validate(row)
                    for row in self.repository.list_docks(shipment_row["destination_facility_id"])
                ]
            appt_row = self.repository.get_current_appointment_for_shipment(shipment_row["shipment_id"])
            appointment = AppointmentSummary.model_validate(appt_row) if appt_row else None
            checkin_row = self.repository.get_checkin_for_shipment(shipment_row["shipment_id"])
            checkin = FacilityCheckinSummary.model_validate(checkin_row) if checkin_row else None

        exception_row = self.repository.get_active_exception_for_driver(principal.user_id)
        exception = DriverExceptionSummary.model_validate(exception_row) if exception_row else None

        chat_messages: list[ChatMessageSummary] = []
        slot_options: list[SlotOption] = []
        if exception_row and exception_row.get("thread_id"):
            chat_messages = [
                ChatMessageSummary.model_validate(row)
                for row in self.repository.list_chat_messages(exception_row["thread_id"])
            ]
            if shipment_row and shipment_row.get("destination_facility_id"):
                slot_options = self._feasible_slots(
                    facility_id=shipment_row["destination_facility_id"],
                    vehicle_row=vehicle_row,
                    shipment_row=shipment_row,
                    after=exception_row.get("declared_eta_ts") or shipment_row.get("latest_eta_ts"),
                    max_leave_at=exception_row.get("latest_acceptable_ts"),
                )

        return DriverSnapshot(
            driver=driver,
            vehicle=VehicleSummary.model_validate(vehicle_row) if vehicle_row else None,
            shipment=shipment,
            facility=FacilitySummary.model_validate(facility_row) if facility_row else None,
            docks=docks,
            appointment=appointment,
            checkin=checkin,
            exception=exception,
            slot_options=slot_options,
            chat_messages=chat_messages,
        )

    # -- chat / exception intake --------------------------------------------

    def handle_voice_chat_message(self, principal: DriverPrincipal, audio_base64: str, mime_type: str) -> ChatResponse:
        """Transcribe a recorded voice note, then handle it exactly like a typed message.

        Voice messages require the LLM path -- there's no deterministic way
        to turn audio into text, so unlike ``handle_chat_message`` there is
        no regex fallback here. If GOOGLE_API_KEY isn't configured, or
        transcription fails outright, this raises a clear, typed error the
        API layer turns into a normal error response (not a 500) so the
        frontend can tell the driver to type instead.
        """
        from setuhaul.backend.driver_chat_eta.llm import agent as llm_agent

        if not llm_agent.is_configured():
            raise BusinessValidationError(
                "Voice messages need the AI assistant to be configured. Please type your message instead."
            )
        try:
            transcript = llm_agent.transcribe_audio(audio_base64, mime_type)
        except Exception as exc:  # noqa: BLE001 - surface as a typed, user-facing error
            raise BusinessValidationError(
                "Could not transcribe that voice message. Please try again or type instead."
            ) from exc
        if not transcript:
            raise BusinessValidationError("I couldn't hear anything in that recording. Please try again.")
        return self.handle_chat_message(principal, transcript)

    def handle_chat_message(self, principal: DriverPrincipal, text: str) -> ChatResponse:
        """Handle one free-text driver chat message.

        Dispatches to the Gemini tool-calling agent when GOOGLE_API_KEY is
        configured, otherwise falls back to the deterministic regex parser.
        The fallback also kicks in if the LLM stack is configured but fails
        to import or errors at runtime (e.g. the API is unreachable) --
        drivers should never see a hard failure just because the LLM layer
        had a bad moment.
        """
        try:
            from setuhaul.backend.driver_chat_eta.llm import agent as llm_agent

            if llm_agent.is_configured():
                return llm_agent.run_chat_turn(self, principal, text)
        except DriverChatError:
            raise
        except Exception:  # noqa: BLE001 - deliberate broad fallback boundary
            import logging

            logging.getLogger(__name__).exception(
                "driver_chat_eta: LLM chat agent failed, falling back to regex parser."
            )
        return self._handle_chat_message_regex(principal, text)

    def _handle_chat_message_regex(self, principal: DriverPrincipal, text: str) -> ChatResponse:
        driver = self.get_my_profile(principal)
        shipment_row = self.repository.get_active_shipment_for_driver(principal.user_id)
        if shipment_row is None:
            # Chat is always available in the UI, with or without a shipment,
            # so this must be a normal reply rather than a raised error --
            # mirrors llm.agent._no_shipment_reply for the LLM path. There is
            # no shipment, so no chat_threads row exists to persist to or
            # reload from -- the driver's message and this reply are
            # attached directly onto the returned snapshot (not written to
            # Supabase) so the frontend, which renders snapshot.chat_messages
            # verbatim, actually shows this exchange instead of it silently
            # vanishing.
            snapshot = self._build_snapshot(principal, driver)
            driver_msg = ChatMessageSummary(
                chat_message_id=_new_id("MSG"),
                thread_id=None,
                sender_type="DRIVER",
                sender_reference=principal.user_id,
                message_text=text,
                message_ts=_now_iso(),
            )
            reply = ChatMessageSummary(
                chat_message_id=_new_id("MSG"),
                thread_id=None,
                sender_type="AGENT",
                sender_reference=None,
                message_text=(
                    f"Hi {driver.driver_name or 'there'}, you don't have an active shipment assigned yet. "
                    "Once dispatch assigns you a load, I can help with delays, dock slots, and check-ins."
                ),
                message_ts=_now_iso(),
            )
            snapshot.chat_messages = [driver_msg, reply]
            return ChatResponse(agent_message=reply, suggested_options=[], exception=None, snapshot=snapshot)

        delay_minutes = self._parse_delay_minutes(text)

        planned_eta = _parse_dt(shipment_row.get("original_eta_ts")) or datetime.utcnow()
        declared_eta = planned_eta + timedelta(minutes=delay_minutes) if delay_minutes else planned_eta
        max_leave_dt = self._parse_leave_before(text, reference_date=declared_eta.date())

        # Record the driver-declared ETA in its own audit table, and reflect
        # it on the shipment so other consumers see the latest value.
        self.repository.insert_eta_update(
            {
                "eta_update_id": _new_id("ETA"),
                "shipment_id": shipment_row["shipment_id"],
                "source_type": "DRIVER_DECLARED",
                "reported_by_driver_id": principal.user_id,
                "declared_eta_ts": declared_eta.isoformat(),
                "confidence_code": "MEDIUM",
                "note": text,
                "created_at": _now_iso(),
            }
        )
        self.repository.update_shipment(shipment_row["shipment_id"], {"latest_eta_ts": declared_eta.isoformat()})

        thread_row = self.repository.get_open_thread_for_driver(principal.user_id)
        if thread_row is None:
            thread_row = self.repository.create_thread(
                {
                    "thread_id": _new_id("TH"),
                    "driver_id": principal.user_id,
                    "shipment_id": shipment_row["shipment_id"],
                    "opened_at": _now_iso(),
                    "thread_status": "OPEN",
                    "thread_intent": "REPORT_DELAY",
                }
            )

        exception_row = self.repository.get_active_exception_for_driver(principal.user_id)
        severity = "HIGH" if delay_minutes >= 60 else "MEDIUM" if delay_minutes else "LOW"
        if exception_row is None:
            exception_row = self.repository.create_exception(
                {
                    "exception_id": _new_id("EXC"),
                    "shipment_id": shipment_row["shipment_id"],
                    "driver_id": principal.user_id,
                    "thread_id": thread_row["thread_id"],
                    "exception_type": "DELAY",
                    "reported_at": _now_iso(),
                    "reported_delay_min": delay_minutes or None,
                    "declared_eta_ts": declared_eta.isoformat(),
                    "latest_acceptable_ts": max_leave_dt.isoformat() if max_leave_dt else None,
                    "severity_code": severity,
                    "exception_status": "OPEN",
                    "description": text,
                }
            )
        else:
            update_payload: dict = {"exception_status": "NEEDS_INFORMATION"}
            if delay_minutes:
                update_payload["reported_delay_min"] = delay_minutes
                update_payload["declared_eta_ts"] = declared_eta.isoformat()
            if max_leave_dt:
                update_payload["latest_acceptable_ts"] = max_leave_dt.isoformat()
            exception_row = self.repository.update_exception(exception_row["exception_id"], update_payload) or exception_row

        self.repository.insert_chat_message(
            {
                "chat_message_id": _new_id("MSG"),
                "thread_id": thread_row["thread_id"],
                "sender_type": "DRIVER",
                "sender_reference": principal.user_id,
                "message_text": text,
                "message_ts": _now_iso(),
            }
        )

        vehicle_row = self.repository.get_vehicle(shipment_row["vehicle_id"]) if shipment_row.get("vehicle_id") else None
        facility_row = (
            self.repository.get_facility(shipment_row["destination_facility_id"])
            if shipment_row.get("destination_facility_id")
            else None
        )

        options: list[SlotOption] = []
        if shipment_row.get("destination_facility_id"):
            options = self._feasible_slots(
                facility_id=shipment_row["destination_facility_id"],
                vehicle_row=vehicle_row,
                shipment_row=shipment_row,
                after=declared_eta.isoformat(),
                max_leave_at=max_leave_dt.isoformat() if max_leave_dt else None,
            )

        agent_text = self._compose_agent_reply(
            driver_name=driver.driver_name,
            facility_name=facility_row.get("facility_name") if facility_row else None,
            declared_eta=declared_eta,
            options=options,
        )

        compatible = [opt for opt in options if opt.is_compatible]
        if compatible:
            self.repository.update_exception(exception_row["exception_id"], {"exception_status": "SLOT_OPTIONS_SHARED"})
            exception_row["exception_status"] = "SLOT_OPTIONS_SHARED"
        else:
            self.repository.update_exception(exception_row["exception_id"], {"exception_status": "ESCALATED"})
            self.repository.update_thread(thread_row["thread_id"], {"thread_status": "ESCALATED"})
            exception_row["exception_status"] = "ESCALATED"

        agent_row = self.repository.insert_chat_message(
            {
                "chat_message_id": _new_id("MSG"),
                "thread_id": thread_row["thread_id"],
                "sender_type": "AGENT",
                "message_text": agent_text,
                "message_ts": _now_iso(),
            }
        )

        snapshot = self._build_snapshot(principal, driver)
        return ChatResponse(
            agent_message=ChatMessageSummary.model_validate(agent_row),
            suggested_options=options,
            exception=DriverExceptionSummary.model_validate(exception_row),
            snapshot=snapshot,
        )

    @staticmethod
    def _parse_delay_minutes(text: str) -> int:
        delay_minutes = 0
        hour_match = _HOUR_DELAY_PATTERN.search(text)
        if hour_match:
            delay_minutes += int(hour_match.group(1)) * 60
        minute_match = _DELAY_PATTERN.search(text)
        if minute_match:
            delay_minutes += int(minute_match.group(1))
        return delay_minutes

    @staticmethod
    def _parse_leave_before(text: str, *, reference_date) -> datetime | None:
        match = _LEAVE_BEFORE_PATTERN.search(text)
        if not match:
            return None
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        meridiem = (match.group(3) or "").lower()
        if meridiem == "pm" and hour != 12:
            hour += 12
        elif meridiem == "am" and hour == 12:
            hour = 0
        try:
            return datetime.combine(reference_date, datetime.min.time()).replace(hour=hour, minute=minute)
        except ValueError:
            return None

    def _feasible_slots(
        self,
        *,
        facility_id: str,
        vehicle_row: dict | None,
        shipment_row: dict,
        after: str | datetime | None,
        max_leave_at: str | datetime | None = None,
    ) -> list[SlotOption]:
        after_dt = _parse_dt(after) or datetime.utcnow()
        max_leave_dt = _parse_dt(max_leave_at)

        docks = {row["dock_id"]: row for row in self.repository.list_docks(facility_id)}
        slots = self.repository.list_open_slots(facility_id)
        holds = self.repository.list_active_holds_for_facility_slots([s["slot_id"] for s in slots])
        now = datetime.utcnow()
        active_holds_by_slot: dict[str, dict] = {}
        for hold in holds:
            expires_at = _parse_dt(hold.get("expires_at"))
            if expires_at is not None and expires_at < now:
                continue  # expired hold -- treat the slot as free
            active_holds_by_slot[hold["slot_id"]] = hold

        required_dock_type = shipment_row.get("required_dock_type") or "ANY"
        needs_refrigeration = bool(shipment_row.get("temperature_control_required"))
        vehicle_capacity = (vehicle_row or {}).get("capacity_kg")

        options: list[SlotOption] = []
        for slot in slots:
            dock = docks.get(slot.get("dock_id"))
            if dock is None:
                continue

            dock_type_ok = required_dock_type == "ANY" or dock.get("dock_type") == required_dock_type
            temp_ok = not needs_refrigeration or bool(dock.get("supports_refrigerated"))
            weight_ok = (
                vehicle_capacity is None
                or dock.get("max_vehicle_weight_kg") is None
                or dock["max_vehicle_weight_kg"] >= vehicle_capacity
            )

            start = _parse_dt(slot.get("slot_start_ts"))
            end = _parse_dt(slot.get("slot_end_ts"))
            if start is None or end is None:
                continue

            is_after_eta = start >= after_dt - timedelta(minutes=15)
            within_leave_constraint = max_leave_dt is None or end <= max_leave_dt

            hold = active_holds_by_slot.get(slot["slot_id"])
            held_by_me = hold is not None and hold.get("shipment_id") == shipment_row.get("shipment_id")
            held_by_other = hold is not None and not held_by_me

            is_compatible = bool(
                dock_type_ok and temp_ok and weight_ok and is_after_eta and within_leave_constraint and not held_by_other
            )
            if held_by_other:
                reason = "Currently held by another shipment"
            elif not dock_type_ok:
                reason = "Dock type does not match this shipment's requirement"
            elif not temp_ok:
                reason = "Dock does not support temperature-controlled freight"
            elif not weight_ok:
                reason = "Dock weight limit is below this vehicle's capacity"
            elif not is_after_eta:
                reason = "Starts before the declared ETA"
            elif not within_leave_constraint:
                reason = "Ends after the driver's must-leave-by constraint"
            else:
                reason = "Fits ETA, dock type, and vehicle compatibility"

            options.append(
                SlotOption(
                    slot_id=slot["slot_id"],
                    dock_id=slot["dock_id"],
                    dock_code=dock.get("dock_code"),
                    start_time=start,
                    end_time=end,
                    is_compatible=is_compatible,
                    compatibility_reason=reason,
                    estimated_wait_minutes=max(0, int((start - after_dt).total_seconds() // 60)),
                    is_held=held_by_me,
                )
            )

        options.sort(key=lambda opt: (not opt.is_compatible, opt.start_time))
        return options[:5]

    # -- structured entry points for the LLM tool-calling agent -------------
    #
    # These mirror the exception-intake and feasibility-check steps in
    # ``_handle_chat_message_regex`` above, but take already-structured
    # arguments (extracted by the LLM from free text) instead of parsing raw
    # text with regexes, and return plain JSON-serializable dicts sized for
    # a tool result instead of API response models. They are also usable
    # directly by anything else that wants the same deterministic behavior
    # without going through chat at all.

    def get_current_feasible_slots(self, principal: DriverPrincipal) -> list[SlotOption]:
        """Return the currently compatible open dock slots for the driver's active shipment."""
        shipment_row = self.repository.get_active_shipment_for_driver(principal.user_id)
        if shipment_row is None:
            raise ShipmentNotFoundError("No active shipment is assigned to you.")
        if not shipment_row.get("destination_facility_id"):
            return []

        vehicle_row = (
            self.repository.get_vehicle(shipment_row["vehicle_id"]) if shipment_row.get("vehicle_id") else None
        )
        exception_row = self.repository.get_active_exception_for_driver(principal.user_id)
        after = (exception_row or {}).get("declared_eta_ts") or shipment_row.get("latest_eta_ts")
        max_leave_at = (exception_row or {}).get("latest_acceptable_ts")

        return self._feasible_slots(
            facility_id=shipment_row["destination_facility_id"],
            vehicle_row=vehicle_row,
            shipment_row=shipment_row,
            after=after,
            max_leave_at=max_leave_at,
        )

    def report_exception(
        self,
        principal: DriverPrincipal,
        *,
        delay_minutes: int = 0,
        declared_eta_iso: str | None = None,
        must_leave_by_iso: str | None = None,
        note: str = "",
    ) -> dict:
        """Record a driver-declared delay/ETA change and return feasible slots.

        Called by the LLM tool layer once it has extracted structured
        fields from the driver's free-text message (see
        ``driver_chat_eta.llm.tools``). Performs the exact same audit
        writes and exception-status transitions as the regex flow: an
        ``eta_updates`` row, updates ``shipments.latest_eta_ts``, opens or
        updates a ``driver_exceptions``/``chat_threads`` pair, and marks the
        exception SLOT_OPTIONS_SHARED or ESCALATED depending on whether any
        compatible slot was found.
        """
        self.get_my_profile(principal)  # raises DriverProfileNotFoundError if missing
        shipment_row = self.repository.get_active_shipment_for_driver(principal.user_id)
        if shipment_row is None:
            raise ShipmentNotFoundError(
                "No active shipment is assigned to you yet. Dispatch will assign a load before you can "
                "report an exception."
            )

        planned_eta = _parse_dt(shipment_row.get("original_eta_ts")) or datetime.utcnow()
        if declared_eta_iso and _parse_dt(declared_eta_iso) is not None:
            declared_eta = _parse_dt(declared_eta_iso)
        else:
            declared_eta = planned_eta + timedelta(minutes=max(delay_minutes, 0))
        max_leave_dt = _parse_dt(must_leave_by_iso)

        self.repository.insert_eta_update(
            {
                "eta_update_id": _new_id("ETA"),
                "shipment_id": shipment_row["shipment_id"],
                "source_type": "DRIVER_DECLARED",
                "reported_by_driver_id": principal.user_id,
                "declared_eta_ts": declared_eta.isoformat(),
                "confidence_code": "MEDIUM",
                "note": note or "Reported via chat",
                "created_at": _now_iso(),
            }
        )
        self.repository.update_shipment(shipment_row["shipment_id"], {"latest_eta_ts": declared_eta.isoformat()})

        thread_row = self.repository.get_open_thread_for_driver(principal.user_id)
        if thread_row is None:
            thread_row = self.repository.create_thread(
                {
                    "thread_id": _new_id("TH"),
                    "driver_id": principal.user_id,
                    "shipment_id": shipment_row["shipment_id"],
                    "opened_at": _now_iso(),
                    "thread_status": "OPEN",
                    "thread_intent": "REPORT_DELAY",
                }
            )

        exception_row = self.repository.get_active_exception_for_driver(principal.user_id)
        severity = "HIGH" if delay_minutes >= 60 else "MEDIUM" if delay_minutes else "LOW"
        if exception_row is None:
            exception_row = self.repository.create_exception(
                {
                    "exception_id": _new_id("EXC"),
                    "shipment_id": shipment_row["shipment_id"],
                    "driver_id": principal.user_id,
                    "thread_id": thread_row["thread_id"],
                    "exception_type": "DELAY",
                    "reported_at": _now_iso(),
                    "reported_delay_min": delay_minutes or None,
                    "declared_eta_ts": declared_eta.isoformat(),
                    "latest_acceptable_ts": max_leave_dt.isoformat() if max_leave_dt else None,
                    "severity_code": severity,
                    "exception_status": "OPEN",
                    "description": note or "Reported via chat",
                }
            )
        else:
            update_payload: dict = {"exception_status": "NEEDS_INFORMATION"}
            if delay_minutes or declared_eta_iso:
                update_payload["reported_delay_min"] = delay_minutes or exception_row.get("reported_delay_min")
                update_payload["declared_eta_ts"] = declared_eta.isoformat()
            if max_leave_dt:
                update_payload["latest_acceptable_ts"] = max_leave_dt.isoformat()
            exception_row = self.repository.update_exception(exception_row["exception_id"], update_payload) or exception_row

        vehicle_row = (
            self.repository.get_vehicle(shipment_row["vehicle_id"]) if shipment_row.get("vehicle_id") else None
        )
        facility_row = (
            self.repository.get_facility(shipment_row["destination_facility_id"])
            if shipment_row.get("destination_facility_id")
            else None
        )

        options: list[SlotOption] = []
        if shipment_row.get("destination_facility_id"):
            options = self._feasible_slots(
                facility_id=shipment_row["destination_facility_id"],
                vehicle_row=vehicle_row,
                shipment_row=shipment_row,
                after=declared_eta.isoformat(),
                max_leave_at=max_leave_dt.isoformat() if max_leave_dt else None,
            )

        compatible = [opt for opt in options if opt.is_compatible]
        new_status = "SLOT_OPTIONS_SHARED" if compatible else "ESCALATED"
        self.repository.update_exception(exception_row["exception_id"], {"exception_status": new_status})
        exception_row["exception_status"] = new_status
        if new_status == "ESCALATED":
            self.repository.update_thread(thread_row["thread_id"], {"thread_status": "ESCALATED"})

        return {
            "exception_id": exception_row["exception_id"],
            "thread_id": thread_row["thread_id"],
            "exception_status": new_status,
            "declared_eta": declared_eta.isoformat(),
            "must_leave_by": max_leave_dt.isoformat() if max_leave_dt else None,
            "facility_name": facility_row.get("facility_name") if facility_row else None,
            "feasible_slots": [
                {
                    "slot_id": opt.slot_id,
                    "dock_id": opt.dock_id,
                    "dock_code": opt.dock_code,
                    "start_time": opt.start_time.isoformat(),
                    "end_time": opt.end_time.isoformat(),
                    "is_compatible": opt.is_compatible,
                    "reason": opt.compatibility_reason,
                    "estimated_wait_minutes": opt.estimated_wait_minutes,
                    "is_held": opt.is_held,
                }
                for opt in options
            ],
        }

    @staticmethod
    def _compose_agent_reply(
        *,
        driver_name: str | None,
        facility_name: str | None,
        declared_eta: datetime | None,
        options: list[SlotOption],
    ) -> str:
        name = driver_name or "there"
        eta_str = declared_eta.strftime("%H:%M") if declared_eta else "your updated time"
        if not [opt for opt in options if opt.is_compatible]:
            return (
                f"Hi {name}, I've logged your exception and updated your ETA to {eta_str}. "
                f"I couldn't find a feasible dock slot at {facility_name or 'the destination facility'} that fits, "
                "so I'm escalating this thread to a human coordinator."
            )
        compatible = [opt for opt in options if opt.is_compatible]
        lines = [
            f"Hi {name}, I've logged your exception and set your ETA to {eta_str}.",
            f"Here are the best available dock slots at {facility_name or 'the destination facility'}:",
        ]
        for opt in compatible[:3]:
            lines.append(
                f"- {opt.dock_code or opt.dock_id}: {opt.start_time.strftime('%H:%M')}"
                f"-{opt.end_time.strftime('%H:%M')} (slot {opt.slot_id})"
            )
        lines.append("Reply to hold one of these, or ask me for other options.")
        return "\n".join(lines)

    # -- slot hold / confirm ------------------------------------------------

    def hold_slot(self, principal: DriverPrincipal, slot_id: str) -> SlotActionResponse:
        driver = self.get_my_profile(principal)
        shipment_row = self.repository.get_active_shipment_for_driver(principal.user_id)
        if shipment_row is None:
            raise ShipmentNotFoundError("No active shipment is assigned to you.")

        slot_row = self.repository.get_slot(slot_id)
        if slot_row is None:
            raise SlotNotFoundError(f"Slot {slot_id} was not found.")
        if slot_row.get("slot_status") != "OPEN":
            raise SlotConflictError("This slot is not open for booking.")

        now = datetime.utcnow()
        existing_holds = self.repository.list_active_holds_for_facility_slots([slot_id])
        for hold in existing_holds:
            expires_at = _parse_dt(hold.get("expires_at"))
            still_active = expires_at is None or expires_at >= now
            if still_active and hold.get("shipment_id") != shipment_row["shipment_id"]:
                raise SlotConflictError("This slot is already held by another driver.")

        # Only one active hold per shipment -- release whatever it held before.
        previous_hold = self.repository.get_active_hold_for_shipment(shipment_row["shipment_id"])
        if previous_hold and previous_hold.get("slot_id") != slot_id:
            self.repository.update_hold(previous_hold["hold_id"], {"hold_status": "RELEASED", "released_at": _now_iso()})

        if not (previous_hold and previous_hold.get("slot_id") == slot_id):
            self.repository.create_hold(
                {
                    "hold_id": _new_id("HOLD"),
                    "slot_id": slot_id,
                    "shipment_id": shipment_row["shipment_id"],
                    "hold_status": "HELD",
                    "held_at": _now_iso(),
                    "expires_at": (now + timedelta(minutes=HOLD_MINUTES)).isoformat(),
                }
            )

        exception_row = self.repository.get_active_exception_for_driver(principal.user_id)
        if exception_row:
            self.repository.update_exception(exception_row["exception_id"], {"exception_status": "WAITING_CONFIRMATION"})

        snapshot = self._build_snapshot(principal, driver)
        return SlotActionResponse(
            slot=AppointmentSlotSummary.model_validate(slot_row),
            snapshot=snapshot,
            message=f"Slot {slot_id} held for {HOLD_MINUTES} minutes.",
        )

    def confirm_slot(self, principal: DriverPrincipal, slot_id: str) -> ConfirmSlotResponse:
        driver = self.get_my_profile(principal)
        shipment_row = self.repository.get_active_shipment_for_driver(principal.user_id)
        if shipment_row is None:
            raise ShipmentNotFoundError("No active shipment is assigned to you.")

        hold = self.repository.get_active_hold_for_shipment(shipment_row["shipment_id"])
        if hold is None or hold.get("slot_id") != slot_id:
            raise SlotConflictError("This slot is not currently held by you, so it cannot be confirmed.")
        expires_at = _parse_dt(hold.get("expires_at"))
        if expires_at is not None and expires_at < datetime.utcnow():
            raise SlotConflictError("Your hold on this slot has expired -- hold it again before confirming.")

        self.repository.update_hold(hold["hold_id"], {"hold_status": "CONVERTED", "released_at": _now_iso()})

        old_appt = self.repository.get_current_appointment_for_shipment(shipment_row["shipment_id"])
        if old_appt:
            self.repository.update_appointment(
                old_appt["appointment_id"],
                {
                    "is_current": 0,
                    "appointment_status": "CANCELLED",
                    "cancelled_at": _now_iso(),
                    "cancellation_reason": "Rebooked via driver chat",
                },
            )

        appointment_row = self.repository.create_appointment(
            {
                "appointment_id": _new_id("APT"),
                "shipment_id": shipment_row["shipment_id"],
                "slot_id": slot_id,
                "appointment_status": "CONFIRMED",
                "booking_source": "DRIVER_CHAT",
                "is_current": 1,
                "booked_at": _now_iso(),
                "confirmed_at": _now_iso(),
            }
        )

        exception_row = self.repository.get_active_exception_for_driver(principal.user_id)
        if exception_row:
            self.repository.update_exception(exception_row["exception_id"], {"exception_status": "RESOLVED"})
            if exception_row.get("thread_id"):
                self.repository.update_thread(
                    exception_row["thread_id"], {"thread_status": "RESOLVED", "closed_at": _now_iso()}
                )
                self.repository.insert_chat_message(
                    {
                        "chat_message_id": _new_id("MSG"),
                        "thread_id": exception_row["thread_id"],
                        "sender_type": "SYSTEM",
                        "message_text": f"Appointment confirmed for slot {slot_id}. Driver notified.",
                        "message_ts": _now_iso(),
                    }
                )

        snapshot = self._build_snapshot(principal, driver)
        return ConfirmSlotResponse(
            appointment=AppointmentSummary.model_validate(appointment_row),
            snapshot=snapshot,
            message="Appointment confirmed and committed to the dock schedule.",
        )

    # -- gate / yard / dock / exit check-ins --------------------------------

    def update_checkin(self, principal: DriverPrincipal, request: CheckinUpdateRequest) -> CheckinResponse:
        driver = self.get_my_profile(principal)
        shipment_row = self.repository.get_active_shipment_for_driver(principal.user_id)
        if shipment_row is None:
            raise ShipmentNotFoundError("No active shipment is assigned to you.")

        now = _now_iso()
        existing = self.repository.get_checkin_for_shipment(shipment_row["shipment_id"])
        choice = request.arrival_status.value

        if choice == "arrived_gate":
            if existing is not None:
                raise BusinessValidationError("A gate check-in already exists for this shipment.")
            row = self.repository.create_checkin(
                {
                    "checkin_id": _new_id("CHK"),
                    "shipment_id": shipment_row["shipment_id"],
                    "facility_id": shipment_row.get("destination_facility_id"),
                    "gate_in_ts": now,
                    "arrival_state": "ON_TIME",
                    "queue_state": "WAITING_EARLY",
                    "updated_at": now,
                }
            )
        else:
            if existing is None:
                raise BusinessValidationError("A shipment must gate-in before any later check-in stage.")
            payload: dict = {"updated_at": now}
            if choice == "waiting_yard":
                payload["yard_queue_enter_ts"] = now
                payload["queue_state"] = "WAITING_EARLY"
            elif choice == "docked":
                appointment = self.repository.get_current_appointment_for_shipment(shipment_row["shipment_id"])
                dock_id = None
                if appointment and appointment.get("slot_id"):
                    slot = self.repository.get_slot(appointment["slot_id"])
                    dock_id = slot.get("dock_id") if slot else None
                payload["dock_in_ts"] = now
                payload["unload_start_ts"] = now
                payload["queue_state"] = "IN_DOCK"
                if dock_id:
                    payload["actual_dock_id"] = dock_id
            elif choice == "completed":
                payload["unload_end_ts"] = now
                payload["gate_out_ts"] = now
                payload["queue_state"] = "COMPLETED"
                self.repository.update_shipment(shipment_row["shipment_id"], {"current_status": "COMPLETED"})
            row = self.repository.update_checkin(existing["checkin_id"], payload) or existing

        snapshot = self._build_snapshot(principal, driver)
        return CheckinResponse(checkin=FacilityCheckinSummary.model_validate(row), snapshot=snapshot)

    # -- escalation -----------------------------------------------------

    def escalate(self, principal: DriverPrincipal, reason: str) -> EscalateResponse:
        driver = self.get_my_profile(principal)
        exception_row = self.repository.get_active_exception_for_driver(principal.user_id)
        if exception_row:
            exception_row = self.repository.update_exception(
                exception_row["exception_id"], {"exception_status": "ESCALATED"}
            ) or exception_row
            if exception_row.get("thread_id"):
                self.repository.update_thread(exception_row["thread_id"], {"thread_status": "ESCALATED"})
                self.repository.insert_chat_message(
                    {
                        "chat_message_id": _new_id("MSG"),
                        "thread_id": exception_row["thread_id"],
                        "sender_type": "OPERATIONS",
                        "message_text": f"Escalated to a human coordinator: {reason}",
                        "message_ts": _now_iso(),
                    }
                )

        snapshot = self._build_snapshot(principal, driver)
        return EscalateResponse(
            exception=DriverExceptionSummary.model_validate(exception_row) if exception_row else None,
            snapshot=snapshot,
        )
