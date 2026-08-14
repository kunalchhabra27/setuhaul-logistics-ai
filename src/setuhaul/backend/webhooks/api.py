"""Receives Supabase Database Webhook calls and invalidates the affected
Redis cache keys -- this is the second, independent invalidation path
alongside the synchronous ``cache.invalidate_*`` calls each service.py
mutation already makes. It exists specifically for writes that bypass our
own FastAPI mutation endpoints entirely (a row edited directly in the
Supabase dashboard, a script, a different service) -- those never run our
mutation code, so without this, our cache would only ever learn about the
change once its TTL happened to expire.

The corresponding Postgres trigger lives in
``supabase/migrations/*_cache_invalidation_webhook.sql`` and is inert until
that migration's target URL is pointed at a real, publicly reachable
deployment of this backend -- Supabase's hosted Postgres cannot reach a
plain localhost dev server. TTL remains the fallback for any environment
where this webhook can't fire (local dev, no public URL, delivery failure).
"""

from __future__ import annotations

import hmac
import logging
from typing import Any, Callable, Literal

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from setuhaul.infrastructure import cache
from setuhaul.infrastructure.settings import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# Only Supabase's own `public` schema is ever expected -- a payload
# explicitly claiming a different schema is ignored, not acted on. A
# missing `schema` field (e.g. an older/manually-built payload) is treated
# as `public` by default, matching what Supabase's real Database Webhooks
# always send in practice; this isn't the security boundary (the secret
# check is) so a permissive default here is fine.
SUPPORTED_SCHEMA = "public"


class SupabaseWebhookPayload(BaseModel):
    """Shape Supabase's Database Webhooks POST for every row change:
    https://supabase.com/docs/guides/database/webhooks -- `record` is the
    new row (INSERT/UPDATE) and `old_record` is the previous row
    (UPDATE/DELETE); exactly one of them is populated for INSERT/DELETE."""

    type: Literal["INSERT", "UPDATE", "DELETE"]
    table: str
    record: dict[str, Any] | None = None
    old_record: dict[str, Any] | None = None
    schema_: str | None = Field(default=None, alias="schema")


def _row(payload: SupabaseWebhookPayload) -> dict[str, Any]:
    """The row's data, preferring the new version but falling back to the
    old one for a DELETE (where `record` is null)."""
    return payload.record or payload.old_record or {}


def _invalidate_shipments_row(payload: SupabaseWebhookPayload) -> bool:
    row = _row(payload)
    shipment_id = row.get("shipment_id")
    # Any shipments-table event sweeps the list caches regardless of
    # whether this specific row carried an id (cheap, defensively correct);
    # the targeted per-shipment invalidation is what "did we have an
    # identifier to act on" actually means for the response status below.
    cache.invalidate_shipments_lists()
    if not shipment_id:
        return False
    cache.invalidate_shipment(shipment_id)
    facility_id = row.get("destination_facility_id")
    if facility_id:
        cache.invalidate_facility_board(facility_id)
    return True


def _invalidate_dock_state_row(payload: SupabaseWebhookPayload) -> bool:
    # appointment_slots / slot_holds / appointments: a change to any of
    # these can shift availability_status for every shipment that could use
    # the affected slot, not just one -- same reasoning as
    # DockSchedulerService's own mutations, see invalidate_dock_boards. This
    # sweep needs no row identifier to be meaningful, so it always reports
    # "did something" even when the row carries no shipment_id.
    cache.invalidate_dock_boards()
    row = _row(payload)
    shipment_id = row.get("shipment_id")
    if shipment_id:
        cache.invalidate_shipment(shipment_id)
    return True


def _invalidate_facility_checkins_row(payload: SupabaseWebhookPayload) -> bool:
    row = _row(payload)
    shipment_id = row.get("shipment_id")
    cache.invalidate_shipments_lists()
    if not shipment_id:
        return False
    cache.invalidate_shipment(shipment_id)
    return True


def _invalidate_change_requests_row(_payload: SupabaseWebhookPayload) -> bool:
    cache.invalidate_change_requests()
    return True


