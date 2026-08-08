-- LOCAL DEVELOPMENT RECONSTRUCTION ONLY.
--
-- This is not an authoritative snapshot of the hosted SetuHaul database. It
-- reconstructs only the table/column/type facts supplied with the challenge.
-- Unknown production defaults, NOT NULL constraints, foreign keys, indexes,
-- triggers, grants and RLS policies are deliberately not inferred here.
-- UUID and timestamp defaults below are local-only conveniences.

create extension if not exists pgcrypto with schema extensions;

create type public.allocation_request_status as enum
  ('requested', 'processing', 'held', 'allocated', 'rejected', 'expired', 'cancelled');
create type public.allocation_status as enum ('allocated', 'confirmed', 'released');
create type public.appointment_status as enum
  ('requested', 'held', 'confirmed', 'completed', 'cancelled', 'no_show');
create type public.arrival_status as enum ('at_gate', 'in_yard', 'at_dock', 'completed');
create type public.carrier_status as enum ('active', 'inactive');
create type public.driver_status as enum ('active', 'inactive', 'suspended');
create type public.eta_source_type as enum ('driver', 'operations');
create type public.exception_status as enum
  ('open', 'in_progress', 'resolved', 'closed', 'escalated');
create type public.message_sender_type as enum
  ('driver', 'agent', 'operations', 'warehouse', 'system');
create type public.queue_status as enum
  ('not_waiting', 'waiting_gate', 'waiting_yard', 'called_to_dock', 'none');
create type public.rule_type as enum
  ('vehicle_type', 'product_class', 'carrier', 'temperature_control',
   'appointment_type', 'operating_hours', 'capacity', 'custom');
create type public.shipment_status as enum
  ('planned', 'in_transit', 'arrived', 'waiting', 'unloading',
   'completed', 'cancelled', 'exception');
create type public.slot_hold_status as enum ('held', 'allocated', 'released', 'expired');
create type public.slot_status as enum ('open', 'held', 'booked', 'blocked', 'closed');
create type public.vehicle_status as enum ('active', 'inactive', 'maintenance');

