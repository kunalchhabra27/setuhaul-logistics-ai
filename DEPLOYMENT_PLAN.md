# SetuHaul Deployment Plan — AWS AgentCore (driver chatbot) + Vercel (everything else)

Based on your answers: the AgentCore-hosted chatbot talks to Supabase **directly** (fastest path, no Vercel round-trip), it keeps using **Gemini** as the model, and this document is a **guide only** — no repo files have been changed yet.

---

## 1. Architecture (confirmed)

```
Driver's browser
      │
      ▼
Vercel — React/Vite frontend (static)
      │  same-origin API calls
      ▼
Vercel — FastAPI backend (single Python serverless function: TMS, WMS/dock_scheduler,
         Check-in, driver_chat_eta REST endpoints + regex fallback chat parser)
      │
      │  driver's Supabase JWT, forwarded as-is
      │
      ├──────────────► Supabase (Postgres + RLS + Auth) ◄────────────┐
      │                        ▲                                     │
      │  for FREE-TEXT chat    │ direct Supabase calls,               │
      │  messages only:        │ driver's JWT passed through          │
      ▼                        │ the invocation payload               │
AWS Bedrock AgentCore Runtime ─┘                                     │
   (containerized driver_chat_eta LLM tool-calling loop)             │
      │                                                               │
      ├─► Google Gemini API (unchanged: ChatGoogleGenerativeAI)      │
      ├─► CloudWatch GenAI Observability (via built-in OTel/ADOT)    │
      └─► LangSmith (via LANGCHAIN_TRACING_V2 env vars)              │
                                                                       │
Redis (Upstash, shared) ──── used by both Vercel (facility/dock cache) and AWS (LLM session
                              scratchpad) via the same REDIS_URL ─────┘
```

Key point: the AWS container does **not** need a copy-pasted fork of your booking logic. It's the **same `setuhaul` Python package**, deployed a second time with a different, much smaller entrypoint file that only wires up `driver_chat_eta`'s LLM path. One repo, one source of truth for the actual booking rules — Vercel and AWS just import different top-level files from it.

