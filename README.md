# 🚛 SetuHaul Exception Handling Agent

> A conversational logistics exception handling system that helps truck drivers report delays and receive deterministic dock rescheduling recommendations.

This project is being developed as part of the **Forward Deployed Engineering (FDE)** program.

Unlike a traditional chatbot, this project separates **conversation** from **business decision making**.

- 🧠 LLM → understands and manages the conversation.
- ⚙️ Deterministic Scheduler → computes feasible appointment options.
- 🏭 Warehouse Database → source of operational truth.
- ☁️ AWS AgentCore + LangSmith + CloudWatch → deployment and observability (future phases).

---

# Business Problem

SetuHaul is a logistics company that transports shipments between warehouses.

Every shipment has a **pre-booked dock appointment** at the destination warehouse.

When a truck is delayed due to:

- tyre puncture
- traffic
- vehicle breakdown
- weather
- loading delays

the driver usually contacts the warehouse coordinator manually.

The coordinator then

1. identifies the shipment
2. checks the existing appointment
3. evaluates whether the appointment is still feasible
4. checks warehouse capacity
5. finds alternate appointment slots
6. communicates with the warehouse
7. confirms the revised appointment

This process is slow and becomes difficult when many delayed trucks request the same limited dock capacity simultaneously.

Our goal is to automate this workflow.

---

# Problem Statement

> How might SetuHaul provide a conversational way for drivers to report delays, ask questions, and consider revised appointments while ensuring many simultaneous requests are handled without conflicting warehouse commitments?

---

# Project Goals

The system should allow a driver to

- report a delay
- update ETA
- ask for alternate appointments
- compare appointment options
- specify constraints
- confirm a revised appointment

while ensuring

- deterministic scheduling
- no double booking
- explainable decisions
- human confirmation before booking

---

# System Architecture

```
                    Driver

                       │

             Natural Language Chat

                       │

         LangGraph Conversation Agent
        (LLM understanding only)

                       │

        Deterministic Scheduling Engine

                       │

         Warehouse Operational Database

                       │

       Appointment Confirmation Service

                       │

        CloudWatch • LangSmith • AgentCore
```

---

# Design Philosophy

This project intentionally separates AI reasoning from operational decisions.

## LLM Responsibilities

The conversational layer is responsible for

- understanding free-text messages
- identifying user intent
- asking clarification questions
- maintaining conversation context
- presenting scheduling options
- collecting driver confirmation

The LLM **never**

- allocates warehouse capacity
- books appointments
- decides priority
- overrides business rules

---

## Deterministic Responsibilities

The scheduling engine is responsible for

- validating shipment information
- computing latest ETA
- checking facility compatibility
- checking dock availability
- checking operating hours
- enforcing driver constraints
- preventing double booking
- ranking feasible slots

Every execution of the scheduling engine with identical inputs produces identical outputs.

---

# Current Scope

This project only handles

✅ Driver delay reporting

✅ Appointment rescheduling

The following are intentionally **out of scope**

- GPS tracking
- Route optimization
- Fleet optimization
- Driver safety decisions
- Customer compensation
- Warehouse labor planning

---

# Scheduling Workflow

```
Driver reports delay

↓

Identify shipment

↓

Retrieve appointment

↓

Determine latest ETA

↓

Check appointment feasibility

↓

Find feasible slots

↓

Rank options

↓

Show driver

↓

Driver accepts

↓

Book appointment
```

---

# Scheduling Constraints

The scheduler considers

- latest ETA
- warehouse operating hours
- dock compatibility
- unloading duration
- existing appointments
- shipment priority
- driver deadline

The scheduler ignores

- labor planning
- fuel optimization
- route optimization

to keep the assignment focused.

---

# Appointment States

```
OPEN

↓

HELD

↓

CONFIRMED
```

A shown appointment is **not** reserved.

A reserved appointment is **not** confirmed.

Only explicit driver confirmation creates a confirmed appointment.

---

# Repository Structure

```
setuhaul-exception-agent/

│

├── README.md

├── requirements.txt

├── pyproject.toml

├── .env.example

│

├── data/

│ ├── schema.sql

│ ├── seed.sql

│ └── database.db

│

├── app/

│ ├── scheduler.py

│ ├── repository.py

│ ├── database.py

│ ├── models.py

│ ├── services/

│ ├── graph/

│ ├── prompts/

│ └── tools/

│

├── tests/

│

├── docs/

│

└── locustfile.py
```

---

# Development Roadmap

## Phase 1

Database

Deterministic Scheduler

Unit Tests

---

## Phase 2

LangGraph

Conversation Memory

Clarification Logic

---

## Phase 3

AgentCore Deployment

CloudWatch

LangSmith

OpenTelemetry

---

## Phase 4

Locust Load Testing

Concurrent Requests

Slot Hold Logic

---

# Scheduling Algorithm

The scheduler follows these steps.

## Step 1

Load shipment

## Step 2

Find latest ETA

## Step 3

Retrieve current appointment

## Step 4

Check whether current appointment is still feasible

## Step 5

Retrieve compatible docks

## Step 6

Find available appointment slots

## Step 7

Filter infeasible slots

## Step 8

Rank feasible slots

## Step 9

Return top recommendations

The scheduler never updates the database until explicit driver confirmation.

---

# Concurrency

The scheduling engine is designed to prevent

- duplicate bookings
- stale availability
- conflicting reservations

Future versions will implement temporary slot holds using transactional database operations.

---

# Example Conversation

Driver

```
Tyre damaged near Neemrana.
Around 90 minutes late.
Can I get something after 7 PM?
```

System

```
I found your active shipment SHP1006.

Your current appointment is no longer feasible.

Here are the next available slots

1. 7:30 PM

2. 8:00 PM

3. 8:30 PM

Would you like to request one?
```

Driver

```
Book the second one.
```

System

```
Checking availability...

The 8:00 PM slot has been confirmed.
```

---

# Technology Stack

| Layer | Technology |
|----------|----------------|
| Language | Python |
| Database | SQLite |
| Validation | Pydantic |
| Testing | Pytest |
| Conversation | LangGraph |
| LLM | LangChain |
| Deployment | AWS Bedrock AgentCore |
| Observability | LangSmith |
| Metrics | CloudWatch |
| Load Testing | Locust |

---

# Local Setup

Clone

```bash
git clone <repo-url>
```

Create virtual environment

```bash
python -m venv .venv
```

Activate

```bash
source .venv/bin/activate
```

Install

```bash
pip install -r requirements.txt
```

Initialize database

```bash
python app/database.py
```

Run tests

```bash
pytest
```

---

# Team Workflow

Every contributor should work on an independent feature branch.

```
main

├── feature/scheduler

├── feature/database

├── feature/langgraph

└── feature/testing
```

No direct commits should be made to `main`.

---

# Contributors

- Disha Chaudary
- Kunal Chabbra
- Adarsh Gaur
- Gajanan

---

# Future Enhancements

- Multi-facility scheduling
- Redis-backed session memory
- Real-time ETA updates
- Constraint optimization using OR-Tools
- Multi-agent coordination
- Facility-level scheduling engine
- Customer notifications
- Analytics dashboard

---

# License

This repository is created as part of the FDE classroom project and is intended for educational purposes.