# SetuHaul Load-Test Quick Start

The local Locust harness covers public API reads, authenticated Driver/TMS/WMS
traffic, dedicated LT-only Check-in lifecycles, and read-only AgentCore
conversations. See the complete [Harness & Observability Guide](../docs/HARNESS.md)
for architecture, verified metrics, telemetry, troubleshooting, and safety.

## Install

```sh
cd /path/to/setuhaul-logistics-ai
source .venv/bin/activate
pip install -e ".[dev]"
```

## Start FastAPI

```sh
set -a
source .env
set +a
export OTEL_ENABLED=true
PYTHONPATH=src uvicorn setuhaul.main:app --host 127.0.0.1 --port 8000
```

## Configure authentication

`TEST_ACCESS_TOKEN` is supported as a manual fallback. Otherwise the harness
logs into Supabase once per selected role and refreshes the in-memory session:

```sh
export SUPABASE_URL=https://your-project.supabase.co
export SUPABASE_PUBLISHABLE_KEY=replace-locally
export LOAD_TEST_EMAIL=test-user@example.com
export LOAD_TEST_PASSWORD=replace-locally
```

Optional role-specific pairs use `LOAD_TEST_DRIVER_*`, `LOAD_TEST_TMS_*`,
`LOAD_TEST_WMS_*`, and `LOAD_TEST_CHECKIN_*`. Keep every value local; tokens and
credentials must never be committed or printed.

## Open the Locust UI

```sh
locust -f load_tests/locustfile.py --class-picker
```

Open `http://localhost:8089`, choose one class, set users and spawn rate, and
start. Use 1 user for sanity, 5 for small concurrency, and 10 for a local
baseline. The UI exposes:

- `ReadOnlyUser`
- `DriverUser`
- `TmsUser`
- `WmsUser`
- `CheckinUser`
- `AgentCoreDriverUser`

Selected-class preflight validates configuration once before users spawn.

## AgentCore

Start the existing local runtime in another terminal:

```sh
set -a
source .env
set +a
export PYTHONPATH=src
agentcore dev --port 8090 --logs
```

Then choose only `AgentCoreDriverUser` in Locust. It targets
`http://localhost:8090/invocations`, gives each virtual user a unique session,
and sends a read-only status question followed by a same-session follow-up.

## Check-in safety

Check-in is mutation-capable and remains fail-closed:

```sh
export LOAD_TEST_ALLOW_MUTATIONS=true
export CHECKIN_MANIFEST=load_tests/data/checkin-smoke.json
export CHECKIN_STAGGER_SECONDS=0.05
```

Only dedicated shipment IDs beginning with `LOAD_TEST_SHIPMENT_PREFIX`
(default `LT-`) are accepted. Use API-prepared manifests from
`load_tests/seed_checkin.py`; never use normal application records.

## Save results

```sh
locust -f load_tests/locustfile.py --headless -u 10 -r 2 -t 60s ReadOnlyUser \
  --csv=load_tests/results/local-api-10 \
  --html=load_tests/results/local-api-10.html
```

Store local results under `load_tests/results/` and compare identical workload,
user, spawn-rate, and duration settings.
