import { createApiClient } from "./api";
import type { NextShipmentIds, ShipmentCreateInput, ShipmentSummary, TmsDriver, TmsFacility, TmsVehicle } from "../types/api";

const api = createApiClient("tms");

export function listShipments() {
  return api.request<ShipmentSummary[]>("/tms/shipments");
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

export function getNextShipmentIds() {
  return api.request<NextShipmentIds>("/tms/shipments/next-ids");
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

export function archiveShipment(shipmentId: string) {
  return api.request<ShipmentSummary>(`/tms/shipments/${encodeURIComponent(shipmentId)}/archive`, {
    method: "POST",
  });
}
