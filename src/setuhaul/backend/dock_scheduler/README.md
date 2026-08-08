# Dock Scheduler

The Dock Scheduler owns docks, appointment slots, appointments, and deterministic scheduling decisions.

It evaluates whether a shipment can be assigned or reassigned to a warehouse slot, and it can consume check-in state as part of that decisioning process.

## Usage

Example Python snippet showing common flows (build DB once using the SQL seed, then create the repo and service):

```py
from pathlib import Path
from setuhaul.db.connection import build_database, connect
from setuhaul.backend.dock_scheduler.repository import DockSchedulerRepository
from setuhaul.backend.dock_scheduler.service import DockSchedulerService

root = Path(__file__).resolve().parents[4]
build_database(root / "data" / "setuhaul_schema_and_seed.sql", root / "data" / "setuhaul_freight_operations.db")
conn = connect(root / "data" / "setuhaul_freight_operations.db")
repo = DockSchedulerRepository(conn)
service = DockSchedulerService(repo)

# Suggest slots for a shipment
options = service.suggest_slots("SHP1006")
for opt in options:
	print(opt.rank, opt.suggestion_type, opt.slot_id, opt.start)

# Hold a slot temporarily
# hold = service.hold_slot("SHP1006", "SLT-...", ttl_minutes=15)

# Request confirmation (moves HELD -> PENDING_CONFIRMATION)
# appointment_id = service.request_confirmation("SHP1006", "SLT-...")

# Confirm booking after driver accepts
# confirmed = service.confirm_booking("SHP1006", "SLT-...", accepted=True)

conn.close()
```

Keep in mind that slot holds and bookings are transactional and the repository will raise scheduler-specific exceptions for unknown shipments, unavailable slots, or invalid bookings.
