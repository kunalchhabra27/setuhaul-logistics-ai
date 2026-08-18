# SetuHaul CloudWatch OTLP Setup

This directory provides a deployment-safe OTLP path:

```text
SetuHaul FastAPI -> OTLP/HTTP -> CloudWatch Agent -> CloudWatch and X-Ray
```

The application sends traces to the agent at `http://127.0.0.1:4318/v1/traces`.
The CloudWatch Agent uses its embedded OpenTelemetry Collector and normal AWS
credential resolution to sign outbound requests to CloudWatch and X-Ray.

## Files

- `cloudwatch-agent.json` is the required base CloudWatch Agent configuration.
- `otel-cloudwatch.yaml` is appended to the agent. It receives OTLP/HTTP on
  `127.0.0.1:4318` and has metrics, logs, and traces pipelines.

The receiver is loopback-only for a same-host application and agent. For a
containerized deployment where the application is outside the agent network
namespace, change the receiver endpoint to `0.0.0.0:4318` and restrict network
access with the platform security controls.

## Credentials and IAM

Prefer an EC2 instance profile, ECS task role, or another short-lived IAM role.
For an on-premises or macOS development host, configure a dedicated IAM user
profile named `AmazonCloudWatchAgent`; do not store credentials in this repo,
`.env`, or the collector configuration.

For a team, attach this policy to the IAM group
`setuhaul-observability-local`. IAM groups grant permissions but cannot
authenticate and do not have access keys. Every teammate needs a separate IAM
user in that group (or an IAM Identity Center role) and must create their own
CLI credential profile. Never share an IAM user's access key between people.

An AWS account ID is not an access key. `aws configure` requires an IAM access
key ID and its matching secret access key, created in IAM for a principal with
the permissions below.

Use this least-privilege ingestion policy, replacing the two log resource ARNs
with the intended account, region, and pre-created log group.
`cloudwatch:PutMetricData` and X-Ray ingestion do not support narrower
resource ARNs. Pre-create the log group so `logs:CreateLogGroup` is not needed.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "WriteOpenTelemetryMetrics",
      "Effect": "Allow",
      "Action": "cloudwatch:PutMetricData",
      "Resource": "*"
    },
    {
      "Sid": "WriteOpenTelemetryTraces",
      "Effect": "Allow",
      "Action": [
        "xray:PutTraceSegments",
        "xray:PutTelemetryRecords",
        "xray:GetSamplingRules",
        "xray:GetSamplingTargets",
        "xray:GetSamplingStatisticSummaries"
      ],
      "Resource": "*"
    },
    {
      "Sid": "WriteSetuHaulOtelLogs",
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:DescribeLogStreams",
        "logs:PutLogEvents"
      ],
      "Resource": [
        "arn:aws:logs:${AWS_REGION}:${AWS_ACCOUNT_ID}:log-group:${SETUHAUL_OTEL_LOG_GROUP}",
        "arn:aws:logs:${AWS_REGION}:${AWS_ACCOUNT_ID}:log-group:${SETUHAUL_OTEL_LOG_GROUP}:*"
      ]
    }
  ]
}
```

Remove `logs:CreateLogGroup` from the policy after pre-creating the log group;
the agent then needs only the scoped stream and write permissions.
`SETUHAUL_OTEL_LOG_GROUP` and `SETUHAUL_OTEL_LOG_STREAM` are agent-process
environment variables. They are required for OTLP application logs.

Before trace ingestion, enable X-Ray Transaction Search in the target region.

## Install the CloudWatch Agent on macOS

This project runs on Apple Silicon, so use the AWS ARM64 package:

```bash
curl -O https://amazoncloudwatch-agent.s3.amazonaws.com/darwin/arm64/latest/amazon-cloudwatch-agent.pkg
sudo installer -pkg ./amazon-cloudwatch-agent.pkg -target /
```

For Intel macOS, replace `arm64` with `amd64`. For Linux or EC2, install the
current CloudWatch Agent package for the host distribution using the AWS
installation guide.

Configure a real IAM credential profile only if no workload role is available:

```bash
aws configure --profile AmazonCloudWatchAgent
aws sts get-caller-identity --profile AmazonCloudWatchAgent
```

Do not enter an AWS account ID at the `AWS Access Key ID` prompt.

## Configure and Start the Agent

Run the agent with `AWS_REGION`, `SETUHAUL_OTEL_LOG_GROUP`, and
`SETUHAUL_OTEL_LOG_STREAM` available to its service process. On production
hosts, set these in the service manager rather than an interactive shell. On
macOS, `sudo` does not reliably preserve shell exports, so put them in the
root launchd environment before starting the agent:

```bash
export AWS_REGION=ap-south-1
export SETUHAUL_OTEL_LOG_GROUP=/setuhaul/otel
export SETUHAUL_OTEL_LOG_STREAM=local

