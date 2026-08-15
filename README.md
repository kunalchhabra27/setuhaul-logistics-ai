# 🚛 SetuHaul Intelligent Dock Rescheduler

> A logistics operations platform with a deterministic backend and a React portal for TMS, dock scheduling, driver communication, and check-in workflows.

---

# Overview

SetuHaul is a freight logistics company operating across multiple warehouses.

Every shipment is assigned a destination warehouse and a dock appointment before the truck begins its journey.

When delays occur due to vehicle breakdowns, traffic, weather, or operational issues, drivers can report exceptions through the portal while the backend keeps all scheduling and state transitions deterministic.

The repository now contains:

- a FastAPI backend with TMS, dock scheduler, check-in, and driver chat routers
- a React + TypeScript + Vite frontend in [`frontend/`](./frontend/)
- deterministic backend tests and a production frontend build

---

# What You Can Do

## Frontend Portal

- open the landing page and choose a workspace
- sign in to a service-specific portal shell using Supabase Auth
- access only the portal your account is authorized for
- move between TMS, dock scheduler, check-in, and driver chat views when authorized
- use the real check-in UI against the backend `/checkins` APIs
- see the existing truck transition animation and portal visuals preserved from the supplied portal ZIP

## Backend Systems

- query and manage TMS records
- ask the dock scheduler for slot suggestions, holds, and confirmations
- record check-in lifecycle transitions
- expose driver chat service availability
- keep all business rules and state transitions in the Python backend, not in React

# Design Philosophy

The architecture intentionally separates **UI**, **conversation**, and **business decision making**.

```text
Driver

      │

      ▼

LangChain Conversational Agent
(Conversation & Understanding)

      │

      ▼

Deterministic Scheduling Engine
(Business Rules)

      │

      ▼

Backend Systems of Record

      │

      ▼

Shared Operational Data Layer
(Supabase PostgreSQL)
```

The conversational layer **never allocates warehouse capacity.**

The scheduling engine **never interprets natural language.**

The FastAPI application is the thin HTTP layer that exposes each backend system as a router while keeping the business logic inside the corresponding service classes.

The new React portal in [`frontend/`](./frontend/) is intentionally thin:

- it renders the existing portal experience
- it reads data from FastAPI where endpoints already exist
- it never reproduces the backend state machine in React
- it keeps auth, API clients, and feature screens separated for future expansion

---

# System Architecture

```text
                          Driver

                             │

                             ▼

                 LangChain Conversational Agent

                             │

        ┌────────────┬────────────┬────────────┬────────────┐

        ▼            ▼            ▼            ▼

      TMS      Dock Scheduler   Driver Chat   Check-in Portal

                             │

                             ▼

                    Messaging Service

                             │

                             ▼

             Shared Operational Data Layer
                 (Supabase PostgreSQL)

                             │

                             ▼

                  Human Coordinator (Escalation)
```

---

# Backend Systems

The repository is organised into independent backend systems.

Each backend owns a single business capability.

---

## Transport Management System (TMS)

Responsible for shipment planning.

Owns

- Drivers
- Vehicles
- Shipments
- Planned ETA
- Shipment Priority
- Origin & Destination
- Driver and shipment context lookups

Does **not** own

- Appointment scheduling
- Driver conversations
- Facility check-ins

### TMS features

- list shipments with pagination and filters
- fetch shipment details
- fetch driver details by phone or ID
- fetch shipments assigned to a driver
- fetch active shipments assigned to a driver
- create drivers, vehicles, and shipments
- update drivers, vehicles, and shipments
- return driver context for portal use
- return shipment context for portal use
- preserve backend authorization rules for reader and admin roles

---

## Dock Scheduler (Warehouse Management System)

Responsible for warehouse scheduling.

Owns

- Facilities
- Docks
- Appointment Slots
- Dock Rules
- Current Appointments
- Deterministic Scheduling Engine
- Slot holds and confirmations

Does **not** own

- ETA updates
- Physical truck arrival
- Driver conversations

### Dock Scheduler features

- suggest feasible dock slots for a shipment
- create temporary holds for a slot
- request confirmation for a hold
- confirm or reject a slot booking
- cancel an active hold
- enforce deterministic conflict handling and error responses
- keep slot allocation logic inside the backend scheduler, not the frontend

---

## Driver Chat / ETA Portal

