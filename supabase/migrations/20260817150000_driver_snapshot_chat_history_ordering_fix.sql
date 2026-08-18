-- Fixes public.driver_snapshot's chat_messages query: it was selecting the
-- OLDEST 100 messages per thread instead of the newest 100.
--
-- Postgres applies ORDER BY before LIMIT, so `order by message_ts asc limit
-- 100` keeps the first 100 rows in ascending (oldest-first) order -- i.e.
-- the 100 OLDEST messages, not the 100 most recent. This bug has been in
-- driver_snapshot since it was introduced (20260815120000_driver_snapshot_
-- rpc.sql) and was carried forward unchanged by
-- 20260817140000_driver_snapshot_chat_history_fix.sql, which only touched
-- how the thread itself is looked up, not this ordering.
--
-- Effect: once a thread passes 100 messages (confirmed in production on a
-- real driver's thread, TH-BFAB7E14, at 149 messages), chat_messages
-- permanently freezes at that thread's oldest 100 rows -- every later
-- /snapshot poll and every /chat response silently omits every message
-- sent since, including brand-new ones in the very same response that just
-- persisted them. This reproduces exactly as "I typed a message, it never
-- shows up and never gets a reply" even though the backend fully succeeded.
--
-- Fix: mirror DriverChatRepository.list_chat_messages (repository.py),
-- which already does this correctly on the Python fallback path -- sort
-- descending to get the newest rows under the limit, then re-sort
-- ascending for chronological output.

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
  -- only for the `exception` field below -- chat history sourcing (below)
  -- doesn't depend on this being non-null.
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
    -- Sort newest-first to apply the limit against the most recent 100
    -- rows, then re-sort ascending for chronological output -- ordering by
    -- message_ts asc with the limit inline (the previous, buggy version)
    -- keeps the OLDEST 100 rows instead, since ORDER BY applies before
    -- LIMIT.
    select coalesce(jsonb_agg(to_jsonb(m)), '[]'::jsonb) into v_chat_messages
    from (
      select *
      from (
        select *
        from public.chat_messages cm
        where cm.thread_id = v_thread_id
        order by cm.message_ts desc
        limit 100
      ) recent
      order by recent.message_ts asc
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
