-- TMS authorization only. This migration deliberately does not change RLS,
-- grants or policies for carriers or any non-TMS-owned table.

alter table public.drivers enable row level security;
alter table public.vehicles enable row level security;
alter table public.shipments enable row level security;

revoke all on table public.drivers, public.vehicles, public.shipments from anon;
revoke all on table public.drivers, public.vehicles, public.shipments from authenticated;
grant select, insert, update on table
  public.drivers, public.vehicles, public.shipments to authenticated;

create policy tms_read_drivers
on public.drivers for select to authenticated
using (
  (select auth.jwt() -> 'app_metadata' ->> 'tms_role')
    in ('ADMIN_1', 'AGENT_READER')
);

create policy tms_admin_insert_drivers
on public.drivers for insert to authenticated
with check (
  (select auth.jwt() -> 'app_metadata' ->> 'tms_role') = 'ADMIN_1'
);

create policy tms_admin_update_drivers
on public.drivers for update to authenticated
using (
  (select auth.jwt() -> 'app_metadata' ->> 'tms_role') = 'ADMIN_1'
)
with check (
  (select auth.jwt() -> 'app_metadata' ->> 'tms_role') = 'ADMIN_1'
);

create policy tms_read_vehicles
on public.vehicles for select to authenticated
using (
  (select auth.jwt() -> 'app_metadata' ->> 'tms_role')
    in ('ADMIN_1', 'AGENT_READER')
);

create policy tms_admin_insert_vehicles
on public.vehicles for insert to authenticated
with check (
  (select auth.jwt() -> 'app_metadata' ->> 'tms_role') = 'ADMIN_1'
);

create policy tms_admin_update_vehicles
on public.vehicles for update to authenticated
using (
  (select auth.jwt() -> 'app_metadata' ->> 'tms_role') = 'ADMIN_1'
)
with check (
  (select auth.jwt() -> 'app_metadata' ->> 'tms_role') = 'ADMIN_1'
);

create policy tms_read_shipments
on public.shipments for select to authenticated
using (
  (select auth.jwt() -> 'app_metadata' ->> 'tms_role')
    in ('ADMIN_1', 'AGENT_READER')
);

create policy tms_admin_insert_shipments
on public.shipments for insert to authenticated
with check (
  (select auth.jwt() -> 'app_metadata' ->> 'tms_role') = 'ADMIN_1'
);

create policy tms_admin_update_shipments
on public.shipments for update to authenticated
using (
  (select auth.jwt() -> 'app_metadata' ->> 'tms_role') = 'ADMIN_1'
)
with check (
  (select auth.jwt() -> 'app_metadata' ->> 'tms_role') = 'ADMIN_1'
);
