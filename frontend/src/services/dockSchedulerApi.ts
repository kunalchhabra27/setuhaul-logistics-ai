import { api } from "./api";
import type { DockSuggestion } from "../types/api";

export function suggestSlots(input: { shipment_id: string; earliest_start?: string; must_finish_by?: string; limit?: number }) {
  return api.request<DockSuggestion[]>("/dock-scheduler/suggest", {
    method: "POST",
    body: JSON.stringify(input),
  });
}
