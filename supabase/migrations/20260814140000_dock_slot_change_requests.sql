-- Lets a TMS dispatcher or a driver request a different dock slot for an
-- already-booked shipment, without immediately mutating the appointment --
-- WMS staff must explicitly approve or decline the request (see
-- DockSchedulerService.decide_change_request) before the underlying
-- appointment is actually moved via the existing rebook primitive
-- (DockSchedulerRepository.book_after_acceptance, which already supports
-- moving a CONFIRMED appointment to a new slot).
--
-- RLS is enabled but left permissive for any authenticated caller (matching
-- 20260810120000_drivers_open_rls.sql / 20260811090000_tms_open_rls.sql):
-- TMS staff, drivers and WMS staff all need to read/write rows here, and
-- infrastructure.auth.get_current_principal already treats any
-- authenticated Supabase user without an explicit tms_role claim as full
-- access for local development, so a self-scoped policy (like
-- staff_facility_assignments' auth.uid() checks) would not add a real
-- guarantee here -- it would just add friction while looking secure.

create table if not exists public.dock_slot_change_requests (
  change_request_id text primary key,
  shipment_id text not null references public.shipments(shipment_id),
  current_appointment_id text null references public.appointments(appointment_id),
  requested_slot_id text not null references public.appointment_slots(slot_id),
  requested_by_role text not null check (requested_by_role in ('TMS', 'DRIVER')),
  requested_by_user_id text not null,
  reason text null,
  request_status text not null default 'PENDING' check (request_status in ('PENDING', 'APPROVED', 'DECLINED')),
  created_at timestamptz not null default now(),
  decided_at timestamptz null,
  decided_by_user_id text null,
  decision_note text null
);

create index if not exists idx_dock_slot_change_requests_shipment
  on public.dock_slot_change_requests (shipment_id);

create index if not exists idx_dock_slot_change_requests_status
  on public.dock_slot_change_requests (request_status);

alter table public.dock_slot_change_requests enable row level security;

drop policy if exists dock_slot_change_requests_read on public.dock_slot_change_requests;
create policy dock_slot_change_requests_read
  on public.dock_slot_change_requests
  for select
  to authenticated
  using (true);

drop policy if exists dock_slot_change_requests_insert on public.dock_slot_change_requests;
create policy dock_slot_change_requests_insert
  on public.dock_slot_change_requests
  for insert
  to authenticated
  with check (true);

drop policy if exists dock_slot_change_requests_update on public.dock_slot_change_requests;
create policy dock_slot_change_requests_update
  on public.dock_slot_change_requests
  for update
  to authenticated
  using (true)
  with check (true);

grant select, insert, update on table public.dock_slot_change_requests to authenticated;

-- Gate check-in approval: a driver marking "arrived at gate" now lands in
-- facility_checkins immediately (unchanged), but must not be treated as a
-- confirmed gate check-in for TMS/WMS-facing status until check-in staff
-- explicitly approve it (see CheckInService.approve_gate_checkin). Stored
-- as integer 0/1 to match the existing boolean-as-integer convention
-- already used on this table's sibling tables (see shipments.archived_flag,
-- vehicles.refrigeration_capable).
alter table public.facility_checkins
  add column if not exists staff_approved_flag integer not null default 0;
