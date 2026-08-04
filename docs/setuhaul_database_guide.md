# SetuHaul freight-operations database guide

## 1. Purpose

This SQLite database is a classroom-ready operational snapshot for the SetuHaul dock-scheduling and driver-conversation problem. It deliberately separates three responsibilities:

1. **Conversation:** drivers report delays, ask questions and provide constraints in natural language.
2. **Operational data:** shipments, ETA updates, dock resources, appointments, check-ins and facility rules remain structured.
3. **Scheduling:** students may build an optional deterministic tool that reads the complete operational state and recommends or books feasible slots.

The database does **not** contain live GPS tracking. The latest arrival estimate comes from the original plan, a driver-declared update, a warehouse estimate or an operations override.

**Operational snapshot:** 4 August 2026, Asia/Kolkata.

## 2. Package contents

- `setuhaul_freight_operations.db`: ready-to-query SQLite database.
- `setuhaul_schema_and_seed.sql`: full reproducible schema and seed data.
- `setuhaul_er_diagram_core.png/svg`: simplified classroom ER diagram.
- `setuhaul_er_diagram_full.png/svg`: complete ER diagram.
- `setuhaul_data_dictionary.csv`: table and column inventory.

## 3. Database size

| Table | Rows | Purpose |
|---|---:|---|
| `carriers` | 4 | Transport companies that provide trucks and drivers. |
| `drivers` | 15 | Driver identity and carrier relationship. |
| `vehicle_types` | 5 | Reference list of vehicle classes and typical dock requirements. |
| `vehicles` | 15 | Physical trucks assigned to shipments. |
| `facilities` | 2 | Warehouses or distribution centres receiving shipments. |
| `docks` | 9 | Physical loading bays at each facility. |
| `facility_contacts` | 5 | Warehouse and gate contacts used for operational communication. |
| `facility_rules` | 6 | Facility-specific timing, no-show and compatibility rules. |
| `shipments` | 21 | One freight movement from an origin to a destination facility. |
| `eta_updates` | 12 | Original, driver-declared or manually overridden ETA changes. |
| `appointment_slots` | 106 | Time windows for one dock resource. |
| `appointments` | 20 | Bookings connecting a shipment to a dock slot, including history. |
| `dock_status_events` | 3 | Breakdown, maintenance and capacity events affecting a dock. |
| `facility_checkins` | 5 | Actual gate, yard, dock and unloading state for a truck. |
| `chat_threads` | 12 | One conversational request or exception thread. |
| `chat_messages` | 20 | Individual driver, agent, warehouse or operations messages. |
| `driver_exceptions` | 10 | Structured exception information extracted from a conversation. |
| `operational_messages` | 5 | External or internal confirmations and notifications. |

## 4. How the data connects

A `shipment` is assigned to one `driver` and one `vehicle`, and is headed to one destination `facility`. The facility contains physical `docks`, while `appointment_slots` divide each dock into usable time windows. An `appointment` connects one shipment to one slot.

While the truck is travelling, `eta_updates` record the latest declared arrival estimate. When the truck reaches the destination, `facility_checkins` record whether it arrived early, on time or late, and whether it is waiting or inside a dock.

A driver conversation is stored in `chat_threads` and `chat_messages`. Important structured facts extracted from the conversation are represented in `driver_exceptions`. Warehouse emails, confirmations and failures are stored in `operational_messages`.

## 5. Scheduling-tool input

An optional scheduling algorithm should examine **all relevant trucks**, not only the driver currently chatting. Its input can include:

- trucks already inside a dock and their likely completion times;
- early and late trucks waiting in the yard;
- trucks still in transit with original or driver-declared ETA;
- each shipment's priority, unload duration, weight and dock requirement;
- confirmed and pending appointments;
- open, blocked and occupied slots;
- dock breakdowns and maintenance windows;
- facility rules such as no-show grace periods and last allowed start time.

