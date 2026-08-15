"""Unit tests for the Redis-backed auth-session cache."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from setuhaul.backend._testing.fake_redis import FakeRedis
from setuhaul.infrastructure import auth_session, redis_client


def _settings(**overrides) -> SimpleNamespace:
    base = dict(session_ttl_seconds=300, session_refresh_threshold_seconds=60)
    base.update(overrides)
    return SimpleNamespace(**base)


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


class TestSessionIdDerivation:
    def test_same_token_produces_same_id(self) -> None:
        assert auth_session.session_id_for("tok-a") == auth_session.session_id_for("tok-a")

    def test_different_tokens_produce_different_ids(self) -> None:
        assert auth_session.session_id_for("tok-a") != auth_session.session_id_for("tok-b")

    def test_id_never_contains_the_raw_token(self) -> None:
        secret_token = "extremely-sensitive-jwt-value"
        assert secret_token not in auth_session.session_id_for(secret_token)


class TestCreateAndGet:
    def test_missing_session_returns_none(self, fake_redis: FakeRedis) -> None:
        assert auth_session.get_cached_session("no-such-token", _settings()) is None

    def test_created_session_is_returned(self, fake_redis: FakeRedis) -> None:
        auth_session.create_session("tok-1", "USER1", "tms", "ADMIN_1", _settings())
        cached = auth_session.get_cached_session("tok-1", _settings())
        assert cached is not None
        assert cached.user_id == "USER1"
        assert cached.portal == "tms"
        assert cached.role == "ADMIN_1"

    def test_driver_session_has_no_role(self, fake_redis: FakeRedis) -> None:
        auth_session.create_session("tok-driver", "DRV1", "driver", None, _settings())
        cached = auth_session.get_cached_session("tok-driver", _settings())
        assert cached is not None
        assert cached.role is None

    def test_raw_token_is_never_stored_in_redis(self, fake_redis: FakeRedis) -> None:
        secret_token = "extremely-sensitive-jwt-value"
        auth_session.create_session(secret_token, "USER1", "tms", "ADMIN_1", _settings())
        for stored_value in fake_redis.raw().values():
            assert secret_token not in stored_value

    def test_session_key_uses_dedicated_namespace(self, fake_redis: FakeRedis) -> None:
        auth_session.create_session("tok-1", "USER1", "tms", "ADMIN_1", _settings())
        (key,) = fake_redis.raw().keys()
        assert key.startswith("session:setuhaul:")

    def test_redis_unavailable_returns_none_not_an_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(redis_client, "get_client", lambda: None)
        assert auth_session.get_cached_session("tok-1", _settings()) is None
        # create_session must also no-op silently, never raise.
        auth_session.create_session("tok-1", "USER1", "tms", "ADMIN_1", _settings())

    def test_corrupt_entry_is_treated_as_a_miss(self, fake_redis: FakeRedis) -> None:
        session_id = auth_session.session_id_for("tok-1")
        fake_redis.set(f"session:setuhaul:{session_id}", "not-json{")
        assert auth_session.get_cached_session("tok-1", _settings()) is None


class TestExpiry:
    def test_session_ttl_is_applied(self, fake_redis: FakeRedis, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, int | None] = {}
        real_set = fake_redis.set

        def _spy_set(key: str, value: str, ex: int | None = None, nx: bool = False):
            captured["ex"] = ex
            return real_set(key, value, ex=ex, nx=nx)

        monkeypatch.setattr(fake_redis, "set", _spy_set)
        auth_session.create_session("tok-1", "USER1", "tms", "ADMIN_1", _settings(session_ttl_seconds=300))
        assert captured["ex"] == 300

    def test_expired_session_is_a_miss(self, fake_redis: FakeRedis) -> None:
        auth_session.create_session("tok-1", "USER1", "tms", "ADMIN_1", _settings())
        fake_redis.expire_all()
        assert auth_session.get_cached_session("tok-1", _settings()) is None


class TestSlidingRefresh:
    def test_hit_within_threshold_does_not_rewrite(self, fake_redis: FakeRedis, monkeypatch: pytest.MonkeyPatch) -> None:
        auth_session.create_session("tok-1", "USER1", "tms", "ADMIN_1", _settings())
        set_calls = {"n": 0}
        real_set = fake_redis.set

        def _spy_set(*a, **k):
            set_calls["n"] += 1
            return real_set(*a, **k)

        monkeypatch.setattr(fake_redis, "set", _spy_set)
        auth_session.get_cached_session("tok-1", _settings(session_refresh_threshold_seconds=60))
        assert set_calls["n"] == 0

    def test_hit_past_threshold_rewrites_last_activity(self, fake_redis: FakeRedis, monkeypatch: pytest.MonkeyPatch) -> None:
        import time as real_time

        auth_session.create_session("tok-1", "USER1", "tms", "ADMIN_1", _settings())
        offset_module = SimpleNamespace(time=lambda: real_time.time() + 1000)
        monkeypatch.setattr(auth_session, "time", offset_module)

        set_calls = {"n": 0}
        real_set = fake_redis.set

        def _spy_set(*a, **k):
            set_calls["n"] += 1
            return real_set(*a, **k)

        monkeypatch.setattr(fake_redis, "set", _spy_set)
        cached = auth_session.get_cached_session("tok-1", _settings(session_refresh_threshold_seconds=60))
        assert cached is not None  # still returns the (pre-refresh) session data for this call
        assert set_calls["n"] == 1


class TestRevoke:
    def test_revoke_deletes_the_session(self, fake_redis: FakeRedis) -> None:
        auth_session.create_session("tok-1", "USER1", "tms", "ADMIN_1", _settings())
        auth_session.revoke_session("tok-1")
        assert auth_session.get_cached_session("tok-1", _settings()) is None

    def test_revoke_is_idempotent_on_a_never_cached_token(self, fake_redis: FakeRedis) -> None:
        auth_session.revoke_session("never-was-cached")  # must not raise

    def test_revoke_when_redis_unavailable_does_not_raise(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(redis_client, "get_client", lambda: None)
        auth_session.revoke_session("tok-1")


class TestCrossSessionIsolation:
    def test_two_users_tokens_never_collide(self, fake_redis: FakeRedis) -> None:
        auth_session.create_session("tok-user-a", "USER-A", "tms", "ADMIN_1", _settings())
        auth_session.create_session("tok-user-b", "USER-B", "tms", "AGENT_READER", _settings())

        cached_a = auth_session.get_cached_session("tok-user-a", _settings())
        cached_b = auth_session.get_cached_session("tok-user-b", _settings())
        assert cached_a is not None and cached_a.user_id == "USER-A"
        assert cached_b is not None and cached_b.user_id == "USER-B"

    def test_concurrent_sessions_for_the_same_user_are_independent(self, fake_redis: FakeRedis) -> None:
        # Same user, two devices/tabs -- two different Supabase access
        # tokens, so two independent cache entries. Revoking one (logout on
        # one device) must not affect the other.
        auth_session.create_session("device-1-token", "USER1", "tms", "ADMIN_1", _settings())
        auth_session.create_session("device-2-token", "USER1", "tms", "ADMIN_1", _settings())

        auth_session.revoke_session("device-1-token")

        assert auth_session.get_cached_session("device-1-token", _settings()) is None
        assert auth_session.get_cached_session("device-2-token", _settings()) is not None
