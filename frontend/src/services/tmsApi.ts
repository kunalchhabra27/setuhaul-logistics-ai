import { api } from "./api";
import type { ShipmentSummary } from "../types/api";

export function listShipments() {
  return api.request<ShipmentSummary[]>("/tms/shipments");
}
