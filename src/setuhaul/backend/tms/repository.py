"""Persistence boundary for TMS-owned tables."""

from __future__ import annotations

import sqlite3
from typing import Any, Iterable, TYPE_CHECKING
from uuid import UUID

from postgrest.exceptions import APIError

from setuhaul.backend.tms.exceptions import BusinessValidationError, ConflictError, PersistenceError
from setuhaul.backend.tms.models import ACTIVE_CONTEXT_STATUSES, ShipmentStatus

if TYPE_CHECKING:
    from supabase import Client
else:
    Client = Any


class TMSRepository:
    def __init__(self, backend: Client | sqlite3.Connection):
        self.backend = backend
        self._is_sqlite = isinstance(backend, sqlite3.Connection)

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

    def get_driver(self, driver_id: UUID) -> dict[str, Any] | None:
        if self._is_sqlite:
            row = self.backend.execute(
                "SELECT * FROM drivers WHERE driver_id = ?", (str(driver_id),)
            ).fetchone()
            return self._normalize_driver(row) if row else None
        try:
            rows = self._data(self.backend.table("drivers").select("*").eq("driver_id", str(driver_id)).limit(1).execute())
        except APIError as exc:
            self._raise_persistence(exc)
        return rows[0] if rows else None

    def get_driver_by_phone(self, phone: str) -> dict[str, Any] | None:
        if self._is_sqlite:
            row = self.backend.execute(
                "SELECT * FROM drivers WHERE phone = ?", (phone,)
            ).fetchone()
            return self._normalize_driver(row) if row else None
        try:
            rows = self._data(self.backend.table("drivers").select("*").eq("phone", phone).limit(1).execute())
        except APIError as exc:
            self._raise_persistence(exc)
        return rows[0] if rows else None

    def create_driver(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._is_sqlite:
            raise PersistenceError("Local TMS create operations are not enabled in this snapshot.")
        return self._create("drivers", payload)

    def update_driver(self, driver_id: UUID, payload: dict[str, Any]) -> dict[str, Any] | None:
        if self._is_sqlite:
            raise PersistenceError("Local TMS update operations are not enabled in this snapshot.")
        return self._update("drivers", "driver_id", driver_id, payload)

    def get_vehicle(self, vehicle_id: UUID) -> dict[str, Any] | None:
        if self._is_sqlite:
            row = self.backend.execute(
                "SELECT * FROM vehicles WHERE vehicle_id = ?", (str(vehicle_id),)
            ).fetchone()
            return self._normalize_vehicle(row) if row else None
        try:
            rows = self._data(self.backend.table("vehicles").select("*").eq("vehicle_id", str(vehicle_id)).limit(1).execute())
        except APIError as exc:
            self._raise_persistence(exc)
        return rows[0] if rows else None

    def get_vehicles(self, vehicle_ids: Iterable[UUID]) -> dict[UUID, dict[str, Any]]:
        values = [str(value) for value in vehicle_ids]
        if not values:
            return {}
        if self._is_sqlite:
            rows = self.backend.execute(
                f"SELECT * FROM vehicles WHERE vehicle_id IN ({','.join('?' for _ in values)})",
                values,
            ).fetchall()
            return {UUID(row["vehicle_id"]): self._normalize_vehicle(row) for row in rows}
        try:
            rows = self._data(self.backend.table("vehicles").select("*").in_("vehicle_id", values).execute())
        except APIError as exc:
            self._raise_persistence(exc)
        return {UUID(row["vehicle_id"]): row for row in rows}

    def create_vehicle(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._is_sqlite:
            raise PersistenceError("Local TMS create operations are not enabled in this snapshot.")
        return self._create("vehicles", payload)

    def update_vehicle(self, vehicle_id: UUID, payload: dict[str, Any]) -> dict[str, Any] | None:
        if self._is_sqlite:
            raise PersistenceError("Local TMS update operations are not enabled in this snapshot.")
        return self._update("vehicles", "vehicle_id", vehicle_id, payload)

    def get_shipment(self, shipment_id: UUID) -> dict[str, Any] | None:
        if self._is_sqlite:
            row = self.backend.execute(
                "SELECT * FROM shipments WHERE shipment_id = ?", (str(shipment_id),)
            ).fetchone()
            return self._normalize_shipment(row) if row else None
        try:
            rows = self._data(self.backend.table("shipments").select("*").eq("shipment_id", str(shipment_id)).limit(1).execute())
        except APIError as exc:
            self._raise_persistence(exc)
        return rows[0] if rows else None

    def list_shipments(self, *, driver_id: UUID | None = None, destination_id: UUID | None = None, status: ShipmentStatus | None = None, active_only: bool = False, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        if self._is_sqlite:
            query = "SELECT * FROM shipments WHERE 1=1"
            params: list[Any] = []
            if driver_id is not None:
                query += " AND driver_id = ?"
                params.append(str(driver_id))
            if destination_id is not None:
                query += " AND destination_id = ?"
                params.append(str(destination_id))
            if status is not None:
                query += " AND current_status = ?"
                params.append(status.value)
            if active_only:
                statuses = sorted(item.value for item in ACTIVE_CONTEXT_STATUSES)
                query += f" AND current_status IN ({','.join('?' for _ in statuses)})"
                params.extend(statuses)
            query += " ORDER BY original_eta_ts LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            rows = self.backend.execute(query, params).fetchall()
            return [self._normalize_shipment(row) for row in rows]
        query = self.backend.table("shipments").select("*")
        if driver_id is not None:
            query = query.eq("driver_id", str(driver_id))
        if destination_id is not None:
            query = query.eq("destination_id", str(destination_id))
        if status is not None:
            query = query.eq("status", status.value)
        if active_only:
            query = query.in_("status", sorted(item.value for item in ACTIVE_CONTEXT_STATUSES))
        query = query.order("planned_eta", desc=False, nullsfirst=False).range(offset, offset + limit - 1)
        try:
            return self._data(query.execute())
        except APIError as exc:
            self._raise_persistence(exc)

    def create_shipment(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._is_sqlite:
            raise PersistenceError("Local TMS create operations are not enabled in this snapshot.")
        return self._create("shipments", payload)

    def update_shipment(self, shipment_id: UUID, payload: dict[str, Any]) -> dict[str, Any] | None:
        if self._is_sqlite:
            raise PersistenceError("Local TMS update operations are not enabled in this snapshot.")
        return self._update("shipments", "shipment_id", shipment_id, payload)

    def _create(self, table: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            rows = self._data(self.backend.table(table).insert(payload).execute())
        except APIError as exc:
            self._raise_persistence(exc)
        if not rows:
            raise PersistenceError(f"The {table} insert returned no record.")
        return rows[0]

    def _update(self, table: str, key: str, record_id: UUID, payload: dict[str, Any]) -> dict[str, Any] | None:
        try:
            rows = self._data(self.backend.table(table).update(payload).eq(key, str(record_id)).execute())
        except APIError as exc:
            self._raise_persistence(exc)
        return rows[0] if rows else None

    @staticmethod
    def _normalize_driver(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        return {
            "driver_id": data["driver_id"],
            "carrier_id": data["carrier_id"],
            "driver_code": data.get("driver_code") or data.get("driver_name"),
            "name": data.get("driver_name"),
            "phone": data.get("phone"),
            "email": None,
            "license_number": data.get("licence_number"),
            "license_expiry": None,
            "home_base": data.get("home_base_city"),
            "active_flag": bool(data.get("driver_status") == "active"),
            "status": data.get("driver_status"),
            "created_at": data.get("created_at"),
            "updated_at": data.get("updated_at"),
        }

    @staticmethod
    def _normalize_vehicle(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        return {
            "vehicle_id": data["vehicle_id"],
            "carrier_id": data["carrier_id"],
            "vehicle_number": data.get("registration_number"),
            "vehicle_type": data.get("vehicle_type_code"),
            "length_ft": None,
            "capacity_weight_kg": data.get("capacity_kg"),
            "refrigeration_required": bool(data.get("refrigeration_capable")),
            "active_flag": bool(data.get("active_flag")),
            "status": "active" if data.get("active_flag") else "inactive",
            "created_at": None,
            "updated_at": None,
        }

    @staticmethod
    def _normalize_shipment(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        return {
            "shipment_id": data["shipment_id"],
            "driver_id": data.get("driver_id"),
            "vehicle_id": data.get("vehicle_id"),
            "origin_id": None,
            "destination_id": data.get("destination_facility_id"),
            "product_class": data.get("product_category"),
            "priority": data.get("priority_code"),
            "planned_eta": data.get("original_eta_ts"),
            "expected_unload_minutes": data.get("expected_unload_min"),
            "status": data.get("current_status"),
            "created_at": data.get("created_at"),
            "updated_at": data.get("updated_at"),
        }