Responsible for communication with drivers.

Owns

- Driver conversations
- ETA updates
- Delay reports
- Exception records
- Conversation Threads

Does **not** own

- Capacity allocation
- Appointment scheduling

### Driver Chat / ETA features

- health endpoint for service availability
- supports the chat/ETA portal shell in the frontend
- remains isolated so future driver message and ETA APIs can be added without changing the UI architecture
- the current frontend intentionally does not fake message persistence or OTP flows

---

## Check-in Portal

Responsible for recording the truck's physical state once it reaches the destination warehouse.

Owns

- Facility Check-ins
- Gate Arrival
- Yard Queue
- Dock Entry
- Unloading Completion

Does **not** own

- Appointment allocation
- ETA interpretation
- Driver conversations

### Check-in Portal features

- fetch the current check-in state for a shipment
- create gate check-ins
- update queue status
- mark shipments as docked
- mark unloads as completed
- enforce backend-validated state transitions only
- refresh the UI from the backend after each mutation

---

# FastAPI App

The current application entrypoint is [`src/setuhaul/main.py`](./src/setuhaul/main.py).

It wires the backend routers into one FastAPI app:

- `/health`
- `/tms/*`
- `/dock-scheduler/*`
- `/checkins/*`
- `/driver-chat-eta/*`

The effective API prefix is `/api/v1` for the feature routers:

- `/api/v1/tms/*`
- `/api/v1/dock-scheduler/*`
- `/api/v1/checkins/*`
- `/api/v1/driver-chat-eta/*`

---

# How To Run

## 1. Prerequisites

Install:

- Python 3.11 or newer
- `uv`
- Node.js 20+ and `npm`

Optional but recommended:

- a local `.env` file for backend secrets
- a local `.env` file in [`frontend/`](./frontend/) for frontend environment variables

## 2. Backend Environment

From the repository root:

```bash
uv sync
```

This installs the Python dependencies declared in [`pyproject.toml`](./pyproject.toml).

If `uv` uses a cache path your system cannot access, run:

```bash
UV_CACHE_DIR=/private/tmp/uv-cache uv sync
```

## 3. Backend Configuration

Set these environment variables for the backend:

```bash
export SUPABASE_URL="https://your-project.supabase.co"
export SUPABASE_PUBLISHABLE_KEY="your-publishable-key"
export FRONTEND_ORIGIN="http://localhost:5173"
export LOG_LEVEL="INFO"
```

Notes:

- `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY` are required by the Python backend.
- `FRONTEND_ORIGIN` is used for CORS.
- The backend does not expose or require a Supabase service-role key.
- backend env examples live in [`.env.example`](./.env.example)

## 4. Start The Backend

From the repository root:

```bash
UV_CACHE_DIR=/private/tmp/uv-cache uv run uvicorn setuhaul.main:app --reload
```

If you prefer to use an activated virtual environment instead of `uv run`:

```bash
source .venv/bin/activate
PYTHONPATH=src uvicorn setuhaul.main:app --reload
```

Backend URLs:

- `http://127.0.0.1:8000/health`
- `http://127.0.0.1:8000/docs`
- `http://127.0.0.1:8000/api/v1/tms/health`
- `http://127.0.0.1:8000/api/v1/checkins/SHP1006`

By default the backend uses the local seeded SQLite dataset so the app stays runnable even when Supabase is unavailable. If you later wire in Supabase-backed persistence again, keep the same API surface and switch only the repository layer.

## 5. Frontend Environment

From the `frontend/` folder:

```bash
cp .env.example .env
```

Edit `frontend/.env` as needed:

```bash
VITE_API_BASE_URL=http://127.0.0.1:8000
VITE_SUPABASE_URL=https://dhwvaqfwdjddmuzzbguc.supabase.co
VITE_SUPABASE_PUBLISHABLE_KEY=sb_publishable_cN-8VtoDPUrfCnpWMXRsPA_v-utcoAU
```

Notes:

- `VITE_API_BASE_URL` should point at the backend origin, not include `/api/v1`.
- the frontend app will add `/api/v1` internally
- frontend and backend point at the same Supabase project
- use the same Supabase URL value in both places:
  - backend: `SUPABASE_URL`
  - frontend: `VITE_SUPABASE_URL`
