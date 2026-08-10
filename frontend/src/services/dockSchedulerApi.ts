import { createApiClient } from "./api";
import type { DockSlot, DockSuggestion, ShipmentSummary } from "../types/api";

const api = createApiClient("wms");

export function suggestSlots(input: { shipment_id: string; earliest_start?: string; must_finish_by?: string; limit?: number }) {
  return api.request<DockSuggestion[]>("/dock-scheduler/suggest", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

// Every compatible slot for a shipment, grouped by dock on the frontend --
// used to render the full visual dock board, unlike suggestSlots() above
// which only returns the top-ranked handful of options.
export function getDockBoard(shipmentId: string) {
  return api.request<DockSlot[]>(`/dock-scheduler/board?shipment_id=${encodeURIComponent(shipmentId)}`);
}

export function holdSlot(input: { shipment_id: string; slot_id: string; ttl_minutes?: number }) {
  return api.request<{ hold_id: string; slot_id: string; shipment_id: string; expires_at: string }>(
    "/dock-scheduler/hold",
    { method: "POST", body: JSON.stringify(input) }
  );
}

export function confirmBooking(input: { shipment_id: string; slot_id: string; accepted?: boolean }) {
  return api.request<{ appointment_id: string; shipment_id: string; slot_id: string }>(
    "/dock-scheduler/confirm",
    { method: "POST", body: JSON.stringify(input) }
  );
}

// Uses the WMS portal's own session token (not TMS's) to hit the shared
// shipments listing -- lets the WMS panel offer a real shipment picker
// instead of a hardcoded shipment id, without depending on a TMS login.
export function listShipmentsForScheduling() {
  return api.request<ShipmentSummary[]>("/tms/shipments");
}
