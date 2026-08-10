-- Remove RLS restrictions on public.drivers so a driver authenticated via
-- Supabase Auth can self-register (insert their own row on first login) and
-- read/update their own profile afterwards, without needing a tms_role
-- claim. This supersedes the ADMIN_1/AGENT_READER-only policies from
-- 20260808095820_tms_authorization.sql for this table only -- vehicles and
-- shipments RLS from that migration are untouched.

drop policy if exists tms_read_drivers on public.drivers;
drop policy if exists tms_admin_insert_drivers on public.drivers;
drop policy if exists tms_admin_update_drivers on public.drivers;

alter table public.drivers disable row level security;

grant select, insert, update on table public.drivers to authenticated;
