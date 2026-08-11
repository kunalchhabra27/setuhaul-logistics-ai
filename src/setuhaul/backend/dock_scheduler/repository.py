"""Persistence boundary for dock scheduling (public.appointment_slots / docks /
appointments / slot_holds / facilities / facility_rules / facility_checkins).

Rewritten against the real Supabase project (verified live on 2026-08-10) --
this used to run against a local SQLite database seeded from
`data/setuhaul_schema_and_seed.sql`. That seed file turned out to be an
accurate mirror of the real Supabase schema (table/column names and value
vocabularies both matched what a live schema probe returned), so the
column/value semantics below are unchanged from the SQLite version. What
changed is the persistence layer itself: the SQLite version leaned on two
SQL views (`v_inbound_operational_state`, `v_slot_availability`) plus
`BEGIN IMMEDIATE` transactions for hold locking. Supabase's PostgREST API
has no equivalent for either -- there are no ad-hoc views and no
multi-statement transactions over REST -- so both are reimplemented here as
plain Python queries/joins. See `_operational_state` and `compatible_slots`
for the view replacements.

Concurrency note: hold creation here is check-then-insert at the application
level rather than the SQLite version's `BEGIN IMMEDIATE` pessimistic lock.
PostgREST does not expose transactions, so a narrow race window exists
between the availability check and the insert. Acceptable for this
demo-scale scheduler; a production version would want a Postgres RPC
function to make hold creation atomic.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, TYPE_CHECKING
from uuid import uuid4

from postgrest.exceptions import APIError

from setuhaul.backend.dock_scheduler.exceptions import (
    DockSchedulerError,
    InvalidBookingError,
    SlotUnavailableError,
    UnknownShipmentError,
)
from setuhaul.backend.dock_scheduler.models import HoldResult, SlotLifecycleStage

if TYPE_CHECKING:
    from supabase import Client
else:
    Client = Any

ACTIVE_APPOINTMENT_STATUSES = ("PENDING_CONFIRMATION", "CONFIRMED", "IN_PROGRESS")
PRIORITY_WEIGHT = {"LOW": 1, "NORMAL": 2, "HIGH": 3, "CRITICAL": 4}


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class PersistenceError(DockSchedulerError):
    """Raised when an unexpected Supabase/PostgREST error occurs."""


class DockSchedulerRepository:
    """Persistence boundary for facilities, slots, appointments, and holds."""

    def __init__(self, backend: Client):
        self.backend = backend

    @staticmethod
    def _data(response: Any) -> list[dict[str, Any]]:
        return list(response.data or [])

    def _raise_persistence(self, exc: APIError) -> None:
        raise PersistenceError(str(getattr(exc, "message", "Dock scheduler database operation failed."))) from exc

    def _select(self, table: str, **filters: Any) -> list[dict[str, Any]]:
        try:
            query = self.backend.table(table).select("*")
            for key, value in filters.items():
                if isinstance(value, (list, tuple, set)):
                    query = query.in_(key, list(value))
                else:
                    query = query.eq(key, value)
            return self._data(query.execute())
        except APIError as exc:
            self._raise_persistence(exc)

    # -- shipment operational state (replaces v_inbound_operational_state) --

    def _operational_state(self, shipment_id: str) -> dict[str, Any]:
        rows = self._select("shipments", shipment_id=shipment_id)
        if not rows:
            raise UnknownShipmentError(f"Unknown shipment: {shipment_id}")
        shipment = rows[0]

        # v_latest_eta: most recent driver-declared ETA, falling back to the
        # shipment's own latest/original ETA columns.
        eta_rows = (
            self.backend.table("eta_updates")
            .select("*")
            .eq("shipment_id", shipment_id)
            .order("created_at", desc=True)
            .order("eta_update_id", desc=True)
            .limit(1)
            .execute()
        )
        eta_rows = self._data(eta_rows)
        latest_declared = eta_rows[0]["declared_eta_ts"] if eta_rows else None
        effective_eta_ts = latest_declared or shipment.get("latest_eta_ts") or shipment.get("original_eta_ts")

        appointment_rows = self._select(
            "appointments",
            shipment_id=shipment_id,
            is_current=1,
            appointment_status=list(ACTIVE_APPOINTMENT_STATUSES),
        )
        appointment = appointment_rows[0] if appointment_rows else None

        planned_dock_code = None
        slot_start_ts = None
        slot_end_ts = None
        slot_id = None
        if appointment:
            slot_id = appointment["slot_id"]
            slot_rows = self._select("appointment_slots", slot_id=slot_id)
            if slot_rows:
                slot_start_ts = slot_rows[0]["slot_start_ts"]
                slot_end_ts = slot_rows[0]["slot_end_ts"]
                dock_rows = self._select("docks", dock_id=slot_rows[0]["dock_id"])
                if dock_rows:
                    planned_dock_code = dock_rows[0]["dock_code"]

        checkin_rows = self._select("facility_checkins", shipment_id=shipment_id)
        checkin = checkin_rows[0] if checkin_rows else None

        return {
            "shipment_id": shipment["shipment_id"],
            "driver_id": shipment.get("driver_id"),
            "vehicle_id": shipment.get("vehicle_id"),
            "destination_facility_id": shipment["destination_facility_id"],
            "priority_code": shipment["priority_code"],
            "required_dock_type": shipment["required_dock_type"],
            "temperature_control_required": shipment["temperature_control_required"],
            "load_weight_kg": shipment["load_weight_kg"],
            "expected_unload_min": shipment["expected_unload_min"],
            "current_status": shipment["current_status"],
            "effective_eta_ts": effective_eta_ts,
            "appointment_id": appointment["appointment_id"] if appointment else None,
            "slot_id": slot_id,
            "slot_start_ts": slot_start_ts,
            "slot_end_ts": slot_end_ts,
            "planned_dock_code": planned_dock_code,
            "gate_in_ts": checkin.get("gate_in_ts") if checkin else None,
            "queue_state": checkin.get("queue_state") if checkin else None,
            "queue_position": checkin.get("queue_position") if checkin else None,
        }

    def shipment(self, shipment_id: str) -> dict[str, Any]:
        return self._operational_state(shipment_id)

    def facility(self, facility_id: str) -> dict[str, Any]:
        rows = self._select("facilities", facility_id=facility_id)
        if not rows:
            raise UnknownShipmentError(f"Unknown facility: {facility_id}")
        return rows[0]

    def facility_rules(self, facility_id: str) -> list[dict[str, Any]]:
        rows = self._select("facility_rules", facility_id=facility_id, active_flag=1)
        return sorted(rows, key=lambda row: row["rule_type"])

    # -- slot availability (replaces v_slot_availability) --------------------

    def _slot_rows_with_availability(
        self, slots: list[dict[str, Any]], docks_by_id: dict[str, dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Attach availability_status/occupant/hold info to raw slot rows."""
        if not slots:
            return []
        slot_ids = [s["slot_id"] for s in slots]

        occupant_rows = self._select(
            "appointments", slot_id=slot_ids, appointment_status=list(ACTIVE_APPOINTMENT_STATUSES)
        )
        occupant_by_slot = {row["slot_id"]: row for row in occupant_rows}
        occupant_shipment_ids = [row["shipment_id"] for row in occupant_rows]
        occupant_shipments = (
            {row["shipment_id"]: row for row in self._select("shipments", shipment_id=occupant_shipment_ids)}
            if occupant_shipment_ids
            else {}
        )

        self._expire_stale_holds()
        hold_rows = self._select("slot_holds", slot_id=slot_ids, hold_status="HELD")
        hold_by_slot = {row["slot_id"]: row for row in hold_rows}

        result: list[dict[str, Any]] = []
        for slot in slots:
            dock = docks_by_id.get(slot["dock_id"], {})
            occ_appointment = occupant_by_slot.get(slot["slot_id"])
            hold = hold_by_slot.get(slot["slot_id"])

            if slot["slot_status"] != "OPEN":
                availability_status = slot["slot_status"]
            elif occ_appointment is not None:
                availability_status = "OCCUPIED"
            elif hold is not None:
                availability_status = "HELD"
            else:
                availability_status = "AVAILABLE"

            occ_shipment = occupant_shipments.get(occ_appointment["shipment_id"]) if occ_appointment else None

            result.append(
                {
                    "slot_id": slot["slot_id"],
                    "facility_id": slot["facility_id"],
                    "dock_id": slot["dock_id"],
                    "dock_code": dock.get("dock_code"),
                    "dock_type": dock.get("dock_type"),
                    "dock_status": dock.get("dock_status"),
                    "supports_refrigerated": dock.get("supports_refrigerated"),
                    "max_vehicle_weight_kg": dock.get("max_vehicle_weight_kg"),
                    "slot_start_ts": slot["slot_start_ts"],
                    "slot_end_ts": slot["slot_end_ts"],
                    "availability_status": availability_status,
                    "appointment_id": occ_appointment["appointment_id"] if occ_appointment else None,
                    "shipment_id": occ_appointment["shipment_id"] if occ_appointment else None,
                    "appointment_status": occ_appointment["appointment_status"] if occ_appointment else None,
                    "occupied_priority": occ_shipment["priority_code"] if occ_shipment else None,
                    "occupied_unload_min": occ_shipment["expected_unload_min"] if occ_shipment else None,
                    "occupied_driver_id": occ_shipment.get("driver_id") if occ_shipment else None,
                    "hold_id": hold["hold_id"] if hold else None,
                    "held_shipment_id": hold["shipment_id"] if hold else None,
                }
            )
        return result

    def compatible_slots(self, shipment_id: str) -> list[dict[str, Any]]:
        target = self._operational_state(shipment_id)

        docks = self._select("docks", facility_id=target["destination_facility_id"], dock_status="ACTIVE")
        docks_by_id = {d["dock_id"]: d for d in docks}
        if not docks_by_id:
            return []

        slots = self._select(
            "appointment_slots", facility_id=target["destination_facility_id"], dock_id=list(docks_by_id.keys())
        )
        enriched = self._slot_rows_with_availability(slots, docks_by_id)

        required_dock_type = target["required_dock_type"]
        temperature_control_required = target["temperature_control_required"]
        max_weight_ok_load = target["load_weight_kg"]

        compatible = [
            row
            for row in enriched
            if (required_dock_type == "ANY" or row["dock_type"] == required_dock_type)
            and (not temperature_control_required or row["supports_refrigerated"])
            and (row["max_vehicle_weight_kg"] is None or row["max_vehicle_weight_kg"] >= max_weight_ok_load)
        ]
        compatible.sort(key=lambda row: (row["slot_start_ts"], row["dock_code"]))
        return compatible

    def driver_names(self, driver_ids: list[str]) -> dict[str, str]:
        """Resolve driver_id -> driver_name for a batch of drivers, so the
        visual dock board can show WMS staff whose shipment holds/occupies
        each slot. dock_scheduler otherwise never needs driver identity --
        this is a narrow, read-only cross-context lookup (same pattern
        driver_chat_eta already uses to read across bounded contexts)."""
        ids = [value for value in {*driver_ids} if value]
        if not ids:
            return {}
        rows = self._select("drivers", driver_id=ids)
        return {row["driver_id"]: row.get("driver_name") for row in rows if row.get("driver_name")}

    def current_appointment(self, shipment_id: str) -> dict[str, Any] | None:
        rows = self._select(
            "appointments", shipment_id=shipment_id, is_current=1, appointment_status=list(ACTIVE_APPOINTMENT_STATUSES)
        )
        if not rows:
            return None
        appointment = rows[0]
        slot_rows = self._select("appointment_slots", slot_id=appointment["slot_id"])
        if not slot_rows:
            return {**appointment, "slot_start_ts": None, "slot_end_ts": None, "dock_code": None}
        slot = slot_rows[0]
        dock_rows = self._select("docks", dock_id=slot["dock_id"])
        dock_code = dock_rows[0]["dock_code"] if dock_rows else None
        return {
            **appointment,
            "slot_start_ts": slot["slot_start_ts"],
            "slot_end_ts": slot["slot_end_ts"],
            "dock_code": dock_code,
        }

    def slot_availability(self, slot_id: str) -> dict[str, Any] | None:
        self._expire_stale_holds()
        slots = self._select("appointment_slots", slot_id=slot_id)
        if not slots:
            return None
        docks = self._select("docks", dock_id=slots[0]["dock_id"])
        docks_by_id = {d["dock_id"]: d for d in docks}
        enriched = self._slot_rows_with_availability(slots, docks_by_id)
        return enriched[0] if enriched else None

    def active_hold_for_slot(self, slot_id: str) -> dict[str, Any] | None:
        self._expire_stale_holds()
        rows = self._select("slot_holds", slot_id=slot_id, hold_status="HELD")
        rows.sort(key=lambda row: row["held_at"], reverse=True)
        return rows[0] if rows else None

    def active_hold_for_shipment(self, shipment_id: str, slot_id: str) -> dict[str, Any] | None:
        self._expire_stale_holds()
        rows = self._select("slot_holds", shipment_id=shipment_id, slot_id=slot_id, hold_status="HELD")
        rows.sort(key=lambda row: row["held_at"], reverse=True)
        return rows[0] if rows else None

    def current_appointment_status(self, shipment_id: str, slot_id: str) -> str | None:
        """Latest appointment_status for a (shipment, slot, is_current) pair.

        Used only by DockSchedulerService.lifecycle_stage_for_slot, a helper
        with no current callers elsewhere in the codebase -- kept working
        rather than removed.
        """
        rows = self._select("appointments", shipment_id=shipment_id, slot_id=slot_id, is_current=1)
        if not rows:
            return None
        rows.sort(key=lambda row: row["booked_at"], reverse=True)
        return rows[0]["appointment_status"]

    # -- mutations ------------------------------------------------------------

    def create_hold(self, shipment_id: str, slot_id: str, ttl_minutes: int) -> HoldResult:
        self._expire_stale_holds()
        slot = self.slot_availability(slot_id)
        if slot is None:
            raise SlotUnavailableError(f"Unknown slot: {slot_id}")
        if slot["availability_status"] not in {"AVAILABLE"}:
            raise SlotUnavailableError("Selected slot is no longer available for hold")

        now = datetime.now().astimezone()
        expires_at = now + timedelta(minutes=ttl_minutes)
        hold_id = f"HLD-{uuid4().hex[:8].upper()}"
        try:
            self.backend.table("slot_holds").insert(
                {
                    "hold_id": hold_id,
                    "slot_id": slot_id,
                    "shipment_id": shipment_id,
                    "hold_status": "HELD",
                    "held_at": now.isoformat(timespec="seconds"),
                    "expires_at": expires_at.isoformat(timespec="seconds"),
                }
            ).execute()
        except APIError as exc:
            self._raise_persistence(exc)

        return HoldResult(
            hold_id=hold_id,
            slot_id=slot_id,
            shipment_id=shipment_id,
            expires_at=expires_at,
            lifecycle_stage=SlotLifecycleStage.HELD,
        )

    def release_hold(self, hold_id: str) -> None:
        now = _now_iso()
        try:
            self.backend.table("slot_holds").update({"hold_status": "RELEASED", "released_at": now}).eq(
                "hold_id", hold_id
            ).eq("hold_status", "HELD").execute()
        except APIError as exc:
            self._raise_persistence(exc)

    def create_pending_appointment(self, shipment_id: str, slot_id: str) -> str:
        now = _now_iso()
        slot = self.slot_availability(slot_id)
        if slot is None:
            raise SlotUnavailableError(f"Unknown slot: {slot_id}")

        hold = self.active_hold_for_shipment(shipment_id, slot_id)
        if slot["availability_status"] == "HELD" and (hold is None or hold["hold_id"] != slot["hold_id"]):
            raise SlotUnavailableError("Slot is held by another shipment")
        if slot["availability_status"] not in {"AVAILABLE", "HELD"}:
            raise SlotUnavailableError("Selected slot is no longer available")

        previous = self.current_appointment(shipment_id)
        try:
            if previous and previous["slot_id"] != slot_id:
                self.backend.table("appointments").update(
                    {"is_current": 0, "appointment_status": "CANCELLED", "cancelled_at": now, "updated_at": now}
                ).eq("appointment_id", previous["appointment_id"]).execute()

            existing = self._select(
                "appointments",
                shipment_id=shipment_id,
                slot_id=slot_id,
                is_current=1,
                appointment_status="PENDING_CONFIRMATION",
            )
            if existing:
                return existing[0]["appointment_id"]

            appointment_id = f"APT-PND-{uuid4().hex[:8].upper()}"
            self.backend.table("appointments").insert(
                {
                    "appointment_id": appointment_id,
                    "shipment_id": shipment_id,
                    "slot_id": slot_id,
                    "appointment_status": "PENDING_CONFIRMATION",
                    "booking_source": "SCHEDULING_TOOL",
                    "is_current": 1,
                    "booked_at": now,
                    "replaced_appointment_id": previous["appointment_id"] if previous else None,
                    "updated_at": now,
                }
            ).execute()
            return appointment_id
        except APIError as exc:
            self._raise_persistence(exc)

    def book_after_acceptance(self, shipment_id: str, slot_id: str, accepted: bool) -> str:
        if not accepted:
            raise InvalidBookingError("Explicit driver acceptance is required before booking")

        now = _now_iso()
        slot = self.slot_availability(slot_id)
        if slot is None:
            raise SlotUnavailableError(f"Unknown slot: {slot_id}")

        hold = self.active_hold_for_shipment(shipment_id, slot_id)
        pending_rows = self._select(
            "appointments",
            shipment_id=shipment_id,
            slot_id=slot_id,
            is_current=1,
            appointment_status="PENDING_CONFIRMATION",
        )
        pending = pending_rows[0] if pending_rows else None

        if slot["availability_status"] == "HELD":
            if hold is None:
                raise SlotUnavailableError("Selected slot is held by another shipment")
        elif slot["availability_status"] != "AVAILABLE":
            if pending is None:
                raise SlotUnavailableError("Selected slot is no longer available")

        try:
            previous = self.current_appointment(shipment_id)
            if previous and previous["slot_id"] != slot_id:
                self.backend.table("appointments").update(
                    {"is_current": 0, "appointment_status": "CANCELLED", "cancelled_at": now, "updated_at": now}
                ).eq("appointment_id", previous["appointment_id"]).execute()

            if pending:
                self.backend.table("appointments").update(
                    {"appointment_status": "CONFIRMED", "confirmed_at": now, "updated_at": now}
                ).eq("appointment_id", pending["appointment_id"]).execute()
                confirmed_id = pending["appointment_id"]
            else:
                confirmed_id = f"APT-{shipment_id}-{slot_id.split('-')[-1]}"
                self.backend.table("appointments").insert(
                    {
                        "appointment_id": confirmed_id,
                        "shipment_id": shipment_id,
                        "slot_id": slot_id,
                        "appointment_status": "CONFIRMED",
                        "booking_source": "DRIVER_CHAT",
                        "is_current": 1,
                        "booked_at": now,
                        "confirmed_at": now,
                        "replaced_appointment_id": previous["appointment_id"] if previous else None,
                        "updated_at": now,
                    }
                ).execute()

            if hold:
                self.backend.table("slot_holds").update({"hold_status": "CONVERTED", "released_at": now}).eq(
                    "hold_id", hold["hold_id"]
                ).execute()
        except APIError as exc:
            self._raise_persistence(exc)

        return confirmed_id

    def cancel_pending(self, shipment_id: str, slot_id: str) -> None:
        now = _now_iso()
        hold = self.active_hold_for_shipment(shipment_id, slot_id)
        try:
            if hold:
                self.backend.table("slot_holds").update({"hold_status": "RELEASED", "released_at": now}).eq(
                    "hold_id", hold["hold_id"]
                ).execute()
            self.backend.table("appointments").update(
                {"is_current": 0, "appointment_status": "CANCELLED", "cancelled_at": now, "updated_at": now}
            ).eq("shipment_id", shipment_id).eq("slot_id", slot_id).eq("is_current", 1).eq(
                "appointment_status", "PENDING_CONFIRMATION"
            ).execute()
        except APIError as exc:
            self._raise_persistence(exc)

    def _expire_stale_holds(self) -> None:
        now = _now_iso()
        try:
            self.backend.table("slot_holds").update({"hold_status": "EXPIRED", "released_at": now}).eq(
                "hold_status", "HELD"
            ).lte("expires_at", now).execute()
        except APIError as exc:
            self._raise_persistence(exc)
