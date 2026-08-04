# Deterministic rescheduling algorithm

## Boundary

The algorithm receives structured data only. It does not interpret driver text and does not use an LLM to select or allocate a slot.

## Inputs

- current shipment and appointment;
- latest effective ETA;
- current slot availability and active appointments;
- vehicle/load-to-dock compatibility;
- expected unload duration;
- driver earliest-start and must-finish-by constraints;
- shipment priority.

## Decision sequence

1. Reject cancelled or completed shipments.
2. Use the latest explicit ETA as the release time.
3. Filter to the destination facility and compatible active docks.
4. Remove blocked, closed, too-early and too-short slots.
5. Remove options ending after the driver's deadline.
6. Prefer the original slot when it remains feasible.
7. Rank remaining open slots by earliest start.
8. For a higher-priority request, optionally suggest displacement only when the lower-priority load has a later feasible open slot.
9. Return ranked suggestions without changing the database.
10. On explicit acceptance, recheck availability inside a write transaction and then confirm the booking.
