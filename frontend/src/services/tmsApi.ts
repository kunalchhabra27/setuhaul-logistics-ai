import { createApiClient } from "./api";
import type {
  ChangeRequest,
  DockSlot,
  ShipmentContext,
  ShipmentCreateInput,
  ShipmentReferenceData,
  ShipmentSummary,
  TmsDriver,
  TmsFacility,
  TmsVehicle,
} from "../types/api";

const api = createApiClient("tms");

export function listShipments() {
  // include_archived=true so the TMS panel can offer an Active/Historical
  // toggle client-side without a second round trip -- the export endpoint
  // already fetches everything regardless of this, so this just keeps the
  // on-screen dashboard and the download consistent with each other.
  return api.request<ShipmentSummary[]>("/tms/shipments?include_archived=true&limit=500");
}

export function listDrivers() {
  return api.request<TmsDriver[]>("/tms/drivers");
}

export function listVehicles() {
  return api.request<TmsVehicle[]>("/tms/vehicles");
}

export function listFacilities() {
  return api.request<TmsFacility[]>("/tms/facilities");
}

export function getShipmentReferenceData() {
  return api.request<ShipmentReferenceData>("/tms/reference/shipment-options");
}

export function assignShipmentDriver(shipmentId: string, driverId: string, vehicleId?: string | null) {
  return api.request<ShipmentSummary>(`/tms/shipments/${encodeURIComponent(shipmentId)}/assign`, {
    method: "POST",
    body: JSON.stringify({ driver_id: driverId, ...(vehicleId ? { vehicle_id: vehicleId } : {}) }),
  });
}

export function createShipment(input: ShipmentCreateInput) {
  return api.request<ShipmentSummary>("/tms/shipments", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function getShipmentContext(shipmentId: string) {
  return api.request<ShipmentContext>(`/tms/context/shipments/${encodeURIComponent(shipmentId)}`);
}

export function archiveShipment(shipmentId: string) {
  return api.request<ShipmentSummary>(`/tms/shipments/${encodeURIComponent(shipmentId)}/archive`, {
    method: "POST",
  });
}

export function cancelShipment(shipmentId: string, reason?: string) {
  return api.request<ShipmentSummary>(`/tms/shipments/${encodeURIComponent(shipmentId)}/cancel`, {
    method: "POST",
    body: JSON.stringify({ reason: reason ?? null }),
  });
}

export async function downloadShipmentsExport() {
  const { blob, filename } = await api.requestBlob("/tms/reports/shipments-export");
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename ?? "setuhaul-shipments.xlsx";
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

// Dock-board lookup for the TMS-side "change dock slot" picker -- same
// endpoint WMS's own board uses, called through TMS's own token.
export function getDockBoardForShipment(shipmentId: string) {
  return api.request<DockSlot[]>(`/dock-scheduler/board?shipment_id=${encodeURIComponent(shipmentId)}`);
}

export function requestDockSlotChange(shipmentId: string, requestedSlotId: string, reason?: string) {
  return api.request<ChangeRequest>("/dock-scheduler/change-requests", {
    method: "POST",
    body: JSON.stringify({
      shipment_id: shipmentId,
      requested_slot_id: requestedSlotId,
      requested_by_role: "TMS",
      reason: reason ?? null,
    }),
  });
}
