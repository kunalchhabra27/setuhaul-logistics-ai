-- Collapses the ~7-9 sequential PostgREST round trips
-- DriverChatService._build_snapshot makes on every /snapshot call and
-- every chat turn (shipment -> vehicle -> facility -> docks -> current
-- appointment (itself a 3-table join done in Python today) -> checkin ->
-- exception -> chat_messages) into ONE server-side call via
-- `.rpc("driver_snapshot", {"p_driver_id": ...})`.
--
-- Deliberately NOT `security definer` -- this runs as the calling
-- (authenticated) role, so it is subject to the exact same RLS policies
-- the individual queries were already subject to. This is a query-shape
-- optimization only, not a privilege change: anything this function can
-- read, the caller could already read one table at a time.
--
-- _feasible_slots/slot_options is intentionally NOT part of this function
-- -- that's dock_scheduler's own live compatible_slots()/
-- ensure_future_slots_for_shipment() computation (see driver_chat_eta/
-- service.py's own in-process per-turn cache for that one), kept separate
-- since it's a genuinely different, mutation-adjacent computation, not a
-- read-only identity/context lookup like everything below.
--
-- Python-side caller: DriverChatRepository.get_driver_snapshot_bundle(),
-- used by DriverChatService._build_snapshot with a full fallback to the
-- original sequential-calls implementation if this RPC errors for any
-- reason (e.g. this migration hasn't been applied yet) -- so deploying the
-- application code and running this migration are independent, safe to do
-- in either order.

create or replace function public.driver_snapshot(p_driver_id text)
returns jsonb
language plpgsql
stable
security invoker
as $$
declare
  v_shipment jsonb;
  v_shipment_id text;
  v_destination_facility_id text;
  v_vehicle_id text;
  v_vehicle jsonb;
  v_facility jsonb;
  v_docks jsonb;
  v_appointment jsonb;
  v_checkin jsonb;
  v_exception jsonb;
  v_thread_id text;
  v_chat_messages jsonb;
begin
  -- Mirrors DriverChatRepository.get_active_shipment_for_driver: the
  -- earliest-ETA non-terminal shipment for this driver.
  select to_jsonb(s) into v_shipment
  from public.shipments s
  where s.driver_id = p_driver_id
    and s.current_status not in ('COMPLETED', 'CANCELLED')
  order by s.original_eta_ts asc
  limit 1;

  if v_shipment is not null then
    v_shipment_id := v_shipment ->> 'shipment_id';
    v_destination_facility_id := v_shipment ->> 'destination_facility_id';
    v_vehicle_id := v_shipment ->> 'vehicle_id';

    if v_vehicle_id is not null then
      select to_jsonb(v) into v_vehicle from public.vehicles v where v.vehicle_id = v_vehicle_id;
    end if;

    if v_destination_facility_id is not null then
      select to_jsonb(f) into v_facility from public.facilities f where f.facility_id = v_destination_facility_id;

      select coalesce(jsonb_agg(to_jsonb(d)), '[]'::jsonb) into v_docks
      from public.docks d
      where d.facility_id = v_destination_facility_id and d.dock_status = 'ACTIVE';
    end if;

    -- Mirrors DockSchedulerRepository.current_appointment()'s join: the
    -- current active appointment plus the slot's start/end and the dock's
    -- code, since the raw appointments row only carries slot_id.
    select to_jsonb(a) || jsonb_build_object(
      'slot_start_ts', sl.slot_start_ts,
      'slot_end_ts', sl.slot_end_ts,
      'dock_code', dk.dock_code
    )
    into v_appointment
    from public.appointments a
    left join public.appointment_slots sl on sl.slot_id = a.slot_id
    left join public.docks dk on dk.dock_id = sl.dock_id
    where a.shipment_id = v_shipment_id
      and a.is_current = 1
      and a.appointment_status in ('PENDING_CONFIRMATION', 'CONFIRMED', 'IN_PROGRESS')
    limit 1;

    select to_jsonb(c) into v_checkin
    from public.facility_checkins c
    where c.shipment_id = v_shipment_id
    limit 1;
  end if;

  -- Mirrors DriverChatRepository.get_active_exception_for_driver: the
  -- most recently reported non-terminal exception for this driver.
  select to_jsonb(e) into v_exception
  from public.driver_exceptions e
  where e.driver_id = p_driver_id
    and e.exception_status not in ('RESOLVED', 'CANCELLED', 'DUPLICATE')
  order by e.reported_at desc
  limit 1;

  if v_exception is not null then
    v_thread_id := v_exception ->> 'thread_id';
    if v_thread_id is not null then
      select coalesce(jsonb_agg(to_jsonb(m)), '[]'::jsonb) into v_chat_messages
      from (
        select *
        from public.chat_messages cm
        where cm.thread_id = v_thread_id
        order by cm.message_ts asc
        limit 100
      ) m;
    end if;
  end if;

  return jsonb_build_object(
    'shipment', v_shipment,
    'vehicle', v_vehicle,
    'facility', v_facility,
    'docks', coalesce(v_docks, '[]'::jsonb),
    'appointment', v_appointment,
    'checkin', v_checkin,
    'exception', v_exception,
    'chat_messages', coalesce(v_chat_messages, '[]'::jsonb)
  );
end;
$$;

grant execute on function public.driver_snapshot(text) to authenticated;
