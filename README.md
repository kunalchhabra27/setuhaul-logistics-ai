# 🚛 SetuHaul Intelligent Dock Rescheduler

> A conversational logistics exception handling system that assists truck drivers in reporting delays while using a deterministic scheduling engine to generate feasible dock appointment recommendations.

---

# Overview

SetuHaul is a freight logistics company operating across multiple warehouses.

Every shipment is assigned a destination warehouse and a dock appointment before the truck begins its journey.

When delays occur due to vehicle breakdowns, traffic, weather, or operational issues, drivers currently contact warehouse coordinators manually to reschedule appointments.

This process is repetitive, time-consuming, and difficult to manage when multiple delayed trucks compete for limited warehouse capacity.

This project builds a **Conversational Exception Handling Agent** that assists drivers while ensuring **all scheduling decisions remain deterministic, explainable, and operationally safe.**

---

# Problem Statement

> How can SetuHaul provide a conversational interface for drivers to report delays, ask questions, and receive revised appointment options while ensuring limited warehouse capacity is allocated deterministically without conflicting promises?

---

# Design Philosophy

The architecture intentionally separates **conversation** from **business decision making**.

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

Does **not** own

- Appointment scheduling
- Driver conversations
- Facility check-ins

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

Does **not** own

- ETA updates
- Physical truck arrival
- Driver conversations

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
| Deployment *(Planned)* | AWS Bedrock AgentCore |
| Observability *(Planned)* | LangSmith |
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

- AWS Bedrock AgentCore
- LangSmith
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