# Check-in Portal

The **Check-in Portal** is the SetuHaul backend service responsible for recording the actual physical lifecycle of a shipment after the truck reaches its destination facility.

It acts as the system of record for observed warehouse arrival events such as gate entry, queue movement, dock entry, and unloading completion.

The Check-in Portal does **not** perform appointment scheduling or capacity allocation.

---

## Purpose

Shipment plans and ETAs describe where a truck is expected to be.

The Check-in Portal records where the truck actually is once it reaches the warehouse.

This distinction is important for the SetuHaul scheduling workflow.

For example:

```text
Planned ETA
    ↓
Latest Driver ETA
    ↓
Actual Gate-In
    ↓
Yard / Queue
    ↓
Dock
    ↓
Completed
```

---

## Example Lifecycle

Shipment:

`SHP1006`

1. Gate arrival at `18:03`

State:

```text
arrival_status = GATE_IN
queue_status   = GATE_QUEUE
```

2. Moved to yard at `18:08`

State:

```text
arrival_status = WAITING
queue_status   = YARD_QUEUE
```

3. Dock entry at `18:25`

State:

```text
arrival_status = DOCKED
queue_status   = NONE
dock_in_at     = 18:25
```

4. Unloading completed at `19:05`

State:

```text
arrival_status = COMPLETED
queue_status   = NONE
completed_at   = 19:05
```

---

## Deterministic Design

The Check-in Portal contains no LLM decision logic.

All state changes are deterministic and based on:

- current persisted facility state
- requested transition
- explicit operational events

The conversational agent may query the Check-in Portal through controlled tools, but it must never fabricate:

- `gate_in_at`
- `dock_in_at`
- `completed_at`

These values represent observed physical events.

---

## Integration Contract

Other SetuHaul services should interact with the Check-in Portal through its service or API interfaces rather than modifying `facility_checkins` directly.

The expected application path is:

```text
External System
      ↓
Check-in API
      ↓
CheckInService
      ↓
CheckInRepository
      ↓
Supabase PostgreSQL
```

---
