# Four-person team workflow

## Suggested ownership

Ownership is for coordination, not silos. Every change still goes through pull requests.

1. **Scheduling engine** — feasibility filters, ranking, priority policy.
2. **Data and transactions** — SQL queries, slot holds, confirmation and concurrency.
3. **Conversation workflow** — thread/session resolution and later LangGraph integration.
4. **Testing and observability** — pytest scenarios, Locust, LangSmith and CloudWatch later.

## Shared identifiers

- `driver_id`: authenticated user.
- `session_id`: one app/browser/runtime connection.
- `exception_id`: the operational delay case.
- `thread_id`: conversation attached to that exception.

Use one durable thread per exception. A returning driver may have a new session while continuing the same thread.

## Initial issue split

- Issue 1: verify seeded scenarios and data queries.
- Issue 2: complete feasibility and ranking tests.
- Issue 3: design transactional slot hold and confirmation.
- Issue 4: document conversation state and thread-resolution rules.

Avoid four people editing the same module simultaneously. Agree interfaces first, then work in separate branches.
