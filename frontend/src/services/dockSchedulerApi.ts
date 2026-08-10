import { api } from "./api";
import type { DockSuggestion, HoldResponse } from "../types/api";

export function suggestSlots(input: { shipment_id: string; earliest_start?: string; must_finish_by?: string; limit?: number }) {
  return api.request<DockSuggestion[]>("/dock-scheduler/suggest", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function holdSlot(input: { shipment_id: string; slot_id: string; ttl_minutes?: number }) {
  return api.request<HoldResponse>("/dock-scheduler/hold", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function requestConfirmation(input: { shipment_id: string; slot_id: string; ttl_minutes?: number }) {
  return api.request<HoldResponse>("/dock-scheduler/request-confirmation", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function confirmBooking(input: { shipment_id: string; slot_id: string; accepted?: boolean }) {
  return api.request<{ appointment_id: string; shipment_id: string; slot_id: string; lifecycle_stage: string }>("/dock-scheduler/confirm", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function cancelHold(input: { hold_id: string }) {
  return api.request<{ status: string; hold_id: string }>("/dock-scheduler/cancel-hold", {
    method: "POST",
    body: JSON.stringify(input),
  });
}
