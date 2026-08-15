"""Low-cardinality, fail-open OpenTelemetry metrics for application boundaries."""

from __future__ import annotations

import logging
from time import perf_counter
from typing import Mapping

from opentelemetry import metrics

from setuhaul.infrastructure.observability import get_request_id

logger = logging.getLogger("setuhaul.domain")

_SAFE_METRIC_KEYS = {"operation", "result", "current_status", "status", "route", "method"}
_SAFE_LOG_KEYS = _SAFE_METRIC_KEYS | {"facility_id", "shipment_id", "thread_id"}
_METER = metrics.get_meter("setuhaul.observability")
_COUNTERS = {
    name: _METER.create_counter(name)
    for name in (
        "setuhaul.http.requests",
        "setuhaul.http.errors",
        "setuhaul.tms.shipments_created",
        "setuhaul.tms.shipments_cancelled",
        "setuhaul.tms.slot_changes",
        "setuhaul.scheduler.slot_searches",
        "setuhaul.scheduler.conflicts",
        "setuhaul.scheduler.no_feasible_slot",
        "setuhaul.scheduler.confirmations",
        "setuhaul.checkin.gate_ins",
        "setuhaul.checkin.queue_updates",
        "setuhaul.checkin.dock_ins",
        "setuhaul.checkin.completions",
        "setuhaul.checkin.invalid_transitions",
        "setuhaul.driver.delay_reports",
        "setuhaul.driver.slot_requests",
        "setuhaul.ai.calls",
        "setuhaul.ai.errors",
        "setuhaul.ai.tool_calls",
    )
}
_DURATION = {
    "http": _METER.create_histogram("setuhaul.http.duration_ms", unit="ms"),
    "ai": _METER.create_histogram("setuhaul.ai.duration_ms", unit="ms"),
}


def safe_attributes(values: Mapping[str, object] | None = None) -> dict[str, str | int | float | bool]:
    if not values:
        return {}
    return {key: value for key, value in values.items() if key in _SAFE_METRIC_KEYS and isinstance(value, (str, int, float, bool))}


def _safe_log_fields(values: Mapping[str, object]) -> dict[str, str | int | float | bool]:
    return {key: value for key, value in values.items() if key in _SAFE_LOG_KEYS and isinstance(value, (str, int, float, bool))}


def increment(name: str, attributes: Mapping[str, object] | None = None) -> None:
    """Increment a fixed metric; telemetry errors never affect callers."""
    try:
        _COUNTERS[name].add(1, safe_attributes(attributes))
    except Exception:  # noqa: BLE001
        logger.warning("Metric emission failed for %s.", name, exc_info=True)


def record_duration(kind: str, duration_ms: float, attributes: Mapping[str, object] | None = None) -> None:
    try:
        _DURATION[kind].record(duration_ms, safe_attributes(attributes))
    except Exception:  # noqa: BLE001
        logger.warning("Duration metric emission failed for %s.", kind, exc_info=True)


def record_http(method: str, route: str, status: int, duration_ms: float) -> None:
    attributes = {"method": method, "route": route, "status": status}
    increment("setuhaul.http.requests", attributes)
    record_duration("http", duration_ms, attributes)
    if status >= 400:
        increment("setuhaul.http.errors", attributes)


def emit_domain_event(event: str, **values: object) -> None:
    """Emit a structured, request-correlated business action without PII."""
    fields = {"event": event, "request_id": get_request_id(), **_safe_log_fields(values)}
    logger.info(event, extra={"structured_fields": fields})


class Duration:
    """Measure a non-business AI boundary while preserving fail-open semantics."""

    def __init__(self, kind: str, attributes: Mapping[str, object] | None = None) -> None:
        self.kind = kind
        self.attributes = attributes
        self.started = 0.0

    def __enter__(self) -> "Duration":
        self.started = perf_counter()
        return self

    def __exit__(self, *_: object) -> None:
        record_duration(self.kind, (perf_counter() - self.started) * 1000, self.attributes)