The LLM should not invent this allocation. A deterministic function, rule engine, optimiser or scheduling service should read the structured state and return valid options.

## 6. Seeded cases and edge cases

| Case | What it tests | Seed reference | Main tables |
|---|---|---|---|
| Normal appointment | A truck arrives within its appointment window and unloads normally. | `SHP1002` | `shipments, appointments, facility_checkins` |
| Early arrival | The truck reaches the gate before its slot and asks for an earlier dock. | `SHP1003 / THR007` | `facility_checkins, chat_threads, appointment_slots` |
| Late arrival already at yard | The truck has missed its slot and is physically waiting. | `SHP1004 / THR008` | `facility_checkins, appointments, driver_exceptions` |
| Late ETA reported before arrival | The driver declares that the current appointment will be missed. | `SHP1006 / THR001` | `eta_updates, driver_exceptions, chat_messages` |
| Multiple ETA updates | The same driver first reports 10:50 and later revises the ETA to 11:20. | `SHP1006` | `eta_updates, v_latest_eta` |
| Uncertain ETA | The driver gives a range or an imprecise delay rather than a reliable time. | `SHP1013 / SHP1017` | `eta_updates.confidence_code, chat_messages.requires_human_review` |
| Dock breakdown | A confirmed appointment becomes infeasible because the assigned dock fails. | `SHP1005 / DEVT001` | `dock_status_events, appointment_slots, facility_checkins` |
| Unload overrun | A truck occupies a dock beyond its expected duration, affecting later trucks. | `SHP1002 / DEVT003` | `facility_checkins, dock_status_events` |
| Appointment cancellation frees capacity | A customer cancellation releases a previously occupied slot. | `SHP1008 / APT1008` | `appointments, v_slot_availability` |
| No-show | The appointment grace period passes but the truck never checks in. | `SHP1018 / APT1018` | `facility_rules, appointments` |
| Reefer compatibility | A temperature-controlled load can use only the reefer dock. | `SHP1010 / SHP1015` | `shipments, docks, facility_rules` |
| Reefer dock unavailable | The only compatible dock is under maintenance after the new ETA. | `SHP1015 / THR005` | `dock_status_events, appointment_slots, driver_exceptions` |
| Heavy vehicle compatibility | A 31-tonne load requires the heavy dock and 90-minute windows. | `SHP1016` | `shipments, docks, appointment_slots` |
| No feasible slot | Every compatible slot after the declared ETA is blocked, occupied or outside operating rules. | `SHP1015` | `v_slot_availability, facility_rules` |
| Simultaneous slot competition | Several delayed trucks request nearby standard-dock slots at the same time. | `SHP1006, SHP1012, SHP1013, SHP1014` | `driver_exceptions, appointments, appointment_slots` |
| Priority conflict | A critical shipment competes with normal and low-priority shipments. | `SHP1009 / SHP1014` | `shipments.priority_code` |
| Race condition protection | Only one pending/confirmed/in-progress appointment can occupy one slot. | `ux_active_appointment_per_slot` | `appointments unique partial index` |
| Appointment history | A missed appointment remains in history even after cancellation or replacement. | `APT1012A / APT1016A` | `appointments.is_current, replaced_appointment_id` |
| Duplicate driver message | A messaging retry creates the same delay report twice. | `THR001 / THR009` | `chat_messages.is_duplicate, driver_exceptions.dedupe_key` |
| Ambiguous shipment | The driver has two assignments and sends a delay without identifying which one. | `DRV004 / THR010` | `chat_threads.shipment_id nullable, chat_messages` |
| Ask-only conversation | A driver asks about possible slots without requesting a booking. | `THR011` | `chat_threads, chat_messages` |
| Cancelled shipment message | The driver asks about an appointment after the shipment was cancelled. | `SHP1019 / THR012` | `shipments.current_status, appointments` |
| Warehouse confirmation pending | A possible slot has been requested but not yet confirmed. | `APT1013A / APT1014A` | `appointments.appointment_status, operational_messages` |
| Communication failure | An operational email fails and should not be treated as confirmation. | `OM004` | `operational_messages.delivery_status` |
| Missing contact data | A facility contact exists but the email field is absent. | `CON005` | `facility_contacts` |
| Early truck does not automatically win | A truck may be physically present but still cannot displace scheduled or higher-priority work. | `SHP1003` | `facility_checkins, appointments, priority_code` |
| Different unloading durations | Standard loads may need 45–75 minutes while heavy loads need 90 minutes. | `SHP1006 / SHP1011` | `shipments.expected_unload_min, appointment_slots` |
| Operating-hour limit | A new start after 21:00 requires manual approval. | `RULE005` | `facility_rules, appointment_slots` |
| No real-time tracking | The system uses original ETA and driver-declared updates, not continuous GPS. | `All inbound shipments` | `shipments.original_eta_ts, eta_updates` |
| Multi-facility dataset | The model supports more than one warehouse without mixing schedules. | `FAC-JAI-01 / FAC-GGN-01` | `facilities, docks, appointment_slots` |