create table public.carriers (
  carrier_id uuid primary key default gen_random_uuid(),
  name text,
  scac_code text unique,
  phone text,
  email text,
  active_flag boolean,
  status public.carrier_status,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table public.contacts (
  contact_id uuid primary key default gen_random_uuid(),
  party_type text,
  name text,
  email text,
  phone text,
  role text,
  is_primary boolean,
  active_flag boolean,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table public.facilities (
  facility_id uuid primary key default gen_random_uuid(),
  name text,
  address text,
  city text,
  state text,
  pincode text,
  timezone text,
  open_time time,
  close_time time,
  contact_id uuid,
  active_flag boolean,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table public.drivers (
  driver_id uuid primary key default gen_random_uuid(),
  carrier_id uuid,
  driver_code text unique,
  name text,
  phone text unique,
  email text,
  license_number text,
  license_expiry date,
  home_base text,
  active_flag boolean,
  status public.driver_status,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table public.vehicles (
  vehicle_id uuid primary key default gen_random_uuid(),
  carrier_id uuid,
  vehicle_number text unique,
  vehicle_type text,
  length_ft numeric,
  capacity_weight_kg numeric,
  refrigeration_required boolean,
  active_flag boolean,
  status public.vehicle_status,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table public.docks (
  dock_id uuid primary key default gen_random_uuid(),
  facility_id uuid,
  dock_name text,
  supported_vehicle_type text,
  supported_product_class text,
  active_flag boolean,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table public.facility_rules (
  rule_id uuid primary key default gen_random_uuid(),
  facility_id uuid,
  rule_type public.rule_type,
  rule_key text,
  rule_value jsonb,
  effective_from timestamptz,
  effective_to timestamptz,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table public.shipments (
  shipment_id uuid primary key default gen_random_uuid(),
  driver_id uuid,
  vehicle_id uuid,
  origin_id uuid,
  destination_id uuid,
  product_class text,
  priority integer,
  planned_eta timestamptz,
  expected_unload_minutes integer,
  status public.shipment_status,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table public.eta_updates (
  eta_update_id uuid primary key default gen_random_uuid(),
  shipment_id uuid,
  declared_eta timestamptz,
  source_type public.eta_source_type,
  declared_at timestamptz,
  confidence_note text,
  created_at timestamptz default now()
);

create table public.facility_checkins (
  checkin_id uuid primary key default gen_random_uuid(),
  shipment_id uuid,
  facility_id uuid,
  gate_in_at timestamptz,
  arrival_status public.arrival_status,
  queue_status public.queue_status,
  dock_in_at timestamptz,
  completed_at timestamptz,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table public.appointment_slots (
  slot_id uuid primary key default gen_random_uuid(),
  facility_id uuid,
  dock_id uuid,
  start_time timestamptz,
  end_time timestamptz,
  capacity_units integer,
  slot_status public.slot_status,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table public.appointments (
  appointment_id uuid primary key default gen_random_uuid(),
  shipment_id uuid,
  slot_id uuid,
  status public.appointment_status,
  booked_at timestamptz,
  confirmed_at timestamptz,
  cancelled_at timestamptz,
  notes text,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table public.driver_exceptions (
  exception_id uuid primary key default gen_random_uuid(),
  driver_id uuid,
  shipment_id uuid,
  exception_type text,
  description text,
  severity integer,
  reported_delay_minutes integer,
  latest_declared_eta timestamptz,
  reported_at timestamptz,
  status public.exception_status,
  resolved_at timestamptz,
  resolved_by uuid,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table public.threads (
  thread_id uuid primary key default gen_random_uuid(),
  exception_id uuid,
  status public.exception_status,
  priority integer,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table public.chat_messages (
  message_id uuid primary key default gen_random_uuid(),
  thread_id uuid,
  sender_type public.message_sender_type,
  sender_id uuid,
  message_type text,
  content text,
  created_at timestamptz default now(),
  read_at timestamptz
);

create table public.allocation_requests (
  request_id uuid primary key default gen_random_uuid(),
  shipment_id uuid,
  exception_id uuid,
  requested_window_start timestamptz,
  requested_window_end timestamptz,
  requested_at timestamptz,
  status public.allocation_request_status,
  requested_constraints jsonb,
  priority_snapshot integer,
  expires_at timestamptz,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table public.slot_holds (
  hold_id uuid primary key default gen_random_uuid(),
  request_id uuid,
  slot_id uuid,
  hold_token text unique,
  status public.slot_hold_status,
  held_until timestamptz,
  created_at timestamptz default now(),
  released_at timestamptz
);

create table public.slot_allocations (
  allocation_id uuid primary key default gen_random_uuid(),
  request_id uuid,
  shipment_id uuid,
  slot_id uuid,
  allocated_at timestamptz,
  allocated_by uuid,
  status public.allocation_status,
  released_at timestamptz
);

create table public.appointment_history (
  history_id uuid primary key default gen_random_uuid(),
  appointment_id uuid,
  field_name text,
  old_value jsonb,
  new_value jsonb,
  changed_at timestamptz,
  changed_by uuid
);

create table public.facility_capacity_changes (
  change_id uuid primary key default gen_random_uuid(),
  facility_id uuid,
  dock_id uuid,
  change_date date,
  reason text,
  added_capacity integer,
  removed_capacity integer,
  effective_from timestamptz,
  effective_to timestamptz,
  created_at timestamptz default now()
);

create table public.operational_messages (
  operational_message_id uuid primary key default gen_random_uuid(),
  facility_id uuid,
  shipment_id uuid,
  contact_id uuid,
  message_type text,
  content text,
  created_at timestamptz default now()
);

create table public.customer_commitments (
  commitment_id uuid primary key default gen_random_uuid(),
  shipment_id uuid,
  commitment_type text,
  committed_time timestamptz,
  committed_by uuid,
  notes text,
  created_at timestamptz default now()
);
