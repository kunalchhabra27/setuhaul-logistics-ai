begin;
select plan(17);

select has_table('public', 'drivers', 'drivers table exists');
select has_table('public', 'vehicles', 'vehicles table exists');
select has_table('public', 'shipments', 'shipments table exists');
select has_index('public', 'drivers', 'drivers_carrier_id_idx', 'driver carrier index exists');
select has_index('public', 'vehicles', 'vehicles_status_idx', 'vehicle status index exists');
select has_index('public', 'shipments', 'shipments_driver_status_idx', 'shipment active lookup index exists');
select col_is_pk('public', 'drivers', 'driver_id', 'driver UUID is primary key');

select ok(
  exists(select 1 from pg_constraint where conname = 'shipments_priority_positive'),
  'priority check constraint exists'
);
select ok(
  exists(select 1 from pg_constraint where conname = 'shipments_driver_id_fkey'),
  'shipment driver foreign key exists'
);
select ok(
  (select relrowsecurity from pg_class where oid = 'public.drivers'::regclass),
  'drivers RLS is enabled'
);
select ok(
  not (select relrowsecurity from pg_class where oid = 'public.facilities'::regclass),
  'TMS migration does not enable facilities RLS'
);
select is(
  (select count(*)::integer from pg_policies where schemaname = 'public' and tablename = 'drivers'),
  3,
  'drivers has exactly three TMS policies'
);
select is(
  (select count(*)::integer from pg_policies where schemaname = 'public' and tablename = 'vehicles'),
  3,
  'vehicles has exactly three TMS policies'
);
select is(
  (select count(*)::integer from pg_policies where schemaname = 'public' and tablename = 'shipments'),
  3,
  'shipments has exactly three TMS policies'
);

select set_config('request.jwt.claims', '{"app_metadata":{"tms_role":"AGENT_READER"}}', true);
set local role authenticated;
select lives_ok($$ select * from public.drivers limit 1 $$, 'reader can select TMS data');
select throws_ok(
  $$ insert into public.drivers (driver_id, carrier_id, name, phone, active_flag, status)
     values ('30000000-0000-0000-0000-000000009901',
             '10000000-0000-0000-0000-000000000001', 'Denied', '+919901', true, 'active') $$,
  '42501', null, 'reader cannot insert TMS data'
);
reset role;

select set_config('request.jwt.claims', '{"app_metadata":{"tms_role":"ADMIN_1"}}', true);
set local role authenticated;
select lives_ok(
  $$ insert into public.drivers
       (driver_id, carrier_id, driver_code, name, phone, active_flag, status)
     values
       ('30000000-0000-0000-0000-000000009902',
        '10000000-0000-0000-0000-000000000001', 'DRV-9902', 'Allowed', '+919902', true, 'active') $$,
  'admin can insert TMS data'
);

select * from finish();
rollback;
