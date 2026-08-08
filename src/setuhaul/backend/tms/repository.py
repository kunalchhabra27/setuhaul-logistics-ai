"""Supabase persistence boundary for TMS-owned tables."""

from __future__ import annotations

from typing import Any, Iterable
from uuid import UUID

from postgrest.exceptions import APIError
from supabase import Client

from setuhaul.backend.tms.exceptions import (
    BusinessValidationError,
    ConflictError,
    PersistenceError,
)
from setuhaul.backend.tms.models import ACTIVE_CONTEXT_STATUSES, ShipmentStatus


class TMSRepository:
    """Perform small, caller-RLS-scoped Supabase Data API operations."""

    def __init__(self, client: Client):
        self.client = client

    @staticmethod
    def _data(response: Any) -> list[dict[str, Any]]:
        return list(response.data or [])

    @staticmethod
    def _raise_persistence(exc: APIError) -> None:
        code = str(getattr(exc, "code", ""))
        message = str(getattr(exc, "message", "Database operation failed."))
        if code == "23505":
            raise ConflictError("A record with the same unique identifier already exists.") from exc
        if code in {"23503", "23514", "22P02"}:
            raise BusinessValidationError(message) from exc
        raise PersistenceError("The TMS database operation failed.") from exc

    def get_driver(self, driver_id: UUID) -> dict[str, Any] | None:
        try:
            rows = self._data(
                self.client.table("drivers").select("*").eq("driver_id", str(driver_id)).limit(1).execute()
            )
        except APIError as exc:
            self._raise_persistence(exc)
        return rows[0] if rows else None

    def get_driver_by_phone(self, phone: str) -> dict[str, Any] | None:
        try:
            rows = self._data(
                self.client.table("drivers").select("*").eq("phone", phone).limit(1).execute()
            )
        except APIError as exc:
            self._raise_persistence(exc)
        return rows[0] if rows else None

    def create_driver(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._create("drivers", payload)

    def update_driver(self, driver_id: UUID, payload: dict[str, Any]) -> dict[str, Any] | None:
        return self._update("drivers", "driver_id", driver_id, payload)

    def get_vehicle(self, vehicle_id: UUID) -> dict[str, Any] | None:
        try:
            rows = self._data(
                self.client.table("vehicles").select("*").eq("vehicle_id", str(vehicle_id)).limit(1).execute()
            )
        except APIError as exc:
            self._raise_persistence(exc)
        return rows[0] if rows else None

    def get_vehicles(self, vehicle_ids: Iterable[UUID]) -> dict[UUID, dict[str, Any]]:
        values = [str(value) for value in vehicle_ids]
        if not values:
            return {}
        try:
            rows = self._data(
                self.client.table("vehicles").select("*").in_("vehicle_id", values).execute()
            )
        except APIError as exc:
            self._raise_persistence(exc)
        return {UUID(row["vehicle_id"]): row for row in rows}

    def create_vehicle(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._create("vehicles", payload)

    def update_vehicle(self, vehicle_id: UUID, payload: dict[str, Any]) -> dict[str, Any] | None:
        return self._update("vehicles", "vehicle_id", vehicle_id, payload)

    def get_shipment(self, shipment_id: UUID) -> dict[str, Any] | None:
        try:
            rows = self._data(
                self.client.table("shipments").select("*").eq("shipment_id", str(shipment_id)).limit(1).execute()
            )
        except APIError as exc:
            self._raise_persistence(exc)
        return rows[0] if rows else None

    def list_shipments(
        self,
        *,
        driver_id: UUID | None = None,
        destination_id: UUID | None = None,
        status: ShipmentStatus | None = None,
        active_only: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        query = self.client.table("shipments").select("*")
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
        return self._create("shipments", payload)

    def update_shipment(self, shipment_id: UUID, payload: dict[str, Any]) -> dict[str, Any] | None:
        return self._update("shipments", "shipment_id", shipment_id, payload)

    def _create(self, table: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            rows = self._data(self.client.table(table).insert(payload).execute())
        except APIError as exc:
            self._raise_persistence(exc)
        if not rows:
            raise PersistenceError(f"The {table} insert returned no record.")
        return rows[0]

    def _update(
        self,
        table: str,
        key: str,
        record_id: UUID,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        try:
            rows = self._data(
                self.client.table(table).update(payload).eq(key, str(record_id)).execute()
            )
        except APIError as exc:
            self._raise_persistence(exc)
        return rows[0] if rows else None
