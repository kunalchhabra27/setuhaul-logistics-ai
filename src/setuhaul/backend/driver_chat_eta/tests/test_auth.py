"""Tests for get_current_driver's Redis session-cache fast path -- mirrors
setuhaul.infrastructure.tests.test_auth for the driver portal's own
(separate) auth dependency."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.security import HTTPAuthorizationCredentials

from setuhaul.backend._testing.fake_redis import FakeRedis
from setuhaul.backend.driver_chat_eta import auth as driver_auth
from setuhaul.backend.driver_chat_eta.exceptions import AuthenticationError
from setuhaul.infrastructure import auth_session, redis_client


@pytest.fixture(autouse=True)
def _reset_client_cache():
    redis_client.reset_client_cache()
    yield
    redis_client.reset_client_cache()


@pytest.fixture()
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    redis = FakeRedis()
    monkeypatch.setattr(redis_client, "get_client", lambda: redis)
    return redis


def _credentials(token: str = "tok-1") -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def _fake_supabase_client(user_id: str = "DRV1", email: str = "driver@example.com"):
    user = SimpleNamespace(id=user_id, email=email)
    auth_ns = SimpleNamespace(get_user=Mock(return_value=SimpleNamespace(user=user)))
    return SimpleNamespace(auth=auth_ns)


def _settings() -> SimpleNamespace:
    return SimpleNamespace(session_ttl_seconds=300, session_refresh_threshold_seconds=60)


class TestGetCurrentDriver:
    def test_no_credentials_raises(self) -> None:
        with pytest.raises(AuthenticationError):
            driver_auth.get_current_driver(credentials=None)

    def test_cache_miss_calls_supabase_once_and_populates_cache(self, fake_redis: FakeRedis, monkeypatch: pytest.MonkeyPatch) -> None:
        supabase_client = _fake_supabase_client()
        monkeypatch.setattr(driver_auth, "create_public_client", lambda settings: supabase_client)
        monkeypatch.setattr(driver_auth, "get_settings", _settings)

        principal = driver_auth.get_current_driver(credentials=_credentials("tok-1"))

        assert principal.user_id == "DRV1"
        assert principal.email == "driver@example.com"
        supabase_client.auth.get_user.assert_called_once_with("tok-1")

    def test_cache_hit_never_calls_supabase(self, fake_redis: FakeRedis, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = _settings()
        monkeypatch.setattr(driver_auth, "get_settings", lambda: settings)
        auth_session.create_session("tok-1", "DRV1", "driver", None, settings)

        supabase_client = _fake_supabase_client()
        monkeypatch.setattr(driver_auth, "create_public_client", lambda s: supabase_client)

        principal = driver_auth.get_current_driver(credentials=_credentials("tok-1"))
        assert principal.user_id == "DRV1"
        supabase_client.auth.get_user.assert_not_called()

    def test_invalid_token_raises_authentication_error(self, fake_redis: FakeRedis, monkeypatch: pytest.MonkeyPatch) -> None:
        supabase_client = SimpleNamespace(auth=SimpleNamespace(get_user=Mock(side_effect=Exception("bad token"))))
        monkeypatch.setattr(driver_auth, "create_public_client", lambda settings: supabase_client)
        monkeypatch.setattr(driver_auth, "get_settings", _settings)

        with pytest.raises(AuthenticationError):
            driver_auth.get_current_driver(credentials=_credentials("bad-token"))

    def test_redis_unavailable_still_verifies_via_supabase(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(redis_client, "get_client", lambda: None)
        supabase_client = _fake_supabase_client()
        monkeypatch.setattr(driver_auth, "create_public_client", lambda settings: supabase_client)
        monkeypatch.setattr(driver_auth, "get_settings", _settings)

        principal = driver_auth.get_current_driver(credentials=_credentials("tok-1"))
        assert principal.user_id == "DRV1"
        supabase_client.auth.get_user.assert_called_once()

    def test_a_cached_tms_session_is_never_honored_for_the_driver_portal(
        self, fake_redis: FakeRedis, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        settings = _settings()
        monkeypatch.setattr(driver_auth, "get_settings", lambda: settings)
        auth_session.create_session("tok-1", "USER1", "tms", "ADMIN_1", settings)

        supabase_client = _fake_supabase_client(user_id="DRV1")
        monkeypatch.setattr(driver_auth, "create_public_client", lambda s: supabase_client)

        principal = driver_auth.get_current_driver(credentials=_credentials("tok-1"))
        assert principal.user_id == "DRV1"
        supabase_client.auth.get_user.assert_called_once()
