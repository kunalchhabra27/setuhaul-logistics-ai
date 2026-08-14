-- driver_chat_eta.repository writes to chat_threads/chat_messages (and
-- reads/writes driver_exceptions, eta_updates) on every chat turn --
-- DriverChatRepository.create_thread() was failing outright with:
--   postgrest.exceptions.APIError: new row violates row-level security
--   policy for table "chat_threads"
-- because these tables have RLS enabled (Supabase's default when a table
-- is created via the dashboard) but were never given a policy in this
-- repo's migrations -- default-deny RLS with zero policies blocks every
-- row, including inserts from the table's own owner. That 403 propagated
-- as an uncaught PersistenceError out of the driver's very first message
-- in a new thread, which is what made the chatbot look completely dead
-- (the request never even reaches the LLM tool-calling loop).
--
-- RLS stays enabled but permissive for any authenticated caller, matching
-- the existing convention for driver-chat/dock-scheduler tables (see
-- 20260810120000_drivers_open_rls.sql, 20260811090000_tms_open_rls.sql,
-- 20260814140000_dock_slot_change_requests.sql): infrastructure.auth
-- already treats any authenticated Supabase user without an explicit
-- tms_role claim as full access for local development, so a self-scoped
-- auth.uid() policy here would add friction without adding a real
-- guarantee.

do $$
declare
  tbl text;
begin
  foreach tbl in array array['chat_threads', 'chat_messages', 'driver_exceptions', 'eta_updates'] loop
    if to_regclass('public.' || tbl) is not null then
      execute format('alter table public.%I enable row level security', tbl);

      execute format('drop policy if exists %I on public.%I', tbl || '_read', tbl);
      execute format(
        'create policy %I on public.%I for select to authenticated using (true)',
        tbl || '_read', tbl
      );

      execute format('drop policy if exists %I on public.%I', tbl || '_insert', tbl);
      execute format(
        'create policy %I on public.%I for insert to authenticated with check (true)',
        tbl || '_insert', tbl
      );

      execute format('drop policy if exists %I on public.%I', tbl || '_update', tbl);
      execute format(
        'create policy %I on public.%I for update to authenticated using (true) with check (true)',
        tbl || '_update', tbl
      );

      execute format('grant select, insert, update on table public.%I to authenticated', tbl);
    end if;
  end loop;
end $$;
