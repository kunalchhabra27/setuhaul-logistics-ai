import { createApiClient } from "./api";
import type { CheckInRecord, CheckInShipmentSummary, TmsFacility } from "../types/api";

const api = createApiClient("checkin");

export function listShipmentsForCheckin() {
  return api.request<CheckInShipmentSummary[]>("/checkins/shipments");
}

export function listCheckinFacilityOptions() {
  return api.request<TmsFacility[]>("/checkins/facilities/options");
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
