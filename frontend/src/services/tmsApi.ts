import { api } from "./api";
import type { ShipmentRecord, ShipmentSummary } from "../types/api";

export function listShipments() {
  return api.request<ShipmentSummary[]>("/tms/shipments");
}

export function getShipment(shipmentId: string) {
  return api.request<ShipmentRecord>(`/tms/shipments/${encodeURIComponent(shipmentId)}`);
}

export function createShipment(input: {
  driver_id: string;
  vehicle_id: string;
  destination_id: string;
  product_class: string;
  priority: number;
  expected_unload_minutes: number;
  origin_id?: string;
  planned_eta?: string;
  status?: string;
}) {
  return api.request<ShipmentRecord>("/tms/shipments", {
    method: "POST",
    body: JSON.stringify(input),
  });
}
