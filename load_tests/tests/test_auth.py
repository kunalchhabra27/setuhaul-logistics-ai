from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from load_tests.auth import LoadTestAuth, LoadTestAuthenticationError


class _Response:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload
        self.is_success = 200 <= status_code < 300

    def json(self) -> dict:
        return self._payload


@pytest.fixture(autouse=True)
def clean_auth_environment(monkeypatch):
    names = [
        "TEST_ACCESS_TOKEN",
        "SUPABASE_URL",
        "SUPABASE_PUBLISHABLE_KEY",
        "LOAD_TEST_EMAIL",
        "LOAD_TEST_PASSWORD",
        "LOAD_TEST_DRIVER_EMAIL",
        "LOAD_TEST_DRIVER_PASSWORD",
        "LOAD_TEST_TMS_EMAIL",
        "LOAD_TEST_TMS_PASSWORD",
    ]
    for name in names:
        monkeypatch.delenv(name, raising=False)


def _configure_generic(monkeypatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "public-test-key")
    monkeypatch.setenv("LOAD_TEST_EMAIL", "load@example.com")
    monkeypatch.setenv("LOAD_TEST_PASSWORD", "generic-password")


def _session(token: str, *, expires_at: float = 4600) -> dict:
    return {"access_token": token, "refresh_token": f"refresh-{token}", "expires_at": expires_at}


def test_explicit_access_token_is_reused(monkeypatch) -> None:
    monkeypatch.setenv("TEST_ACCESS_TOKEN", "explicit-token")
    calls: list = []
    auth = LoadTestAuth(post=lambda *args, **kwargs: calls.append((args, kwargs)))

    assert auth.access_token("driver") == "explicit-token"
    assert calls == []


def test_missing_token_with_credentials_logs_in(monkeypatch) -> None:
    _configure_generic(monkeypatch)
    auth = LoadTestAuth(post=lambda *args, **kwargs: _Response(200, _session("generated")), now=lambda: 1000)

    assert auth.access_token("driver") == "generated"


def test_generated_token_is_cached(monkeypatch) -> None:
    _configure_generic(monkeypatch)
    calls = 0

    def post(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return _Response(200, _session("cached"))

    auth = LoadTestAuth(post=post, now=lambda: 1000)
    assert auth.access_token("tms") == "cached"
    assert auth.access_token("tms") == "cached"
    assert calls == 1


def test_virtual_users_share_one_login(monkeypatch) -> None:
    _configure_generic(monkeypatch)
    calls = 0

    def post(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return _Response(200, _session("shared"))

    auth = LoadTestAuth(post=post, now=lambda: 1000)
    with ThreadPoolExecutor(max_workers=8) as pool:
        tokens = list(pool.map(lambda _: auth.access_token("wms"), range(24)))

    assert tokens == ["shared"] * 24
    assert calls == 1


def test_near_expiry_session_refreshes(monkeypatch) -> None:
    _configure_generic(monkeypatch)
    responses = iter([_Response(200, _session("first", expires_at=1050)), _Response(200, _session("refreshed"))])
    urls: list[str] = []

    def post(url, **_kwargs):
        urls.append(url)
        return next(responses)

    auth = LoadTestAuth(post=post, now=lambda: 1000)
    assert auth.access_token("driver") == "first"
    assert auth.access_token("driver") == "refreshed"
    assert urls[1].endswith("grant_type=refresh_token")


def test_refresh_failure_performs_one_clean_relogin(monkeypatch) -> None:
    _configure_generic(monkeypatch)
    responses = iter(
        [
            _Response(200, _session("first", expires_at=1050)),
            _Response(401, {}),
            _Response(200, _session("relogged")),
        ]
    )
    auth = LoadTestAuth(post=lambda *_args, **_kwargs: next(responses), now=lambda: 1000)

    assert auth.access_token("driver") == "first"
    assert auth.access_token("driver") == "relogged"


def test_credentials_and_tokens_are_not_logged(monkeypatch, caplog) -> None:
    _configure_generic(monkeypatch)
    caplog.set_level(logging.DEBUG)
    auth = LoadTestAuth(post=lambda *_args, **_kwargs: _Response(401, {"token": "secret-token"}))

    with pytest.raises(LoadTestAuthenticationError) as exc_info:
        auth.access_token("driver")

    combined = f"{exc_info.value} {caplog.text}"
    assert "generic-password" not in combined
    assert "secret-token" not in combined
    assert "public-test-key" not in combined


def test_missing_credentials_has_clear_error() -> None:
    auth = LoadTestAuth()

    with pytest.raises(LoadTestAuthenticationError, match="Driver authentication requires"):
        auth.access_token("driver")


def test_role_specific_credentials_override_generic(monkeypatch) -> None:
    _configure_generic(monkeypatch)
    monkeypatch.setenv("LOAD_TEST_DRIVER_EMAIL", "driver@example.com")
    monkeypatch.setenv("LOAD_TEST_DRIVER_PASSWORD", "driver-password")
    payloads: list[dict] = []

    def post(_url, **kwargs):
        payloads.append(kwargs["json"])
        return _Response(200, _session("driver-token"))

    auth = LoadTestAuth(post=post, now=lambda: 1000)
    assert auth.access_token("driver") == "driver-token"
    assert payloads == [{"email": "driver@example.com", "password": "driver-password"}]


def test_generic_credentials_are_role_fallback(monkeypatch) -> None:
    _configure_generic(monkeypatch)
    payloads: list[dict] = []

    def post(_url, **kwargs):
        payloads.append(kwargs["json"])
        return _Response(200, _session("fallback-token"))

    auth = LoadTestAuth(post=post, now=lambda: 1000)
    assert auth.access_token("tms") == "fallback-token"
    assert payloads[0]["email"] == "load@example.com"


def test_network_failure_is_sanitized(monkeypatch) -> None:
    _configure_generic(monkeypatch)

    def post(*_args, **_kwargs):
        raise httpx.ConnectError("network unavailable")

    with pytest.raises(LoadTestAuthenticationError, match="login request failed"):
        LoadTestAuth(post=post).access_token("driver")
