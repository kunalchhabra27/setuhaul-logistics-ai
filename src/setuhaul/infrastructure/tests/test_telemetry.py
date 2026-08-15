"""Regression coverage for observability's disabled/failure-isolated mode."""

from __future__ import annotations

import sys
from contextlib import contextmanager
from types import ModuleType

from fastapi import FastAPI

from setuhaul.infrastructure.telemetry import (
    _otlp_trace_endpoint,
    _safe_log_attributes,
    initialize_telemetry,
    langsmith_trace_context,
    operation_span,
    set_current_langsmith_metadata,
)
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


def test_langsmith_context_uses_named_run_and_safe_metadata(monkeypatch) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-key")
    monkeypatch.setenv("LANGSMITH_PROJECT", "test-project")
    captured: dict = {}

    class FakeRun:
        def add_metadata(self, metadata: dict) -> None:
            captured["enriched"] = metadata

    @contextmanager
    def fake_tracing_context(**kwargs):
        captured["context"] = kwargs
        yield

    @contextmanager
    def fake_trace(name, **kwargs):
        captured["name"] = name
        captured["trace"] = kwargs
        yield

    fake_module = ModuleType("langsmith")
    fake_module.tracing_context = fake_tracing_context
    fake_module.trace = fake_trace
    fake_module.get_current_run_tree = lambda: FakeRun()
    monkeypatch.setitem(sys.modules, "langsmith", fake_module)

    with langsmith_trace_context({"operation": "driver_chat", "driver_name": "private"}):
        set_current_langsmith_metadata(
            {"shipment_id": "SHP-1", "thread_id": "TH-1", "driver_name": "private"}
        )

    assert captured["name"] == "setuhaul.driver_chat"
    assert captured["trace"]["project_name"] == "test-project"
    assert captured["trace"]["metadata"] == {"operation": "driver_chat"}
    assert captured["enriched"] == {"shipment_id": "SHP-1", "thread_id": "TH-1"}


def test_langsmith_setup_failure_is_fail_open(monkeypatch) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-key")

    @contextmanager
    def broken_context(**_kwargs):
        raise RuntimeError("unavailable")
        yield

    fake_module = ModuleType("langsmith")
    fake_module.tracing_context = broken_context
    fake_module.trace = broken_context
    monkeypatch.setitem(sys.modules, "langsmith", fake_module)

    with langsmith_trace_context({"operation": "driver_chat"}):
        result = "business-result"

    assert result == "business-result"