def _invalidate_drivers_row(payload: SupabaseWebhookPayload) -> bool:
    row = _row(payload)
    driver_id = row.get("driver_id")
    if not driver_id:
        return False
    cache.invalidate_driver(driver_id)
    return True


def _invalidate_vehicles_row(payload: SupabaseWebhookPayload) -> bool:
    row = _row(payload)
    vehicle_id = row.get("vehicle_id")
    if not vehicle_id:
        return False
    cache.invalidate_vehicle(vehicle_id)
    return True


def _invalidate_facilities_row(payload: SupabaseWebhookPayload) -> bool:
    row = _row(payload)
    facility_id = row.get("facility_id")
    if not facility_id:
        return False
    cache.invalidate_facility(facility_id)
    return True


def _invalidate_docks_row(payload: SupabaseWebhookPayload) -> bool:
    row = _row(payload)
    facility_id = row.get("facility_id")
    if not facility_id:
        return False
    cache.invalidate_facility(facility_id)
    return True


def _invalidate_carriers_row(_payload: SupabaseWebhookPayload) -> bool:
    cache.invalidate_carriers()
    return True


# One handler per table we actually cache a view of. Anything not listed
# here has no cached read backing it, so there's nothing to invalidate --
# the endpoint just no-ops for it (see the 200-with-"ignored" response
# below) rather than erroring, since Supabase will retry a non-2xx delivery.
# Each handler returns True if it performed a meaningful, targeted
# invalidation, False if the row had no usable identifier to act on.
_TABLE_HANDLERS: dict[str, Callable[[SupabaseWebhookPayload], bool]] = {
    "shipments": _invalidate_shipments_row,
    "appointment_slots": _invalidate_dock_state_row,
    "slot_holds": _invalidate_dock_state_row,
    "appointments": _invalidate_dock_state_row,
    "facility_checkins": _invalidate_facility_checkins_row,
    "dock_slot_change_requests": _invalidate_change_requests_row,
    "drivers": _invalidate_drivers_row,
    "vehicles": _invalidate_vehicles_row,
    "facilities": _invalidate_facilities_row,
    "docks": _invalidate_docks_row,
    "carriers": _invalidate_carriers_row,
}


@router.post("/supabase")
def handle_supabase_webhook(
    payload: SupabaseWebhookPayload,
    x_webhook_secret: str | None = Header(default=None),
) -> dict[str, str]:
    """Verify the shared secret, then bust whatever this row's table maps to.

    Always resolves quickly and never raises past this boundary for a
    recognized table -- an invalidation failure here degrades to "TTL will
    catch it eventually", never a 500 that makes Supabase retry-storm the
    delivery. The one exception is the secret check itself: a missing/wrong
    secret is a real 401, not silently accepted, so this can't be used as an
    open cache-flush endpoint by anyone who finds the URL. The comparison is
    constant-time (``hmac.compare_digest``) so response timing can't be used
    to brute-force the secret one character at a time.
    """
    settings = get_settings()
    configured_secret = settings.webhook_secret
    if not configured_secret or not x_webhook_secret or not hmac.compare_digest(x_webhook_secret, configured_secret):
        raise HTTPException(status_code=401, detail="Invalid or missing webhook secret.")

    if payload.schema_ is not None and payload.schema_ != SUPPORTED_SCHEMA:
        return {"status": "ignored", "table": payload.table}

    handler = _TABLE_HANDLERS.get(payload.table)
    if handler is None:
        return {"status": "ignored", "table": payload.table}

    try:
        invalidated = handler(payload)
    except Exception:  # noqa: BLE001 - invalidation must never fail the webhook delivery
        logger.exception(
            "webhooks.supabase: invalidation failed for table=%s type=%s; TTL will still expire it.",
            payload.table,
            payload.type,
        )
        return {"status": "invalidation-error", "table": payload.table}

    if not invalidated:
        return {"status": "no-op-missing-identifier", "table": payload.table}
    return {"status": "invalidated", "table": payload.table}
