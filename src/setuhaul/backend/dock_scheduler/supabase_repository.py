from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from setuhaul.backend.dock_scheduler.models import HoldResult
from setuhaul.infrastructure.supabase_client import create_public_client
from setuhaul.infrastructure.settings import get_settings


def _iso(ts: datetime) -> str:
    return ts.isoformat(timespec="seconds")


class SupabaseDockSchedulerRepository:
    """A lightweight Supabase-backed repository implementing the same surface
    as the SQLite `DockSchedulerRepository` used by the WMS service.

    This implementation assumes the project contains the same views and tables
    used by the SQLite repository (for example `v_inbound_operational_state`,
    `v_slot_availability`, `appointments`, `slot_holds`, `docks`, etc.). It uses
    the public/publishable key via `create_public_client`.
    """

    def __init__(self) -> None:
        settings = get_settings()
        self.client = create_public_client(settings)

    def shipment(self, shipment_id: str) -> dict[str, Any]:
        resp = (
            self.client.postgrest.from_("v_inbound_operational_state").select("*").eq("shipment_id", shipment_id).limit(1).execute()
        )
        data = resp.data or []
        if not data:
            raise Exception(f"Unknown shipment: {shipment_id}")
        return data[0]

    def facility(self, facility_id: str) -> dict[str, Any]:
        resp = self.client.postgrest.from_("facilities").select("*").eq("facility_id", facility_id).limit(1).execute()
        data = resp.data or []
        if not data:
            raise Exception(f"Unknown facility: {facility_id}")
        return data[0]

    def facility_rules(self, facility_id: str) -> list[dict[str, Any]]:
        resp = (
            self.client.postgrest.from_("facility_rules").select("*").eq("facility_id", facility_id).eq("active_flag", 1).order("rule_type").execute()
        )
        return resp.data or []

    def compatible_slots(self, shipment_id: str) -> list[dict[str, Any]]:
        # Rely on the view `v_slot_availability` to return available slots
        # filtered by facility via the view definition.
        resp = (
            self.client.postgrest.from_("v_slot_availability").select("*, docks(*)").eq("shipment_id", shipment_id).order("slot_start_ts").execute()
        )
        return resp.data or []

    def current_appointment(self, shipment_id: str) -> dict | None:
        resp = (
            self.client.postgrest.from_("appointments").select("*, appointment_slots(slot_start_ts,slot_end_ts,dock_id)").eq("shipment_id", shipment_id).eq("is_current", 1).in_("appointment_status", ["PENDING_CONFIRMATION","CONFIRMED","IN_PROGRESS"]).limit(1).execute()
        )
        data = resp.data or []
        return data[0] if data else None

    def slot_availability(self, slot_id: str) -> dict | None:
        resp = self.client.postgrest.from_("v_slot_availability").select("*").eq("slot_id", slot_id).limit(1).execute()
        data = resp.data or []
        return data[0] if data else None

    def active_hold_for_slot(self, slot_id: str) -> dict | None:
        resp = (
            self.client.postgrest.from_("slot_holds").select("*").eq("slot_id", slot_id).eq("hold_status", "HELD").order("held_at", desc=True).limit(1).execute()
        )
        data = resp.data or []
        return data[0] if data else None

    def active_hold_for_shipment(self, shipment_id: str, slot_id: str) -> dict | None:
        resp = (
            self.client.postgrest.from_("slot_holds").select("*").eq("shipment_id", shipment_id).eq("slot_id", slot_id).eq("hold_status", "HELD").order("held_at", desc=True).limit(1).execute()
        )
        data = resp.data or []
        return data[0] if data else None

    def create_hold(self, shipment_id: str, slot_id: str, ttl_minutes: int) -> HoldResult:
        now = datetime.now().astimezone()
        expires_at = now + timedelta(minutes=ttl_minutes)
        # Generate hold_id locally to match SQLite behaviour
        from uuid import uuid4

        hold_id = f"HLD-{uuid4().hex[:8].upper()}"
        payload = {
            "hold_id": hold_id,
            "slot_id": slot_id,
            "shipment_id": shipment_id,
            "hold_status": "HELD",
            "held_at": _iso(now),
            "expires_at": _iso(expires_at),
        }
        self.client.postgrest.from_("slot_holds").insert(payload).execute()
        return HoldResult(hold_id=hold_id, slot_id=slot_id, shipment_id=shipment_id, expires_at=expires_at)

    def release_hold(self, hold_id: str) -> None:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        self.client.postgrest.from_("slot_holds").update({"hold_status": "RELEASED", "released_at": now}).eq("hold_id", hold_id).execute()

    def create_pending_appointment(self, shipment_id: str, slot_id: str) -> str:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        from uuid import uuid4

        appointment_id = f"APT-PND-{uuid4().hex[:8].upper()}"
        payload = {
            "appointment_id": appointment_id,
            "shipment_id": shipment_id,
            "slot_id": slot_id,
            "appointment_status": "PENDING_CONFIRMATION",
            "booking_source": "SCHEDULING_TOOL",
            "is_current": True,
            "booked_at": now,
            "updated_at": now,
        }
        self.client.postgrest.from_("appointments").insert(payload).execute()
        return appointment_id

    def book_after_acceptance(self, shipment_id: str, slot_id: str, accepted: bool) -> str:
        if not accepted:
            raise Exception("Explicit driver acceptance is required before booking")
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        appointment_id = f"APT-{shipment_id}-{slot_id.split('-')[-1]}"
        payload = {
            "appointment_id": appointment_id,
            "shipment_id": shipment_id,
            "slot_id": slot_id,
            "appointment_status": "CONFIRMED",
            "booking_source": "DRIVER_CHAT",
            "is_current": True,
            "booked_at": now,
            "confirmed_at": now,
            "updated_at": now,
        }
        self.client.postgrest.from_("appointments").insert(payload).execute()
        # mark any converted holds
        hold = self.active_hold_for_shipment(shipment_id, slot_id)
        if hold:
            self.client.postgrest.from_("slot_holds").update({"hold_status": "CONVERTED", "released_at": now}).eq("hold_id", hold["hold_id"]).execute()
        return appointment_id

    def cancel_pending(self, shipment_id: str, slot_id: str) -> None:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        hold = self.active_hold_for_shipment(shipment_id, slot_id)
        if hold:
            self.client.postgrest.from_("slot_holds").update({"hold_status": "RELEASED", "released_at": now}).eq("hold_id", hold["hold_id"]).execute()
        self.client.postgrest.from_("appointments").update({"is_current": False, "appointment_status": "CANCELLED", "cancelled_at": now, "updated_at": now}).match({"shipment_id": shipment_id, "slot_id": slot_id, "is_current": True, "appointment_status": "PENDING_CONFIRMATION"}).execute()

    def _expire_stale_holds(self, within_transaction: bool = False) -> None:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        self.client.postgrest.from_("slot_holds").update({"hold_status": "EXPIRED", "released_at": now}).lte("expires_at", now).eq("hold_status", "HELD").execute()
