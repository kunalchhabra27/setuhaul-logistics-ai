// Mirrors src/setuhaul/backend/driver_chat_eta/models.py, which in turn
// mirrors the real Supabase schema (every id is text, enums are
// UPPER_SNAKE_CASE, booleans are 0/1 ints coerced to bool by Pydantic).

export type DriverStatus = "ACTIVE" | "OFF_DUTY" | "SUSPENDED" | "INACTIVE";
export type ShipmentStatus = "PLANNED" | "ASSIGNED" | "IN_TRANSIT" | "AT_GATE" | "WAITING" | "IN_DOCK" | "COMPLETED" | "CANCELLED";
export type DriverSlotStatus = "OPEN" | "BLOCKED" | "CLOSED";
export type DriverAppointmentStatus = "PENDING_CONFIRMATION" | "CONFIRMED" | "IN_PROGRESS" | "COMPLETED" | "CANCELLED" | "NO_SHOW" | "REJECTED";
export type ArrivalState = "EARLY" | "ON_TIME" | "LATE" | "NO_SHOW";
export type QueueState = "NOT_QUEUED" | "WAITING_EARLY" | "WAITING_LATE" | "WAITING_DOCK_UNAVAILABLE" | "CALLED_TO_DOCK" | "IN_DOCK" | "COMPLETED";
export type DriverExceptionStatus = "OPEN" | "NEEDS_INFORMATION" | "SLOT_OPTIONS_SHARED" | "WAITING_CONFIRMATION" | "RESOLVED" | "ESCALATED" | "DUPLICATE" | "CANCELLED";
export type DriverMessageSenderType = "DRIVER" | "AGENT" | "OPERATIONS" | "WAREHOUSE" | "SYSTEM";
export type ArrivalUpdateChoice = "arrived_gate" | "waiting_yard" | "docked" | "completed";

export interface DriverProfile {
  driver_id: string;
  carrier_id?: string | null;
  driver_name?: string | null;
  phone?: string | null;
  licence_number?: string | null;
  home_base_city?: string | null;
  driver_status?: DriverStatus | null;
}

export interface DriverVehicleSummary {
  vehicle_id: string;
  carrier_id?: string | null;
  vehicle_type_code?: string | null;
  registration_number?: string | null;
  capacity_kg?: number | null;
  refrigeration_capable?: boolean | null;
  active_flag?: boolean | null;
}

export interface DriverFacilitySummary {
  facility_id: string;
  facility_name?: string | null;
  city?: string | null;
  state?: string | null;
  timezone?: string | null;
  open_time?: string | null;
  close_time?: string | null;
}

export interface DriverDockSummary {
  dock_id: string;
  facility_id: string;
  dock_code?: string | null;
  dock_type?: string | null;
  supports_refrigerated?: boolean | null;
  max_vehicle_weight_kg?: number | null;
  dock_status?: string | null;
}

export interface DriverShipmentSummary {
  shipment_id: string;
  order_reference?: string | null;
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
}

export interface DriverAppointmentSlotSummary {
  slot_id: string;
  facility_id?: string | null;
  dock_id?: string | null;
  slot_start_ts?: string | null;
  slot_end_ts?: string | null;
  slot_status?: DriverSlotStatus | null;
}

export interface DriverAppointmentSummary {
  appointment_id: string;
  shipment_id?: string | null;
  slot_id?: string | null;
  appointment_status?: DriverAppointmentStatus | null;
  booking_source?: string | null;
  booked_at?: string | null;
  confirmed_at?: string | null;
  cancelled_at?: string | null;
  dock_code?: string | null;
  slot_start_ts?: string | null;
  slot_end_ts?: string | null;
}

export interface DriverFacilityCheckinSummary {
  checkin_id: string;
  shipment_id?: string | null;
  facility_id?: string | null;
  gate_in_ts?: string | null;
  yard_queue_enter_ts?: string | null;
  dock_in_ts?: string | null;
  unload_start_ts?: string | null;
  unload_end_ts?: string | null;
  gate_out_ts?: string | null;
  arrival_state?: ArrivalState | null;
  queue_state?: QueueState | null;
  actual_dock_id?: string | null;
  notes?: string | null;
}

export interface DriverExceptionSummary {
  exception_id: string;
  shipment_id?: string | null;
  thread_id?: string | null;
  exception_type?: string | null;
  reported_at?: string | null;
  reported_delay_min?: number | null;
  declared_eta_ts?: string | null;
  earliest_acceptable_ts?: string | null;
  latest_acceptable_ts?: string | null;
  severity_code?: string | null;
  exception_status?: DriverExceptionStatus | null;
  description?: string | null;
}

export interface DriverChatMessageSummary {
  chat_message_id: string;
  thread_id?: string | null;
  sender_type?: DriverMessageSenderType | null;
  sender_reference?: string | null;
  message_text?: string | null;
  message_ts?: string | null;
}

export interface DriverSlotOption {
  slot_id: string;
  dock_id: string;
  dock_code?: string | null;
  start_time: string;
  end_time: string;
  is_compatible: boolean;
  compatibility_reason: string;
  estimated_wait_minutes: number;
  is_held: boolean;
  is_booked_by_me: boolean;
}

export interface DriverSnapshot {
  driver: DriverProfile;
  vehicle?: DriverVehicleSummary | null;
  shipment?: DriverShipmentSummary | null;
  facility?: DriverFacilitySummary | null;
  docks: DriverDockSummary[];
  appointment?: DriverAppointmentSummary | null;
  checkin?: DriverFacilityCheckinSummary | null;
  exception?: DriverExceptionSummary | null;
  slot_options: DriverSlotOption[];
  chat_messages: DriverChatMessageSummary[];
  conversation_id?: string | null;
}

export interface DriverChatResponse {
  conversation_id?: string | null;
  agent_message: DriverChatMessageSummary;
  suggested_options: DriverSlotOption[];
  exception?: DriverExceptionSummary | null;
  snapshot: DriverSnapshot;
}

export interface DriverProfileCompleteRequest {
  driver_name: string;
  phone: string;
  licence_number: string;
  home_base_city: string;
  carrier_id: string;
}

export interface DriverCarrierSummary {
  carrier_id: string;
  carrier_name?: string | null;
  has_active_vehicle: boolean;
}