## 7. Important database constraints

- A slot can have only one active `PENDING_CONFIRMATION`, `CONFIRMED` or `IN_PROGRESS` appointment. This is enforced through `ux_active_appointment_per_slot`.
- A shipment can have only one current active appointment. Old appointments remain as history.
- Dock, vehicle, shipment and status fields use `CHECK` constraints to prevent invalid codes.
- Foreign keys are enabled and validated.
- Timestamps are stored as ISO-8601 text with the `+05:30` offset for clarity in SQLite.

## 8. Useful views

- `v_latest_eta`: one effective ETA per shipment using the latest update.
- `v_slot_availability`: labels every slot as `AVAILABLE`, `OCCUPIED`, `BLOCKED` or `CLOSED`.
- `v_inbound_operational_state`: joins the latest ETA, current appointment and current facility state.
- `v_current_facility_queue`: shows trucks physically waiting at a facility.

## 9. Starter queries


```sql
-- 1. Current inbound picture: booked slot, latest ETA and yard state
SELECT *
FROM v_inbound_operational_state
WHERE destination_facility_id = 'FAC-JAI-01'
ORDER BY effective_eta_ts;

-- 2. Trucks physically waiting in the yard
SELECT *
FROM v_current_facility_queue
WHERE facility_id = 'FAC-JAI-01'
ORDER BY queue_position;

-- 3. Available standard-dock slots after a driver's declared ETA
SELECT *
FROM v_slot_availability
WHERE facility_id = 'FAC-JAI-01'
  AND dock_type = 'STANDARD'
  AND availability_status = 'AVAILABLE'
  AND slot_start_ts >= '2026-08-04T11:20:00+05:30'
ORDER BY slot_start_ts, dock_code;

-- 4. All open exceptions competing around the same time
SELECT
    e.exception_id,
    e.shipment_id,
    e.declared_eta_ts,
    s.priority_code,
    s.required_dock_type,
    s.expected_unload_min,
    e.exception_status
FROM driver_exceptions e
LEFT JOIN shipments s ON s.shipment_id = e.shipment_id
WHERE e.exception_status IN ('OPEN','NEEDS_INFORMATION','WAITING_CONFIRMATION')
ORDER BY e.declared_eta_ts, s.priority_code DESC;

-- 5. Test the race-condition constraint
-- The second active booking for the same slot will fail with a UNIQUE constraint error.
```

## 10. Student design questions

1. Which information should the agent clarify before calling any scheduling tool?
2. Should a displayed option reserve capacity, or should capacity change only after explicit driver confirmation?
3. How should the system handle two drivers choosing the same slot at nearly the same time?
4. How should priority, physical arrival, waiting time and appointment commitments be balanced?
5. When should a pending warehouse confirmation expire?
6. What should happen when no feasible compatible slot exists?
7. Which actions require deterministic validation even if an LLM suggested them?