- use the same Supabase publishable key value in both places:
  - backend: `SUPABASE_PUBLISHABLE_KEY`
  - frontend: `VITE_SUPABASE_PUBLISHABLE_KEY`
- frontend authorization is department-scoped through the signed-in Supabase user metadata
- Supabase auth bootstrap is isolated under `frontend/src/auth/`
- OTP/Twilio flows are intentionally not faked
- frontend env examples live in [`frontend/.env.example`](./frontend/.env.example)

## 6. Start The Frontend

## Frontend

The portal frontend now lives in [`frontend/`](./frontend/).

From a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Frontend URLs:

- `http://127.0.0.1:5173`
- `http://127.0.0.1:5173/auth/tms`
- `http://127.0.0.1:5173/portal/checkin`

The check-in portal is wired to the real backend `/checkins` APIs and refreshes state after each mutation. TMS and dock scheduler screens call the existing backend endpoints; driver chat currently consumes the existing health endpoint until more API coverage is available.

Auth and authorization notes:

- `/auth/drivers`, `/auth/tms`, `/auth/wms`, and `/auth/checkin` are service-specific entry points
- a valid Supabase session means the user is authenticated, not authorized for every portal
- portal access is restricted by the user's `service_role` metadata
- unauthorized service switches redirect to the matching auth page with an access-denied message

Important routing note:

- the backend API is mounted under `/api/v1`
- check-in URLs in Swagger should use `/api/v1/checkins/...`
- if you open `/checkins/...` directly, that is a different path and may return 404

---

---

# API Reference

All routes below are prefixed by the FastAPI app and are available from the same server.

## System

- `GET /health`

## TMS

All routes below are under `/api/v1/tms`.

Public:

- `GET /api/v1/tms/health`

Read access:

- `GET /api/v1/tms/drivers?phone=...`
- `GET /api/v1/tms/drivers/{driver_id}`
- `GET /api/v1/tms/drivers/{driver_id}/shipments`
- `GET /api/v1/tms/drivers/{driver_id}/active-shipments`
- `GET /api/v1/tms/vehicles/{vehicle_id}`
- `GET /api/v1/tms/shipments`
- `GET /api/v1/tms/shipments/{shipment_id}`
- `GET /api/v1/tms/context/drivers/{driver_id}`
- `GET /api/v1/tms/context/shipments/{shipment_id}`

Write access:

- `POST /api/v1/tms/drivers`
- `PATCH /api/v1/tms/drivers/{driver_id}`
- `POST /api/v1/tms/vehicles`
- `PATCH /api/v1/tms/vehicles/{vehicle_id}`
- `POST /api/v1/tms/shipments`
- `PATCH /api/v1/tms/shipments/{shipment_id}`

Query parameters for `GET /tms/shipments`:

- `driver_id`
- `destination_id`
- `status`
- `limit`
- `offset`

## Dock Scheduler

All routes below are under `/api/v1/dock-scheduler`.

- `POST /api/v1/dock-scheduler/suggest`
- `POST /api/v1/dock-scheduler/hold`
- `POST /api/v1/dock-scheduler/request-confirmation`
- `POST /api/v1/dock-scheduler/confirm`
- `POST /api/v1/dock-scheduler/cancel-hold`

## Check-in Portal

All routes below are under `/api/v1/checkins`.

- `GET /api/v1/checkins/{shipment_id}`
- `POST /api/v1/checkins/gate`
- `PATCH /api/v1/checkins/queue`
- `PATCH /api/v1/checkins/dock`
- `PATCH /api/v1/checkins/complete`

Example gate-in payload:

```json
{
  "shipment_id": "SHP1006",
  "facility_id": "FAC-JAI-01",
  "gate_in_at": "2026-08-08T18:03:00+05:30"
}
```

Example queue update payload:

```json
{
  "shipment_id": "SHP1006",
  "queue_status": "YARD_QUEUE"
}
```

Example dock-in payload:

```json
{
  "shipment_id": "SHP1006",
  "dock_in_at": "2026-08-08T18:25:00+05:30"
}
```

Example completion payload:

```json
{
  "shipment_id": "SHP1006",
  "completed_at": "2026-08-08T19:05:00+05:30"
}
```

### Check-in test values

The backend service accepts business identifiers like `SHP1006` and `FAC-JAI-01`, but the live Supabase schema shown in the attached table definition stores `shipment_id` and `facility_id` as UUIDs.

