"""Tests for POST /api/v1/auth/logout."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from setuhaul.backend._testing.fake_redis import FakeRedis
from setuhaul.infrastructure import auth_session, redis_client
from setuhaul.main import app


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


def _settings():
    from types import SimpleNamespace

    return SimpleNamespace(session_ttl_seconds=300, session_refresh_threshold_seconds=60)


def test_logout_revokes_a_cached_session(fake_redis: FakeRedis) -> None:
    auth_session.create_session("tok-1", "USER1", "tms", "ADMIN_1", _settings())
    assert auth_session.get_cached_session("tok-1", _settings()) is not None

    response = TestClient(app).post("/api/v1/auth/logout", headers={"Authorization": "Bearer tok-1"})

    assert response.status_code == 204
    assert auth_session.get_cached_session("tok-1", _settings()) is None


def test_logout_with_no_credentials_is_still_204(fake_redis: FakeRedis) -> None:
    response = TestClient(app).post("/api/v1/auth/logout")
    assert response.status_code == 204


def test_logout_for_a_never_cached_token_is_still_204(fake_redis: FakeRedis) -> None:
    response = TestClient(app).post("/api/v1/auth/logout", headers={"Authorization": "Bearer never-cached"})
    assert response.status_code == 204


def test_logout_does_not_affect_a_different_users_session(fake_redis: FakeRedis) -> None:
    auth_session.create_session("tok-user-a", "USER-A", "tms", "ADMIN_1", _settings())
    auth_session.create_session("tok-user-b", "USER-B", "tms", "ADMIN_1", _settings())

    TestClient(app).post("/api/v1/auth/logout", headers={"Authorization": "Bearer tok-user-a"})

    assert auth_session.get_cached_session("tok-user-a", _settings()) is None
    assert auth_session.get_cached_session("tok-user-b", _settings()) is not None
