"""Regression coverage for observability's disabled/failure-isolated mode."""

from __future__ import annotations

from fastapi import FastAPI

from setuhaul.infrastructure.telemetry import _otlp_trace_endpoint, _safe_log_attributes, initialize_telemetry, operation_span
from setuhaul.infrastructure.metrics import safe_attributes


def test_operation_span_is_noop_when_disabled(monkeypatch) -> None:
    monkeypatch.setenv("OTEL_ENABLED", "false")
    with operation_span("test.operation", {"shipment_id": "SHP-TEST"}):
        result = "business-result"
    assert result == "business-result"


def test_telemetry_setup_does_not_break_without_optional_sdk(monkeypatch) -> None:
    monkeypatch.setenv("OTEL_ENABLED", "true")
    app = FastAPI()
    # The current test environment need not install OTel. Setup must always
    # leave a usable FastAPI application either way.
    initialize_telemetry(app)
    assert app.state.telemetry_initialized is True


def test_otlp_http_endpoint_adds_the_trace_path(monkeypatch) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")

    assert _otlp_trace_endpoint() == "http://127.0.0.1:4318/v1/traces"


def test_otlp_http_endpoint_normalizes_a_legacy_trace_url(monkeypatch) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318/v1/traces")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")

    assert _otlp_trace_endpoint() == "http://127.0.0.1:4318/v1/traces"


def test_unsupported_otlp_protocol_disables_only_the_exporter(monkeypatch) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc")

    assert _otlp_trace_endpoint() is None


def test_metric_labels_exclude_high_cardinality_identifiers() -> None:
    assert safe_attributes({"shipment_id": "SHP-123", "thread_id": "TH-123", "route": "/health", "status": 200}) == {
        "route": "/health",
        "status": 200,
    }


def test_otlp_log_attributes_preserve_request_correlation_without_unapproved_fields() -> None:
    assert _safe_log_attributes({"request_id": "req-123", "shipment_id": "SHP-123", "driver_name": "private"}) == {
        "setuhaul.request_id": "req-123",
        "setuhaul.shipment_id": "SHP-123",
    }
