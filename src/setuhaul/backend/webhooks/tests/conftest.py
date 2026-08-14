"""Shared fixtures for webhooks tests."""

from __future__ import annotations

import pytest

from setuhaul.infrastructure import redis_client


@pytest.fixture(autouse=True)
def _no_real_redis_by_default(monkeypatch: pytest.MonkeyPatch):
    """See tms/tests/conftest.py's fixture of the same name -- REDIS_URL in
    .env points at a real shared instance; disable it by default so this
    module's tests exercise invalidation against an in-memory fake, not the
    real cache. test_api.py's `fake_redis` fixture opts back in per-test."""
    redis_client.reset_client_cache()
    monkeypatch.setattr(redis_client, "get_client", lambda: None)
    yield
    redis_client.reset_client_cache()
