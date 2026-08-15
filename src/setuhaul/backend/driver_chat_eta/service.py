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

``handle_chat_message`` is a thin, three-way dispatcher (see its own
docstring for the full fallback order): when ``AGENTCORE_RUNTIME_ARN`` is
configured, free-text driver messages are routed to the AWS Bedrock
AgentCore-hosted LLM agent (``infrastructure.agentcore_client``, see
``agentcore_app/`` and ``DEPLOYMENT_PLAN.md``) -- the same
``driver_chat_eta.llm`` tool-calling loop, just running in its own
container. When that isn't configured (local dev), and ``GOOGLE_API_KEY``
is, the same agent runs in-process instead. Either way, the agent calls
back into ``report_exception``, ``get_current_feasible_slots``,
``hold_slot``, ``confirm_slot``, ``update_checkin``, and ``escalate`` below
-- the exact same deterministic, RLS-scoped methods used by the REST
endpoints the frontend's buttons call. The LLM never talks to Supabase
directly and never bypasses these methods' validation. When neither is
configured (or either fails), ``_handle_chat_message_regex`` below is used
instead: a small auditable regex/rule layer that extracts a delay in
minutes and an optional "must leave by" time from the raw text.

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

from setuhaul.backend.dock_scheduler.exceptions import (
    DockSchedulerError,
    InvalidBookingError as DockInvalidBookingError,
    SlotUnavailableError as DockSlotUnavailableError,
    UnknownShipmentError as DockUnknownShipmentError,
)
from setuhaul.backend.dock_scheduler.models import ChangeRequestRole, DriverConstraints, SuggestionType
from setuhaul.backend.dock_scheduler.repository import DockSchedulerRepository
from setuhaul.backend.dock_scheduler.service import DockSchedulerService
from setuhaul.backend.driver_chat_eta.auth import DriverPrincipal
from setuhaul.infrastructure.metrics import emit_domain_event, increment
from setuhaul.backend.driver_chat_eta.exceptions import (
    BusinessValidationError,
    DriverChatError,
    DriverProfileNotFoundError,
    PersistenceError,
    ShipmentNotFoundError,
    SlotConflictError,
    SlotNotFoundError,
)
from setuhaul.backend.driver_chat_eta.models import (
    AppointmentSlotSummary,
    AppointmentSummary,
    ChatMessageSummary,
    CarrierSummary,
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
from setuhaul.infrastructure.telemetry import operation_span

HOLD_MINUTES = 5
# Fixed emergency-dispatch contact number for safety-critical situations a
# driver reports mid-trip (engine failure, accident, medical emergency,
# hazmat spill) -- see flag_emergency_situation/send_emergency_alert below.
# This is deliberately a constant, not a per-facility/per-driver lookup: the
# FDE challenge scope only calls for one fixed emergency contact, reused via
# the existing Twilio helper (infrastructure/sms.py).
EMERGENCY_CONTACT_PHONE = "+919131394176"
# Cap on how many feasible slots get surfaced to the LLM per tool call (see
# report_exception below and llm/tools.py's list_feasible_dock_slots) -- kept
# short to avoid bloating the model's context/cost. The driver-facing
# snapshot/DockSlotBoard is unaffected; it always gets the full list from
# _feasible_slots().
LLM_SLOT_SUMMARY_LIMIT = 8
# This regex parser is the deterministic FALLBACK only -- the primary path
# is the LLM tool-calling agent (llm/agent.py), which understands Hindi and
# any other language natively since it's a real LLM. This layer only ever
# runs when HUGGINGFACEHUB_API_TOKEN isn't configured or the LLM call itself
# fails for some reason (see handle_chat_message's except Exception below).
# It was originally English-only, which meant a Hindi message falling back
# to this path silently parsed as "0 minutes late" with no delay detected
# at all. Devanagari digits are normalized to ASCII first (see
# _normalize_digits), and the unit words also match common Hindi delay
# vocabulary (मिनट = minutes, घंटा/घंटे = hour/hours) so a bare fallback
# turn still extracts a real delay instead of silently dropping it. This is
# still a partial mitigation, not full Hindi understanding -- word order,
# grammar, and phrases outside these patterns (e.g. "देर से पहुंचूंगा")
# still won't be understood by this layer; that's expected to be the LLM
# agent's job, so the real fix for full Hindi support is keeping that path
# reliable, not expanding this fallback further.
_DEVANAGARI_DIGIT_MAP = str.maketrans("०१२३४५६७८९", "0123456789")
_DELAY_PATTERN = re.compile(r"(\d{1,3})\s*(?:min(?:ute)?s?|मिनट)", re.IGNORECASE)
_HOUR_DELAY_PATTERN = re.compile(r"(\d{1,2})\s*(?:hour|hr|घंट[ेाों]*)s?", re.IGNORECASE)
_LEAVE_BEFORE_PATTERN = re.compile(
    r"(?:before|leave by|out by|तक)\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", re.IGNORECASE
)


def _normalize_digits(text: str) -> str:
    """Convert Devanagari numerals (०-९) to ASCII digits so the regex
    patterns above (which only match \\d) can find them. No-op for text
    that's already ASCII."""
    return text.translate(_DEVANAGARI_DIGIT_MAP)


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
        # Dock booking (feasibility, hold, confirm) goes through the same
        # validated WMS scheduling engine WMS staff use -- built from the
        # same caller-scoped Supabase client, so RLS is unchanged. This
        # removes what used to be a second, independently-drifting
        # implementation of dock_scheduler-owned mutations living only in
        # this chatbot; there is now one source of truth for
        # appointment_slots/slot_holds/appointments.
        self.dock_scheduler = DockSchedulerService(DockSchedulerRepository(repository.client))
        # Request-scoped memoization for _feasible_slots (see its own
        # docstring) -- a DriverChatService instance is built fresh per
        # HTTP request/chat turn (see api.get_service and
        # agentcore_app/main.py's entrypoint), so this dict's lifetime is
        # naturally scoped to one turn and never needs explicit clearing or
        # a TTL. Not a general-purpose cache and not shared across requests.
        self._feasible_slots_cache: dict[tuple, list[SlotOption]] = {}

    # -- profile ----------------------------------------------------------

    def list_carriers(self) -> list[CarrierSummary]:
        carriers_with_vehicles = self.repository.list_active_vehicle_carrier_ids()
        return [
            CarrierSummary(
                carrier_id=row["carrier_id"],
                carrier_name=row.get("carrier_name"),
                has_active_vehicle=row["carrier_id"] in carriers_with_vehicles,
            )
            for row in self.repository.list_carriers()
        ]

    def list_home_base_cities(self) -> list[str]:
        return self.repository.list_home_base_cities()

    def complete_profile(self, principal: DriverPrincipal, request: ProfileCompleteRequest) -> DriverProfile:
        if self.repository.get_carrier(request.carrier_id) is None:
            raise BusinessValidationError(f"Carrier {request.carrier_id} was not found. Choose one from the list.")
        payload = {
            "carrier_id": request.carrier_id,
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
        """Build the driver's snapshot, preferring the single-round-trip
        `driver_snapshot` RPC (see repository.get_driver_snapshot_bundle)
        over the ~7-9 sequential calls _build_snapshot_sequential makes.
        Falls back to the sequential path on ANY failure -- a missing
        migration (function not deployed yet), a malformed/unexpected
        bundle shape, or any other RPC/network hiccup -- so this is purely
        an optimization, never a new failure mode for /snapshot or a chat
        turn. Logged once per failure so a persistently-missing migration
        is visible without spamming on every call.
        """
        try:
            bundle = self.repository.get_driver_snapshot_bundle(principal.user_id)
            if bundle is None:
                raise PersistenceError("driver_snapshot RPC returned no data.")
            return self._build_snapshot_from_bundle(principal, driver, bundle)
        except Exception:  # noqa: BLE001 - deliberate broad fallback, see docstring above
            import logging

            logging.getLogger(__name__).warning(
                "driver_chat_eta: driver_snapshot RPC unavailable, falling back to sequential snapshot "
                "(has supabase/migrations/20260815120000_driver_snapshot_rpc.sql been applied?).",
                exc_info=True,
            )
            return self._build_snapshot_sequential(principal, driver)

    def _build_snapshot_from_bundle(
        self, principal: DriverPrincipal, driver: DriverProfile, bundle: dict
    ) -> DriverSnapshot:
        """Construct a DriverSnapshot from the driver_snapshot RPC's jsonb
        result -- same shape/semantics as _build_snapshot_sequential below,
        just sourced from one server-side query instead of many. Slot
        feasibility is deliberately NOT part of the bundle (see the
        migration's docstring) -- computed here exactly as the sequential
        path does, via the same defensively-wrapped _feasible_slots call.
        """
        shipment_row = bundle.get("shipment")
        shipment = ShipmentSummary.model_validate(shipment_row) if shipment_row else None

        vehicle_row = bundle.get("vehicle")
        facility_row = bundle.get("facility")
        docks = [DockSummary.model_validate(row) for row in bundle.get("docks") or []]
        appt_row = bundle.get("appointment")
        appointment = AppointmentSummary.model_validate(appt_row) if appt_row else None
        checkin_row = bundle.get("checkin")
        checkin = FacilityCheckinSummary.model_validate(checkin_row) if checkin_row else None

        exception_row = bundle.get("exception")
        exception = DriverExceptionSummary.model_validate(exception_row) if exception_row else None
        chat_messages = [ChatMessageSummary.model_validate(row) for row in bundle.get("chat_messages") or []]

        slot_options: list[SlotOption] = []
        if shipment_row and shipment_row.get("destination_facility_id"):
            try:
                slot_options = self._feasible_slots(
                    shipment_row=shipment_row,
                    after=(
                        (exception_row or {}).get("declared_eta_ts")
                        or shipment_row.get("latest_eta_ts")
                        or shipment_row.get("original_eta_ts")
                    ),
                    max_leave_at=(exception_row or {}).get("latest_acceptable_ts"),
                )
            except Exception:  # noqa: BLE001 - same defensive degrade-to-empty as the sequential path
                import logging

                logging.getLogger(__name__).exception(
                    "driver_chat_eta: failed to compute feasible slots for shipment %s; showing none.",
                    shipment_row.get("shipment_id"),
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

    def _build_snapshot_sequential(self, principal: DriverPrincipal, driver: DriverProfile) -> DriverSnapshot:
        """Original ~7-9-round-trip implementation -- kept as the fallback
        _build_snapshot uses when the driver_snapshot RPC isn't available.
        """
        shipment_row = self.repository.get_active_shipment_for_driver(principal.user_id)
        with operation_span("driver_chat.load_active_shipment", {"operation": "load_active_shipment"}):
            shipment_row = self.repository.get_active_shipment_for_driver(principal.user_id)
        shipment = ShipmentSummary.model_validate(shipment_row) if shipment_row else None

        vehicle_row = None
        facility_row = None
        docks: list[DockSummary] = []
        appointment = None
        checkin = None

        if shipment_row:
            with operation_span(
                "driver_chat.load_shipment_context",
                {"operation": "load_shipment_context", "shipment_id": shipment_row["shipment_id"]},
            ):
                if shipment_row.get("vehicle_id"):
                    vehicle_row = self.repository.get_vehicle(shipment_row["vehicle_id"])
                if shipment_row.get("destination_facility_id"):
                    facility_row = self.repository.get_facility(shipment_row["destination_facility_id"])
                    docks = [
                        DockSummary.model_validate(row)
                        for row in self.repository.list_docks(shipment_row["destination_facility_id"])
                    ]
            # dock_scheduler.repository.current_appointment() joins in
            # dock_code/slot_start_ts/slot_end_ts (the raw appointments row
            # only has slot_id) -- lets the driver UI show a real "confirmed
            # at Dock D1, 11:00-12:00" banner instead of nothing. Same
            # defensive fallback as the slot_options block above: a failure
            # here must degrade to "no appointment shown", not break the
            # whole snapshot/chat turn.
                try:
                    appt_row = self.dock_scheduler.repository.current_appointment(shipment_row["shipment_id"])
                except Exception:  # noqa: BLE001 - deliberate broad fallback, see slot_options above
                    import logging

                    logging.getLogger(__name__).exception(
                        "driver_chat_eta: failed to read current appointment for shipment %s.",
                        shipment_row.get("shipment_id"),
                    )
                    appt_row = None
                appointment = AppointmentSummary.model_validate(appt_row) if appt_row else None
                checkin_row = self.repository.get_checkin_for_shipment(shipment_row["shipment_id"])
                checkin = FacilityCheckinSummary.model_validate(checkin_row) if checkin_row else None

        with operation_span("driver_chat.load_exception_context", {"operation": "load_exception_context"}):
            exception_row = self.repository.get_active_exception_for_driver(principal.user_id)
        exception = DriverExceptionSummary.model_validate(exception_row) if exception_row else None

        chat_messages: list[ChatMessageSummary] = []
        if exception_row and exception_row.get("thread_id"):
            chat_messages = [
                ChatMessageSummary.model_validate(row)
                for row in self.repository.list_chat_messages(exception_row["thread_id"])
            ]

        # Dock slot booking must be available as soon as a shipment is
        # assigned, not only after the driver reports a delay -- this used
        # to live inside the exception-only branch above, so a driver with
        # no active exception always saw an empty slot board on the WMS-fed
        # dock cards regardless of what was actually open. Falls back
        # through declared ETA -> driver-updated ETA -> the shipment's
        # original planned ETA, so a freshly assigned shipment (no exception,
        # no driver-declared update yet) still gets a sensible window instead
        # of defaulting to "now".
        slot_options: list[SlotOption] = []
        if shipment_row and shipment_row.get("destination_facility_id"):
            try:
                with operation_span(
                    "driver_chat.prepare_slot_context",
                    {"operation": "prepare_slot_context", "shipment_id": shipment_row["shipment_id"]},
                ):
                    slot_options = self._feasible_slots(
                        shipment_row=shipment_row,
                        after=(
                            (exception_row or {}).get("declared_eta_ts")
                            or shipment_row.get("latest_eta_ts")
                            or shipment_row.get("original_eta_ts")
                        ),
                        max_leave_at=(exception_row or {}).get("latest_acceptable_ts"),
                    )
            except Exception:  # noqa: BLE001 - deliberate broad fallback, see comment below
                # _build_snapshot is on the critical path for the /snapshot
                # endpoint, every chat turn (including the LLM path's
                # broad-except fallback to the regex handler below), and
                # hold/confirm -- a dock-feasibility failure (bad slot data,
                # a transient Supabase hiccup, an unexpected data shape,
                # etc.) must degrade to "no slots shown" rather than take
                # down chat/snapshot/check-in entirely. Deliberately catches
                # Exception, not just DriverChatError -- a raw KeyError/
                # TypeError from malformed row data would otherwise still
                # propagate and break the whole snapshot. Logged so it's
                # never silently swallowed.
                import logging

                logging.getLogger(__name__).exception(
                    "driver_chat_eta: failed to compute feasible slots for shipment %s; showing none.",
                    shipment_row.get("shipment_id"),
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

        # Voice notes depend on GOOGLE_API_KEY (Gemini transcription), not
        # HUGGINGFACEHUB_API_TOKEN (the main HF-hosted chat agent) -- these
        # are two independent optional capabilities now, see agent.py's
        # module docstring. A deployment can have the chat agent configured
        # with no transcription key at all; that just means the driver has
        # to type.
        if not llm_agent.transcription_is_configured():
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

        Three-way fallback chain, each step catching only its own failure
        mode and falling through to the next rather than surfacing a hard
        error to the driver:
        1. If AGENTCORE_RUNTIME_ARN is set (production, once agentcore_app/
           is deployed per DEPLOYMENT_PLAN.md), route to the AWS
           Bedrock AgentCore-hosted LLM agent via
           infrastructure.agentcore_client -- same LangChain tool-calling
           loop, just running in its own container instead of in-process.
        2. Otherwise, if HUGGINGFACEHUB_API_TOKEN is set (local dev / no AWS
           credentials configured), run the HF-hosted tool-calling agent
           in-process exactly as before.
        3. If either of those raises anything other than a deliberate
           DriverChatError, fall back to the deterministic regex parser --
           drivers should never see a hard failure just because the LLM
           layer (wherever it's running) had a bad moment.
        """
        from setuhaul.infrastructure.agentcore_client import AgentCoreUnavailableError

        try:
            from setuhaul.infrastructure.agentcore_client import invoke_driver_chat_agent
            from setuhaul.infrastructure.agentcore_client import is_configured as agentcore_is_configured

            if agentcore_is_configured():
                return invoke_driver_chat_agent(principal, text)

            from setuhaul.backend.driver_chat_eta.llm import agent as llm_agent

            if llm_agent.is_configured():
                return llm_agent.run_chat_turn(self, principal, text)
        except AgentCoreUnavailableError:
            # The AWS call itself failed (network/timeout/malformed
            # response) -- not a deliberate error from inside the agent's
            # own tool calls. Fall through to the regex parser exactly like
            # any other LLM-layer hiccup, rather than re-raising like the
            # DriverChatError branch below does.
            import logging

            logging.getLogger(__name__).exception(
                "driver_chat_eta: AgentCore invocation failed, falling back to regex parser."
            )
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

        facility_row = (
            self.repository.get_facility(shipment_row["destination_facility_id"])
            if shipment_row.get("destination_facility_id")
            else None
        )

        options: list[SlotOption] = []
        if shipment_row.get("destination_facility_id"):
            # Unlike _build_snapshot's own call to _feasible_slots (which is
            # deliberately wrapped, see its comment), this call used to be
            # unguarded -- a dock-feasibility failure here (e.g. the
            # unbounded-query bug that used to live in
            # dock_scheduler.repository.compatible_slots) propagated straight
            # out of handle_chat_message as a raw 500, since this is the
            # regex fallback path and nothing above it catches anything
            # broader than DriverChatError. Same defensive degrade-to-empty
            # policy as _build_snapshot, for the same reason: a slot lookup
            # hiccup must never take down the whole chat turn.
            try:
                options = self._feasible_slots(
                    shipment_row=shipment_row,
                    after=declared_eta.isoformat(),
                    max_leave_at=max_leave_dt.isoformat() if max_leave_dt else None,
                )
            except Exception:  # noqa: BLE001 - deliberate broad fallback, see comment above
                import logging

                logging.getLogger(__name__).exception(
                    "driver_chat_eta: failed to compute feasible slots for shipment %s; showing none.",
                    shipment_row.get("shipment_id"),
                )

        # Actually book the earliest compatible slot here too -- not just
        # list options -- so a driver never loses the auto-booking behavior
        # just because this turn landed on the fallback (LLM not configured,
        # or a transient/persistent LLM failure, e.g. Google's AQ.-key
        # rollout issue affecting langchain_google_genai as of Aug 2026; see
        # llm/agent.py). This used to only ever compose a "here are your
        # options, reply to hold one" message and stop -- since the
        # Hold/Confirm chat buttons were removed from ChatPanel.tsx when
        # auto-booking replaced them, that left this fallback path unable to
        # ever actually book anything. Wrapped defensively: any unexpected
        # failure here falls back to the old list-only reply rather than
        # breaking the whole chat turn.
        booking_result: dict | None = None
        try:
            booking_result = self.auto_book_earliest_feasible_slot(principal)
        except DriverChatError:
            import logging

            logging.getLogger(__name__).exception(
                "driver_chat_eta: auto-book failed in the regex fallback path for shipment %s.",
                shipment_row.get("shipment_id"),
            )

        if booking_result is not None:
            agent_text = self._compose_autobook_reply(booking_result)
            if booking_result.get("status") == "already_booked":
                exception_row["exception_status"] = "RESOLVED"
            # For "request_submitted"/"escalated", auto_book_earliest_feasible_slot
            # / escalate() already wrote the real status transition
            # (WAITING_CONFIRMATION / ESCALATED) to Supabase themselves -- re-read
            # it so the response reflects the current DB state rather than
            # whatever this dict had before. A resolved exception is no longer
            # "active", so this can come back None; keep the locally-stamped
            # copy above in that case rather than crashing on a None exception
            # in the response below.
            refreshed = self.repository.get_active_exception_for_driver(principal.user_id)
            if refreshed:
                exception_row = refreshed
        else:
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
        text = _normalize_digits(text)
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
        text = _normalize_digits(text)
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

    @staticmethod
    def _fits_eta_window(
        start: datetime, end: datetime, after_dt: datetime, max_leave_dt: datetime | None
    ) -> tuple[bool, bool]:
        """(is_after_eta, within_leave_constraint) for a slot window against a
        driver's current declared ETA / must-leave-by constraint. Shared by
        `_feasible_slots` (computing SlotOption.is_compatible for every open
        slot) and `auto_book_earliest_feasible_slot`'s already-booked check
        (re-validating whether the shipment's EXISTING confirmed slot still
        satisfies a newly-declared ETA) so the two never drift apart.
        """
        is_after_eta = start >= after_dt - timedelta(minutes=15)
        within_leave_constraint = max_leave_dt is None or end <= max_leave_dt
        return is_after_eta, within_leave_constraint

    def _feasible_slots(
        self,
        *,
        shipment_row: dict,
        after: str | datetime | None,
        max_leave_at: str | datetime | None = None,
    ) -> list[SlotOption]:
        """Feasible dock slots for a driver-chat turn.

        Base compatibility -- dock type, refrigeration, weight capacity, and
        current availability_status (AVAILABLE/HELD/OCCUPIED/BLOCKED/CLOSED)
        -- comes from DockSchedulerRepository.compatible_slots(), the exact
        same computation WMS staff see on the WMS dock board. This method
        only layers the driver-chat-specific ETA window and must-leave-by
        constraints on top -- those aren't WMS concerns, so they stay here
        rather than in dock_scheduler.

        Memoized per DriverChatService instance (see self._feasible_slots_cache
        in __init__) keyed on (shipment_id, after, max_leave_at) -- a single
        LLM-driven chat turn calls this up to four times (the initial
        snapshot, report_delay_or_eta_change's tool, book_next_available_dock_slot's
        tool, and the post-turn snapshot rebuild) with IDENTICAL arguments
        whenever nothing that would change availability happened in
        between, since filing a change request (this chatbot never books
        directly anymore, see auto_book_earliest_feasible_slot) doesn't
        mutate slot availability_status at all. A genuinely different
        `after`/`max_leave_at` (e.g. after report_delay_or_eta_change
        changes the declared ETA) is a different cache key, so it's still
        recomputed -- this only removes redundant, not stale, computation.
        """
        after_dt = _parse_dt(after) or datetime.utcnow()
        max_leave_dt = _parse_dt(max_leave_at)
        shipment_id = shipment_row["shipment_id"]

        cache_key = (shipment_id, after_dt.isoformat(), max_leave_dt.isoformat() if max_leave_dt else None)
        cached = self._feasible_slots_cache.get(cache_key)
        if cached is not None:
            return list(cached)

        try:
            self.dock_scheduler.repository.ensure_future_slots_for_shipment(shipment_id)
            rows = self.dock_scheduler.repository.compatible_slots(shipment_id)
        except DockSchedulerError as exc:
            raise PersistenceError(str(exc)) from exc

        options: list[SlotOption] = []
        for row in rows:
            start = _parse_dt(row.get("slot_start_ts"))
            end = _parse_dt(row.get("slot_end_ts"))
            if start is None or end is None:
                continue

            is_after_eta, within_leave_constraint = self._fits_eta_window(start, end, after_dt, max_leave_dt)
            availability = row.get("availability_status")
            held_by_me = availability == "HELD" and row.get("held_shipment_id") == shipment_id
            booked_by_me = availability == "OCCUPIED" and row.get("shipment_id") == shipment_id
            bookable = availability == "AVAILABLE" or held_by_me

            is_compatible = bool(bookable and is_after_eta and within_leave_constraint)
            if booked_by_me:
                reason = "Your confirmed dock appointment"
            elif availability == "OCCUPIED":
                reason = "Already booked by another shipment"
            elif availability == "HELD" and not held_by_me:
                reason = "Currently held by another shipment"
            elif availability in {"BLOCKED", "CLOSED"}:
                reason = "Slot is not open for booking"
            elif not is_after_eta:
                reason = "Starts before the declared ETA"
            elif not within_leave_constraint:
                reason = "Ends after the driver's must-leave-by constraint"
            else:
                reason = "Fits ETA, dock type, and vehicle compatibility"

            options.append(
                SlotOption(
                    slot_id=row["slot_id"],
                    dock_id=row["dock_id"],
                    dock_code=row.get("dock_code"),
                    start_time=start,
                    end_time=end,
                    is_compatible=is_compatible,
                    compatibility_reason=reason,
                    estimated_wait_minutes=max(0, int((start - after_dt).total_seconds() // 60)),
                    is_held=held_by_me,
                    is_booked_by_me=booked_by_me,
                )
            )

        # No cap here -- this list backs DriverSnapshot.slot_options, which
        # the DockSlotBoard UI renders grouped per-dock (mirroring the WMS
        # dock board, which also shows every slot). A global top-N cap used
        # to live here and silently truncated the combined list across ALL
        # docks to 5 total, which is why some docks appeared to have no
        # slots at all even though real ones existed. LLM-facing call sites
        # that actually want a short list (report_exception's tool result,
        # list_feasible_dock_slots) slice this themselves.
        options.sort(key=lambda opt: (not opt.is_compatible, opt.start_time))
        self._feasible_slots_cache[cache_key] = options
        return list(options)

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

        exception_row = self.repository.get_active_exception_for_driver(principal.user_id)
        after = (exception_row or {}).get("declared_eta_ts") or shipment_row.get("latest_eta_ts")
        max_leave_at = (exception_row or {}).get("latest_acceptable_ts")

        return self._feasible_slots(
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

        facility_row = (
            self.repository.get_facility(shipment_row["destination_facility_id"])
            if shipment_row.get("destination_facility_id")
            else None
        )

        options: list[SlotOption] = []
        if shipment_row.get("destination_facility_id"):
            options = self._feasible_slots(
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

        increment("setuhaul.driver.delay_reports", {"result": new_status})
        emit_domain_event(
            "driver_delay_reported",
            shipment_id=shipment_row["shipment_id"],
            thread_id=thread_row["thread_id"],
            result=new_status,
        )

        return {
            "exception_id": exception_row["exception_id"],
            "thread_id": thread_row["thread_id"],
            "exception_status": new_status,
            "declared_eta": declared_eta.isoformat(),
            "must_leave_by": max_leave_dt.isoformat() if max_leave_dt else None,
            "facility_name": facility_row.get("facility_name") if facility_row else None,
            # Capped here (not in _feasible_slots itself) -- this dict is fed
            # straight into the LLM's context as a tool result, so it needs a
            # short, best-first list; the driver-facing snapshot/DockSlotBoard
            # wants the full set instead. `options` is already sorted
            # compatible-first, so slicing keeps the best candidates.
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
                for opt in options[:LLM_SLOT_SUMMARY_LIMIT]
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

    @staticmethod
    def _compose_autobook_reply(result: dict) -> str:
        """Plain-language version of what the LLM tool-calling agent would
        have said about an auto_book_earliest_feasible_slot() result --
        used by the regex fallback so a driver gets a clear answer about
        what actually happened, not just a list of slots. Mirrors the
        status values documented on the book_next_available_dock_slot tool
        in llm/tools.py.
        """
        status = result.get("status")
        if status in ("already_booked", "request_submitted"):
            dock = result.get("dock_code") or result.get("slot_id")
            start = result.get("start_time", "")
            end = result.get("end_time", "")
            start_str = start[11:16] if isinstance(start, str) and len(start) >= 16 else start
            end_str = end[11:16] if isinstance(end, str) and len(end) >= 16 else end
            if status == "already_booked":
                return f"This shipment already has a confirmed dock appointment at {dock}, {start_str}-{end_str} -- no need to book another."
            if result.get("via_swap"):
                return (
                    f"Requested dock slot {result.get('slot_id')} at {dock}, {start_str}-{end_str} -- taking this "
                    f"slot would need moving shipment {result.get('displaced_shipment_id')} to make room, so "
                    "this has been submitted to WMS for approval, not booked automatically."
                )
            return f"Requested dock slot {result.get('slot_id')} at {dock}, {start_str}-{end_str} -- submitted to WMS for approval. You'll be notified once it's confirmed."
        # "gated_in", "escalated", or anything unrecognized -- the assistant
        # never books or approves anything on its own; every feasible
        # candidate becomes a PENDING change request for a human WMS user to
        # decide (see auto_book_earliest_feasible_slot), so a request either
        # lands as "request_submitted" above, is refused outright because
        # the shipment already gated in ("gated_in"), or, if nothing
        # feasible existed at all, "escalated" to a human coordinator.
        return result.get("message") or (
            "No compatible dock slot was found for your declared ETA, so this has been escalated to a human coordinator."
        )

    # -- slot hold / confirm ------------------------------------------------

    def hold_slot(self, principal: DriverPrincipal, slot_id: str) -> SlotActionResponse:
        """Place a short hold on a dock slot via the shared WMS scheduling
        engine (DockSchedulerService) -- the same engine WMS staff use, so
        the chatbot and the WMS dock board never disagree about what's held
        or available. As a side effect this now also re-validates dock type/
        refrigeration/weight compatibility on hold (dock_scheduler.hold_slot
        checks the slot is still in compatible_slots), which the old
        chatbot-only implementation never re-checked at hold time.
        """
        driver = self.get_my_profile(principal)
        shipment_row = self.repository.get_active_shipment_for_driver(principal.user_id)
        if shipment_row is None:
            raise ShipmentNotFoundError("No active shipment is assigned to you.")
        shipment_id = shipment_row["shipment_id"]

        slot_row = self.repository.get_slot(slot_id)
        if slot_row is None:
            raise SlotNotFoundError(f"Slot {slot_id} was not found.")

        # Only one active hold per shipment -- release whatever it held
        # elsewhere before placing the new one, same guarantee as before.
        previous_hold = self.repository.get_active_hold_for_shipment(shipment_id)
        if previous_hold and previous_hold.get("slot_id") != slot_id:
            self.dock_scheduler.cancel_hold(previous_hold["hold_id"])

        if not (previous_hold and previous_hold.get("slot_id") == slot_id):
            try:
                self.dock_scheduler.hold_slot(shipment_id, slot_id, ttl_minutes=HOLD_MINUTES)
            except (DockInvalidBookingError, DockSlotUnavailableError) as exc:
                raise SlotConflictError(str(exc)) from exc
            except DockUnknownShipmentError as exc:
                raise ShipmentNotFoundError(str(exc)) from exc
            except DockSchedulerError as exc:
                raise PersistenceError(str(exc)) from exc

        exception_row = self.repository.get_active_exception_for_driver(principal.user_id)
        if exception_row:
            self.repository.update_exception(exception_row["exception_id"], {"exception_status": "WAITING_CONFIRMATION"})

        snapshot = self._build_snapshot(principal, driver)
        return SlotActionResponse(
            slot=AppointmentSlotSummary.model_validate(slot_row),
            snapshot=snapshot,
            message=f"Slot {slot_id} held for {HOLD_MINUTES} minutes.",
        )

    def auto_book_earliest_feasible_slot(self, principal: DriverPrincipal) -> dict:
        """Let the agent identify the driver's best dock slot and FILE it as a
        change request for WMS to approve -- it never books or approves
        anything on its own.

        The chatbot is the driver's assistant, not WMS: it picks the
        earliest slot that is currently compatible with the shipment (dock
        type, refrigeration, weight) AND fits the driver's latest declared
        ETA (exactly the same ranking `_feasible_slots` already produces --
        compatible-first, earliest start first), then FILES that as a
        change request via `dock_scheduler.create_change_request` (status
        PENDING) -- the exact same request a human-initiated driver/TMS
        dock-slot-change ends up in (see the WMS change-requests queue).
        Nothing is committed to `appointments` until a WMS user reviews and
        approves it there, considering carrier, shipment weight/dock
        compatibility, declared ETA, and the reason attached to the
        request. Only the driver-facing UX changes versus the old manual
        Hold/Confirm flow: no manual click, no waiting on a 5-minute hold
        timer, and no menu of options -- just one proposal submitted for
        approval.

        Re-evaluates from scratch on every call, even when the shipment
        already has a confirmed appointment: if the driver's currently
        declared ETA/must-leave-by no longer fits that appointment's window,
        or a genuinely EARLIER compatible slot has opened up, a NEW change
        request proposing the better slot is filed (this is also how
        "change my already-booked slot" works -- there's no separate
        code path for it) instead of just confirming "you're already
        booked". Only short-circuits as already-booked when the existing
        appointment is both still feasible for the current ETA and already
        the earliest such option -- see the `already`/`better_slot_available`
        handling below.

        Also considers whether a *better* (earlier) slot could be freed up
        by displacing a genuinely lower-priority shipment -- see
        `_best_priority_swap`, which reuses `DeterministicReschedulingEngine`
        (the same engine `suggest_slots`/the WMS "suggest" endpoint use) to
        find a PRIORITY_SWAP candidate. This is filed as a change request
        with `displaced_shipment_id`/`displaced_to_slot_id` set, exactly
        like the direct case -- WMS decides whether to approve the swap
        (which moves the displaced shipment first, then rebooks the
        requester) via the normal `decide_change_request` flow, not the
        assistant.
        - The swap is attempted BEFORE the direct request, not after -- the
          displaced occupant's own replacement slot is sometimes the very
          same slot this shipment could request directly (small facilities
          especially), so filing that request first would make WMS's later
          approval of the swap fail to find its own displacement target. If
          filing the swap request itself fails, a direct request is filed
          as a fallback so the driver isn't left with nothing.
        - If neither a direct slot nor a swap candidate exists, this
          escalates to a human coordinator exactly as before.

        This does NOT touch the DockSlotBoard's own manual Hold slot/Confirm
        booking buttons (`hold_slot`/`confirm_slot` below, and their REST
        endpoints) -- those remain untouched self-service actions outside of
        chat. This method is only ever called from the LLM tool-calling loop
        (see llm/tools.py's `book_next_available_dock_slot`).
        """
        shipment_row = self.repository.get_active_shipment_for_driver(principal.user_id)
        if shipment_row is None:
            raise ShipmentNotFoundError("No active shipment is assigned to you.")
        shipment_id = shipment_row["shipment_id"]

        # Once the driver has physically gated in at the facility, the dock
        # booking is no longer something the chatbot should be proposing
        # changes to -- the driver is already on-site, so any further move
        # has to go through WMS/gate staff directly (who can see the yard in
        # real time), not a request filed and waiting in a queue. This check
        # deliberately comes before any slot/feasibility computation so a
        # gated-in shipment never files (or even evaluates) a change
        # request. Only blocks the CHATBOT's own proposal path -- it does
        # not touch WMS's own manual re-assignment tools.
        checkin_row = self.repository.get_checkin_for_shipment(shipment_id)
        if checkin_row and checkin_row.get("gate_in_ts"):
            return {
                "status": "gated_in",
                "message": (
                    "This shipment has already checked in at the facility, so the dock booking can't be "
                    "changed from chat anymore -- please speak to the gate/WMS staff on site for any changes."
                ),
            }

        exception_row = self.repository.get_active_exception_for_driver(principal.user_id)
        after = (
            (exception_row or {}).get("declared_eta_ts")
            or shipment_row.get("latest_eta_ts")
            or shipment_row.get("original_eta_ts")
        )
        max_leave_at = (exception_row or {}).get("latest_acceptable_ts")
        after_dt = _parse_dt(after) or datetime.utcnow()
        max_leave_dt = _parse_dt(max_leave_at)
        options = self._feasible_slots(shipment_row=shipment_row, after=after, max_leave_at=max_leave_at)

        already = next((opt for opt in options if opt.is_booked_by_me), None)
        compatible = [opt for opt in options if opt.is_compatible]
        best_direct = compatible[0] if compatible else None

        # Only short-circuit on "already booked" if that existing confirmed
        # appointment (a) still satisfies the driver's CURRENT declared ETA/
        # must-leave-by constraint, AND (b) is already the earliest such fit
        # -- i.e. nothing genuinely better has opened up. Every other case
        # falls through and re-evaluates the booking from scratch, same as
        # if nothing were booked yet:
        #   - Bug fix: `already` used to be returned unconditionally the
        #     moment ANY confirmed appointment existed for this shipment,
        #     even when the driver had just reported a fresh delay that
        #     pushed their ETA past the existing slot's start time (e.g.
        #     "I am getting delayed by 1 day" after already having a
        #     same-day 19:00-20:00 appointment) -- SlotOption.is_compatible
        #     can never catch this itself, because `bookable` in
        #     `_feasible_slots` deliberately excludes OCCUPIED slots
        #     (including the shipment's own current one), so the existing
        #     appointment's own is_compatible flag is always False
        #     regardless of whether its time window still fits. Re-checking
        #     the window directly here (same predicate `_feasible_slots`
        #     uses for every other slot, via `_fits_eta_window`) is what
        #     lets a newly-reported delay correctly fall through to picking
        #     a new/later slot instead of getting stuck on the stale one.
        #   - Behavior change (requested explicitly): a driver reporting
        #     ANY new delay/ETA should make the assistant re-check for a
        #     better-fitting slot, not just bail out the moment the old one
        #     is still technically compatible. If a genuinely EARLIER
        #     compatible slot exists than the one already booked (capacity
        #     freed up, an earlier dock opened, etc.), move the appointment
        #     there instead of leaving the driver parked on a
        #     later-than-necessary slot just because "it still works".
        #     Never rebooks to the *same* slot it's already on -- that
        #     would just be a pointless cancel+recreate.
        if already:
            still_fits, still_within_leave = self._fits_eta_window(
                already.start_time, already.end_time, after_dt, max_leave_dt
            )
            fits_current_eta = still_fits and still_within_leave
            better_slot_available = best_direct is not None and best_direct.start_time < already.start_time
            if fits_current_eta and not better_slot_available:
                return {
                    "status": "already_booked",
                    "slot_id": already.slot_id,
                    "dock_code": already.dock_code,
                    "start_time": already.start_time.isoformat(),
                    "end_time": already.end_time.isoformat(),
                    "message": "This shipment already has a confirmed dock appointment -- no need to book another.",
                }
            # Either the existing appointment no longer fits (the driver's
            # new ETA is after the slot's start, or a new must-leave-by
            # constraint now excludes it) or a strictly earlier compatible
            # slot has opened up -- fall through and treat this exactly like
            # any other booking attempt. `already`'s own slot never appears
            # in `compatible` above (booked_by_me implies availability ==
            # "OCCUPIED", which `_feasible_slots` never marks bookable), so
            # `best_direct` is guaranteed to be a genuinely different,
            # currently-open slot; `book_after_acceptance` (used by
            # `_commit_direct_booking`/the swap path) already knows how to
            # move a shipment off a CONFIRMED appointment onto a new slot --
            # it cancels the stale one and books the new one atomically.
        best_swap = self._best_priority_swap(
            shipment_id=shipment_id,
            after_dt=after_dt,
            max_leave_dt=max_leave_dt,
        )

        swap_is_better = best_swap is not None and (best_direct is None or best_swap.start < best_direct.start_time)

        if swap_is_better:
            # File the swap-based request BEFORE falling back to a direct
            # request, not after -- best_swap's displaced_to_slot_id is, in
            # the smallest facilities, sometimes the very same slot as
            # best_direct (the displaced occupant's only other compatible
            # slot IS the one this shipment could request directly). Trying
            # the swap request first avoids a self-inflicted collision at
            # WMS-approval time; the direct slot is only used as a fallback
            # below if filing the swap request itself fails.
            #
            # IMPORTANT: this only FILES the request (dock_scheduler.
            # create_change_request, status PENDING) -- it deliberately does
            # NOT call decide_change_request itself anymore. The chatbot is
            # the driver's assistant, not WMS: it can propose a booking or a
            # swap, but only a human WMS user approves it (see the WMS
            # change-requests queue this lands in, the same one TMS/driver
            # self-service change requests already use).
            try:
                request = self.dock_scheduler.create_change_request(
                    shipment_id=shipment_id,
                    requested_slot_id=best_swap.slot_id,
                    requested_by_role=ChangeRequestRole.DRIVER,
                    requested_by_user_id=principal.user_id,
                    reason=f"Requested by the dispatch assistant on the driver's behalf: {best_swap.reason}",
                    displaced_shipment_id=best_swap.displaced_shipment_id,
                    displaced_to_slot_id=best_swap.displaced_to_slot_id,
                )
            except DockSchedulerError:
                import logging

                logging.getLogger(__name__).exception(
                    "driver_chat_eta: failed to file a priority-swap change request for shipment %s.", shipment_id
                )
                request = None

            if request is not None:
                self._mark_request_pending(exception_row, request["change_request_id"])
                return {
                    "status": "request_submitted",
                    "change_request_id": request["change_request_id"],
                    "slot_id": best_swap.slot_id,
                    "dock_code": best_swap.dock_code,
                    "start_time": best_swap.start.isoformat(),
                    "end_time": best_swap.end.isoformat(),
                    "via_swap": True,
                    "displaced_shipment_id": best_swap.displaced_shipment_id,
                    "message": (
                        f"Requested dock slot {best_swap.slot_id} at "
                        f"{best_swap.dock_code or best_swap.slot_id}, "
                        f"{best_swap.start.strftime('%H:%M')}-{best_swap.end.strftime('%H:%M')} -- taking this "
                        f"slot would need moving shipment {best_swap.displaced_shipment_id} to make room, so this "
                        "has been submitted to WMS for approval, not booked automatically."
                    ),
                }
            # Filing the swap request itself failed -- fall through to a
            # direct request below exactly as if no swap had been found, so
            # the driver isn't left with nothing just because that attempt
            # didn't land.

        if best_direct is None:
            self.escalate(principal, "No feasible dock slot was found matching the driver's declared ETA.")
            return {
                "status": "escalated",
                "message": "No compatible dock slot was found for the declared ETA, so this was escalated to a human coordinator.",
            }

        try:
            request = self.dock_scheduler.create_change_request(
                shipment_id=shipment_id,
                requested_slot_id=best_direct.slot_id,
                requested_by_role=ChangeRequestRole.DRIVER,
                requested_by_user_id=principal.user_id,
                reason="Requested by the dispatch assistant on the driver's behalf: earliest compatible slot for the declared ETA.",
            )
        except DockSchedulerError as exc:
            raise PersistenceError(str(exc)) from exc

        self._mark_request_pending(exception_row, request["change_request_id"])
        return {
            "status": "request_submitted",
            "change_request_id": request["change_request_id"],
            "slot_id": best_direct.slot_id,
            "dock_code": best_direct.dock_code,
            "start_time": best_direct.start_time.isoformat(),
            "end_time": best_direct.end_time.isoformat(),
            "via_swap": False,
            "message": (
                f"Requested dock slot {best_direct.slot_id} at {best_direct.dock_code or best_direct.dock_id}, "
                f"{best_direct.start_time.strftime('%H:%M')}-{best_direct.end_time.strftime('%H:%M')} -- "
                "submitted to WMS for approval. You'll be notified once it's confirmed."
            ),
        }

    def get_latest_change_request_status(self, principal: DriverPrincipal) -> dict:
        """Status of the most recently filed dock-slot change request for the
        driver's active shipment -- lets the chatbot answer "is my request
        approved?" / "did WMS decide yet?" with a real, current answer
        instead of guessing or re-proposing a new booking. PENDING means
        still waiting on a human WMS user; APPROVED means the requested slot
        is now the shipment's CONFIRMED appointment; DECLINED means WMS
        rejected it and the shipment's previous appointment (if any) is
        unaffected. `has_request: False` means nothing has ever been filed
        for this shipment (e.g. the driver hasn't asked to book/change a
        slot yet).
        """
        shipment_row = self.repository.get_active_shipment_for_driver(principal.user_id)
        if shipment_row is None:
            raise ShipmentNotFoundError("No active shipment is assigned to you.")
        requests = self.dock_scheduler.repository.list_change_requests(shipment_id=shipment_row["shipment_id"])
        if not requests:
            return {
                "has_request": False,
                "message": "No dock slot request has been filed for this shipment yet.",
            }
        latest = requests[0]  # list_change_requests sorts created_at descending
        return {
            "has_request": True,
            "change_request_id": latest.get("change_request_id"),
            "status": latest.get("request_status"),
            "dock_code": latest.get("dock_code"),
            "slot_start_ts": latest.get("slot_start_ts"),
            "slot_end_ts": latest.get("slot_end_ts"),
            "displaced_shipment_id": latest.get("displaced_shipment_id"),
            "requested_at": latest.get("created_at"),
            "decided_at": latest.get("decided_at"),
            "decision_note": latest.get("decision_note"),
        }

    def _mark_request_pending(self, exception_row: dict | None, change_request_id: str) -> None:
        """Move the driver's active exception (if any) to WAITING_CONFIRMATION
        once a booking/change request has been filed with WMS -- the same
        status hold_slot() already uses for the driver's own self-service
        hold, since both mean the same thing from the driver's point of
        view: something is pending someone else's decision, not final yet.
        Does not touch appointments/slots itself -- create_change_request
        already did the actual persistence; this only keeps the
        exception/thread status honest for the driver-facing UI.
        """
        if not exception_row:
            return
        self.repository.update_exception(exception_row["exception_id"], {"exception_status": "WAITING_CONFIRMATION"})
        exception_row["exception_status"] = "WAITING_CONFIRMATION"
        if exception_row.get("thread_id"):
            self.repository.insert_chat_message(
                {
                    "chat_message_id": _new_id("MSG"),
                    "thread_id": exception_row["thread_id"],
                    "sender_type": "SYSTEM",
                    "message_text": f"Dock slot change request {change_request_id} submitted to WMS for approval.",
                    "message_ts": _now_iso(),
                }
            )

    def _best_priority_swap(
        self, *, shipment_id: str, after_dt: datetime, max_leave_dt: datetime | None
    ):
        """Earliest PRIORITY_SWAP candidate for this shipment, if any.

        Reuses `DeterministicReschedulingEngine` (via `DockSchedulerService.
        suggest_slots`) rather than reimplementing swap logic here -- a
        PRIORITY_SWAP suggestion only exists when this shipment's own
        `priority_code` genuinely outranks the slot's current occupant (see
        `PRIORITY_WEIGHT` in dock_scheduler/constraints.py) AND that
        occupant has its own later compatible slot to move to, so this never
        proposes bumping someone with equal or higher priority. Always
        optional -- any failure here degrades to None (no swap considered),
        never breaks auto-booking.
        """
        try:
            constraints = DriverConstraints(earliest_start=after_dt, must_finish_by=max_leave_dt)
            suggestions = self.dock_scheduler.suggest_slots(shipment_id, constraints, limit=10)
        except Exception:  # noqa: BLE001 - optional enhancement, must never break auto-booking
            import logging

            logging.getLogger(__name__).exception(
                "driver_chat_eta: failed to compute priority-swap suggestions for shipment %s.", shipment_id
            )
            return None
        swaps = [s for s in suggestions if s.suggestion_type is SuggestionType.PRIORITY_SWAP]
        return min(swaps, key=lambda s: s.start) if swaps else None

    def confirm_slot(self, principal: DriverPrincipal, slot_id: str) -> ConfirmSlotResponse:
        """Confirm a held slot via the shared WMS scheduling engine."""
        driver = self.get_my_profile(principal)
        shipment_row = self.repository.get_active_shipment_for_driver(principal.user_id)
        if shipment_row is None:
            raise ShipmentNotFoundError("No active shipment is assigned to you.")
        shipment_id = shipment_row["shipment_id"]

        hold = self.repository.get_active_hold_for_shipment(shipment_id)
        if hold is None or hold.get("slot_id") != slot_id:
            raise SlotConflictError("This slot is not currently held by you, so it cannot be confirmed.")
        expires_at = _parse_dt(hold.get("expires_at"))
        if expires_at is not None and expires_at < datetime.utcnow():
            raise SlotConflictError("Your hold on this slot has expired -- hold it again before confirming.")

        try:
            self.dock_scheduler.confirm_booking(shipment_id, slot_id, accepted=True)
        except (DockInvalidBookingError, DockSlotUnavailableError) as exc:
            raise SlotConflictError(str(exc)) from exc
        except DockUnknownShipmentError as exc:
            raise ShipmentNotFoundError(str(exc)) from exc
        except DockSchedulerError as exc:
            raise PersistenceError(str(exc)) from exc

        # dock_scheduler's version joins in dock_code/slot_start_ts/slot_end_ts
        # so the immediate confirm response can show a real "confirmed at
        # Dock D1, 11:00-12:00" message, not just an opaque slot id.
        appointment_row = self.dock_scheduler.repository.current_appointment(shipment_id)
        if appointment_row is None:
            raise PersistenceError("Booking succeeded but the confirmed appointment could not be re-read.")

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
            # Deliberately NOT setting shipments.current_status here -- a
            # driver marking themselves as arrived is their own unverified
            # claim (staff_approved_flag defaults to 0), and per
            # CheckInService.approve_gate_checkin's own docstring, TMS/WMS
            # must not see AT_GATE until check-in staff explicitly confirm
            # it there. Setting it here would silently bypass that approval
            # gate (see task #84 / 20260814140000_dock_slot_change_requests.
            # sql's staff_approved_flag column comment).
        else:
            if existing is None:
                raise BusinessValidationError("A shipment must gate-in before any later check-in stage.")
            # Once staff have approved the gate check-in (staff_approved_flag),
            # further self-reported progression (yard -> dock) is trustworthy
            # enough to reflect on shipments.current_status -- this used to
            # never advance past whatever approve_gate_checkin last set, so a
            # driver moving gate -> yard -> dock left TMS showing AT_GATE (or
            # even the pre-gate status) the whole time. Before staff approval,
            # these stages still record the driver's own timestamps in
            # facility_checkins (so nothing about self-service check-in is
            # blocked), they just don't overwrite the fleet-wide status yet.
            staff_approved = bool(existing.get("staff_approved_flag"))
            payload: dict = {"updated_at": now}
            if choice == "waiting_yard":
                payload["yard_queue_enter_ts"] = now
                payload["queue_state"] = "WAITING_EARLY"
                if staff_approved:
                    self.repository.update_shipment(shipment_row["shipment_id"], {"current_status": "WAITING"})
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
                if staff_approved:
                    self.repository.update_shipment(shipment_row["shipment_id"], {"current_status": "IN_DOCK"})
            elif choice == "completed":
                payload["unload_end_ts"] = now
                payload["gate_out_ts"] = now
                payload["queue_state"] = "COMPLETED"
                # Auto-archive on completion -- previously a shipment stayed
                # in the active TMS view until a dispatcher clicked Archive
                # by hand; completion is unambiguous enough to do this
                # automatically instead.
                self.repository.update_shipment(
                    # archived_flag is stored as integer 0/1, not a native
                    # boolean column -- PostgREST rejects a JSON true/false
                    # literal against it (see TMSRepository._coerce_booleans).
                    shipment_row["shipment_id"],
                    {"current_status": "COMPLETED", "archived_flag": 1},
                )
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

    # -- safety-critical emergency escalation --------------------------------

    def flag_emergency_situation(self, principal: DriverPrincipal, reason: str) -> EscalateResponse:
        """Mark the driver's thread as a safety-critical emergency (engine
        failure, accident, medical emergency, hazmat spill, or anything else
        outside what dock/ETA logic can resolve) -- distinct from `escalate`
        above, which handles the ordinary "no compatible slot"/"driver asked
        for a human" case.

        Unlike `escalate`, this CREATES an exception/thread if none is
        active yet -- a mid-trip emergency has no reason to depend on the
        driver having already reported a delay first. Sets
        `severity_code="CRITICAL"` so the frontend can show the "Send
        Emergency Alert" button purely from the returned exception (see
        `ContextBar.tsx`'s badge and the equivalent chat-panel button) --
        no separate flag on ChatResponse needed. Does NOT send the SMS
        itself; that only happens when the driver explicitly taps the
        button, via `send_emergency_alert` below, so a mention of "engine"
        or "accident" alone never silently pages the emergency contact.
        """
        driver = self.get_my_profile(principal)
        shipment_row = self.repository.get_active_shipment_for_driver(principal.user_id)
        exception_row = self.repository.get_active_exception_for_driver(principal.user_id)

        thread_row = self.repository.get_open_thread_for_driver(principal.user_id)
        if thread_row is None:
            thread_row = self.repository.create_thread(
                {
                    "thread_id": _new_id("TH"),
                    "driver_id": principal.user_id,
                    "shipment_id": shipment_row["shipment_id"] if shipment_row else None,
                    "opened_at": _now_iso(),
                    "thread_status": "ESCALATED",
                    "thread_intent": "EMERGENCY",
                }
            )
        else:
            self.repository.update_thread(thread_row["thread_id"], {"thread_status": "ESCALATED"})

        emergency_payload = {
            "exception_status": "ESCALATED",
            "severity_code": "CRITICAL",
            "description": reason,
        }
        if exception_row is None:
            exception_row = self.repository.create_exception(
                {
                    "exception_id": _new_id("EXC"),
                    "shipment_id": shipment_row["shipment_id"] if shipment_row else None,
                    "driver_id": principal.user_id,
                    "thread_id": thread_row["thread_id"],
                    "exception_type": "BREAKDOWN",
                    "reported_at": _now_iso(),
                    "exception_status": "ESCALATED",
                    "severity_code": "CRITICAL",
                    "description": reason,
                }
            )
        else:
            exception_row = self.repository.update_exception(exception_row["exception_id"], emergency_payload) or {
                **exception_row,
                **emergency_payload,
            }

        self.repository.insert_chat_message(
            {
                "chat_message_id": _new_id("MSG"),
                "thread_id": thread_row["thread_id"],
                "sender_type": "OPERATIONS",
                "message_text": f"Safety-critical situation flagged, escalated to a human coordinator: {reason}",
                "message_ts": _now_iso(),
            }
        )

        snapshot = self._build_snapshot(principal, driver)
        return EscalateResponse(exception=DriverExceptionSummary.model_validate(exception_row), snapshot=snapshot)

    def send_emergency_alert(self, principal: DriverPrincipal, reason: str) -> dict:
        """Send the actual emergency SMS -- only called when the driver taps
        the frontend's "Send Emergency Alert" button (see
        driverChatApi.ts's sendEmergencyAlert), never automatically just
        because the LLM called flag_emergency_situation. Best-effort by
        design (infrastructure.sms.send_sms never raises) -- a Twilio outage
        must not block the driver from doing anything else in the app.
        """
        from setuhaul.infrastructure.sms import send_sms

        driver = self.get_my_profile(principal)
        shipment_row = self.repository.get_active_shipment_for_driver(principal.user_id)

        lines = [
            "SETUHAUL EMERGENCY ALERT",
            f"Driver: {driver.driver_name or principal.user_id} ({principal.user_id})",
            f"Driver phone: {driver.phone or 'not on file'}",
        ]
        if shipment_row:
            lines.append(f"Shipment: {shipment_row.get('shipment_id')} (order {shipment_row.get('order_reference')})")
            lines.append(f"Origin: {shipment_row.get('origin_city') or shipment_row.get('origin_name') or 'unknown'}")
            if shipment_row.get("destination_facility_id"):
                facility_row = self.repository.get_facility(shipment_row["destination_facility_id"])
                if facility_row:
                    lines.append(f"Destination: {facility_row.get('facility_name')}")
        else:
            lines.append("No active shipment on file.")
        lines.append(f"Reported: {reason}")

        message_sid = send_sms(EMERGENCY_CONTACT_PHONE, "\n".join(lines))
        return {"status": "sent" if message_sid else "unavailable", "message_sid": message_sid}
