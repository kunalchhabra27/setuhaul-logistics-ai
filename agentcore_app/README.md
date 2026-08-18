# agentcore_app — AWS Bedrock AgentCore entrypoint

This directory holds the **second front door** onto the same `setuhaul` package
the rest of the app already uses: `app.py` wires up only `driver_chat_eta`'s
LLM tool-calling path (via `bedrock_agentcore.runtime.BedrockAgentCoreApp`)
instead of the full FastAPI app in `src/setuhaul/main.py`. Nothing here
reimplements booking/ETA/dock logic — it imports the exact same
`DriverChatService`/`DriverChatRepository`/`driver_chat_eta.llm.agent` code
used by the Vercel-hosted API.

See `../DEPLOYMENT_PLAN.md` for the full architecture and rollout plan.

## Status: done — `SetuHaulDriverChat/` at the repo root is the real project

`agentcore create --name SetuHaulDriverChat --framework LangChain_LangGraph
--model-provider Gemini --build Container --memory none` has been run (from
the repo root). This directory (`agentcore_app/`) was the reference
implementation; its logic now also lives at
`SetuHaulDriverChat/app/SetuHaulDriverChat/main.py`, already wired up —
nothing further to copy.

What was done, since it's not obvious from the CLI alone:
- The CLI's `agentcore.json` sets `"codeLocation": "app/SetuHaulDriverChat/"`
  — that's the **entire Docker build context** for a Container build. It
  does not see the rest of the repo, so `setuhaul` (the shared package) had
  to be **vendored** (physically copied) into
  `SetuHaulDriverChat/app/SetuHaulDriverChat/setuhaul/` rather than imported
  via `PYTHONPATH` pointing outside the build context. `main.py` there
  imports it as a plain top-level package (`from setuhaul.backend... import
  ...`) — no path tricks needed since it sits right next to `main.py`.
- `pyproject.toml` in that same directory has `supabase`, `redis`,
  `pydantic`, `pydantic-settings`, `python-dotenv` added (pinned to match
  the repo root's versions) and the unused generated `langgraph`/`mcp`/
  `langchain-mcp-adapters` deps removed (the real agent doesn't use
  LangGraph's `create_react_agent` or MCP tools).
- `agentcore/.env.local` there has a filled-in template for local
  `agentcore dev` testing — GOOGLE_API_KEY etc. still need to be pasted in.

### Keeping the vendored copy in sync

**Whenever `src/setuhaul` changes, re-run this before `agentcore dev` /
`agentcore deploy`** (from the repo root):
```bash
rm -rf SetuHaulDriverChat/app/SetuHaulDriverChat/setuhaul
cp -r src/setuhaul SetuHaulDriverChat/app/SetuHaulDriverChat/setuhaul
```
This is manual on purpose (no build-time symlink/copy step exists yet) —
worth automating into a pre-deploy script once this is deploying regularly.

### Regenerating the lockfile after the dependency change

The Dockerfile runs `uv sync --frozen`, which requires `uv.lock` to already
match `pyproject.toml`. Since deps were edited above, run once from
`SetuHaulDriverChat/app/SetuHaulDriverChat/`:
```bash
uv lock
```
before the first `agentcore dev` or `agentcore deploy`.

## Local test loop (once copied into the CLI-generated project)

```bash
cd SetuHaulDriverChat
# put GOOGLE_API_KEY, SUPABASE_URL, etc. in agentcore/.env.local (see below)

agentcore dev
# in a second terminal:
agentcore dev "I am 2 hours late, book me a slot"
```

`agentcore dev` starts a local server on `:8080` (mimicking the real
Runtime) and opens an inspector UI in the browser; the second command
sends a prompt to it. Use `--stream` to see the response stream in real
time, `--logs` to tail server logs non-interactively.

Get a real driver access token quickly from the browser: log into the
Drivers portal, open devtools console, and read it out of
`localStorage["setuhaul.supabase.session.drivers"]` (`.access_token`), or
add a temporary print statement to `DriverAuthModal`/the Supabase client
during local testing. (The actual payload this entrypoint expects is JSON
with `driver_jwt`/`driver_id`/`driver_email`/`message` — see `app.py`.)

Once local testing looks right (a sensible `ChatResponse`-shaped JSON, not
an import error or a Supabase 401), deploy for real:

```bash
agentcore deploy
agentcore status   # prints the Runtime ARN
```

Save the printed **Runtime ARN** — the Vercel side needs it as
`AGENTCORE_RUNTIME_ARN` (see `../src/setuhaul/infrastructure/agentcore_client.py`
and `DEPLOYMENT_PLAN.md` §3.4).

## Environment variables to set on the runtime

For local `agentcore dev`, put these in the generated `agentcore/.env.local`.
For the deployed Runtime, set them via the AWS console after `agentcore
deploy`, or (recommended for the secret-ish ones) `agentcore add
credential --name <Name> --type api-key --api-key <value>` before
deploying. Full explanation of each in `../DEPLOYMENT_PLAN.md` §2.5.

**The live model is HF-hosted, not Gemini** (tasks #136/#137 swapped it) — `HUGGINGFACEHUB_API_TOKEN`
is the one that actually makes chat replies work from this runtime; `GOOGLE_API_KEY` here is unused
by the actual chat path (only the vendored copy's unused voice-transcription import needs it to
resolve).

| Variable | Purpose |
|---|---|
| `HUGGINGFACEHUB_API_TOKEN` | **Required** — without it, every invocation ImportErrors on `langchain_huggingface`, gets swallowed by `app.py`'s broad except, and silently falls back to the regex parser |
| `DRIVER_CHAT_LLM_MODEL` | e.g. `meta-llama/Llama-3.3-70B-Instruct` |
| `DRIVER_CHAT_LLM_PROVIDER` | `auto` |
| `GOOGLE_API_KEY` | Optional, unused by this container's actual chat replies |
| `SUPABASE_URL` | same value as everywhere else |
| `SUPABASE_PUBLISHABLE_KEY` | same value as everywhere else |
| `REDIS_URL` | same Upstash URL used on Vercel — shares the session-scratchpad/facility cache |
| `LANGCHAIN_TRACING_V2` | `true` |
| `LANGCHAIN_API_KEY` | from smith.langchain.com |
| `LANGCHAIN_PROJECT` | e.g. `setuhaul-driver-chat-prod` |
