import { createApiClient } from "./api";
import type { CheckInRecord, FacilityStaffAssignment, ShipmentSummary, TmsFacility } from "../types/api";

const api = createApiClient("checkin");

// Uses the Check-in portal's own session token (not TMS's) to hit the shared
// shipments listing -- lets the Check-in panel offer a real shipment picker
// instead of a hardcoded shipment id, without depending on a TMS login.
export function listShipmentsForCheckin() {
  return api.request<ShipmentSummary[]>("/tms/shipments");
}

// -- facility-scoped registration (see PortalWorkspace's Check-in facility gate) --

export function listFacilitiesForRegistration() {
  return api.request<TmsFacility[]>("/tms/facilities");
}

export function getMyCheckinFacility() {
  return api.request<FacilityStaffAssignment>("/tms/facility-staff/me");
}

export function registerMyCheckinFacility(facilityId: string) {
  return api.request<FacilityStaffAssignment>("/tms/facility-staff/register", {
    method: "POST",
    body: JSON.stringify({ facility_id: facilityId }),
  });
}

// Shipments scoped to ONLY the Check-in staff member's own registered
// facility -- resolved server-side, never a client-supplied parameter.
export function listShipmentsForMyFacilityCheckin() {
  return api.request<ShipmentSummary[]>("/tms/facility-staff/shipments");
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

// Staff sign-off on a driver-reported gate arrival -- only after this does
// the shipment's status become visible to TMS/WMS as checked in.
export function approveGateCheckin(shipmentId: string) {
  return api.request<CheckInRecord>("/checkins/approve-gate", {
    method: "PATCH",
    body: JSON.stringify({ shipment_id: shipmentId }),
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
