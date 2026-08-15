"""Tests for get_current_principal's Redis session-cache fast path: a hit
must skip Supabase entirely, a miss must fall through to the exact same
verification as before and then populate the cache."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.security import HTTPAuthorizationCredentials

from setuhaul.backend._testing.fake_redis import FakeRedis
from setuhaul.backend.tms.exceptions import AuthenticationError
from setuhaul.backend.tms.models import TMSRole
from setuhaul.infrastructure import auth, auth_session, redis_client


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


def _fake_supabase_client(user_id: str = "USER1", tms_role: str | None = "ADMIN_1"):
    user = SimpleNamespace(id=user_id, app_metadata={"tms_role": tms_role} if tms_role else {}, email="u@example.com")
    response = SimpleNamespace(user=user)
    auth_ns = SimpleNamespace(get_user=Mock(return_value=response))
    return SimpleNamespace(auth=auth_ns)


class TestGetCurrentPrincipal:
    def test_no_credentials_raises_without_touching_redis_or_supabase(self) -> None:
        with pytest.raises(AuthenticationError):
            auth.get_current_principal(credentials=None)

    def test_cache_miss_calls_supabase_once_and_populates_cache(self, fake_redis: FakeRedis, monkeypatch: pytest.MonkeyPatch) -> None:
        supabase_client = _fake_supabase_client(tms_role="AGENT_READER")
        monkeypatch.setattr(auth, "create_public_client", lambda settings: supabase_client)
        monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(session_ttl_seconds=300, session_refresh_threshold_seconds=60))

        principal = auth.get_current_principal(credentials=_credentials("tok-1"))

        assert principal.user_id == "USER1"
        assert principal.role is TMSRole.AGENT_READER
        supabase_client.auth.get_user.assert_called_once_with("tok-1")

        cached = auth_session.get_cached_session("tok-1", SimpleNamespace(session_ttl_seconds=300, session_refresh_threshold_seconds=60))
        assert cached is not None
        assert cached.user_id == "USER1"
        assert cached.portal == "tms"

    def test_cache_hit_never_calls_supabase(self, fake_redis: FakeRedis, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = SimpleNamespace(session_ttl_seconds=300, session_refresh_threshold_seconds=60)
        monkeypatch.setattr(auth, "get_settings", lambda: settings)
        auth_session.create_session("tok-1", "USER1", "tms", "ADMIN_1", settings)

        supabase_client = _fake_supabase_client()
        monkeypatch.setattr(auth, "create_public_client", lambda s: supabase_client)

        principal = auth.get_current_principal(credentials=_credentials("tok-1"))

        assert principal.user_id == "USER1"
        assert principal.role is TMSRole.ADMIN_1
        supabase_client.auth.get_user.assert_not_called()

    def test_missing_tms_role_claim_defaults_to_admin_and_is_cached_as_such(
        self, fake_redis: FakeRedis, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        supabase_client = _fake_supabase_client(tms_role=None)
        monkeypatch.setattr(auth, "create_public_client", lambda settings: supabase_client)
        settings = SimpleNamespace(session_ttl_seconds=300, session_refresh_threshold_seconds=60)
        monkeypatch.setattr(auth, "get_settings", lambda: settings)

        principal = auth.get_current_principal(credentials=_credentials("tok-1"))
        assert principal.role is TMSRole.ADMIN_1

        # A subsequent request must hit the cache with the same resolved role
        # -- not re-derive "no claim -> default" from a cached None.
        cached = auth_session.get_cached_session("tok-1", settings)
        assert cached is not None and cached.role == "ADMIN_1"

    def test_invalid_token_raises_authentication_error(self, fake_redis: FakeRedis, monkeypatch: pytest.MonkeyPatch) -> None:
        supabase_client = SimpleNamespace(auth=SimpleNamespace(get_user=Mock(side_effect=Exception("bad token"))))
        monkeypatch.setattr(auth, "create_public_client", lambda settings: supabase_client)
        monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(session_ttl_seconds=300, session_refresh_threshold_seconds=60))

        with pytest.raises(AuthenticationError):
            auth.get_current_principal(credentials=_credentials("bad-token"))

    def test_redis_unavailable_still_verifies_via_supabase_and_authenticates_correctly(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A Redis outage must never authenticate anyone by itself, and must
        never block a legitimate request either -- it just always pays the
        full Supabase verification cost, same as before this feature."""
        monkeypatch.setattr(redis_client, "get_client", lambda: None)
        supabase_client = _fake_supabase_client()
        monkeypatch.setattr(auth, "create_public_client", lambda settings: supabase_client)
        monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(session_ttl_seconds=300, session_refresh_threshold_seconds=60))

        principal = auth.get_current_principal(credentials=_credentials("tok-1"))
        assert principal.user_id == "USER1"
        supabase_client.auth.get_user.assert_called_once()

    def test_a_cached_driver_portal_session_is_never_honored_for_tms(
        self, fake_redis: FakeRedis, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cross-portal isolation: a session cached under the driver portal
        must not let its token authenticate as a TMS principal via the cache
        -- must fall through to a real (re-)verification."""
        settings = SimpleNamespace(session_ttl_seconds=300, session_refresh_threshold_seconds=60)
        monkeypatch.setattr(auth, "get_settings", lambda: settings)
        auth_session.create_session("tok-1", "DRV1", "driver", None, settings)

        supabase_client = _fake_supabase_client(user_id="USER1", tms_role="ADMIN_1")
        monkeypatch.setattr(auth, "create_public_client", lambda s: supabase_client)

        principal = auth.get_current_principal(credentials=_credentials("tok-1"))
        assert principal.user_id == "USER1"
        supabase_client.auth.get_user.assert_called_once()
