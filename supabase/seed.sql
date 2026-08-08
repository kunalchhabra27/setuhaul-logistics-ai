-- Deterministic local development fixtures.
-- Carrier and facility rows are shared/external reference fixtures, not TMS
-- lifecycle ownership. No WMS, ETA, check-in, chat or allocation data is seeded.

insert into public.carriers
  (carrier_id, name, scac_code, phone, email, active_flag, status)
values
  ('10000000-0000-0000-0000-000000000001', 'SetuHaul North Carrier', 'SHN1', '+91-9000000001', 'north@example.test', true, 'active'),
  ('10000000-0000-0000-0000-000000000002', 'SetuHaul West Carrier', 'SHW1', '+91-9000000002', 'west@example.test', true, 'active');

insert into public.facilities
  (facility_id, name, city, state, timezone, open_time, close_time, active_flag)
values
  ('20000000-0000-0000-0000-000000000001', 'Jaipur Distribution Centre', 'Jaipur', 'Rajasthan', 'Asia/Kolkata', '06:00', '22:00', true),
  ('20000000-0000-0000-0000-000000000002', 'Surat Origin Hub', 'Surat', 'Gujarat', 'Asia/Kolkata', '00:00', '23:59', true),
  ('20000000-0000-0000-0000-000000000003', 'Pune Distribution Centre', 'Pune', 'Maharashtra', 'Asia/Kolkata', '06:00', '22:00', true);

insert into public.drivers
  (driver_id, carrier_id, driver_code, name, phone, home_base, active_flag, status)
values
  ('30000000-0000-0000-0000-000000000027', '10000000-0000-0000-0000-000000000001', 'DRV-027', 'Ravi Kumar', '+91-9000000027', 'Jaipur', true, 'active'),
  ('30000000-0000-0000-0000-000000000028', '10000000-0000-0000-0000-000000000002', 'DRV-028', 'Suresh Patil', '+91-9000000028', 'Surat', true, 'active'),
  ('30000000-0000-0000-0000-000000000029', '10000000-0000-0000-0000-000000000001', 'DRV-029', 'Aman Singh', '+91-9000000029', 'Delhi', true, 'active'),
  ('30000000-0000-0000-0000-000000000030', '10000000-0000-0000-0000-000000000001', 'DRV-030', 'Inactive Driver', '+91-9000000030', 'Jaipur', false, 'inactive'),
  ('30000000-0000-0000-0000-000000000031', '10000000-0000-0000-0000-000000000002', 'DRV-031', 'Available Driver', '+91-9000000031', 'Pune', true, 'active');

insert into public.vehicles
  (vehicle_id, carrier_id, vehicle_number, vehicle_type, length_ft,
   capacity_weight_kg, refrigeration_required, active_flag, status)
values
  ('40000000-0000-0000-0000-000000000031', '10000000-0000-0000-0000-000000000001', 'VEH-031', 'dry_van', 32, 15000, false, true, 'active'),
  ('40000000-0000-0000-0000-000000000032', '10000000-0000-0000-0000-000000000002', 'MH02LD8342', 'closed_body', 24, 10000, false, true, 'active'),
  ('40000000-0000-0000-0000-000000000033', '10000000-0000-0000-0000-000000000001', 'RJ14RF0033', 'reefer', 32, 16000, true, true, 'active'),
  ('40000000-0000-0000-0000-000000000034', '10000000-0000-0000-0000-000000000001', 'RJ14MT0034', 'dry_van', 32, 15000, false, false, 'maintenance');

insert into public.shipments
  (shipment_id, driver_id, vehicle_id, origin_id, destination_id,
   product_class, priority, planned_eta, expected_unload_minutes, status)
values
  ('50000000-0000-0000-0000-000000001042', '30000000-0000-0000-0000-000000000027', '40000000-0000-0000-0000-000000000031', '20000000-0000-0000-0000-000000000002', '20000000-0000-0000-0000-000000000001', 'dry_freight', 2, '2026-08-08T17:20:00+05:30', 40, 'in_transit'),
  ('50000000-0000-0000-0000-000000001043', '30000000-0000-0000-0000-000000000028', '40000000-0000-0000-0000-000000000032', '20000000-0000-0000-0000-000000000002', '20000000-0000-0000-0000-000000000003', 'oranges', 3, '2026-08-08T20:15:00+05:30', 55, 'in_transit'),
  ('50000000-0000-0000-0000-000000001044', '30000000-0000-0000-0000-000000000029', '40000000-0000-0000-0000-000000000031', '20000000-0000-0000-0000-000000000002', '20000000-0000-0000-0000-000000000001', 'consumer_goods', 1, '2026-08-08T18:00:00+05:30', 45, 'planned'),
  ('50000000-0000-0000-0000-000000001045', '30000000-0000-0000-0000-000000000029', '40000000-0000-0000-0000-000000000033', '20000000-0000-0000-0000-000000000002', '20000000-0000-0000-0000-000000000001', 'frozen_food', 4, '2026-08-08T19:00:00+05:30', 60, 'exception'),
  ('50000000-0000-0000-0000-000000001046', '30000000-0000-0000-0000-000000000027', '40000000-0000-0000-0000-000000000031', '20000000-0000-0000-0000-000000000002', '20000000-0000-0000-0000-000000000001', 'dry_freight', 2, '2026-08-07T15:00:00+05:30', 40, 'completed'),
  ('50000000-0000-0000-0000-000000001047', '30000000-0000-0000-0000-000000000028', '40000000-0000-0000-0000-000000000032', '20000000-0000-0000-0000-000000000002', '20000000-0000-0000-0000-000000000003', 'oranges', 1, '2026-08-09T09:00:00+05:30', 55, 'cancelled');
