# Deployment: caching layer (local dev, AWS ElastiCache production, Supabase webhook)

This repo has no existing deployment pipeline (confirmed by audit: no Dockerfile, no CI/CD deploy step, no VPC, no AWS SDK dependency anywhere) and has never been deployed to production. This document describes how the caching layer (`src/setuhaul/infrastructure/cache.py` + `redis_client.py`) is meant to run locally today, and how to point it at AWS ElastiCache **when** this app is actually deployed to AWS — it does not assume that deployment exists yet.

## Local development

No AWS access required. Two options, both fully supported by the same `cache.py`/`redis_client.py` code with zero branching:

1. **Today's default** — `.env`'s `REDIS_URL` pointing at the team's shared Redis Cloud instance. Nothing to change.
2. **Local container** — `docker-compose up -d valkey` (see `docker-compose.yml`, root of the repo), then set `REDIS_URL=redis://localhost:6379/0`.

Either way, if Redis/Valkey is unreachable, the app runs correctly anyway — every cache read falls open to Supabase (see "Failure behavior" below). Caching is a performance optimization, never a hard dependency for local dev.

## Production: AWS ElastiCache Serverless for Valkey

Framed as "when this backend is deployed to AWS" — that deployment does not exist in this repo yet (no VPC, no compute, no IaC). What follows is the target design so pointing at a real ElastiCache endpoint later is a configuration change, not a code change.

**Why Serverless Valkey**: every Redis command this app issues (`GET`, `SET` with `EX`/`NX`, `DEL`, `SCAN`, `EVAL` for the two stampede-lock Lua scripts) is standard and fully supported by Valkey (a Redis 7.2 fork) — nothing Redis-proprietary is used anywhere in `cache.py` or `session_store.py`. This app's cache usage is bursty and short-TTL (8–300 seconds), not sustained high-throughput, which fits Serverless's per-request/storage billing and zero capacity planning far better than a fixed node-based cluster.

**Cluster mode**: ElastiCache Serverless's single configuration endpoint speaks the Redis Cluster protocol (`MOVED`/`ASK` redirects, hash-slot sharding). Set `CACHE_CLUSTER_MODE=true` so `redis_client.get_client()` builds a `redis.cluster.RedisCluster` instead of a plain `redis.Redis` — see that module's docstring for the full reasoning. `cache.py`'s keys are hash-tagged (`cache:setuhaul:{shipment:SHP1}`) so a key and its own stampede-lock companion always land on the same cluster slot, and every multi-key delete is grouped by slot first (`redis_client._key_slot`) — see `cache.py`'s module docstring for why.

### Network / security

- ElastiCache Serverless in **private subnets only** — no public endpoint.
- A dedicated security group allowing inbound `6379` **only from the backend compute's own security group** (SG-to-SG rule, never `0.0.0.0/0`).
- **TLS in transit** is Serverless's default and cannot be disabled — set `CACHE_TLS=true` (the default).
- Encryption at rest is default-on for Serverless.

### Authentication — RBAC/password now, IAM explicitly deferred

Production auth is **RBAC username + password/auth-token** (`CACHE_USERNAME` + `CACHE_AUTH_TOKEN`, the latter sourced from AWS Secrets Manager at deploy time, injected as an env var — never committed). This works identically against today's Redis Cloud and a future ElastiCache endpoint, no code branching needed.

**IAM authentication is explicitly deferred, not implemented.** A real ElastiCache IAM auth integration needs a renewable credential provider — a `redis-py` `CredentialProvider` that generates a short-lived (~15 min) SigV4-signed token via `boto3` and re-signs before each new pooled connection — which requires adding `boto3` as a dependency (not present anywhere in this repo today) and cannot be verified without a real IAM role in a real AWS account. Given no AWS deployment exists yet, `CACHE_AUTH_TOKEN` is a plain RBAC password, not an IAM credential — do not conflate the two. IAM auth is a well-defined future enhancement, not attempted here.

### Environment variables

| Variable | Purpose | Default |
|---|---|---|
| `REDIS_URL` | Fallback connection string (used when `CACHE_HOST` is unset) — today's Redis Cloud value | none |
| `CACHE_HOST` | ElastiCache Serverless configuration endpoint hostname | none (falls back to `REDIS_URL`) |
| `CACHE_PORT` | Port | `6379` |
| `CACHE_TLS` | Enable TLS | `true` |
| `CACHE_USERNAME` | RBAC username | none |
| `CACHE_AUTH_TOKEN` | RBAC password (from Secrets Manager at deploy time) | none |
| `CACHE_CLUSTER_MODE` | Use `RedisCluster` instead of standalone `Redis` | `false` |
| `WEBHOOK_SECRET` | Shared secret for `POST /api/v1/webhooks/supabase` | none (endpoint rejects everything until set) |

### Health checks and failure behavior

`GET /health` never depends on cache/Redis reachability — by design, a dead cache is not an application outage. Every cache-touching function in `cache.py`/`redis_client.py` fails open: a cache miss/error falls through to a live Supabase read, and the request succeeds, just slightly slower. `redis_client.get_client()`'s failure cooldown (5s) bounds how often a dead endpoint is re-probed — see that module's docstring for the exact latency bound ("one connection-attempt cost per cooldown window per process," not per Redis operation).

### Observability

Structured JSON logs (`infrastructure/logging.py`'s existing `JsonFormatter`, already CloudWatch-friendly) at DEBUG for routine events (`cache_hit`, `cache_miss`, `lock_acquired`) and INFO/WARNING/ERROR for anything worth noticing (`lock_contention`, `lock_wait_timeout`, `cache_connection_error`, authentication failures, unexpected/programming errors, webhook invalidation failures). Never logs credentials or cached payload contents — only key names, table names, exception types, and timing.

### Key-format migration note

Deploying `cache.py`'s hash-tagged key format makes every pre-existing (non-hash-tagged) cache entry permanently unreachable by the new code. **This is safe and requires no action**: old entries are simply never looked up again and expire on their own TTL (at most a few minutes), causing a handful of ordinary extra cache misses right after deploy — never incorrect data. **Do not flush Redis/ElastiCache** to "clean this up" — that trades a gradual, harmless trickle of misses for a synchronized mass-miss spike at the exact moment of deploy. This applies **only** to `cache.py`'s own `cache:setuhaul:...` keys; `driver_chat_eta.llm.session_store`'s `chat:setuhaul:{driver_id}:{thread_id}` keys are a separate, unaffected scheme (untouched by this migration).

## Supabase webhook reachability

`POST /api/v1/webhooks/supabase` receives Supabase Database Webhook calls for writes that bypass this backend entirely (dashboard edits, external scripts). **Supabase's hosted Postgres must be able to reach the deployed backend over HTTPS at a real, public URL** — a plain `localhost` dev server is not reachable from Supabase's infrastructure, so this webhook is inert in local development by design (see `supabase/migrations/20260814170000_cache_invalidation_webhook.sql`'s own comments). TTL remains the fallback safety net for any environment where the webhook can't fire.

To activate against a real deployment, set on the Supabase project (not committed to this repo):

```sql
alter database postgres set app.settings.cache_invalidation_url =
  'https://<your-deployed-backend-origin>/api/v1/webhooks/supabase';
alter database postgres set app.settings.cache_invalidation_secret =
  '<same value as the backend's WEBHOOK_SECRET env var>';
```
