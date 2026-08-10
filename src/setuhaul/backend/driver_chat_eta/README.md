# Driver Chat & ETA (backend)

Python/FastAPI backend for the driver-facing portal: exception reporting
chat, dock slot negotiation, and gate/yard/dock check-ins. Implements the
exception -> dock slot -> unload flow, backed by Supabase using the
caller's own JWT (`setuhaul.infrastructure.supabase_client`), the same
pattern as `backend/tms` and `backend/checkin_portal`.

- `models.py` -- Pydantic request/response models, matching the real
  Supabase schema in `supabase/migrations/`.
- `auth.py` -- `DriverPrincipal` dependency: any driver who has verified
  their email with Supabase Auth is a valid caller (unlike
  `infrastructure.auth`, this does not require a `tms_role` claim).
- `repository.py` -- caller-scoped Supabase queries.
- `service.py` -- the deterministic business logic (exception intake,
  feasibility check, slot hold/confirm, check-ins, escalation). No LLM
  call is made here; `src/setuhaul/orchestration` is not implemented yet
  in this codebase, so parsing is a small auditable regex/rule layer.
- `api.py` -- FastAPI router, mounted at `/api/v1/driver-chat-eta/*` by
  `src/setuhaul/main.py`.

The frontend that calls these routes lives in `frontend/` (see
`frontend/src/components/drivers/` and `frontend/src/services/driverChatApi.ts`),
not in this folder -- this folder is backend-only.

## Run locally

```
pip install -e .
uvicorn setuhaul.main:app --reload --port 8000
```

## Chatbot: Gemini tool-calling agent

`POST /driver-chat-eta/chat` is handled by `llm/agent.py` when `GOOGLE_API_KEY`
is set in `.env`, falling back to a small deterministic regex parser
(`service._handle_chat_message_regex`) when it isn't -- so the endpoint
never hard-fails just because the LLM isn't configured yet.

- `llm/schemas.py` -- Pydantic input schema per tool call (these become the
  JSON schema Gemini sees; field descriptions are written as instructions
  to the model).
- `llm/tools.py` -- the six tools, each a thin wrapper around an existing
  `DriverChatService` method: `report_delay_or_eta_change` (wraps
  `service.report_exception`), `list_feasible_dock_slots` (wraps
  `service.get_current_feasible_slots`), `hold_dock_slot`
  (`service.hold_slot`), `confirm_dock_slot` (`service.confirm_slot`),
  `update_arrival_checkin` (`service.update_checkin`), `escalate_to_human`
  (`service.escalate`). The LLM never talks to Supabase directly and can't
  do anything a button in the UI couldn't already do -- it calls the exact
  same RLS-scoped, caller-authenticated service methods either way.
- `llm/prompts.py` -- builds the system prompt fresh every turn from a live
  snapshot (shipment/exception/slot state), so the model is grounded in the
  current database rather than stale memory.
- `llm/session_store.py` -- Redis-backed hot working memory (the tool-call
  scratchpad) for the current thread, keyed `chat:setuhaul:{driver_id}:{thread_id}`
  with a 30-minute TTL. If `REDIS_URL` isn't set, or Redis is unreachable,
  this degrades gracefully: `agent.py` reconstructs a coarser memory (just
  the driver/agent text turns, no tool-call detail) from the permanent
  `chat_messages` table instead. Nothing is ever lost -- Redis is a cache,
  not the source of truth; every turn's driver message and final agent
  reply are written to `chat_messages` in Supabase regardless of whether
  Redis is configured.
- `llm/agent.py` -- the tool-calling loop itself (`run_chat_turn`): binds
  the tools, sends the system prompt + conversation history to
  `ChatGoogleGenerativeAI`, and keeps executing tool calls and feeding
  their JSON results back to the model (up to 5 rounds) until it replies
  with plain text instead of another tool call.

Env vars (see `.env`): `GOOGLE_API_KEY` (required to enable the LLM path),
`DRIVER_CHAT_LLM_MODEL` (defaults to `gemini-2.5-flash`), `REDIS_URL`
(optional).

If a driver has no active shipment assigned yet, `agent.py` skips the LLM
call entirely (every tool requires a shipment, so there's nothing useful
for it to do) and returns a canned "no load assigned yet" reply without
opening a chat thread. If the LLM stack raises anything unexpected at
runtime (network error, bad response, missing package), `service.py`
catches it and falls back to the regex parser rather than returning a 500
to the driver.

## RLS on `drivers`

`supabase/migrations/20260808095820_tms_authorization.sql` originally
restricted `drivers` to callers whose JWT has `app_metadata.tms_role =
ADMIN_1` (or `AGENT_READER` for reads), which blocked a self-registering
driver from inserting their own row. `20260810120000_drivers_open_rls.sql`
drops those policies and disables RLS on `public.drivers` entirely so any
authenticated user can insert/select/update rows in that table. **This
migration is not applied automatically** -- run it against your project
(`supabase db push` or paste it into the SQL editor) before self-service
signup will work.

Trade-off to be aware of: with RLS off, any authenticated user (not just
the row's own owner) can read or write any row in `drivers`, including
other drivers' phone numbers and licence numbers. That's an accepted
simplification for now, not a hardened multi-tenant setup. If that becomes
a problem later, replace the disable with `auth.uid()::text = driver_id`
-scoped select/insert/update policies instead of leaving RLS off.

Other tables this backend touches (`vehicles`, `shipments`, `eta_updates`,
`appointment_slots`, `slot_holds`, `appointments`, `facility_checkins`,
`driver_exceptions`, `chat_threads`, `chat_messages`, `carriers`) still
have whatever RLS `supabase/migrations/` currently defines for them (mostly
none defined yet, so default-deny under RLS-off tables or open under
no-RLS tables -- check each table's current policies in Supabase before
relying on them). No service-role key is used anywhere in this backend --
every query runs as the caller, by design.
