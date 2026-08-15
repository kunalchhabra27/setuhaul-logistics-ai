# Redis cache matrix

The Redis layer is a cache only; Supabase remains authoritative. Keys use the
`setuhaul:v1` namespace, hashed authorization/entity boundaries, canonical
query hashes, and generation-qualified payload keys.

| Portal/API family | Cached reads | Scope | TTL | Invalidation dependencies |
| --- | --- | --- | --- | --- |
| Driver | profile, reference lists, snapshot parts | driver/user | 15–300s | driver, shipment, appointment, check-in, dock state |
| TMS | drivers, vehicles, shipments, lists, facilities, shipment context/reference data | role; facility is included in the canonical query for facility lists | 20–300s | driver, vehicle, facility, shipment, check-in, dock state |
| WMS/Dock Scheduler | dock board, change-request queue | user until facility context is available | 8s | shipment, appointment slots, holds, appointments, change requests |
| Check-in | enriched status | user until facility context is available | 20s | shipment and facility check-in state |

Dynamic chatbot responses, exports, authorization failures, missing entities,
and 404 responses are not cached. Valid empty collections may be cached.

Shipment mutations invalidate TMS shipment/detail/list/context generations,
Driver snapshot generations, WMS dock-board generations, and Check-in-derived
generations. Generation bumps replace broad keyspace scans; stale physical keys
expire naturally.