Use these values when you want to test the Supabase-backed table directly:

- `shipment_id`: a real UUID from `public.shipments.shipment_id`
- `facility_id`: a real UUID from `public.facilities.facility_id`

Use these values when you want to test the local seeded SQLite backend:

- `shipment_id`: `SHP1006`
- `facility_id`: `FAC-JAI-01`

Recommended local test sequence for the check-in portal:

1. `GET /api/v1/checkins/SHP1006`
2. `POST /api/v1/checkins/gate`
3. `PATCH /api/v1/checkins/queue`
4. `PATCH /api/v1/checkins/dock`
5. `PATCH /api/v1/checkins/complete`

Example payloads for the local backend:

```json
{
  "shipment_id": "SHP1006",
  "facility_id": "FAC-JAI-01",
  "gate_in_at": "2026-08-08T18:03:00+05:30"
}
```

```json
{
  "shipment_id": "SHP1006",
  "queue_status": "YARD_QUEUE"
}
```

```json
{
  "shipment_id": "SHP1006",
  "dock_in_at": "2026-08-08T18:25:00+05:30"
}
```

```json
{
  "shipment_id": "SHP1006",
  "completed_at": "2026-08-08T19:05:00+05:30"
}
```

If you are calling the live Supabase table directly, replace `SHP1006` and `FAC-JAI-01` with the UUID values that exist in your project.

## Driver Chat / ETA

All routes below are under `/api/v1/driver-chat-eta`.

- `GET /api/v1/driver-chat-eta/health`

---

## Frontend Integration Notes

The React frontend uses these integration points:

- check-in status refresh after every mutation
- TMS shipment list reads from `/api/v1/tms/shipments`
- dock scheduler suggestions read from `/api/v1/dock-scheduler/suggest`
- driver chat shell reads `/api/v1/driver-chat-eta/health`
- Supabase auth bootstrap lives under `frontend/src/auth/`
- Twilio OTP integration is intentionally not faked and should be added only when backend support is ready

## Current Local Validation

- Check-in portal backend tests pass locally.
- Frontend production build passes locally.
- Backend business logic, scheduler logic, and state machine logic were not refactored for the frontend integration.

---

# Feature Matrix

## Landing Page

- animated SetuHaul hero
- service cards for TMS, dock scheduler, check-in, and driver portal
- truck transition animation between service routes
- responsive desktop and mobile layout

## Auth Page

- per-service sign-in shell
- isolated auth state per workspace
- Supabase Auth sign-in and registration
- department-scoped authorization based on user metadata

## Portal Workspace

- header with service switching
- sidebar navigation
- workspace-specific panels
- preserved portal styling and motion
- protected route access per service

## Check-in Workspace

- fetch shipment check-in state
- gate check-in
- queue update
- mark docked
- complete unload
- backend refresh after every mutation
- local SQLite fallback remains available when Supabase is unavailable

## TMS Workspace

- shipment table view
- API-backed shipment listing
- future-ready structure for shipment create/update flows

## Dock Scheduler Workspace

- slot suggestion list
- API-backed scheduler calls
- future-ready hold/confirmation structure

## Driver Chat Workspace

- service health integration
- placeholder conversation shell
- isolated for future chat/ETA API expansion

---

# Verification

What currently passes:

- backend test suite
- frontend production build

Recommended local commands:

```bash
UV_CACHE_DIR=/private/tmp/uv-cache uv run pytest -q
cd frontend && npm run build
```

If you want to run the app end-to-end, keep both servers open:

```bash
UV_CACHE_DIR=/private/tmp/uv-cache uv run uvicorn setuhaul.main:app --reload
cd frontend && npm run dev
```

If you need to force the local backend explicitly:

```bash
DATA_BACKEND=local UV_CACHE_DIR=/private/tmp/uv-cache uv run uvicorn setuhaul.main:app --reload
```

---

# Repository Structure