The only *new* code is:
- A thin AgentCore entrypoint (`agentcore_app/app.py`) — shown in full below.
- A thin proxy inside `service.handle_chat_message` that calls out to AgentCore instead of running Gemini in-process (replaces today's direct `llm_agent.run_chat_turn(...)` call). The regex fallback (`_handle_chat_message_regex`) stays exactly as-is as the safety net if the AWS call fails or times out — same pattern you already have today, just pointed at a different LLM location.

---

## 2. Part 1 — AWS Bedrock AgentCore (driver chatbot)

### 2.1 Prerequisites

- An AWS account, and the AWS CLI configured (`aws configure` or SSO) with an identity that has the permissions in §2.6.
- A region where Bedrock AgentCore is available (e.g. `us-east-1` or `us-west-2` — check the current list in the AWS console, this expands over time).
- **Node.js 20+** — the current `agentcore` CLI (`@aws/agentcore`) is an npm package, not a pip package. (The old `bedrock-agentcore-starter-toolkit` Python CLI, which used `configure`/`launch`, is now legacy/deprecated — if it's still installed alongside the new one, uninstall it: `pip uninstall bedrock-agentcore-starter-toolkit` — both register an `agentcore` command and will conflict.)
- **AWS CDK** — the new CLI deploys via CDK under the hood: `npm install -g aws-cdk`, then `cdk bootstrap` once per account/region.
- Python 3.11+ and `uv` locally (the CLI manages a venv for the generated agent code).
- Docker, only if you use `--build Container` below (needed here, since the agent imports the local `setuhaul` package, not something installable from PyPI).

Install the CLI:
```bash
npm install -g @aws/agentcore
agentcore --version
```

### 2.2 The entrypoint file

**Note on project layout**: unlike the old (deprecated) CLI, the new `agentcore` CLI's `create` command scaffolds its own project directory (`agentcore/` config + `app/<Name>/main.py` + `app/<Name>/pyproject.toml`) — it doesn't accept `--entrypoint <existing file>`. So the flow is: run `agentcore create` (§2.4) first to get that scaffold, then drop the code below into the generated `app/<Name>/main.py` (replacing its starter content) and merge `agentcore_app/requirements.txt` (§2.3) into the generated `pyproject.toml`. The repo's own `agentcore_app/app.py` (already scaffolded, shown below) is the reference copy to reuse — it doesn't need to move, but a duplicate of its logic needs to end up inside the CLI-generated `app/<Name>/main.py`.

`agentcore_app/app.py` (sits alongside `src/`, imports the existing package — nothing under `src/setuhaul` needs to move):

```python
"""AWS Bedrock AgentCore entrypoint for the driver chatbot's LLM path.

Reuses the exact same DriverChatService / DriverChatRepository / LangChain
agent code the Vercel-hosted FastAPI app uses for driver_chat_eta -- this
file is only a different front door onto the same package, not a
reimplementation.
"""
from bedrock_agentcore.runtime import BedrockAgentCoreApp

from setuhaul.backend.driver_chat_eta.auth import DriverPrincipal
from setuhaul.backend.driver_chat_eta.llm import agent as llm_agent
from setuhaul.backend.driver_chat_eta.repository import DriverChatRepository
from setuhaul.backend.driver_chat_eta.service import DriverChatService
from setuhaul.infrastructure.settings import get_settings
from setuhaul.infrastructure.supabase_client import create_caller_client

app = BedrockAgentCoreApp()


@app.entrypoint
def invoke(payload: dict, context=None) -> dict:
    """payload shape: {"driver_jwt": str, "driver_id": str, "driver_email": str, "message": str}"""
    settings = get_settings()
    client = create_caller_client(settings, payload["driver_jwt"])
    service = DriverChatService(DriverChatRepository(client))
    principal = DriverPrincipal(
        user_id=payload["driver_id"],
        email=payload.get("driver_email", ""),
        access_token=payload["driver_jwt"],
    )
    response = llm_agent.run_chat_turn(service, principal, payload["message"])
    return response.model_dump(mode="json")


if __name__ == "__main__":
    app.run()
```

`create_caller_client` is the exact same caller-scoped Supabase client factory the rest of the app already uses (with its `@lru_cache`d connection reuse) — RLS behaves identically to today, and double-booking protection is unchanged because it's the same `dock_scheduler` code underneath.

### 2.3 requirements file for the AWS side

Create `agentcore_app/requirements.txt` — deliberately smaller than the main app's, since this container never runs FastAPI/uvicorn (`BedrockAgentCoreApp` serves its own `/invocations` + `/ping` endpoints on port 8080):

```
bedrock-agentcore
langchain-core
langchain-google-genai
supabase
redis
pydantic
pydantic-settings
python-dotenv
```

(Match exact versions to what's already pinned in your `pyproject.toml` for these packages, so behavior doesn't drift between the two deployments.)

### 2.4 Create, test locally, and deploy

Step 1 — scaffold the project (run this **one level above** the repo, or in a scratch directory; it creates a new `SetuHaulDriverChat/` folder, it does not modify the existing repo):
```bash
agentcore create --name SetuHaulDriverChat --framework LangChain_LangGraph --model-provider Gemini --build Container --memory none
cd SetuHaulDriverChat
```
This generates:
```
SetuHaulDriverChat/
  agentcore/
    agentcore.json        # project + agent config
    aws-targets.json       # AWS account/region targets
    .env.local             # local env vars (gitignored) -- put GOOGLE_API_KEY etc. here for local `agentcore dev`
  app/
    SetuHaulDriverChat/
      main.py              # starter agent code -- REPLACE with agentcore_app/app.py's contents (§2.2)
      pyproject.toml       # deps -- MERGE with agentcore_app/requirements.txt (§2.3)
  README.md
```
`--build Container` matters here: the agent imports the existing `setuhaul` package from this repo, which isn't on PyPI, so the CodeZip build type (the CLI's default) won't work — Container lets you control the Docker build context to include `src/`.

Step 2 — wire in the real agent code: replace the generated `app/SetuHaulDriverChat/main.py` with `agentcore_app/app.py`'s content, and merge `agentcore_app/requirements.txt`'s packages into the generated `pyproject.toml`. You'll also need the Docker build context to reach this repo's `src/` directory — once the CLI generates its Dockerfile (Container build type), check it the same way §2.2 flags for the old flow (either `pip install .` against a copied-in repo root, or `PYTHONPATH`) and adjust if needed.

Step 3 — set local env vars in `agentcore/.env.local` (see §2.5), then test locally:
```bash
agentcore dev
# in a second terminal:
agentcore dev "I am 2 hours late, book me a slot"
```
`agentcore dev` starts a local server on `:8080` mimicking the real Runtime and opens an inspector UI in the browser — this replaces the old `launch --local` + `invoke`.

Step 4 — deploy for real:
```bash
agentcore deploy
```
This packages the agent (container image, since `--build Container`), synthesizes and deploys CDK/CloudFormation resources (IAM role, Runtime, etc.). Use `agentcore deploy --dry-run` first if you want to preview, `-v` for verbose output.

Step 5 — get the Runtime ARN (Vercel needs it, §3.6):
```bash
agentcore status
```

Test the deployed agent directly:
```bash
agentcore invoke "I am 2 hours late, book me a slot"
```

### 2.5 Environment variables / secrets on the runtime

For local `agentcore dev` testing, put these in the generated `agentcore/.env.local` (gitignored). For the deployed Runtime, either add them as plain env vars via the AWS console / `update-agent-runtime` after `agentcore deploy`, or (recommended for `GOOGLE_API_KEY`/`LANGCHAIN_API_KEY`) use `agentcore add credential --name <Name> --type api-key --api-key <value>` before `agentcore deploy`, which stores it in Secrets Manager and wires it in automatically:

**Update (post-tasks #136/#137): the live tool-calling model is no longer Gemini.** The app was
later swapped onto an open-weights model served via Hugging Face Inference Providers --
`llm/agent.py`'s `is_configured()`/`_run_chat_turn` (which this container's entrypoint calls
directly, with no fallback check) gate on `HUGGINGFACEHUB_API_TOKEN`, not `GOOGLE_API_KEY`.
`GOOGLE_API_KEY` is still listed below because `agentcore_app`'s vendored `setuhaul` copy imports
`langchain_google_genai` for the (Vercel-only) voice-transcription path, but it is **not** what
answers a driver's chat message from this runtime -- `HUGGINGFACEHUB_API_TOKEN` is the one that
actually matters here.

| Variable | Value |
|---|---|
| `HUGGINGFACEHUB_API_TOKEN` | **Required** — your Hugging Face Inference Providers token (a "Read" token from huggingface.co/settings/tokens is enough); without this, every chat turn silently falls back to the regex parser instead of running the LLM |
| `DRIVER_CHAT_LLM_MODEL` | `meta-llama/Llama-3.3-70B-Instruct` (or whatever you're pinned to) |
| `DRIVER_CHAT_LLM_PROVIDER` | `auto` (lets HF route to the fastest available provider) |
| `GOOGLE_API_KEY` | Optional here — only needed if you want feature parity with Vercel's voice-transcription path; not used by this container's actual chat replies |
| `DRIVER_CHAT_TRANSCRIPTION_MODEL` | `gemini-2.5-flash` (only relevant if `GOOGLE_API_KEY` is set) |
| `SUPABASE_URL` | Same value as your other environments |
| `SUPABASE_PUBLISHABLE_KEY` | Same value as your other environments |
| `REDIS_URL` | The **same** Upstash `rediss://...` URL used on Vercel (§3.5) — keeps the LLM session scratchpad and facility/dock cache shared across both deployments |
| `LANGCHAIN_TRACING_V2` | `true` |
| `LANGCHAIN_API_KEY` | Your LangSmith API key (§2.7) |
| `LANGCHAIN_PROJECT` | e.g. `setuhaul-driver-chat-prod` |

AgentCore Runtime allows up to 50 env vars, each value up to 2048 characters — plenty of room here. For anything you consider more sensitive than the rest (typically `GOOGLE_API_KEY`), you can instead store it in **AWS Secrets Manager** and fetch it at cold-start inside `agentcore_app/app.py` before `app.run()` — worth doing once you're past initial testing.

### 2.6 IAM permissions checklist

Attach to whatever identity runs the `agentcore` CLI commands (a deploy-time role/user, not the runtime's own execution role). The new CLI deploys via CDK/CloudFormation, so the permission surface is broader than the old CodeBuild-based flow:

- `cloudformation:*` on stacks named for your project (CDK deploy/update/delete)
- `bedrock-agentcore:CreateAgentRuntime`, `UpdateAgentRuntime`, `GetAgentRuntime`, `ListAgentRuntimes`, `InvokeAgentRuntime`, `DeleteAgentRuntime`
- `ecr:CreateRepository`, `GetAuthorizationToken`, `BatchCheckLayerAvailability`, `InitiateLayerUpload`, `UploadLayerPart`, `CompleteLayerUpload`, `PutImage` (Container build type)
- `s3:GetObject`, `PutObject`, `ListBucket` (the CDK staging bucket created by `cdk bootstrap`)
- `iam:PassRole`, `CreateRole`, `PutRolePolicy`, `AttachRolePolicy`, `DeleteRole` (CDK manages the runtime's execution role)
- `logs:CreateLogGroup`, `CreateLogStream`, `PutLogEvents`
- `xray:PutTraceSegments`, `PutTelemetryRecords`
- `cloudwatch:PutMetricData`
- `secretsmanager:GetSecretValue`, `CreateSecret`, `PutSecretValue` (used by `agentcore add credential`, §2.5)

Run `cdk bootstrap` once per account/region before your first `agentcore deploy` — this is the AWS CDK's own one-time setup (creates the staging S3 bucket and a few IAM roles CDK itself needs).

The **runtime's own execution role** (the one AgentCore assumes while your agent is actually running) is created and attached automatically by CDK during `agentcore deploy` — you generally don't need to hand-write this policy unless your org requires custom roles.

### 2.7 OpenTelemetry → CloudWatch ("GenAI Observability")

1. **One-time per account/region**: CloudWatch console → *Application Signals* → *Transaction Search* → **Enable**. Without this, AgentCore's spans/traces won't show up in the dashboard even though they're being emitted.
2. Deploying through `agentcore deploy` (as above) already wires ADOT (AWS Distro for OpenTelemetry) automatically — you get the *Agents View*, *Sessions View*, and *Traces View* out of the box, no extra code. Metrics include tokens per request, per-tool-call latency, session duration, and error/failure counts — exactly what you'll want live during the 100-driver load test. You can also pull traces straight from the CLI: `agentcore logs` (stream/search runtime logs) and `agentcore traces list` / `agentcore traces get` (list/download traces).
3. Optional, for deeper custom spans (e.g. wrapping `_best_priority_swap` or a specific Supabase call with your own span name): add `aws-opentelemetry-distro` to `agentcore_app/requirements.txt` and use `opentelemetry.trace`'s `tracer.start_as_current_span(...)` inside the relevant function. Not required for the basics.
4. View it: **CloudWatch console → GenAI Observability**.

### 2.8 LangSmith (independent of CloudWatch — both can run at once)

LangSmith uses LangChain's own callback system, not OpenTelemetry, so it doesn't conflict with the CloudWatch/ADOT setup above — you get both simultaneously for free once the env vars in §2.5 are set.

1. Sign up at [smith.langchain.com](https://smith.langchain.com), create a project (e.g. `setuhaul-driver-chat-prod`), copy the API key from **Settings → API Keys**.
2. The three env vars from §2.5 (`LANGCHAIN_TRACING_V2`, `LANGCHAIN_API_KEY`, `LANGCHAIN_PROJECT`) are the entire integration — `ChatGoogleGenerativeAI` and the LangChain tool-calling loop auto-trace the moment they're present. No code changes.
3. Every chat turn shows up as a full trace tree in the project: the tool-calling loop, each tool's args/result, token usage, latency per LLM call and per tool. Build a saved view/dashboard in LangSmith filtered by tags — for easier per-driver debugging, consider passing `config={"tags": [f"shipment:{shipment_id}", f"thread:{thread_id}"]}` into the `chain.invoke(...)` calls in `llm/agent.py` so you can filter LangSmith by shipment or thread later (small, optional code change).

---

## 3. Part 2 — Vercel (frontend + rest of the backend)

### 3.1 Project layout

Two Vercel projects from the same GitHub repo is the cleanest split:
- **`setuhaul-frontend`** → root directory `frontend/`, framework preset "Vite". Deploys as a static site.
- **`setuhaul-api`** → root directory `/` (repo root), Python runtime, entrypoint `src/setuhaul/main.py`.

Alternative: one Vercel project with `vercel.json` rewrites routing `/api/*` to the Python function and everything else to the static frontend build — fine too, just more config in one place. Two projects is easier to reason about and gives independent deploy/rollback for frontend vs. backend.

### 3.2 `vercel.json` for the API project

```json
{
  "functions": {
    "src/setuhaul/main.py": {
      "maxDuration": 120
    }
  }
}
```
- `maxDuration` in seconds. Gemini calls in this session took 20–40+ seconds under quota pressure earlier, and the regex fallback adds a bit more — **120s is a safe starting point**, not the Hobby-plan default (10s, which will 504 on any real chat turn). This requires a **Pro or Enterprise** Vercel plan; Hobby caps too low for this workload. Values above 800s are in beta and need extra per-function config if you ever need them.
- Since driver chat now round-trips to AWS AgentCore instead of calling Gemini in-process, budget for that extra network hop too — test actual p95 latency after §4's code change and tune `maxDuration` accordingly.

### 3.3 Getting a plain `requirements.txt`

Vercel's Python builder installs from a `requirements.txt` (or `pyproject.toml`, but the simpler/more reliable path is a plain requirements file) at the function's root — it does **not** run `uv sync`. Generate one from your existing `pyproject.toml`/`uv.lock` before each deploy (or via CI):

```bash
uv export --no-hashes --no-dev -o requirements.txt
```
Commit this file (or regenerate it in a CI step before `vercel deploy`) so Vercel's build step has something to install from.

### 3.4 Environment variables on Vercel

| Variable | Notes |
|---|---|
| `SUPABASE_URL` | same as today |
| `SUPABASE_PUBLISHABLE_KEY` | same as today |
| `REDIS_URL` | Upstash, see §3.5 — shared with the AWS side |
| `AGENTCORE_RUNTIME_ARN` | the ARN from `agentcore status` after `agentcore deploy` (§2.4) — **new** setting, replaces the old `is_configured()` check that used to look at `GOOGLE_API_KEY` (see §4) |
| `AWS_REGION` | region the AgentCore runtime is deployed in |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | a narrowly-scoped IAM user, permission **only** for `bedrock-agentcore:InvokeAgentRuntime` on that one runtime ARN — Vercel functions can't assume an AWS IAM role natively, so static (rotated) keys are the practical option here |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` / `TWILIO_FROM_NUMBER` | unchanged, only if still using SMS notifications |

Note `GOOGLE_API_KEY` no longer needs to be set on Vercel at all once §4's change lands — Gemini is only called from inside the AWS container now.

### 3.5 Redis on Vercel

Vercel has no built-in Redis. Use **Upstash Redis** (available directly from the Vercel Marketplace/Integrations tab, or standalone at upstash.com):
1. Create a database, pick a region close to your Vercel/AWS regions.
2. Copy the `rediss://` connection URL — this is your `REDIS_URL`, used identically on both Vercel and AWS.
3. Your existing `redis_cache.py`/`session_store.py` code works unchanged (`redis.from_url(...)`) — Upstash speaks the standard Redis protocol over TLS, no code changes needed. (Their REST-API client is an optional lower-latency alternative for pure serverless workloads, but not required.)

### 3.6 Frontend → API wiring

If using two separate Vercel projects, set the frontend's `VITE_API_BASE_URL` (or whatever env var your `frontend/src/services/api.ts` reads) to the API project's deployed URL, and confirm CORS is configured on the FastAPI side (`allow_origins` including the frontend's Vercel domain).

---

## 4. Code changes this plan requires (not yet made — this is a guide)

1. **New**: `agentcore_app/app.py` and `agentcore_app/requirements.txt` (§2.2–2.3).
2. **New**: a small AWS client wrapper, e.g. `src/setuhaul/infrastructure/agentcore_client.py`, using `boto3.client("bedrock-agentcore")`'s `invoke_agent_runtime(...)` call — takes the driver's JWT/id/email/message, returns the parsed JSON response.
3. **Modify**: `driver_chat_eta/service.py`'s `handle_chat_message` — replace the `llm_agent.is_configured()` / `llm_agent.run_chat_turn(...)` branch with a call to the new AgentCore client, gated on whether `settings.agentcore_runtime_arn` is set. Keep the existing `except DriverChatError: raise` / `except Exception: fall back to regex` structure exactly as-is — it already does the right thing, it just needs a different thing to try first.
4. **Modify**: `infrastructure/settings.py` — add `agentcore_runtime_arn: str | None`, `aws_region: str | None`, `langchain_tracing_v2`, `langchain_api_key`, `langchain_project` fields (the LangChain ones are only read inside `agentcore_app/app.py` via plain `os.environ`, so you may not need them in the shared `Settings` model at all — your call).
5. **New**: `vercel.json`, generated `requirements.txt` (§3.2–3.3).

Given you asked for the guide only, I haven't touched any of these files yet — say the word if you want me to scaffold them next.

---

## 5. Every key/secret you'll need to obtain yourself

| Key | Where to get it | Used by |
|---|---|---|
| AWS account + IAM credentials for deploy | AWS Console → IAM (or your org's AWS access process) | Running `agentcore` CLI commands locally |
| Scoped IAM access key for Vercel → AgentCore calls | Create a dedicated IAM user, attach an inline policy for `bedrock-agentcore:InvokeAgentRuntime` on your one runtime ARN, generate an access key | Vercel API project |
| Gemini API key (**paid tier**) | [Google AI Studio](https://aistudio.google.com/) → API keys, upgrade billing so you're not on the 20-req/day free tier we hit earlier this session | AWS AgentCore container |
| LangSmith API key | [smith.langchain.com](https://smith.langchain.com) → Settings → API Keys | AWS AgentCore container |
| Upstash Redis URL | [upstash.com](https://upstash.com) (or Vercel Marketplace) → create database → copy `rediss://` URL | Both Vercel and AWS |
| Supabase URL + publishable key | Already have these (Supabase project settings) — unchanged | Both Vercel and AWS |
| Twilio credentials | Already have these if SMS is in use — unchanged | Vercel only |
| Vercel account, upgraded to Pro (for `maxDuration` beyond 10s) | [vercel.com](https://vercel.com) | Both Vercel projects |

I'm not able to enter any of these into the app for you — they're all things you'll create/copy yourself in each provider's dashboard, then paste into AgentCore's env var config and Vercel's project settings.

---

## 6. Suggested rollout order

1. Get a paid Gemini key first — the free-tier quota exhaustion is what caused most of tonight's flakiness and will do the same in AWS if you don't fix it before deploying.
2. Stand up AgentCore in isolation (§2), test with `agentcore invoke` directly — confirm booking/rebooking works correctly from a raw payload before wiring anything else to it.
3. Enable CloudWatch Transaction Search + confirm traces show up in GenAI Observability (§2.7), then add LangSmith env vars (§2.8) and confirm traces show up there too.
4. Make the four code changes in §4 on a branch, test locally against the deployed AgentCore runtime (your local FastAPI dev server calling out to AWS).
5. Deploy the API project to Vercel first (§3), confirm `/api/v1/driver-chat-eta/chat` round-trips through AgentCore correctly in a Vercel preview deployment.
6. Deploy the frontend project, point it at the API project's URL, smoke-test all four portals end to end.
7. Only then run the 100-concurrent-driver load test — watch CloudWatch GenAI Observability and LangSmith side by side during the run.

---

## Sources

- [Amazon Bedrock AgentCore Runtime instances GA announcement](https://aws.amazon.com/about-aws/whats-new/2026/08/aws-bedrock-agentcore-runtime-instances-generally-available/)
- [Amazon Bedrock AgentCore — official product page](https://aws.amazon.com/bedrock/agentcore/)
- [Get started with the AgentCore CLI](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-get-started-cli.html) — the current CLI (`@aws/agentcore`, `create`/`dev`/`deploy`/`invoke`); this is what's actually installed
- [aws/agentcore-cli GitHub repo + full command reference](https://github.com/aws/agentcore-cli) (see `docs/commands.md` there for exhaustive flags)
- ~~bedrock-agentcore-starter-toolkit (`configure`/`launch`)~~ — legacy, deprecated in favor of the above; kept here only for context on why some older tutorials look different
- [IAM Permissions for AgentCore Runtime](https://docs.aws.amazon.com/it_it/bedrock-agentcore/latest/devguide/runtime-permissions.html)
- [Use any agent framework with AgentCore Runtime](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/using-any-agent-framework.html)
- [Add observability to your Amazon Bedrock AgentCore resources](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-configure.html)
- [Getting started — Amazon CloudWatch AgentCore Observability](https://docs.aws.amazon.com/en_en/AmazonCloudWatch/latest/monitoring/AgentCore-GettingStarted.html)
- [Referencing your own AWS Secrets Manager secrets in Bedrock AgentCore Identity](https://aws.amazon.com/blogs/machine-learning/reference-your-own-aws-secrets-manager-secrets-in-amazon-bedrock-agentcore-identity/)
- [Vercel Functions — Configuring Maximum Duration](https://vercel.com/docs/functions/configuring-functions/duration)
- [Vercel — Deploy a FastAPI app](https://vercel.com/docs/frameworks/backend/fastapi)
- [Vercel Functions — Limitations](https://vercel.com/docs/functions/limitations)
