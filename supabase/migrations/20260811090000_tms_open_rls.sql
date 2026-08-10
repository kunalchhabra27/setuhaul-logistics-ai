-- Extend the RLS-removal already applied to public.drivers
-- (20260810120000_drivers_open_rls.sql) to the remaining TMS-owned tables so
-- any authenticated user can read/write vehicles and shipments for local
-- testing, without needing an app_metadata.tms_role claim set via the
-- Supabase Admin API. This supersedes the ADMIN_1/AGENT_READER-only policies
-- from 20260808095820_tms_authorization.sql.
--
-- Trade-off: any authenticated user can now read/write any row in these
-- tables. Fine for local development / demo; reintroduce scoped policies
-- before this goes anywhere near production data.

drop policy if exists tms_read_vehicles on public.vehicles;
drop policy if exists tms_admin_insert_vehicles on public.vehicles;
drop policy if exists tms_admin_update_vehicles on public.vehicles;

drop policy if exists tms_read_shipments on public.shipments;
drop policy if exists tms_admin_insert_shipments on public.shipments;
drop policy if exists tms_admin_update_shipments on public.shipments;

alter table public.vehicles disable row level security;
alter table public.shipments disable row level security;

grant select, insert, update on table public.vehicles, public.shipments to authenticated;