```text
setuhaul-exception-agent/
│
├── README.md
├── pyproject.toml
├── requirements.txt
├── .gitignore
├── .env.example
├── locustfile.py
│
├── docs/
│   ├── architecture.md
│   ├── business_flow.md
│   ├── scheduling_engine.md
│   ├── api_contracts.md
│   ├── database_design.md
│   ├── concurrency_strategy.md
│   └── demo_scenarios.md
│
├── src/
│   └── setuhaul/
│       │
│       ├── __init__.py
│       │
│       ├── backend/
│       │   ├── __init__.py
│       │   │
│       │   ├── tms/
│       │   │   ├── __init__.py
│       │   │   ├── README.md
│       │   │   ├── SKILLS.md
│       │   │   ├── models.py
│       │   │   ├── repository.py
│       │   │   ├── service.py
│       │   │   ├── api.py
│       │   │   ├── exceptions.py
│       │   │   └── tests/
│       │   │       ├── __init__.py
│       │   │       ├── test_repository.py
│       │   │       ├── test_service.py
│       │   │       └── test_api.py
│       │   │
│       │   ├── dock_scheduler/
│       │   │   ├── __init__.py
│       │   │   ├── README.md
│       │   │   ├── SKILLS.md
│       │   │   ├── models.py
│       │   │   ├── repository.py
│       │   │   ├── service.py
│       │   │   ├── scheduler.py
│       │   │   ├── ranking.py
│       │   │   ├── constraints.py
│       │   │   ├── api.py
│       │   │   ├── exceptions.py
│       │   │   └── tests/
│       │   │       ├── __init__.py
│       │   │       ├── test_repository.py
│       │   │       ├── test_constraints.py
│       │   │       ├── test_scheduler.py
│       │   │       ├── test_priority_rules.py
│       │   │       ├── test_concurrency.py
│       │   │       └── test_api.py
│       │   │
│       │   ├── driver_chat_eta/
│       │   │   ├── __init__.py
│       │   │   ├── README.md
│       │   │   ├── SKILLS.md
│       │   │   ├── models.py
│       │   │   ├── repository.py
│       │   │   ├── service.py
│       │   │   ├── thread_manager.py
│       │   │   ├── deduplication.py
│       │   │   ├── api.py
│       │   │   ├── exceptions.py
│       │   │   └── tests/
│       │   │       ├── __init__.py
│       │   │       ├── test_repository.py
│       │   │       ├── test_service.py
│       │   │       ├── test_thread_manager.py
│       │   │       ├── test_deduplication.py
│       │   │       └── test_api.py
│       │   │
│       │   └── checkin_portal/
│       │       ├── __init__.py
│       │       ├── README.md
│       │       ├── SKILLS.md
│       │       ├── models.py
│       │       ├── repository.py
│       │       ├── service.py
│       │       ├── state_machine.py
│       │       ├── api.py
│       │       ├── exceptions.py
│       │       └── tests/
│       │           ├── __init__.py
│       │           ├── test_repository.py
│       │           ├── test_service.py
│       │           ├── test_state_machine.py
│       │           └── test_api.py
│       │
│       ├── orchestration/
│       │   ├── __init__.py
│       │   ├── README.md
│       │   ├── SKILLS.md
│       │   ├── agent.py
│       │   ├── prompts.py
│       │   ├── chains.py
│       │   ├── tools.py
│       │   ├── tool_registry.py
│       │   ├── session_manager.py
│       │   └── tests/
│       │       ├── __init__.py
│       │       ├── test_tools.py
│       │       └── test_session_manager.py
│       │
│       ├── infrastructure/
│       │   ├── __init__.py
│       │   ├── README.md
│       │   ├── SKILLS.md
│       │   ├── settings.py
│       │   ├── supabase_client.py
│       │   ├── auth.py
│       │   ├── logging.py
│       │   ├── observability.py
│       │   └── database.py
│       │
│       └── main.py
│
├── tests/
│   ├── integration/
│   │   ├── test_delay_to_reschedule_flow.py
│   │   ├── test_driver_rejects_option.py
│   │   ├── test_slot_conflict_retry.py
│   │   ├── test_docked_shipment_locked.py
│   │   └── test_duplicate_message_flow.py
│   │
│   └── fixtures/
│       ├── shipments.py
│       ├── appointments.py
│       └── checkins.py
│
└── scripts/
    ├── seed_supabase.py
    ├── reset_local_data.py
    └── run_demo.py
locust.py
```

---

# Backend Folder Structure

Every backend follows the same internal structure.

```text
backend/

└── module_name/

    ├── README.md

    ├── models.py

    ├── repository.py

    ├── service.py

    ├── api.py

    └── tests/
```

