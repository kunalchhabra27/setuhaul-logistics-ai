-- Add an archive flag to public.shipments so completed shipments can be
-- archived out of the active TMS view without deleting history.
-- Stored as integer 0/1 to match the existing boolean-as-integer convention
-- already used on this table (see refrigeration_capable, active_flag).

alter table public.shipments
  add column if not exists archived_flag integer not null default 0;
