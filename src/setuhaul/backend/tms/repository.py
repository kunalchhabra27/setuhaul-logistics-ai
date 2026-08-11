"""Persistence boundary for TMS-owned tables (public.drivers / vehicles / shipments)."""

from __future__ import annotations

import re
from typing import Any, Iterable, TYPE_CHECKING

from postgrest.exceptions import APIError

from setuhaul.backend.tms.exceptions import BusinessValidationError, ConflictError, PersistenceError
from setuhaul.backend.tms.models import ACTIVE_CONTEXT_STATUSES, ShipmentStatus

if TYPE_CHECKING:
    from supabase import Client
else:
    Client = Any

DRIVER_COLUMNS = "driver_id,carrier_id,driver_name,phone,licence_number,home_base_city,driver_status"
VEHICLE_COLUMNS = "vehicle_id,carrier_id,registration_number,vehicle_type_code,capacity_kg,refrigeration_capable,active_flag"
SHIPMENT_COLUMNS = (
    "shipment_id,order_reference,carrier_id,driver_id,vehicle_id,origin_name,origin_city,"
    "destination_facility_id,customer_name,product_category,load_weight_kg,"
    "required_dock_type,temperature_control_required,priority_code,planned_departure_ts,"
    "original_eta_ts,latest_eta_ts,expected_unload_min,current_status,"
    "created_at,updated_at"
)
# archived_flag is selected separately (ARCHIVE_COLUMN) since it depends on a migration
# (20260811100000_tms_shipments_archive.sql) that may not be applied yet -- keeping it out
# of the base column list means shipment listing keeps working before that migration runs.
ARCHIVE_COLUMN = "archived_flag"
FACILITY_COLUMNS = "facility_id,facility_name,city,state"
STAFF_FACILITY_COLUMNS = "staff_user_id,facility_id,created_at,updated_at"


