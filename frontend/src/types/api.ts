export type ApiErrorShape = {
  error?: {
    code?: string;
    message?: string;
    details?: unknown;
  };
  detail?: string;
};

export type CheckInRecord = {
  checkin_id: string;
  shipment_id: string;
  facility_id: string;
  gate_in_at: string | null;
  arrival_status: "GATE_IN" | "WAITING" | "DOCKED" | "COMPLETED";
  queue_status: "NONE" | "GATE_QUEUE" | "YARD_QUEUE" | "CALLED_TO_DOCK";
  dock_in_at: string | null;
  completed_at: string | null;
};

export type ShipmentSummary = {
  shipment_id: string;
  status?: string;
  planned_eta?: string | null;
  destination_id?: string;
  driver_id?: string;
};

export type DockSuggestion = {
  rank: number;
  suggestion_type: string;
  slot_id: string;
  dock_code: string;
  start: string;
  end: string;
  reason: string;
  lifecycle_stage: string;
  displaced_shipment_id?: string | null;
  displaced_to_slot_id?: string | null;
};
