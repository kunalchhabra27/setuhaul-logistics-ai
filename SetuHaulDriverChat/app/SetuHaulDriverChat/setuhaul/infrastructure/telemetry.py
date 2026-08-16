"""Failure-isolated OpenTelemetry helpers for the SetuHaul harness.

This module intentionally observes API and service boundaries only.  It does
not participate in scheduling, check-in state transitions, or persistence
decisions, so telemetry availability can never change a business result.
"""

from __future__ import annotations

import logging
import os
from copy import copy
from contextlib import ExitStack, contextmanager
from typing import Any, Callable, Iterator, TypeVar

from fastapi import FastAPI

logger = logging.getLogger(__name__)

_SAFE_ATTRIBUTE_KEYS = {
    "facility_id",
    "shipment_id",
    "thread_id",
    "exception_id",
    "operation",
    "result",
    "status",
    "environment",
}
T = TypeVar("T")
_OTLP_HTTP_PROTOCOLS = {"http/protobuf", "http"}
_SAFE_LOG_ATTRIBUTE_KEYS = {
    "request_id",
    "method",
    "endpoint",
    "status",
    "duration_ms",
    "event",
    "operation",
    "result",
    "shipment_id",
    "facility_id",
    "thread_id",
    "exception_id",
}


def _enabled() -> bool:
    return os.getenv("OTEL_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def _otlp_signal_endpoint(signal: str) -> str | None:
    """Return the OTLP/HTTP signal path expected by a local collector."""
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if not endpoint:
        return None
    protocol = os.getenv("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf").strip().lower()
    if protocol not in _OTLP_HTTP_PROTOCOLS:
        logger.warning(
            "Unsupported OTLP protocol %r; continuing with local-only telemetry.",
            protocol,
        )
        return None
    normalized = endpoint.rstrip("/")
    for known_signal in ("traces", "metrics", "logs"):
        known_suffix = f"/v1/{known_signal}"
        if normalized.endswith(known_suffix):
            normalized = normalized[: -len(known_suffix)]
            break
    suffix = f"/v1/{signal}"
    return f"{normalized}{suffix}"


def _otlp_trace_endpoint() -> str | None:
    return _otlp_signal_endpoint("traces")


def _safe_attributes(attributes: dict[str, Any] | None) -> dict[str, str | int | float | bool]:
    """Keep telemetry useful without allowing credentials or PII into spans."""
    if not attributes:
        return {}
    return {
        key: value
        for key, value in attributes.items()
        if key in _SAFE_ATTRIBUTE_KEYS and value is not None and isinstance(value, (str, int, float, bool))
    }


def _safe_log_attributes(fields: object) -> dict[str, str | int | float | bool]:
    """Flatten only approved structured fields for OTLP log correlation."""
    if not isinstance(fields, dict):
        return {}
    return {
        f"setuhaul.{key}": value
        for key, value in fields.items()
        if key in _SAFE_LOG_ATTRIBUTE_KEYS and value is not None and isinstance(value, (str, int, float, bool))
    }


class _StructuredOtelLoggingHandler(logging.Handler):
    """Forward existing SetuHaul JSON log fields to OTLP without changing stdout."""

    def __init__(self, logger_provider: Any) -> None:
        super().__init__()
        from opentelemetry.sdk._logs import LoggingHandler

        self._delegate = LoggingHandler(logger_provider=logger_provider)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            # Do not mutate the record that the existing JSON stdout handler sees.
            otel_record = copy(record)
            otel_record.__dict__.pop("structured_fields", None)
            otel_record.__dict__.update(_safe_log_attributes(getattr(record, "structured_fields", None)))
            self._delegate.emit(otel_record)
        except Exception:  # noqa: BLE001 - logging export must never affect a request
            return


def _configure_otlp_logging(resource: Any, endpoint: str | None) -> None:
    """Attach an OTLP bridge to existing SetuHaul loggers when an endpoint exists."""
    if not endpoint:
        return
    try:
        from opentelemetry import _logs
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
        from opentelemetry.sdk._logs import LoggerProvider
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor

        provider = LoggerProvider(resource=resource)
        provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter(endpoint=_otlp_signal_endpoint("logs"))))
        _logs.set_logger_provider(provider)

        application_logger = logging.getLogger("setuhaul")
        if not any(isinstance(handler, _StructuredOtelLoggingHandler) for handler in application_logger.handlers):
            application_logger.addHandler(_StructuredOtelLoggingHandler(provider))
    except Exception:  # noqa: BLE001 - logs are optional observability
        logger.warning("OTLP log exporter setup failed; continuing without OTLP logs.", exc_info=True)


