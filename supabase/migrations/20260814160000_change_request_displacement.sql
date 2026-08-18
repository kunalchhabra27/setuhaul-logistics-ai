-- Lets a dock_slot_change_requests row represent a priority-swap proposal,
-- not just a plain "move this shipment to a free slot" request: when the
-- requested slot is currently occupied by a lower-priority shipment,
-- DeterministicReschedulingEngine.suggest() (dock_scheduler/scheduler.py)
-- already knows which shipment would need to move and to which of its own
-- compatible slots (see SlotSuggestion.displaced_shipment_id /
-- displaced_to_slot_id) -- these two columns let that information survive
-- from the driver-chat auto-book path (DriverChatService.
-- auto_book_earliest_feasible_slot) into the request WMS reviews, and let
-- DockSchedulerService.decide_change_request move the displaced shipment
-- to its own new slot *before* rebooking the original requester onto the
-- now-freed slot.
--
-- Both nullable: an ordinary (non-swap) change request -- the only kind
-- this table supported before today -- leaves them null.

alter table public.dock_slot_change_requests
  add column if not exists displaced_shipment_id text null references public.shipments(shipment_id);

alter table public.dock_slot_change_requests
  add column if not exists displaced_to_slot_id text null references public.appointment_slots(slot_id);
