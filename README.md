# SetuHaul Logistics Exception Agent

A minimal FDE project for handling driver delay exceptions and dock-appointment rescheduling.

## Current build: deterministic application logic

The repository currently implements the operational layer first:

- reads the seeded SetuHaul SQLite data;
- identifies the shipment's effective ETA, priority, unload duration and dock requirement;
- filters slots deterministically by ETA, compatibility, capacity and driver deadline;
- ranks open slots by earliest feasible start;
- can suggest a priority-based swap only when the displaced shipment has a later feasible slot;
- refuses to book unless the driver explicitly accepts;
- uses a database transaction and the supplied unique index to prevent conflicting bookings.

The LLM/conversation layer is intentionally not part of the scheduling decision. It will later extract or clarify structured inputs and call this engine as a controlled tool.

## Minimal policy

1. Keep the original appointment when it is still feasible.
2. Otherwise propose the earliest compatible open slot after the latest declared ETA.
3. Respect an optional `must_finish_by` driver constraint.
4. A higher-priority shipment may receive a swap suggestion only if the lower-priority shipment can move to a later compatible open slot.
5. Suggestions are not bookings. Booking happens only after explicit driver acceptance.
6. If there is no feasible option, escalate to human operations.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -m setuhaul.cli SHP1006 --rebuild
pytest
```

Example with a driver deadline:

```bash
python -m setuhaul.cli SHP1006 \
  --must-finish-by 2026-08-04T14:00:00+05:30
```

## Project structure

```text
src/setuhaul/
├── cli.py
├── models.py
├── db/
│   ├── connection.py
│   └── repository.py
└── scheduling/
    └── engine.py
```

## Next build phase

Add the chat workflow around this engine:

`identify shipment -> clarify delay/ETA -> call deterministic scheduler -> show options -> wait for explicit acceptance -> confirm transactionally -> retry or escalate`

## Team collaboration

See [TEAM_WORKFLOW.md](TEAM_WORKFLOW.md) and [CONTRIBUTING.md](CONTRIBUTING.md).

The repository includes:

- a pull-request template;
- feature and bug issue templates;
- GitHub Actions running `pytest` on every PR;
- a branch-based workflow suitable for four contributors.

Recommended first team split:

| Contributor | Initial focus |
|---|---|
| 1 | Scheduling feasibility and ranking |
| 2 | SQL repository and transactional booking |
| 3 | Conversation/thread state design |
| 4 | Test scenarios, concurrency and documentation |
