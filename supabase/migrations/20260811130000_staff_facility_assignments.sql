-- Tracks which single warehouse facility a WMS/Check-in staff account is
-- registered against, so shipment queries in those two portals can be
-- scoped to "my facility only" (see TMSService.list_shipments_for_staff /
-- the /tms/facility-staff/* endpoints). One row per staff Supabase Auth
-- user -- re-registering updates the existing row rather than adding a
-- second facility (upsert on staff_user_id).
--
-- Enforcement note: the actual "another facility's staff can't see these
-- shipments" guarantee is done in the FastAPI service layer (the facility
-- filter is derived server-side from this table via the caller's own JWT,
-- never accepted as a client-supplied parameter) -- RLS here additionally
-- makes sure a staff account can only ever read/write its OWN assignment
-- row, not impersonate another staff member's facility. Unlike
-- drivers/vehicles/shipments (RLS disabled repo-wide for local demo
-- purposes -- see 20260810120000_drivers_open_rls.sql /
-- 20260811090000_tms_open_rls.sql), this is a brand new table with no
-- existing open-access assumption, so RLS is left ON here.

create table if not exists public.staff_facility_assignments (
  staff_user_id text primary key,
  facility_id text not null references public.facilities(facility_id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.staff_facility_assignments enable row level security;

drop policy if exists staff_facility_self_select on public.staff_facility_assignments;
create policy staff_facility_self_select
  on public.staff_facility_assignments
  for select
  to authenticated
  using (staff_user_id = auth.uid()::text);

drop policy if exists staff_facility_self_insert on public.staff_facility_assignments;
create policy staff_facility_self_insert
  on public.staff_facility_assignments
  for insert
  to authenticated
  with check (staff_user_id = auth.uid()::text);

drop policy if exists staff_facility_self_update on public.staff_facility_assignments;
create policy staff_facility_self_update
  on public.staff_facility_assignments
  for update
  to authenticated
  using (staff_user_id = auth.uid()::text)
  with check (staff_user_id = auth.uid()::text);

grant select, insert, update on table public.staff_facility_assignments to authenticated;
