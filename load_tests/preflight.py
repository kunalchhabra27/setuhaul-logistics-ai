"""Selected-workload validation performed once before Locust spawns users."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

import httpx

from load_tests.auth import LoadTestAuthenticationError, bearer_headers


class LoadTestPreflightError(RuntimeError):
    """A configuration issue that should stop a load test before user spawn."""


_AUTH_ROLES = {
    "DriverUser": "driver",
    "TmsUser": "tms",
    "WmsUser": "wms",
    "CheckinUser": "checkin",
}


def agentcore_reachable(host: str) -> None:
    try:
        response = httpx.get(f"{host.rstrip('/')}/ping", timeout=3.0)
    except httpx.HTTPError as exc:
        raise LoadTestPreflightError(f"AgentCoreDriverUser cannot reach {host}.") from exc
    if not response.is_success:
        raise LoadTestPreflightError(
            f"AgentCoreDriverUser health check at {host} returned HTTP {response.status_code}."
        )


def validate_selected_workloads(
    user_classes: Iterable[type],
    *,
    requested_users: int = 1,
    environment_host: str | None = None,
    auth_resolver: Callable[[str], dict[str, str]] | None = None,
    checkin_validator: Callable[[int], Any] | None = None,
    agentcore_probe: Callable[[str], None] | None = None,
) -> None:
    """Validate only classes selected by Locust's class picker or CLI."""
    resolve_auth = auth_resolver or (lambda role: bearer_headers(role=role))
    probe_agentcore = agentcore_probe or agentcore_reachable

    for user_class in user_classes:
        class_name = user_class.__name__
        role = _AUTH_ROLES.get(class_name)
        if role:
            try:
                resolve_auth(role)
            except (LoadTestAuthenticationError, RuntimeError) as exc:
                raise LoadTestPreflightError(f"{class_name}: {exc}") from exc

        if class_name == "CheckinUser":
            if checkin_validator is None:
                from load_tests.scenarios.checkin import validate_checkin_configuration

                checkin_validator = validate_checkin_configuration
            try:
                checkin_validator(requested_users)
            except (OSError, ValueError, KeyError, RuntimeError) as exc:
                raise LoadTestPreflightError(f"CheckinUser: {exc}") from exc

        if class_name == "AgentCoreDriverUser":
            host = environment_host or getattr(user_class, "host", "http://localhost:8090")
            probe_agentcore(host)


def preflight_environment(environment: Any) -> None:
    options = getattr(environment, "parsed_options", None)
    requested_users = int(getattr(options, "num_users", None) or getattr(options, "users", None) or 1)
    validate_selected_workloads(
        environment.user_classes,
        requested_users=requested_users,
        environment_host=environment.host,
    )
