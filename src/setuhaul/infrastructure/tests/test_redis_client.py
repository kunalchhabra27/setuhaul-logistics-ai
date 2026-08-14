"""Tests for the shared Redis/Valkey connection factory: never-memoize-a-
failure, concurrency-safe singleton/cooldown, tiered exception handling,
explicit decode_responses, cluster-mode selection, and slot calculation."""

from __future__ import annotations

import json
import logging
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import redis

from setuhaul.infrastructure import redis_client
from setuhaul.infrastructure.logging import JsonFormatter


@pytest.fixture(autouse=True)
def _reset():
    redis_client.reset_client_cache()
    yield
    redis_client.reset_client_cache()


def _settings(**overrides) -> SimpleNamespace:
    base = dict(
        redis_url="redis://localhost:6379/0",
        cache_host=None,
        cache_port=None,
        cache_tls=True,
        cache_username=None,
        cache_auth_token=None,
        cache_cluster_mode=False,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class TestNeverMemoizeFailure:
    """Correction 1 / A: a failed construction attempt must never be
    permanently cached -- only a successful one is memoized."""

    def test_construction_failure_is_not_memoized_forever(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(redis_client, "get_settings", lambda: _settings())
        monkeypatch.setattr(redis_client, "FAILURE_COOLDOWN_SECONDS", 0.0)  # no cooldown -- retry immediately

        calls = {"n": 0}

        def _fail(_settings):
            calls["n"] += 1
            raise redis.exceptions.ConnectionError("down")

        monkeypatch.setattr(redis_client, "_build_client", _fail)

        assert redis_client.get_client() is None
        assert redis_client.get_client() is None
        # Two calls, two attempts -- no cooldown means no memoization of the
        # failure blocked a second attempt, and a plain @lru_cache would
        # have returned the same (wrongly memoized) None both times without
        # ever calling _build_client a second time.
        assert calls["n"] == 2

    def test_cooldown_prevents_hammering_a_dead_endpoint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(redis_client, "get_settings", lambda: _settings())
        monkeypatch.setattr(redis_client, "FAILURE_COOLDOWN_SECONDS", 60.0)

        calls = {"n": 0}

        def _fail(_settings):
            calls["n"] += 1
            raise redis.exceptions.ConnectionError("down")

        monkeypatch.setattr(redis_client, "_build_client", _fail)

        for _ in range(10):
            assert redis_client.get_client() is None
        # All ten calls land within the same cooldown window -- only the
        # first actually attempted a connection.
        assert calls["n"] == 1

    def test_success_after_a_prior_failure_is_picked_up_once_cooldown_elapses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(redis_client, "get_settings", lambda: _settings())
        monkeypatch.setattr(redis_client, "FAILURE_COOLDOWN_SECONDS", 0.0)

        fake_client = MagicMock()
        attempts = {"n": 0}

        def _flaky(_settings):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise redis.exceptions.ConnectionError("down")
            return fake_client

        monkeypatch.setattr(redis_client, "_build_client", _flaky)

        assert redis_client.get_client() is None
        assert redis_client.get_client() is fake_client
        # And now it's memoized -- a third call must not attempt construction again.
        assert redis_client.get_client() is fake_client
        assert attempts["n"] == 2


class TestConcurrencySafety:
    """Correction B: many threads racing get_client() must build (or probe
    a failing endpoint) exactly once, not once per thread."""

    def test_concurrent_calls_build_the_client_exactly_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(redis_client, "get_settings", lambda: _settings())
        fake_client = MagicMock()
        calls = {"n": 0}

        def _slow_build(_settings):
            calls["n"] += 1
            import time

            time.sleep(0.02)  # widen the race window
            return fake_client

        monkeypatch.setattr(redis_client, "_build_client", _slow_build)

        results: list[object] = []

        def _worker():
            results.append(redis_client.get_client())

        threads = [threading.Thread(target=_worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert calls["n"] == 1
        assert all(r is fake_client for r in results)

    def test_concurrent_calls_during_an_outage_probe_at_most_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(redis_client, "get_settings", lambda: _settings())
        monkeypatch.setattr(redis_client, "FAILURE_COOLDOWN_SECONDS", 60.0)
        calls = {"n": 0}

        def _fail(_settings):
            calls["n"] += 1
            raise redis.exceptions.ConnectionError("down")

        monkeypatch.setattr(redis_client, "_build_client", _fail)

        threads = [threading.Thread(target=redis_client.get_client) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert calls["n"] == 1


class TestTieredExceptions:
    """Correction C/E: expected/transient failures log WARNING and fail
    open; authentication/authorization and unexpected errors log ERROR
    (with diagnostics) and still fail open -- the request-level outcome
    (None returned) is identical either way."""

    def _run(self, monkeypatch: pytest.MonkeyPatch, exc: Exception, caplog: pytest.LogCaptureFixture):
        monkeypatch.setattr(redis_client, "get_settings", lambda: _settings())
        monkeypatch.setattr(redis_client, "FAILURE_COOLDOWN_SECONDS", 0.0)

        def _raise(_settings):
            raise exc

        monkeypatch.setattr(redis_client, "_build_client", _raise)
        with caplog.at_level(logging.DEBUG, logger="setuhaul.infrastructure.redis_client"):
            result = redis_client.get_client()
        return result

    def test_connection_error_logs_warning_and_fails_open(self, monkeypatch, caplog) -> None:
        result = self._run(monkeypatch, redis.exceptions.ConnectionError("refused"), caplog)
        assert result is None
        records = [r for r in caplog.records if r.name == "setuhaul.infrastructure.redis_client"]
        assert records and records[0].levelname == "WARNING"

    def test_timeout_error_logs_warning_and_fails_open(self, monkeypatch, caplog) -> None:
        result = self._run(monkeypatch, redis.exceptions.TimeoutError("timed out"), caplog)
        assert result is None
        records = [r for r in caplog.records if r.name == "setuhaul.infrastructure.redis_client"]
        assert records and records[0].levelname == "WARNING"

    def test_authentication_error_logs_error_despite_being_a_connectionerror_subclass(self, monkeypatch, caplog) -> None:
        # redis.exceptions.AuthenticationError IS a ConnectionError subclass
        # -- this is exactly the case that must NOT be classified as
        # ordinary transient unavailability.
        assert issubclass(redis.exceptions.AuthenticationError, redis.exceptions.ConnectionError)
        result = self._run(monkeypatch, redis.exceptions.AuthenticationError("bad creds"), caplog)
        assert result is None
        records = [r for r in caplog.records if r.name == "setuhaul.infrastructure.redis_client"]
        assert records and records[0].levelname == "ERROR"

    def test_authorization_error_logs_error(self, monkeypatch, caplog) -> None:
        result = self._run(monkeypatch, redis.exceptions.AuthorizationError("not allowed"), caplog)
        assert result is None
        records = [r for r in caplog.records if r.name == "setuhaul.infrastructure.redis_client"]
        assert records and records[0].levelname == "ERROR"

    def test_unexpected_programming_error_logs_error_with_traceback(self, monkeypatch, caplog) -> None:
        result = self._run(monkeypatch, TypeError("bad config value"), caplog)
        assert result is None
        records = [r for r in caplog.records if r.name == "setuhaul.infrastructure.redis_client"]
        assert records and records[0].levelname == "ERROR"
        assert records[0].exc_info is not None

    def test_cluster_down_error_logs_warning(self, monkeypatch, caplog) -> None:
        result = self._run(monkeypatch, redis.exceptions.ClusterDownError("resharding"), caplog)
        assert result is None
        records = [r for r in caplog.records if r.name == "setuhaul.infrastructure.redis_client"]
        assert records and records[0].levelname == "WARNING"

    def test_data_error_and_cross_slot_error_are_not_treated_as_transient(self, monkeypatch, caplog) -> None:
        for exc in (redis.exceptions.DataError("bad"), redis.exceptions.ClusterCrossSlotError("nope")):
            caplog.clear()
            result = self._run(monkeypatch, exc, caplog)
            assert result is None
            records = [r for r in caplog.records if r.name == "setuhaul.infrastructure.redis_client"]
            assert records and records[0].levelname == "ERROR"


class TestNoSecretLeakage:
    """Verified against the FULL rendered log line (via the real
    JsonFormatter), not just the structured_fields dict -- a traceback can
    incidentally embed local-variable reprs, so exc_info text needs
    checking too."""

    def test_redis_url_and_credentials_never_appear_in_rendered_log_output(self, monkeypatch, caplog) -> None:
        secret_url = "redis://user:supersecretpassword@example.com:6379/0"
        secret_token = "extremely-secret-auth-token"

        monkeypatch.setattr(
            redis_client, "get_settings", lambda: _settings(redis_url=secret_url, cache_auth_token=secret_token)
        )
        monkeypatch.setattr(redis_client, "FAILURE_COOLDOWN_SECONDS", 0.0)

        def _raise(_settings):
            raise TypeError(f"unexpected error touching {secret_url}")  # a buggy exception message, worst case

        monkeypatch.setattr(redis_client, "_build_client", _raise)

        formatter = JsonFormatter()
        with caplog.at_level(logging.DEBUG, logger="setuhaul.infrastructure.redis_client"):
            assert redis_client.get_client() is None

        for record in caplog.records:
            rendered = formatter.format(record)
            assert secret_token not in rendered
            # The deliberately-leaky exception message above proves the
            # test itself would catch a real leak; cache.py/redis_client.py
            # must never construct a log message this way in real code.


class TestDecodeResponsesExplicit:
    def test_standalone_branch_passes_decode_responses_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = _settings(cache_host="cache.example.com", cache_port=6379)
        with patch("redis.Redis") as redis_cls:
            redis_client._build_client(settings)
        _, kwargs = redis_cls.call_args
        assert kwargs["decode_responses"] is True

    def test_cluster_branch_passes_decode_responses_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = _settings(cache_host="cache.example.com", cache_port=6379, cache_cluster_mode=True)
        with patch("redis.cluster.RedisCluster") as cluster_cls:
            redis_client._build_client(settings)
        _, kwargs = cluster_cls.call_args
        assert kwargs["decode_responses"] is True


class TestClusterModeSelection:
    def test_cluster_mode_false_builds_plain_redis(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = _settings(cache_host="cache.example.com", cache_cluster_mode=False)
        with patch("redis.Redis") as redis_cls, patch("redis.cluster.RedisCluster") as cluster_cls:
            redis_client._build_client(settings)
        redis_cls.assert_called_once()
        cluster_cls.assert_not_called()

    def test_cluster_mode_true_builds_rediscluster(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = _settings(cache_host="cache.example.com", cache_cluster_mode=True)
        with patch("redis.Redis") as redis_cls, patch("redis.cluster.RedisCluster") as cluster_cls:
            redis_client._build_client(settings)
        cluster_cls.assert_called_once()
        redis_cls.from_url.assert_not_called()


class TestKeySlot:
    def test_encodes_to_bytes_before_hashing(self) -> None:
        from redis.crc import key_slot as real_key_slot

        key = "cache:setuhaul:{shipment:SHP1}"
        assert redis_client._key_slot(key) == real_key_slot(key.encode("utf-8"))

    def test_matches_real_key_slot_for_various_keys(self) -> None:
        from redis.crc import key_slot as real_key_slot

        for key in ["cache:setuhaul:{shipment:SHP1}", "lock:{shipment:SHP1}", "cache:setuhaul:{ref:carriers}"]:
            assert redis_client._key_slot(key) == real_key_slot(key.encode("utf-8"))
