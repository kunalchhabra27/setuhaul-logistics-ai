alter table public.drivers
add column if not exists auth_user_id text;

create unique index if not exists drivers_auth_user_id_key
on public.drivers (auth_user_id)
where auth_user_id is not null;