sudo launchctl setenv AWS_REGION "$AWS_REGION"
sudo launchctl setenv SETUHAUL_OTEL_LOG_GROUP "$SETUHAUL_OTEL_LOG_GROUP"
sudo launchctl setenv SETUHAUL_OTEL_LOG_STREAM "$SETUHAUL_OTEL_LOG_STREAM"

sudo /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
  -a fetch-config -m onPremise -s \
  -c file:"$PWD/observability/cloudwatch-agent.json"

sudo /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
  -a append-config -m onPremise -s \
  -c file:"$PWD/observability/otel-cloudwatch.yaml"
```

On EC2, use `-m ec2` and attach the least-privilege policy to the instance
role. The agent control command requires a base JSON configuration before the
OTel YAML can be appended.

## Start SetuHaul with OTLP

Set the generic endpoint to the collector base address. The application adds
the required `/v1/traces` path for the OTLP/HTTP trace exporter.

```bash
export AWS_REGION=ap-south-1
export OTEL_ENABLED=true
export OTEL_SERVICE_NAME=setuhaul-api
export OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf

source .venv/bin/activate
PYTHONPATH=src uvicorn setuhaul.main:app --host 127.0.0.1 --port 8000
```

Disable all application OpenTelemetry instrumentation at any time:

```bash
export OTEL_ENABLED=false
unset OTEL_EXPORTER_OTLP_ENDPOINT
```

## Verify the Complete Path

1. Confirm the collector is listening before starting FastAPI:

   ```bash
   lsof -nP -iTCP:4318 -sTCP:LISTEN
   ```

2. Start FastAPI with the environment above. It must reach `Uvicorn running`
   without an `OpenTelemetry setup failed` warning.

3. Generate an automatic FastAPI server span:

   ```bash
   curl http://127.0.0.1:8000/health
   ```

4. Inspect the agent diagnostic log for receiver/exporter errors:

   ```bash
   tail -f /opt/aws/amazon-cloudwatch-agent/logs/amazon-cloudwatch-agent.log
   ```

5. In the `ap-south-1` CloudWatch console, verify traces through Transaction
   Search/X-Ray. Trace delivery can take a few minutes.

6. Verify metrics in CloudWatch Metrics/Query Studio and log events in
   `SETUHAUL_OTEL_LOG_GROUP`. Application log records retain the JSON message
   and expose safe correlation attributes such as `setuhaul.request_id`.

The application exports automatic and existing trace spans plus fixed,
low-cardinality HTTP, TMS, scheduler, Check-in, Driver, and AI metrics. It
also mirrors existing `setuhaul.*` structured logs into OTLP without replacing
stdout JSON logging. `RequestObservabilityMiddleware` structured request logs,
`X-Request-ID`, duration, and status logging remain unchanged; domain-action
logs are additive and request-correlated.

## Fail-Open Behavior

If the agent is stopped, port `4318` is unavailable, or AWS credentials are
missing, the exporter fails asynchronously or initialization falls back to
local-only telemetry. FastAPI routes and deterministic decisions continue to
run normally. This configuration never grants telemetry control over API
responses, scheduling, persistence, or business state.

## Sources

- [CloudWatch Agent OpenTelemetry support](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch-OTLPCloudWatchAgent.html)
- [CloudWatch Agent OTLP receivers](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch-Agent-OpenTelemetry-metrics.html)
- [CloudWatch Agent credential resolution](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch-Agent-Credentials-Preference.html)
- [OTLP metrics IAM permissions](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/metrics-otel-send.html)
- [X-Ray write-only permissions](https://docs.aws.amazon.com/aws-managed-policy/latest/reference/AWSXrayWriteOnlyAccess.html)