def initialize_telemetry(app: FastAPI) -> None:
    """Configure request tracing once, without making telemetry a dependency.

    OTLP is suitable for a CloudWatch Agent or AWS ADOT Collector.  When no
    exporter endpoint is configured, spans remain local and requests still run
    normally.  Every failure is deliberately downgraded to a warning.
    """
    if not _enabled() or getattr(app.state, "telemetry_initialized", False):
        return
    app.state.telemetry_initialized = True
    try:
        from opentelemetry import metrics, trace
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.metrics import MeterProvider

        resource = Resource.create(
            {
                "service.name": os.getenv("OTEL_SERVICE_NAME", "setuhaul-backend"),
                "deployment.environment": os.getenv("ENVIRONMENT", "development"),
            }
        )
        provider = TracerProvider(resource=resource)
        endpoint = _otlp_trace_endpoint()
        metric_readers = []
        if endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
                from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
                from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

                provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
                metric_readers.append(
                    PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=_otlp_signal_endpoint("metrics")))
                )
            except Exception:  # noqa: BLE001 - exporter failure must be isolated
                logger.warning("OTLP exporter setup failed; continuing with local-only telemetry.", exc_info=True)
        trace.set_tracer_provider(provider)
        metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=metric_readers))
        _configure_otlp_logging(resource, endpoint)
        FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
    except Exception:  # noqa: BLE001 - optional observability must never break startup
        logger.warning("OpenTelemetry setup failed; continuing without tracing.", exc_info=True)


@contextmanager
def operation_span(name: str, attributes: dict[str, Any] | None = None) -> Iterator[None]:
    """Create a safe service-boundary span or a no-op context when disabled."""
    if not _enabled():
        yield
        return
    try:
        from opentelemetry import trace
        from opentelemetry.trace import Status, StatusCode
    except Exception:  # noqa: BLE001 - optional dependency may not be installed yet
        logger.warning("Telemetry span failed; continuing request without it.", exc_info=True)
        yield
        return

    # The SDK's default processors handle exporter failures asynchronously.
    # Keep business exceptions outside the telemetry setup catch so they are
    # recorded and then propagated exactly as before.
    with trace.get_tracer("setuhaul.harness").start_as_current_span(name) as span:
        for key, value in _safe_attributes(attributes).items():
            span.set_attribute(key, value)
        try:
            yield
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            raise


def observe_operation(name: str, attributes: dict[str, Any] | None, callback: Callable[[], T]) -> T:
    """Run an existing operation under a safe span without changing its flow."""
    with operation_span(name, attributes):
        try:
            result = callback()
        except Exception:
            set_current_span_attributes({"result": "error"})
            raise
        set_current_span_attributes({"result": "success"})
        return result


def set_current_span_attributes(attributes: dict[str, Any] | None) -> None:
    """Enrich an already-active boundary span with safe runtime identifiers."""
    if not _enabled():
        return
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        for key, value in _safe_attributes(attributes).items():
            span.set_attribute(key, value)
    except Exception:  # noqa: BLE001 - enrichment is strictly best-effort
        logger.warning("Telemetry span enrichment failed.", exc_info=True)


def langsmith_enabled() -> bool:
    """Whether the existing LangChain agent should emit LangSmith traces."""
    return os.getenv("LANGSMITH_TRACING", "false").strip().lower() in {"1", "true", "yes", "on"}


@contextmanager
def langsmith_trace_context(metadata: dict[str, Any] | None = None) -> Iterator[None]:
    """Scope LangSmith tracing to the Driver Chat agent only.

    LangChain detects this context for the LLM and tool calls it performs.
    Missing credentials or SDK failures leave the existing agent untouched.
    """
    if not langsmith_enabled() or not os.getenv("LANGSMITH_API_KEY"):
        yield
        return
    try:
        from langsmith import trace, tracing_context

        project = os.getenv("LANGSMITH_PROJECT", "setuhaul-harness")
    except Exception:  # noqa: BLE001 - LangSmith is optional
        logger.warning("LangSmith setup failed; continuing without LangSmith tracing.", exc_info=True)
        yield
        return

    safe_metadata = _safe_attributes(metadata)
    stack = ExitStack()
    try:
        stack.enter_context(
            tracing_context(
                enabled=True,
                project_name=project,
                tags=["driver-chat"],
                metadata=safe_metadata,
            )
        )
        stack.enter_context(
            trace(
                "setuhaul.driver_chat",
                run_type="chain",
                inputs={"operation": "driver_chat"},
                project_name=project,
                tags=["driver-chat"],
                metadata=safe_metadata,
            )
        )
    except Exception:  # noqa: BLE001 - tracing setup must never affect chat
        stack.close()
        logger.warning("LangSmith setup failed; continuing without LangSmith tracing.", exc_info=True)
        yield
        return

    business_error: BaseException | None = None
    try:
        yield
    except BaseException as exc:
        business_error = exc
        raise
    finally:
        try:
            stack.__exit__(
                type(business_error) if business_error else None,
                business_error,
                business_error.__traceback__ if business_error else None,
            )
        except Exception:  # noqa: BLE001 - tracing teardown is also fail-open
            logger.warning("LangSmith teardown failed; continuing request normally.", exc_info=True)


def set_current_langsmith_metadata(metadata: dict[str, Any] | None) -> None:
    """Add safe identifiers to the active Driver Chat run, if one exists."""
    if not langsmith_enabled() or not os.getenv("LANGSMITH_API_KEY"):
        return
    try:
        from langsmith import get_current_run_tree

        run = get_current_run_tree()
        if run is not None:
            run.add_metadata(_safe_attributes(metadata))
    except Exception:  # noqa: BLE001 - enrichment is strictly best-effort
        logger.warning("LangSmith metadata enrichment failed.", exc_info=True)
