"""Persistence boundary for TMS-owned tables (public.drivers / vehicles / shipments)."""

from __future__ import annotations

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
        if code in {"23503", "23514", "22P02"}:
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

    def update_shipment(self, shipment_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        return self._update("shipments", "shipment_id", shipment_id, payload)

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

    # -- shared helpers ---------------------------------------------------

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