class TMSRepository:
    def __init__(self, backend: Client):
        self.backend = backend

    @staticmethod
    def _data(response: Any) -> list[dict[str, Any]]:
        return list(response.data or [])

    def _raise_persistence(self, exc: APIError) -> None:
        code = str(getattr(exc, "code", ""))
        message = str(getattr(exc, "message", "Database operation failed."))
        if code == "23505":
            raise ConflictError("A record with the same unique identifier already exists.") from exc
        if code in {"23502", "23503", "23514", "22P02"}:
            # 23502 = not_null_violation, 23503 = foreign_key_violation,
            # 23514 = check_violation, 22P02 = invalid_text_representation --
            # all of these mean the request itself was malformed/incomplete
            # rather than a genuine server failure, so surface Postgres's own
            # message instead of the generic 500-ish PersistenceError below.
            raise BusinessValidationError(message) from exc
        raise PersistenceError("The TMS database operation failed.") from exc

    # -- drivers ------------------------------------------------------------

    def get_driver(self, driver_id: str) -> dict[str, Any] | None:
        try:
            rows = self._data(
                self.backend.table("drivers").select(DRIVER_COLUMNS).eq("driver_id", driver_id).limit(1).execute()
            )
        except APIError as exc:
            self._raise_persistence(exc)
        return rows[0] if rows else None

    def get_driver_by_phone(self, phone: str) -> dict[str, Any] | None:
        try:
            rows = self._data(
                self.backend.table("drivers").select(DRIVER_COLUMNS).eq("phone", phone).limit(1).execute()
            )
        except APIError as exc:
            self._raise_persistence(exc)
        return rows[0] if rows else None

    def list_drivers(self, *, limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
        try:
            return self._data(
                self.backend.table("drivers")
                .select(DRIVER_COLUMNS)
                .order("driver_id", desc=False)
                .range(offset, offset + limit - 1)
                .execute()
            )
        except APIError as exc:
            self._raise_persistence(exc)

    def create_driver(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload = {**payload, "driver_id": payload.get("driver_id") or self._next_sequential_id("drivers", "driver_id", "DRV", 3)}
        return self._create("drivers", payload)

    def update_driver(self, driver_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        return self._update("drivers", "driver_id", driver_id, payload)

    # -- vehicles -------------------------------------------------------------

    def get_vehicle(self, vehicle_id: str) -> dict[str, Any] | None:
        try:
            rows = self._data(
                self.backend.table("vehicles").select(VEHICLE_COLUMNS).eq("vehicle_id", vehicle_id).limit(1).execute()
            )
        except APIError as exc:
            self._raise_persistence(exc)
        return rows[0] if rows else None

    def get_vehicles(self, vehicle_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
        values = [value for value in vehicle_ids if value]
        if not values:
            return {}
        try:
            rows = self._data(
                self.backend.table("vehicles").select(VEHICLE_COLUMNS).in_("vehicle_id", values).execute()
            )
        except APIError as exc:
            self._raise_persistence(exc)
        return {row["vehicle_id"]: row for row in rows}

    def list_vehicles(self, *, limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
        try:
            return self._data(
                self.backend.table("vehicles")
                .select(VEHICLE_COLUMNS)
                .order("vehicle_id", desc=False)
                .range(offset, offset + limit - 1)
                .execute()
            )
        except APIError as exc:
            self._raise_persistence(exc)

    def create_vehicle(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload = {**payload, "vehicle_id": payload.get("vehicle_id") or self._next_sequential_id("vehicles", "vehicle_id", "VEH", 3)}
        return self._create("vehicles", payload)

    def update_vehicle(self, vehicle_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        return self._update("vehicles", "vehicle_id", vehicle_id, payload)

    # -- shipments ------------------------------------------------------------

    def get_shipment(self, shipment_id: str) -> dict[str, Any] | None:
        try:
            rows = self._data(
                self.backend.table("shipments")
                .select(f"{SHIPMENT_COLUMNS},{ARCHIVE_COLUMN}")
                .eq("shipment_id", shipment_id)
                .limit(1)
                .execute()
            )
        except APIError as exc:
            self._raise_persistence(exc)
        return rows[0] if rows else None

    def list_shipments(
        self,
        *,
        driver_id: str | None = None,
        destination_facility_id: str | None = None,
        status: ShipmentStatus | None = None,
        active_only: bool = False,
        unassigned_only: bool = False,
        include_archived: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        query = self.backend.table("shipments").select(f"{SHIPMENT_COLUMNS},{ARCHIVE_COLUMN}")
        if driver_id is not None:
            query = query.eq("driver_id", driver_id)
        if destination_facility_id is not None:
            query = query.eq("destination_facility_id", destination_facility_id)
        if status is not None:
            query = query.eq("current_status", status.value)
        if active_only:
            query = query.in_("current_status", sorted(item.value for item in ACTIVE_CONTEXT_STATUSES))
        if unassigned_only:
            query = query.is_("driver_id", "null")
        if not include_archived:
            # archived_flag is an integer column (0/1), not native boolean -- passing
            # Python False here gets str()'d by postgrest-py into the filter as
            # "eq.False", which Postgres rejects ("invalid input syntax for type
            # integer"). Use 0 explicitly.
            query = query.eq(ARCHIVE_COLUMN, 0)
        query = query.order("original_eta_ts", desc=False, nullsfirst=False).range(offset, offset + limit - 1)
        try:
            return self._data(query.execute())
        except APIError as exc:
            self._raise_persistence(exc)

    def create_shipment(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._create("shipments", payload)

    def generate_shipment_id(self) -> str:
        """Next sequential shipment id, continuing the seed data's SHP1001-style
        numbering (e.g. highest existing SHP#### + 1) instead of a random suffix."""
        return self._next_sequential_id("shipments", "shipment_id", "SHP", 4)

    def update_shipment(self, shipment_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        return self._update("shipments", "shipment_id", shipment_id, payload)

    # -- WMS / check-in trace (read-only; dock_scheduler / checkin_portal own
    #    these tables) -----------------------------------------------------
    #
    # TMS has no need to mutate appointments, slots, docks, or check-ins --
    # it only reads them, through the same caller-scoped client, to let a
    # dispatcher trace a shipment's dock booking and arrival status without
    # switching portals. Same pattern driver_chat_eta already uses to read
    # across bounded contexts: direct table reads, no cross-service HTTP
    # calls, RLS enforces what the caller is actually allowed to see.

    def current_appointment_for_shipment(self, shipment_id: str) -> dict[str, Any] | None:
        try:
            rows = self._data(
                self.backend.table("appointments")
                .select("appointment_id,shipment_id,slot_id,appointment_status")
                .eq("shipment_id", shipment_id)
                .eq("is_current", 1)
                .limit(1)
                .execute()
            )
        except APIError as exc:
            self._raise_persistence(exc)
        if not rows:
            return None
        appointment = rows[0]
        slot_id = appointment.get("slot_id")
        if not slot_id:
            return {**appointment, "slot_start_ts": None, "slot_end_ts": None, "dock_code": None}
        try:
            slot_rows = self._data(
                self.backend.table("appointment_slots")
                .select("slot_id,dock_id,slot_start_ts,slot_end_ts")
                .eq("slot_id", slot_id)
                .limit(1)
                .execute()
            )
        except APIError as exc:
            self._raise_persistence(exc)
        if not slot_rows:
            return {**appointment, "slot_start_ts": None, "slot_end_ts": None, "dock_code": None}
        slot = slot_rows[0]
        dock_code = None
        if slot.get("dock_id"):
            try:
                dock_rows = self._data(
                    self.backend.table("docks").select("dock_code").eq("dock_id", slot["dock_id"]).limit(1).execute()
                )
            except APIError as exc:
                self._raise_persistence(exc)
            dock_code = dock_rows[0]["dock_code"] if dock_rows else None
        return {
            **appointment,
            "slot_start_ts": slot.get("slot_start_ts"),
            "slot_end_ts": slot.get("slot_end_ts"),
            "dock_code": dock_code,
        }

    def checkin_for_shipment(self, shipment_id: str) -> dict[str, Any] | None:
        try:
            rows = self._data(
                self.backend.table("facility_checkins")
                .select("shipment_id,arrival_state,queue_state,gate_in_ts,dock_in_ts,unload_end_ts")
                .eq("shipment_id", shipment_id)
                .limit(1)
                .execute()
            )
        except APIError as exc:
            self._raise_persistence(exc)
        return rows[0] if rows else None

    # -- facilities -------------------------------------------------------

    def list_facilities(self, *, limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
        try:
            return self._data(
                self.backend.table("facilities")
                .select(FACILITY_COLUMNS)
                .order("facility_name", desc=False)
                .range(offset, offset + limit - 1)
                .execute()
            )
        except APIError as exc:
            self._raise_persistence(exc)

    def list_shipment_reference_data(self) -> dict[str, list[Any]]:
        """Distinct, already-used values for the open-ended (non-enum) text
        columns on shipments -- origin_name/origin_city/product_category have
        no lookup table and no CHECK constraint in the schema, so "real data
        from Supabase" means whatever has actually been typed into existing
        rows, de-duplicated here (PostgREST has no SELECT DISTINCT). Powers
        the shipment-creation dropdowns instead of letting a dispatcher type
        a fresh, unvalidated string for a value that should be one of a
        small recurring set.
        """
        try:
            rows = self._data(
                self.backend.table("shipments").select("origin_name,origin_city,product_category").execute()
            )
        except APIError as exc:
            self._raise_persistence(exc)
        seen_origins: set[tuple[str, str]] = set()
        origins: list[dict[str, str]] = []
        categories: set[str] = set()
        for row in rows:
            name, city = row.get("origin_name"), row.get("origin_city")
            if name and (name, city) not in seen_origins:
                seen_origins.add((name, city))
                origins.append({"origin_name": name, "origin_city": city})
            category = row.get("product_category")
            if category:
                categories.add(category)
        origins.sort(key=lambda o: o["origin_name"])
        return {"origins": origins, "product_categories": sorted(categories)}

    def get_facility(self, facility_id: str) -> dict[str, Any] | None:
        try:
            rows = self._data(
                self.backend.table("facilities").select(FACILITY_COLUMNS).eq("facility_id", facility_id).limit(1).execute()
            )
        except APIError as exc:
            self._raise_persistence(exc)
        return rows[0] if rows else None

    # -- staff facility assignments (WMS/Check-in facility scoping) -------
    #
    # One row per Supabase Auth staff user, tracking which single warehouse
    # facility they registered for. staff_user_id is always the caller's own
    # verified auth id (see TMSService.register_staff_facility) -- never a
    # client-supplied value -- so this table (and the facility filter it
    # drives on shipment queries) can't be used to read or claim another
    # facility's data.

    def get_staff_facility(self, staff_user_id: str) -> dict[str, Any] | None:
        try:
            rows = self._data(
                self.backend.table("staff_facility_assignments")
                .select(STAFF_FACILITY_COLUMNS)
                .eq("staff_user_id", staff_user_id)
                .limit(1)
                .execute()
            )
        except APIError as exc:
            self._raise_persistence(exc)
        return rows[0] if rows else None

    def register_staff_facility(self, staff_user_id: str, facility_id: str) -> dict[str, Any]:
        payload = {"staff_user_id": staff_user_id, "facility_id": facility_id}
        try:
            rows = self._data(
                self.backend.table("staff_facility_assignments")
                .upsert(payload, on_conflict="staff_user_id")
                .execute()
            )
        except APIError as exc:
            self._raise_persistence(exc)
        if not rows:
            raise PersistenceError("The staff facility assignment upsert returned no record.")
        return rows[0]

    # -- shared helpers ---------------------------------------------------

    def _next_sequential_id(self, table: str, id_column: str, prefix: str, default_width: int) -> str:
        """Generate the next sequential id like "PREFIX0001", continuing from
        whatever ids already exist in the table (e.g. DRV001, DRV002, ...
        -> DRV003) rather than a random suffix -- every id column in this
        schema is plain text with no DB-side default/sequence, so the app
        must always compute one before insert.

        Read-then-insert with no DB-side lock: a genuine race under
        concurrent creates could hand out the same id twice, which would
        then fail on the table's primary-key uniqueness constraint. Same
        caveat already accepted elsewhere in this codebase (see
        dock_scheduler's hold creation) -- acceptable for this app's scale.
        """
        try:
            rows = self._data(self.backend.table(table).select(id_column).execute())
        except APIError as exc:
            self._raise_persistence(exc)
        pattern = re.compile(rf"^{re.escape(prefix)}(\d+)$")
        max_n = 0
        width = default_width
        for row in rows:
            match = pattern.match(str(row.get(id_column) or ""))
            if not match:
                continue
            digits = match.group(1)
            max_n = max(max_n, int(digits))
            width = max(width, len(digits))
        return f"{prefix}{str(max_n + 1).zfill(width)}"

    @staticmethod
    def _coerce_booleans(payload: dict[str, Any]) -> dict[str, Any]:
        """Convert Python bool values to 0/1 ints.

        drivers/vehicles/shipments store booleans as integer columns (see
        refrigeration_capable, active_flag, temperature_control_required,
        archived_flag) rather than native Postgres boolean -- PostgREST/Postgres
        rejects a JSON `true`/`false` literal against an integer column.
        """
        return {key: (int(value) if isinstance(value, bool) else value) for key, value in payload.items()}

    def _create(self, table: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            rows = self._data(self.backend.table(table).insert(self._coerce_booleans(payload)).execute())
        except APIError as exc:
            self._raise_persistence(exc)
        if not rows:
            raise PersistenceError(f"The {table} insert returned no record.")
        return rows[0]

    def _update(self, table: str, key: str, record_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        try:
            rows = self._data(self.backend.table(table).update(self._coerce_booleans(payload)).eq(key, record_id).execute())
        except APIError as exc:
            self._raise_persistence(exc)
        return rows[0] if rows else None
