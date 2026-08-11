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

export type ShipmentStatus =
  | "PLANNED"
  | "ASSIGNED"
  | "IN_TRANSIT"
  | "AT_GATE"
  | "WAITING"
  | "IN_DOCK"
  | "COMPLETED"
  | "CANCELLED";

export type ShipmentSummary = {
  shipment_id: string;
  order_reference?: string | null;
  carrier_id?: string | null;
  driver_id?: string | null;
  vehicle_id?: string | null;
  origin_name?: string | null;
  origin_city?: string | null;
  destination_facility_id?: string | null;
  customer_name?: string | null;
  product_category?: string | null;
  load_weight_kg?: number | null;
  required_dock_type?: string | null;
  temperature_control_required?: boolean | null;
  priority_code?: string | null;
  planned_departure_ts?: string | null;
  original_eta_ts?: string | null;
  latest_eta_ts?: string | null;
  expected_unload_min?: number | null;
  current_status?: ShipmentStatus | null;
  archived_flag?: boolean | null;
  created_at?: string | null;
  updated_at?: string | null;
};

export type ShipmentCreateInput = {
  // Required fields here mirror the real Supabase shipments table's NOT
  // NULL columns -- omitting any of these used to reach Postgres and fail
  // with an opaque "database operation failed" error instead of being
  // caught by the form itself.
  order_reference: string;
  carrier_id: string;
  driver_id: string;
  vehicle_id: string;
  origin_name: string;
  origin_city: string;
  destination_facility_id: string;
  customer_name: string;
  product_category: string;
  load_weight_kg: number;
  planned_departure_ts: string;
  original_eta_ts: string;
  expected_unload_min: number;
  required_dock_type?: string;
  temperature_control_required?: boolean;
  priority_code?: string;
  latest_eta_ts?: string;
};

export type TmsDriver = {
  driver_id: string;
  carrier_id?: string | null;
  driver_name?: string | null;
  phone?: string | null;
  licence_number?: string | null;
  home_base_city?: string | null;
  driver_status?: "ACTIVE" | "OFF_DUTY" | "SUSPENDED" | "INACTIVE" | null;
};

export type TmsVehicle = {
  vehicle_id: string;
  carrier_id?: string | null;
  registration_number?: string | null;
  vehicle_type_code?: string | null;
  capacity_kg?: number | null;
  refrigeration_capable?: boolean | null;
  active_flag?: boolean | null;
};

export type TmsFacility = {
  facility_id: string;
  facility_name?: string | null;
  city?: string | null;
  state?: string | null;
};

export type FacilityStaffAssignment = {
  staff_user_id: string;
  facility_id: string;
  facility_name?: string | null;
};

export type OriginSummary = {
  origin_name: string;
  origin_city?: string | null;
};

export type ShipmentReferenceData = {
  origins: OriginSummary[];
  product_categories: string[];
};

export type DockSlot = {
  slot_id: string;
  dock_code: string;
  dock_type: string;
  start: string;
  end: string;
  availability_status: "AVAILABLE" | "HELD" | "OCCUPIED" | "BLOCKED" | "CLOSED" | string;
  occupant_shipment_id?: string | null;
  occupant_driver_name?: string | null;
};

export type DockTraceSummary = {
  appointment_id?: string | null;
  appointment_status?: string | null;
  dock_code?: string | null;
  slot_start_ts?: string | null;
  slot_end_ts?: string | null;
};

export type CheckinTraceSummary = {
  arrival_state?: string | null;
  queue_state?: string | null;
  gate_in_ts?: string | null;
  dock_in_ts?: string | null;
  unload_end_ts?: string | null;
};

export type ShipmentContext = {
  driver: { driver_id: string; driver_name?: string | null; carrier_id?: string | null; driver_status?: string | null };
  vehicle: { vehicle_id: string; registration_number?: string | null; vehicle_type_code?: string | null };
  shipment: ShipmentSummary;
  dock?: DockTraceSummary | null;
  checkin?: CheckinTraceSummary | null;
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
