-- Reviewed additive constraints and indexes for TMS-owned tables only.
-- Constraints are NOT VALID so additive deployment does not silently reject
-- unknown legacy rows; PostgreSQL still enforces them for new/changed rows.

alter table public.vehicles
  add constraint vehicles_length_ft_positive
  check (length_ft is null or length_ft > 0) not valid,
  add constraint vehicles_capacity_weight_kg_positive
  check (capacity_weight_kg is null or capacity_weight_kg > 0) not valid;

alter table public.shipments
  add constraint shipments_priority_positive
  check (priority > 0) not valid,
  add constraint shipments_expected_unload_minutes_positive
  check (expected_unload_minutes > 0) not valid;

do $$
begin
  if not exists (
    select 1 from pg_constraint
    where contype = 'f'
      and conrelid = 'public.drivers'::regclass
      and confrelid = 'public.carriers'::regclass
  ) then
    alter table public.drivers
      add constraint drivers_carrier_id_fkey
      foreign key (carrier_id) references public.carriers(carrier_id)
      on update no action on delete no action not valid;
  end if;

  if not exists (
    select 1 from pg_constraint
    where contype = 'f'
      and conrelid = 'public.vehicles'::regclass
      and confrelid = 'public.carriers'::regclass
  ) then
    alter table public.vehicles
      add constraint vehicles_carrier_id_fkey
      foreign key (carrier_id) references public.carriers(carrier_id)
      on update no action on delete no action not valid;
  end if;

  if not exists (
    select 1 from pg_constraint
    where contype = 'f'
      and conrelid = 'public.shipments'::regclass
      and confrelid = 'public.drivers'::regclass
  ) then
    alter table public.shipments
      add constraint shipments_driver_id_fkey
      foreign key (driver_id) references public.drivers(driver_id)
      on update no action on delete no action not valid;
  end if;

  if not exists (
    select 1 from pg_constraint
    where contype = 'f'
      and conrelid = 'public.shipments'::regclass
      and confrelid = 'public.vehicles'::regclass
  ) then
    alter table public.shipments
      add constraint shipments_vehicle_id_fkey
      foreign key (vehicle_id) references public.vehicles(vehicle_id)
      on update no action on delete no action not valid;
  end if;
end
$$;

create index drivers_carrier_id_idx on public.drivers (carrier_id);
create index drivers_status_idx on public.drivers (status);
create index vehicles_carrier_id_idx on public.vehicles (carrier_id);
create index vehicles_status_idx on public.vehicles (status);
create index vehicles_vehicle_type_idx on public.vehicles (vehicle_type);
create index shipments_driver_status_idx on public.shipments (driver_id, status);
create index shipments_vehicle_id_idx on public.shipments (vehicle_id);
create index shipments_origin_id_idx on public.shipments (origin_id);
create index shipments_destination_id_idx on public.shipments (destination_id);
create index shipments_planned_eta_idx on public.shipments (planned_eta);
create index shipments_status_idx on public.shipments (status);
create index shipments_destination_planned_eta_idx
  on public.shipments (destination_id, planned_eta);
