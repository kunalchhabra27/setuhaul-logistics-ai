# SetuHaul Load-Test Harness

This directory is intentionally outside application code. It runs the same API
workloads before and after Redis integration without changing business logic.

Install the optional harness dependencies once from the repository root:

```sh
pip install -e ".[dev]"
```

## Safety

- Set `LOCUST_HOST` and `TEST_ACCESS_TOKEN`; no credential is stored here.
- Read-only traffic runs by default.
- Set `LOAD_TEST_ALLOW_MUTATIONS=true` only against a controlled environment.
- Every mutation requires a shipment ID starting with `LOAD_TEST_SHIPMENT_PREFIX`
  (default `LT-`). Never point mutation scenarios at production data.
- Check-in completion requires a confirmed WMS appointment for each prepared
  shipment, exactly as the live API does.

## Prepare the 100-shipment Check-in run

Create 100 dedicated `LT-...` shipments at one test facility and give each a
confirmed appointment. Export them as a comma-separated value:

```sh
export LOCUST_HOST=http://localhost:8000
export TEST_ACCESS_TOKEN=replace-with-a-test-user-token
export LOAD_TEST_ALLOW_MUTATIONS=true
export CHECKIN_TEST_FACILITY_ID=FAC-TEST
export CHECKIN_SHIPMENT_IDS="LT-001,LT-002,...,LT-100"
```

Run the UI and start 100 users with a fast ramp:

```sh
locust -f load_tests/locustfile.py --host="$LOCUST_HOST"
```

Choose only `CheckinUser`, set users to `100`, and use a non-zero spawn rate.
Each virtual user takes one shipment through gate, queue, dock, and completion,
with staggered waits. Do not reuse those completed test shipments for another
run; reseed them first.

For headless results that can be compared before/after Redis:

```sh
locust -f load_tests/locustfile.py --host="$LOCUST_HOST" \
  --headless -u 100 -r 10 -t 2m \
  --csv=artifacts/locust-before-redis --html=artifacts/locust-before-redis.html
```

Repeat with the same users, ramp, duration, prepared data, and application
configuration after Redis. Compare Locust request count, RPS, failures, median,
p95, p99, and maximum latency from its CSV/HTML report.

## Other scenarios

- `DriverUser`: snapshot, current profile, and existing slot-options chat flow.
- `TmsUser`: lists/reads shipments and assignment data. Optional creation reads
  `TMS_CREATE_SHIPMENT_JSON`; optional assignment uses `TMS_TEST_SHIPMENT_ID`
  and `TMS_ASSIGN_DRIVER_ID`. Both remain disabled until mutation opt-in.
- `WmsUser`: dock board and deterministic suggestions. Optional hold/confirm
  requires `WMS_TEST_SHIPMENT_ID` and `WMS_TEST_SLOT_ID` with mutation opt-in.
  Optional slot-change requests additionally require `WMS_CHANGE_SLOT_ID`.

## OTel, LangSmith, and CloudWatch

```sh
export OTEL_ENABLED=true
export OTEL_SERVICE_NAME=setuhaul-backend
# Point this at an ADOT / CloudWatch Agent OTLP HTTP receiver when deployed.
export OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318

export LANGSMITH_TRACING=true
export LANGSMITH_API_KEY=replace-with-langsmith-key
export LANGSMITH_PROJECT=setuhaul-harness
```

All values are optional. Missing packages, LangSmith credentials, OTLP
connectivity, or a CloudWatch Agent only emit warnings; normal API behavior
continues. LangSmith context is deliberately limited to Driver Chat's existing
LangChain agent and captures its LLM/tool/final-response trace without recording
full driver messages in custom telemetry attributes.

Create a CloudWatch dashboard from ADOT-exported spans/metrics with these views:

- API: request count, p95/p99 latency, 4xx, and 5xx by route.
- TMS: `tms.shipment.*` operation count/errors/latency.
- Dock Scheduler: slot-search, hold, confirm, conflict/no-feasible-slot rates.
- Check-in: gate, queue, dock, complete, and invalid-transition rates.
- AI: `driver_chat.agent_execution` latency/errors, LangSmith tool-call and token
  data where the provider supplies it, plus escalation counts from application logs.
