from __future__ import annotations

import pytest

from load_tests.auth import LoadTestAuthenticationError
from load_tests.preflight import LoadTestPreflightError, validate_selected_workloads


def _user_class(name: str, host: str = "") -> type:
    return type(name, (), {"host": host})


@pytest.mark.parametrize(
    ("class_name", "expected_role"),
    [("DriverUser", "driver"), ("TmsUser", "tms"), ("WmsUser", "wms")],
)
def test_selected_portal_validates_only_its_role(class_name: str, expected_role: str) -> None:
    roles: list[str] = []

    validate_selected_workloads([_user_class(class_name)], auth_resolver=lambda role: roles.append(role) or {})

    assert roles == [expected_role]


def test_checkin_validates_auth_and_dedicated_configuration() -> None:
    roles: list[str] = []
    counts: list[int] = []

    validate_selected_workloads(
        [_user_class("CheckinUser")],
        requested_users=5,
        auth_resolver=lambda role: roles.append(role) or {},
        checkin_validator=lambda count: counts.append(count),
    )

    assert roles == ["checkin"]
    assert counts == [5]


def test_agentcore_does_not_require_supabase_authentication() -> None:
    auth_calls: list[str] = []
    hosts: list[str] = []

    validate_selected_workloads(
        [_user_class("AgentCoreDriverUser", "http://localhost:8090")],
        auth_resolver=lambda role: auth_calls.append(role) or {},
        agentcore_probe=lambda host: hosts.append(host),
    )

    assert auth_calls == []
    assert hosts == ["http://localhost:8090"]


def test_missing_configuration_is_reported_once_before_user_spawn() -> None:
    calls = 0

    def missing(_role: str) -> dict:
        nonlocal calls
        calls += 1
        raise LoadTestAuthenticationError("credentials missing")

    with pytest.raises(LoadTestPreflightError, match="DriverUser: credentials missing"):
        validate_selected_workloads([_user_class("DriverUser")], auth_resolver=missing)

    assert calls == 1


def test_unselected_workloads_do_not_block_read_only_run() -> None:
    validate_selected_workloads(
        [_user_class("ReadOnlyUser")],
        auth_resolver=lambda _role: pytest.fail("unselected auth validator was called"),
        checkin_validator=lambda _count: pytest.fail("unselected Check-in validator was called"),
        agentcore_probe=lambda _host: pytest.fail("unselected AgentCore probe was called"),
    )


def test_environment_host_overrides_agentcore_default() -> None:
    hosts: list[str] = []
    validate_selected_workloads(
        [_user_class("AgentCoreDriverUser", "http://localhost:8090")],
        environment_host="http://127.0.0.1:9000",
        agentcore_probe=lambda host: hosts.append(host),
    )
    assert hosts == ["http://127.0.0.1:9000"]
