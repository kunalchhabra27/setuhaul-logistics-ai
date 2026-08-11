"""Persistence boundary for facility check-ins (public.facility_checkins).

Rewritten against the real Supabase project (verified live on 2026-08-10),
replacing the local SQLite version this used to run against. The real table's
`arrival_state` (EARLY/ON_TIME/LATE/NO_SHOW -- timeliness vs. schedule) and
`queue_state` (NOT_QUEUED/WAITING_EARLY/WAITING_LATE/WAITING_DOCK_UNAVAILABLE/
CALLED_TO_DOCK/IN_DOCK/COMPLETED -- gate-to-dock progression) columns and
value vocabularies exactly matched what the old SQLite prototype (seeded from
`data/setuhaul_schema_and_seed.sql`) already assumed, confirmed via a live
schema probe. So the domain-mapping logic below (`_to_domain_*`/`_to_db_*`) is
carried over unchanged from the SQLite version -- only the storage calls
themselves moved from sqlite3 to the Supabase Data API.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, TYPE_CHECKING

from postgrest.exceptions import APIError

if TYPE_CHECKING:
    from supabase import Client
else:
    Client = Any

CHECKIN_COLUMNS = (
    "checkin_id,shipment_id,facility_id,gate_in_ts,yard_queue_enter_ts,dock_in_ts,"
    "unload_start_ts,unload_end_ts,gate_out_ts,arrival_state,queue_state,"
    "queue_position,actual_dock_id,notes,updated_at"
)


class PersistenceError(Exception):
    """Raised when an unexpected Supabase/PostgREST error occurs."""


class CheckInRepository:
    """Access facility check-in records stored in Supabase."""

    def __init__(self, backend: Client):
        self.backend = backend

    @staticmethod
    def _data(response: Any) -> list[dict[str, Any]]:
        return list(response.data or [])

    def _raise_persistence(self, exc: APIError) -> None:
        raise PersistenceError(str(getattr(exc, "message", "Check-in database operation failed."))) from exc

    def get_driver_contact_for_shipment(self, shipment_id: str) -> dict[str, Any] | None:
        """Look up the phone number of the driver currently assigned to a
        shipment, for the gate check-in SMS notification. Reads straight from
        the shipments/drivers tables via the same caller-scoped client used
        everywhere else in this repository -- there is no cross-table view,
        so this is two plain lookups rather than a join. Returns None if the
        shipment has no driver assigned or the driver has no phone on file.
        """
        try:
            shipment_rows = self._data(
                self.backend.table("shipments")
                .select("shipment_id,driver_id,order_reference")
                .eq("shipment_id", shipment_id)
                .limit(1)
                .execute()
            )
        except APIError as exc:
            self._raise_persistence(exc)
        if not shipment_rows or not shipment_rows[0].get("driver_id"):
            return None

        driver_id = shipment_rows[0]["driver_id"]
        try:
            driver_rows = self._data(
                self.backend.table("drivers")
                .select("driver_id,driver_name,phone")
                .eq("driver_id", driver_id)
                .limit(1)
                .execute()
            )
        except APIError as exc:
            self._raise_persistence(exc)
        if not driver_rows or not driver_rows[0].get("phone"):
            return None

        return {
            "driver_id": driver_id,
            "driver_name": driver_rows[0].get("driver_name"),
            "phone": driver_rows[0]["phone"],
            "order_reference": shipment_rows[0].get("order_reference"),
        }

    def shipment_exists(self, shipment_id: str) -> bool:
        try:
            rows = self._data(
                self.backend.table("shipments").select("shipment_id").eq("shipment_id", shipment_id).limit(1).execute()
            )
        except APIError as exc:
            self._raise_persistence(exc)
        return bool(rows)

    def get_confirmed_dock_for_shipment(self, shipment_id: str) -> dict[str, Any] | None:
        """The CONFIRMED WMS appointment's dock for a shipment, if any --
        used to require a real dock booking before staff can mark a truck
        docked, and to auto-fill actual_dock_id from it (same rule
        driver_chat_eta's own check-in flow already applies). Reads
        dock_scheduler-owned tables directly through the same caller-scoped
        client, not a second implementation of appointment lookup."""
        try:
            appt_rows = self._data(
                self.backend.table("appointments")
                .select("appointment_id,slot_id")
                .eq("shipment_id", shipment_id)
                .eq("is_current", 1)
                .eq("appointment_status", "CONFIRMED")
                .limit(1)
                .execute()
            )
        except APIError as exc:
            self._raise_persistence(exc)
        if not appt_rows or not appt_rows[0].get("slot_id"):
            return None
        try:
            slot_rows = self._data(
                self.backend.table("appointment_slots")
                .select("slot_id,dock_id")
                .eq("slot_id", appt_rows[0]["slot_id"])
                .limit(1)
                .execute()
            )
        except APIError as exc:
            self._raise_persistence(exc)
        if not slot_rows or not slot_rows[0].get("dock_id"):
            return None
        try:
            dock_rows = self._data(
                self.backend.table("docks").select("dock_id,dock_code").eq("dock_id", slot_rows[0]["dock_id"]).limit(1).execute()
            )
        except APIError as exc:
            self._raise_persistence(exc)
        if not dock_rows:
            return None
        return {"dock_id": dock_rows[0]["dock_id"], "dock_code": dock_rows[0].get("dock_code")}

    def get_by_shipment(self, shipment_id: str) -> dict[str, Any] | None:
        try:
            rows = self._data(
                self.backend.table("facility_checkins")
                .select(CHECKIN_COLUMNS)
                .eq("shipment_id", shipment_id)
                .limit(1)
                .execute()
            )
        except APIError as exc:
            self._raise_persistence(exc)
        if not rows:
            return None
        record = rows[0]
        return {
            **record,
            "arrival_status": self._to_domain_arrival_status(record.get("arrival_state")),
            "queue_status": self._to_domain_queue_status(record.get("queue_state")),
            "gate_in_at": record.get("gate_in_ts"),
            "dock_in_at": record.get("dock_in_ts"),
            "completed_at": record.get("unload_end_ts"),
        }

    def create_gate_checkin(self, checkin_id: str, shipment_id: str, facility_id: str, gate_in_at: str | datetime) -> None:
        try:
            self.backend.table("facility_checkins").insert(
                {
                    "checkin_id": checkin_id,
                    "shipment_id": shipment_id,
                    "facility_id": facility_id,
                    "gate_in_ts": str(gate_in_at),
                    "arrival_state": self._to_db_arrival_status("GATE_IN"),
                    "queue_state": self._to_db_queue_status("GATE_QUEUE"),
                    "updated_at": str(gate_in_at),
                }
            ).execute()
        except APIError as exc:
            self._raise_persistence(exc)

    def update_queue(self, shipment_id: str, queue_status: str) -> None:
        try:
            self.backend.table("facility_checkins").update(
                {
                    "arrival_state": self._to_db_arrival_status("WAITING"),
                    "queue_state": self._to_db_queue_status(queue_status),
                    "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                }
            ).eq("shipment_id", shipment_id).execute()
        except APIError as exc:
            self._raise_persistence(exc)

    def mark_docked(self, shipment_id: str, dock_in_at: str | datetime, actual_dock_id: str | None = None) -> None:
        payload: dict[str, Any] = {
            "arrival_state": self._to_db_arrival_status("DOCKED"),
            "queue_state": self._to_db_queue_status("NONE"),
            "dock_in_ts": str(dock_in_at),
            "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        if actual_dock_id:
            payload["actual_dock_id"] = actual_dock_id
        try:
            self.backend.table("facility_checkins").update(payload).eq("shipment_id", shipment_id).execute()
        except APIError as exc:
            self._raise_persistence(exc)

    def mark_completed(self, shipment_id: str, completed_at: str | datetime) -> None:
        try:
            self.backend.table("facility_checkins").update(
                {
                    "arrival_state": self._to_db_arrival_status("COMPLETED"),
                    "queue_state": self._to_db_queue_status("NONE"),
                    "unload_end_ts": str(completed_at),
                    "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                }
            ).eq("shipment_id", shipment_id).execute()
        except APIError as exc:
            self._raise_persistence(exc)

    # -- domain <-> real-column value mapping ---------------------------------
    #
    # arrival_status (API/domain): GATE_IN / WAITING / DOCKED / COMPLETED
    # arrival_state (real column): EARLY / ON_TIME / LATE / NO_SHOW
    #
    # queue_status (API/domain): NONE / GATE_QUEUE / YARD_QUEUE / CALLED_TO_DOCK
    # queue_state (real column): NOT_QUEUED / WAITING_EARLY / WAITING_LATE /
    #   WAITING_DOCK_UNAVAILABLE / CALLED_TO_DOCK / IN_DOCK / COMPLETED

    @staticmethod
    def _to_domain_arrival_status(value: str | None) -> str | None:
        mapping = {
            "EARLY": "GATE_IN",
            "ON_TIME": "WAITING",
            "LATE": "DOCKED",
            "NO_SHOW": "COMPLETED",
        }
        return mapping.get(value, value)

    @staticmethod
    def _to_domain_queue_status(value: str | None) -> str | None:
        mapping = {
            "NOT_QUEUED": "NONE",
            "WAITING_EARLY": "GATE_QUEUE",
            "WAITING_LATE": "YARD_QUEUE",
            "WAITING_DOCK_UNAVAILABLE": "YARD_QUEUE",
            "CALLED_TO_DOCK": "CALLED_TO_DOCK",
            "IN_DOCK": "CALLED_TO_DOCK",
            "COMPLETED": "NONE",
        }
        return mapping.get(value, value)

    @staticmethod
    def _to_db_arrival_status(value: str) -> str:
        mapping = {
            "GATE_IN": "EARLY",
            "WAITING": "ON_TIME",
            "DOCKED": "LATE",
            "COMPLETED": "NO_SHOW",
        }
        return mapping.get(value, value)

    @staticmethod
    def _to_db_queue_status(value: str) -> str:
        mapping = {
            "NONE": "NOT_QUEUED",
            "GATE_QUEUE": "WAITING_EARLY",
            "YARD_QUEUE": "WAITING_LATE",
            "CALLED_TO_DOCK": "CALLED_TO_DOCK",
        }
        return mapping.get(value, value)
