-- Fixes public.driver_snapshot (see 20260815120000_driver_snapshot_rpc.sql)
-- so chat_messages is sourced from the driver's most recent thread
-- regardless of that thread's status, instead of being gated behind a
-- currently-ACTIVE (non-terminal) exception.
--
-- Bug: v_thread_id was only ever set from v_exception's thread_id, and
-- v_exception explicitly excludes RESOLVED/CANCELLED/DUPLICATE exceptions.
-- The instant an exception resolves -- e.g. confirm_slot marks both the
-- exception AND its thread RESOLVED in the same call, then inserts an
-- "Appointment confirmed..." SYSTEM message into that now-resolved thread --
-- chat_messages comes back empty on every subsequent /snapshot poll and
-- /chat response, forever, for that driver. A resolved thread is a normal,
-- expected end state whose history should stay visible, not a signal to
-- hide it. Mirrors the equivalent fix in
-- DriverChatRepository.get_latest_thread_for_driver /
-- DriverChatService._build_snapshot_sequential.

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
  -- most recently reported non-terminal exception for this driver. Used
  -- only for the `exception` field below now -- chat history sourcing
  -- (below) no longer depends on this being non-null.
  select to_jsonb(e) into v_exception
  from public.driver_exceptions e
  where e.driver_id = p_driver_id
    and e.exception_status not in ('RESOLVED', 'CANCELLED', 'DUPLICATE')
  order by e.reported_at desc
  limit 1;

  -- Mirrors DriverChatRepository.get_latest_thread_for_driver: the driver's
  -- most recent thread regardless of status, so chat history stays visible
  -- after the thread/exception resolves.
  select t.thread_id into v_thread_id
  from public.chat_threads t
  where t.driver_id = p_driver_id
  order by t.opened_at desc
  limit 1;

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
