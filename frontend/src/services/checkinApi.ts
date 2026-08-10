import { createApiClient } from "./api";
import type { CheckInRecord, ShipmentSummary } from "../types/api";

const api = createApiClient("checkin");

// Uses the Check-in portal's own session token (not TMS's) to hit the shared
// shipments listing -- lets the Check-in panel offer a real shipment picker
// instead of a hardcoded shipment id, without depending on a TMS login.
export function listShipmentsForCheckin() {
  return api.request<ShipmentSummary[]>("/tms/shipments");
}

export function fetchCheckInStatus(shipmentId: string) {
  return api.request<CheckInRecord>(`/checkins/${encodeURIComponent(shipmentId)}`);
}

export function gateCheckIn(input: { shipment_id: string; facility_id: string; gate_in_at: string }) {
  return api.request<CheckInRecord>("/checkins/gate", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function updateQueue(input: { shipment_id: string; queue_status: "NONE" | "GATE_QUEUE" | "YARD_QUEUE" | "CALLED_TO_DOCK" }) {
  return api.request<CheckInRecord>("/checkins/queue", {
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export function markDocked(input: { shipment_id: string; dock_in_at: string }) {
  return api.request<CheckInRecord>("/checkins/dock", {
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export function completeUnload(input: { shipment_id: string; completed_at: string }) {
  return api.request<CheckInRecord>("/checkins/complete", {
    method: "PATCH",
    body: JSON.stringify(input),
  });
}
