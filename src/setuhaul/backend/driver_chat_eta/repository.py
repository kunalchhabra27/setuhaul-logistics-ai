"""Persistence boundary for driver-chat-eta-owned Supabase queries.

Every method here runs through a caller-scoped Supabase client (the driver's
own JWT), so every read/write is subject to whatever RLS policies exist on
these tables. See README.md in this folder for the follow-up migration this
backend expects. Table/column names match the real schema the user supplied
(see the module docstring in models.py) -- every id is ``text``, every
enum-ish column is UPPER_SNAKE_CASE ``text``, and boolean-ish columns are
``integer`` 0/1.
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from postgrest.exceptions import APIError

from setuhaul.backend.driver_chat_eta.exceptions import ConflictError, PersistenceError

if TYPE_CHECKING:
    from supabase import Client
else:
    Client = Any


def to_int_flag(value: bool | None) -> int | None:
    if value is None:
        return None
    return 1 if value else 0


class DriverChatRepository:
    def __init__(self, client: Client):
        self.client = client

    @staticmethod
    def _rows(response: Any) -> list[dict[str, Any]]:
        return list(response.data or [])

    def _raise_persistence(self, exc: APIError) -> None:
        code = str(getattr(exc, "code", ""))
        message = str(getattr(exc, "message", "Database operation failed."))
        if code == "23505":
            raise ConflictError("A record with the same unique identifier already exists.") from exc
        raise PersistenceError(f"The driver-chat-eta database operation failed: {message}") from exc

    # -- drivers ------------------------------------------------------

    def get_driver(self, driver_id: str) -> dict[str, Any] | None:
        try:
            rows = self._rows(self.client.table("drivers").select("*").eq("driver_id", driver_id).limit(1).execute())
        except APIError as exc:
            self._raise_persistence(exc)
        return rows[0] if rows else None

    def upsert_driver(self, driver_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        payload = {**payload, "driver_id": driver_id}
        try:
            rows = self._rows(self.client.table("drivers").upsert(payload, on_conflict="driver_id").execute())
        except APIError as exc:
            self._raise_persistence(exc)
        if not rows:
            raise PersistenceError("The driver profile upsert returned no record.")
        return rows[0]

    # -- carriers -------------------------------------------------------

    def list_carriers(self) -> list[dict[str, Any]]:
        """Existing carriers for the profile-completion dropdown.

        Read-only on purpose -- drivers pick from what's already in Supabase
        rather than typing a name that used to silently create a new carrier
        record with a random id (see git history for the old
        find_or_create_carrier behaviour this replaced).
        """
        try:
            return self._rows(
                self.client.table("carriers")
                .select("carrier_id,carrier_name")
                .order("carrier_name", desc=False)
                .execute()
            )
        except APIError as exc:
            self._raise_persistence(exc)

    def get_carrier(self, carrier_id: str) -> dict[str, Any] | None:
        try:
            rows = self._rows(
                self.client.table("carriers").select("carrier_id,carrier_name").eq("carrier_id", carrier_id).limit(1).execute()
            )
        except APIError as exc:
            self._raise_persistence(exc)
        return rows[0] if rows else None

    def list_active_vehicle_carrier_ids(self) -> set[str]:
        """carrier_ids that currently have at least one active vehicle on
        file -- used to flag, at carrier-selection time, a carrier a driver
        is about to register under that has no active vehicle yet (rather
        than only surfacing that gap later when TMS tries to create a
        shipment for them)."""
        try:
            rows = self._rows(
                self.client.table("vehicles").select("carrier_id").eq("active_flag", 1).execute()
            )
        except APIError as exc:
            self._raise_persistence(exc)
        return {row["carrier_id"] for row in rows if row.get("carrier_id")}

    def list_home_base_cities(self) -> list[str]:
        """Distinct, already-used driver home_base_city values -- there's no
        lookup table for this column, so "real data from Supabase" means
        whatever cities existing driver rows already have on file."""
        try:
            rows = self._rows(self.client.table("drivers").select("home_base_city").execute())
        except APIError as exc:
            self._raise_persistence(exc)
        cities = {row["home_base_city"] for row in rows if row.get("home_base_city")}
        return sorted(cities)

    # -- vehicles / facilities / docks ---------------------------------

    def get_vehicle(self, vehicle_id: str) -> dict[str, Any] | None:
        try:
            rows = self._rows(
                self.client.table("vehicles").select("*").eq("vehicle_id", vehicle_id).limit(1).execute()
            )
        except APIError as exc:
            self._raise_persistence(exc)
        return rows[0] if rows else None

    def get_facility(self, facility_id: str) -> dict[str, Any] | None:
        try:
            rows = self._rows(
                self.client.table("facilities").select("*").eq("facility_id", facility_id).limit(1).execute()
            )
        except APIError as exc:
            self._raise_persistence(exc)
        return rows[0] if rows else None

    def list_docks(self, facility_id: str) -> list[dict[str, Any]]:
        try:
            return self._rows(
                self.client.table("docks")
                .select("*")
                .eq("facility_id", facility_id)
                .eq("dock_status", "ACTIVE")
                .execute()
            )
        except APIError as exc:
            self._raise_persistence(exc)

    # -- shipments --------------------------------------------------------

    def get_active_shipment_for_driver(self, driver_id: str) -> dict[str, Any] | None:
        try:
            rows = self._rows(
                self.client.table("shipments")
                .select("*")
                .eq("driver_id", driver_id)
                .not_.in_("current_status", ["COMPLETED", "CANCELLED"])
                .order("original_eta_ts", desc=False)
                .limit(1)
                .execute()
            )
        except APIError as exc:
            self._raise_persistence(exc)
        return rows[0] if rows else None

    def update_shipment(self, shipment_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        try:
            rows = self._rows(
                self.client.table("shipments").update(payload).eq("shipment_id", shipment_id).execute()
            )
        except APIError as exc:
            self._raise_persistence(exc)
        return rows[0] if rows else None

    def insert_eta_update(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            rows = self._rows(self.client.table("eta_updates").insert(payload).execute())
        except APIError as exc:
            self._raise_persistence(exc)
        if not rows:
            raise PersistenceError("The ETA update insert returned no record.")
        return rows[0]

    # -- appointment slots & holds ------------------------------------------

    def list_open_slots(self, facility_id: str) -> list[dict[str, Any]]:
        try:
            return self._rows(
                self.client.table("appointment_slots")
                .select("*")
                .eq("facility_id", facility_id)
                .eq("slot_status", "OPEN")
                .order("slot_start_ts", desc=False)
                .execute()
            )
        except APIError as exc:
            self._raise_persistence(exc)

    def get_slot(self, slot_id: str) -> dict[str, Any] | None:
        try:
            rows = self._rows(
                self.client.table("appointment_slots").select("*").eq("slot_id", slot_id).limit(1).execute()
            )
        except APIError as exc:
            self._raise_persistence(exc)
        return rows[0] if rows else None

    def list_active_holds_for_facility_slots(self, slot_ids: list[str]) -> list[dict[str, Any]]:
        if not slot_ids:
            return []
        try:
            return self._rows(
                self.client.table("slot_holds")
                .select("*")
                .in_("slot_id", slot_ids)
                .eq("hold_status", "HELD")
                .execute()
            )
        except APIError as exc:
            self._raise_persistence(exc)

    def get_active_hold_for_shipment(self, shipment_id: str) -> dict[str, Any] | None:
        try:
            rows = self._rows(
                self.client.table("slot_holds")
                .select("*")
                .eq("shipment_id", shipment_id)
                .eq("hold_status", "HELD")
                .order("held_at", desc=True)
                .limit(1)
                .execute()
            )
        except APIError as exc:
            self._raise_persistence(exc)
        return rows[0] if rows else None

    def create_hold(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            rows = self._rows(self.client.table("slot_holds").insert(payload).execute())
        except APIError as exc:
            self._raise_persistence(exc)
        if not rows:
            raise PersistenceError("The slot hold insert returned no record.")
        return rows[0]

    def update_hold(self, hold_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        try:
            rows = self._rows(self.client.table("slot_holds").update(payload).eq("hold_id", hold_id).execute())
        except APIError as exc:
            self._raise_persistence(exc)
        return rows[0] if rows else None

    # -- appointments -------------------------------------------------

    def get_current_appointment_for_shipment(self, shipment_id: str) -> dict[str, Any] | None:
        try:
            rows = self._rows(
                self.client.table("appointments")
                .select("*")
                .eq("shipment_id", shipment_id)
                .eq("is_current", 1)
                .limit(1)
                .execute()
            )
        except APIError as exc:
            self._raise_persistence(exc)
        return rows[0] if rows else None

    def create_appointment(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            rows = self._rows(self.client.table("appointments").insert(payload).execute())
        except APIError as exc:
            self._raise_persistence(exc)
        if not rows:
            raise PersistenceError("The appointment insert returned no record.")
        return rows[0]

    def update_appointment(self, appointment_id: str, payload: dict[str, Any]) -> None:
        try:
            self.client.table("appointments").update(payload).eq("appointment_id", appointment_id).execute()
        except APIError as exc:
            self._raise_persistence(exc)

    # -- facility checkins ----------------------------------------------

    def get_checkin_for_shipment(self, shipment_id: str) -> dict[str, Any] | None:
        try:
            rows = self._rows(
                self.client.table("facility_checkins").select("*").eq("shipment_id", shipment_id).limit(1).execute()
            )
        except APIError as exc:
            self._raise_persistence(exc)
        return rows[0] if rows else None

    def create_checkin(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            rows = self._rows(self.client.table("facility_checkins").insert(payload).execute())
        except APIError as exc:
            self._raise_persistence(exc)
        if not rows:
            raise PersistenceError("The check-in insert returned no record.")
        return rows[0]

    def update_checkin(self, checkin_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        try:
            rows = self._rows(
                self.client.table("facility_checkins").update(payload).eq("checkin_id", checkin_id).execute()
            )
        except APIError as exc:
            self._raise_persistence(exc)
        return rows[0] if rows else None

    # -- driver exceptions ------------------------------------------------

    def get_active_exception_for_driver(self, driver_id: str) -> dict[str, Any] | None:
        try:
            rows = self._rows(
                self.client.table("driver_exceptions")
                .select("*")
                .eq("driver_id", driver_id)
                .not_.in_("exception_status", ["RESOLVED", "CANCELLED", "DUPLICATE"])
                .order("reported_at", desc=True)
                .limit(1)
                .execute()
            )
        except APIError as exc:
            self._raise_persistence(exc)
        return rows[0] if rows else None

    def create_exception(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            rows = self._rows(self.client.table("driver_exceptions").insert(payload).execute())
        except APIError as exc:
            self._raise_persistence(exc)
        if not rows:
            raise PersistenceError("The exception insert returned no record.")
        return rows[0]

    def update_exception(self, exception_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        try:
            rows = self._rows(
                self.client.table("driver_exceptions").update(payload).eq("exception_id", exception_id).execute()
            )
        except APIError as exc:
            self._raise_persistence(exc)
        return rows[0] if rows else None

    # -- chat threads & messages ------------------------------------------

    def get_open_thread_for_driver(self, driver_id: str) -> dict[str, Any] | None:
        try:
            rows = self._rows(
                self.client.table("chat_threads")
                .select("*")
                .eq("driver_id", driver_id)
                .not_.in_("thread_status", ["RESOLVED", "CLOSED"])
                .order("opened_at", desc=True)
                .limit(1)
                .execute()
            )
        except APIError as exc:
            self._raise_persistence(exc)
        return rows[0] if rows else None

    def get_thread_for_driver(self, thread_id: str, driver_id: str) -> dict[str, Any] | None:
        """Return a thread only when it belongs to the authenticated driver."""
        try:
            rows = self._rows(
                self.client.table("chat_threads")
                .select("*")
                .eq("thread_id", thread_id)
                .eq("driver_id", driver_id)
                .limit(1)
                .execute()
            )
        except APIError as exc:
            self._raise_persistence(exc)
        return rows[0] if rows else None

    def create_thread(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            rows = self._rows(self.client.table("chat_threads").insert(payload).execute())
        except APIError as exc:
            self._raise_persistence(exc)
        if not rows:
            raise PersistenceError("The thread insert returned no record.")
        return rows[0]

    def update_thread(self, thread_id: str, payload: dict[str, Any]) -> None:
        try:
            self.client.table("chat_threads").update(payload).eq("thread_id", thread_id).execute()
        except APIError as exc:
            self._raise_persistence(exc)

    def list_chat_messages(self, thread_id: str, limit: int = 100) -> list[dict[str, Any]]:
        try:
            return self._rows(
                self.client.table("chat_messages")
                .select("*")
                .eq("thread_id", thread_id)
                .order("message_ts", desc=False)
                .limit(limit)
                .execute()
            )
        except APIError as exc:
            self._raise_persistence(exc)

    def insert_chat_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            rows = self._rows(self.client.table("chat_messages").insert(payload).execute())
        except APIError as exc:
            self._raise_persistence(exc)
        if not rows:
            raise PersistenceError("The chat message insert returned no record.")
        return rows[0]
