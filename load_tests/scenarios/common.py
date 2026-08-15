"""Shared safe configuration helpers for Locust scenarios."""

from __future__ import annotations

import json
import os
from typing import Any

from load_tests.auth import bearer_headers


API_PREFIX = "/api/v1"


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def mutations_allowed() -> bool:
    return env("LOAD_TEST_ALLOW_MUTATIONS").lower() in {"1", "true", "yes", "on"}


def dedicated_shipment_id(value: str) -> str:
    """Reject accidental writes to non-load-test shipments."""
    prefix = env("LOAD_TEST_SHIPMENT_PREFIX", "LT-")
    if not value or not value.startswith(prefix):
        raise RuntimeError(f"Load-test mutations require a shipment ID beginning with {prefix!r}.")
    return value


def json_env(name: str) -> dict[str, Any] | None:
    value = env(name)
    if not value:
        return None
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise RuntimeError(f"{name} must contain a JSON object.")
    return parsed


def event_time() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