This allows every backend system to remain independently testable and maintainable.

---

# Scheduling Workflow

```text
Driver reports delay

↓

Identify shipment

↓

Retrieve latest ETA

↓

Retrieve current appointment

↓

Is current appointment feasible?

        │

        ├── Yes

        │

        ▼

Keep Appointment

        │

        └── No

↓

Find compatible docks

↓

Find feasible appointment slots

↓

Rank appointment options

↓

Present options to driver

↓

Driver confirms

↓

Revalidate capacity

↓

Update appointment

↓

Notify stakeholders
```

---

# Deterministic Scheduling Engine

The scheduling engine is entirely deterministic.

Inputs

- Current Appointments
- Available Slots
- Latest ETA
- Driver Constraints
- Shipment Priority
- Dock Compatibility

Outputs

- Ranked Appointment Recommendations

The same inputs will always generate the same scheduling outcome.

---

# Human-in-the-Loop

Appointments are never automatically confirmed.

```
Generate Options

↓

Driver Selects

↓

Validate Again

↓

Confirm Appointment
```

If another request claims the slot before confirmation

```
Conflict Detected

↓

Re-run Scheduler

↓

Return Updated Options
```

---

# Conversational Layer

The conversational interface is implemented using **LangChain**.

Responsibilities

- Understand driver messages
- Identify shipment
- Extract ETA updates
- Ask clarification questions
- Present scheduling options
- Capture driver confirmation

The conversational layer never makes scheduling decisions.

---

# Authentication & Data Layer

The project uses **Supabase** as the shared operational data layer.

Supabase provides

- PostgreSQL database
- Authentication
- Row-Level Security (RLS)
- API access
- Real-time capabilities (future scope)

All backend systems communicate with the same operational database while maintaining clear ownership boundaries over their respective tables.

## Observability, Load Testing & Agent Runtime

SetuHaul includes a local engineering harness that preserves normal API,
authentication, scheduler, and state-machine ownership:

- **Locust** provides local concurrency testing for public reads,
  authenticated portal APIs, dedicated LT-only Check-in lifecycles, and the
  local AgentCore conversation endpoint.
- **OpenTelemetry** emits distributed traces, request and operation metrics,
  and correlated structured logs from the existing FastAPI services.
- **LangSmith** traces the Driver Chat LangChain, LLM, and tool path in project
  `setuhaul` under run name `setuhaul.driver_chat`.
- **AgentCore** runs the existing Driver Chat through a local runtime on port
  `8090` with same-session continuity. AWS deployment has not been performed.
- **Automatic harness authentication** obtains and refreshes a dedicated
  test-user JWT from local Supabase credentials. `TEST_ACCESS_TOKEN` remains a
  manual fallback; secrets remain in memory or gitignored local files.

📘 [Harness & Observability Guide](docs/HARNESS.md)

---

# Technology Stack

| Layer | Technology |
|---------|------------|
| Language | Python 3.11 |
| API Framework | FastAPI |
| LLM Framework | LangChain |
| Database | Supabase PostgreSQL |
| Authentication | Supabase Auth |
| Validation | Pydantic |
| Testing | Pytest |
| Load Testing | Locust |
| Agent Runtime | AWS Bedrock AgentCore (local ready; cloud not deployed) |
| Observability | OpenTelemetry and LangSmith |
| Monitoring *(Planned)* | Amazon CloudWatch |

---

# Development Roadmap

## Phase 1

- Backend Systems
- Supabase Integration
- Deterministic Scheduling Engine

## Phase 2

- LangChain Agent
- Tool Integration
- Conversation Context

## Phase 3

- AWS Bedrock AgentCore cloud deployment
- CloudWatch

## Phase 4

- Concurrent Scheduling
- Load Testing
- Production Hardening

---

# Contributing

1. Create a feature branch.

```bash
git checkout -b feature/checkin-portal
```

2. Commit frequently using meaningful commit messages.

3. Open a Pull Request.

4. Request a review before merging into `main`.

Direct commits to the `main` branch are discouraged.

---

# Future Enhancements

- Distributed session management
- Multi-facility scheduling
- Real-time warehouse notifications
- Advanced scheduling heuristics
- Human escalation dashboard
- Operational analytics
- OR-Tools based optimization

---

# License

Developed as part of the **SetuHaul Forward Deployed Engineering Challenge**.
