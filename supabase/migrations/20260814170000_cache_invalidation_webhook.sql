-- Fires the backend's cache-invalidation webhook (POST /api/v1/webhooks/
-- supabase, see src/setuhaul/backend/webhooks/api.py) on every INSERT/
-- UPDATE/DELETE to a table our Redis layer (src/setuhaul/infrastructure/
-- cache.py) caches a view of. This is the second, independent invalidation
-- path for writes that never go through our own FastAPI mutation
-- endpoints at all -- a row edited directly in the Supabase dashboard, a
-- script, or any other service. Every mutation our own backend performs
-- already busts the right cache keys synchronously (see the
-- cache.invalidate_* calls in each module's service.py); this trigger
-- covers everything else.
--
-- ## This is inert until configured
--
-- supabase_functions.http_request needs a real, publicly reachable target
-- URL -- Supabase's hosted Postgres cannot reach a plain localhost dev
-- server, so this does nothing useful in local development. TTL (see
-- cache.TTL_REFERENCE/TTL_MODERATE/TTL_HOT/TTL_SNAPSHOT_PART) remains the
-- fallback safety net for that case, and for any webhook delivery failure.
--
-- Before this has any effect against a real deployment, set these two
-- Postgres settings (NOT committed here -- they belong to your deployment,
-- never to a migration file):
--
--   alter database postgres set app.settings.cache_invalidation_url =
--     'https://<your-deployed-backend-origin>/api/v1/webhooks/supabase';
--   alter database postgres set app.settings.cache_invalidation_secret =
--     '<the same value as the backend''s WEBHOOK_SECRET env var>';
--
-- Until cache_invalidation_url is set, the trigger function below no-ops
-- (checked explicitly) rather than raising and blocking every write to
-- every cached table.

create or replace function public.notify_cache_invalidation()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  webhook_url text := current_setting('app.settings.cache_invalidation_url', true);
  webhook_secret text := current_setting('app.settings.cache_invalidation_secret', true);
begin
  if webhook_url is null or webhook_url = '' then
    return coalesce(NEW, OLD);
  end if;

  perform supabase_functions.http_request(
    webhook_url,
    'POST',
    jsonb_build_object(
      'Content-Type', 'application/json',
      'X-Webhook-Secret', coalesce(webhook_secret, '')
    ),
    jsonb_build_object(
      'type', TG_OP,
      'table', TG_TABLE_NAME,
      'record', case when TG_OP = 'DELETE' then null else to_jsonb(NEW) end,
      'old_record', case when TG_OP in ('UPDATE', 'DELETE') then to_jsonb(OLD) else null end
    ),
    -- timeout in ms -- short and non-blocking; supabase_functions.http_request
    -- is itself async (backed by pg_net), so this bounds how long Postgres
    -- waits for pg_net to accept the request, not the webhook's own runtime.
    '2000'
  );

  return coalesce(NEW, OLD);
end;
$$;

-- One trigger per table this backend caches a view of (see
-- src/setuhaul/backend/webhooks/api.py's _TABLE_HANDLERS map -- keep these
-- two lists in sync). Tables with no cached read (eta_updates,
-- driver_exceptions, chat_threads/messages, operational_messages, etc.)
-- deliberately have no trigger here -- there is nothing to invalidate.

drop trigger if exists cache_invalidation on public.shipments;
create trigger cache_invalidation
  after insert or update or delete on public.shipments
  for each row execute function public.notify_cache_invalidation();

drop trigger if exists cache_invalidation on public.appointment_slots;
create trigger cache_invalidation
  after insert or update or delete on public.appointment_slots
  for each row execute function public.notify_cache_invalidation();

drop trigger if exists cache_invalidation on public.slot_holds;
create trigger cache_invalidation
  after insert or update or delete on public.slot_holds
  for each row execute function public.notify_cache_invalidation();

drop trigger if exists cache_invalidation on public.appointments;
create trigger cache_invalidation
  after insert or update or delete on public.appointments
  for each row execute function public.notify_cache_invalidation();

drop trigger if exists cache_invalidation on public.facility_checkins;
create trigger cache_invalidation
  after insert or update or delete on public.facility_checkins
  for each row execute function public.notify_cache_invalidation();

drop trigger if exists cache_invalidation on public.dock_slot_change_requests;
create trigger cache_invalidation
  after insert or update or delete on public.dock_slot_change_requests
  for each row execute function public.notify_cache_invalidation();

drop trigger if exists cache_invalidation on public.drivers;
create trigger cache_invalidation
  after insert or update or delete on public.drivers
  for each row execute function public.notify_cache_invalidation();

drop trigger if exists cache_invalidation on public.vehicles;
create trigger cache_invalidation
  after insert or update or delete on public.vehicles
  for each row execute function public.notify_cache_invalidation();

drop trigger if exists cache_invalidation on public.facilities;
create trigger cache_invalidation
  after insert or update or delete on public.facilities
  for each row execute function public.notify_cache_invalidation();

drop trigger if exists cache_invalidation on public.docks;
create trigger cache_invalidation
  after insert or update or delete on public.docks
  for each row execute function public.notify_cache_invalidation();

drop trigger if exists cache_invalidation on public.carriers;
create trigger cache_invalidation
  after insert or update or delete on public.carriers
  for each row execute function public.notify_cache_invalidation();
