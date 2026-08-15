"""Unit tests for the shared Redis cache-aside helpers."""

from __future__ import annotations

import threading
import time
from unittest.mock import Mock

import pytest

from setuhaul.backend._testing.fake_redis import FakeRedis, FakeRedisCluster
from setuhaul.infrastructure import cache, redis_client


@pytest.fixture(autouse=True)
def _reset_client_cache():
    """``redis_client.get_client()`` memoizes a successfully-constructed
    client in module-level state (not ``lru_cache`` -- see its docstring for
    why) -- reset that state before every test so one test's monkeypatched
    client never leaks into the next."""
    redis_client.reset_client_cache()
    yield
    redis_client.reset_client_cache()


@pytest.fixture()
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    redis = FakeRedis()
    monkeypatch.setattr(redis_client, "get_client", lambda: redis)
    return redis


def test_get_json_miss_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(redis_client, "get_client", lambda: None)
    assert cache.get_json("cache:setuhaul:missing") is None
    # set_json/delete must also no-op silently, never raise, when Redis is unavailable.
    cache.set_json("cache:setuhaul:missing", {"a": 1}, 30)
    cache.delete("cache:setuhaul:missing")


def test_set_then_get_round_trips(fake_redis: FakeRedis) -> None:
    key = cache.shipment_key("SHP1")
    assert cache.get_json(key) is None
    cache.set_json(key, {"shipment_id": "SHP1", "current_status": "PLANNED"}, 30)
    assert cache.get_json(key) == {"shipment_id": "SHP1", "current_status": "PLANNED"}


def test_corrupt_entry_is_treated_as_a_miss(fake_redis: FakeRedis) -> None:
    key = cache.shipment_key("SHP1")
    fake_redis.set(key, "not-json{")
    assert cache.get_json(key) is None


def test_invalidate_shipment_clears_every_module_view(fake_redis: FakeRedis) -> None:
    shipment_id = "SHP1"
    cache.set_json(cache.shipment_key(shipment_id), {"a": 1}, 30)
    cache.set_json(cache.shipment_context_key(shipment_id), {"b": 1}, 30)
    cache.set_json(cache.dock_board_key(shipment_id), [{"c": 1}], 30)
    cache.set_json(cache.checkin_status_key(shipment_id), {"d": 1}, 30)

    cache.invalidate_shipment(shipment_id)

    for family in ("shipment", "shipment-context", "dock-board", "checkin"):
        assert fake_redis.get(cache._generation_key(family, shipment_id)) == "1"


def test_invalidate_dock_boards_sweeps_every_shipment(fake_redis: FakeRedis) -> None:
    cache.set_json(cache.dock_board_key("SHP1"), [{"a": 1}], 30)
    cache.set_json(cache.dock_board_key("SHP2"), [{"b": 1}], 30)
    cache.set_json(cache.reference_key("facilities"), [{"c": 1}], 300)

    cache.invalidate_dock_boards()

    assert fake_redis.get(cache._generation_key("dock-board", "global")) == "1"
    # Unrelated keys must survive the sweep.
    assert cache.get_json(cache.reference_key("facilities")) == [{"c": 1}]


def test_invalidate_shipments_lists_sweeps_every_scope_and_fingerprint(fake_redis: FakeRedis) -> None:
    cache.set_json(cache.shipments_list_key("all", "fp1"), [{"a": 1}], 30)
    cache.set_json(cache.shipments_list_key("FAC-JAI-01", "fp2"), [{"b": 1}], 30)

    cache.invalidate_shipments_lists()

    assert fake_redis.get(cache._generation_key("shipments-list", "global")) == "1"


def test_invalidate_vehicle(fake_redis: FakeRedis) -> None:
    cache.set_json(cache.vehicle_key("VEH1"), {"a": 1}, 30)
    cache.invalidate_vehicle("VEH1")
    assert fake_redis.get(cache._generation_key("vehicle", "VEH1")) == "1"


def test_invalidate_facility_sweeps_its_own_keys_and_every_facilities_list_page(fake_redis: FakeRedis) -> None:
    cache.set_json(cache.facility_key("FAC1"), {"a": 1}, 30)
    cache.set_json(cache.docks_key("FAC1"), [{"b": 1}], 30)
    cache.set_json(cache.reference_key("facilities:200:0"), [{"c": 1}], 300)
    cache.set_json(cache.reference_key("facilities:50:100"), [{"d": 1}], 300)
    cache.set_json(cache.reference_key("carriers"), [{"e": 1}], 300)

    cache.invalidate_facility("FAC1")

    assert fake_redis.get(cache._generation_key("facility", "FAC1")) == "1"
    assert fake_redis.get(cache._generation_key("docks", "FAC1")) == "1"
    assert fake_redis.get(cache._generation_key("facility-list", "global")) == "1"
    # Unrelated reference data must survive.
    assert cache.get_json(cache.reference_key("carriers")) == [{"e": 1}]


def test_invalidate_carriers(fake_redis: FakeRedis) -> None:
    cache.set_json(cache.reference_key("carriers"), [{"a": 1}], 300)
    cache.invalidate_carriers()
    assert fake_redis.get(cache._generation_key("reference", "carriers")) == "1"


def test_invalidate_change_requests(fake_redis: FakeRedis) -> None:
    cache.set_json(cache.change_requests_key(None), [{"a": 1}], 30)
    cache.set_json(cache.change_requests_key("PENDING"), [{"b": 1}], 30)
    cache.invalidate_change_requests()
    assert fake_redis.get(cache._generation_key("change-requests", "global")) == "1"


class TestGetOrSet:
    def test_hit_never_calls_fetch(self, fake_redis: FakeRedis) -> None:
        key = cache.shipment_key("SHP1")
        cache.set_json(key, {"cached": True}, 30)
        fetch = Mock(side_effect=AssertionError("fetch must not be called on a cache hit"))

        assert cache.get_or_set(key, 30, fetch) == {"cached": True}
        fetch.assert_not_called()

    def test_miss_acquires_lock_fetches_once_caches_and_releases(self, fake_redis: FakeRedis) -> None:
        key = cache.shipment_key("SHP1")
        fetch = Mock(return_value={"from": "fetch"})

        result = cache.get_or_set(key, 30, fetch)

        assert result == {"from": "fetch"}
        fetch.assert_called_once()
        assert cache.get_json(key) == {"from": "fetch"}
        assert fake_redis.get(cache._lock_key_for(key)) is None

    def test_falls_back_to_fetch_when_redis_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(redis_client, "get_client", lambda: None)
        fetch = Mock(return_value={"from": "fetch"})

        assert cache.get_or_set("some:key", 30, fetch) == {"from": "fetch"}
        fetch.assert_called_once()

    def test_waiter_picks_up_holders_result_without_calling_fetch_itself(
        self, fake_redis: FakeRedis, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Simulates the stampede-protection path: another process already
        holds the lock, and populates the key shortly after -- the waiter
        must return that value instead of issuing its own duplicate fetch."""
        monkeypatch.setattr(cache, "LOCK_POLL_INTERVAL_SECONDS", 0.01)
        monkeypatch.setattr(cache, "LOCK_WAIT_SECONDS", 1.0)
        key = cache.shipment_key("SHP1")
        fake_redis.set(cache._lock_key_for(key), "other-process-token", nx=True)

        def _delayed_holder_write() -> None:
            time.sleep(0.05)
            cache.set_json(key, {"from": "holder"}, 30)

        writer = threading.Thread(target=_delayed_holder_write)
        writer.start()
        try:
            fetch = Mock(side_effect=AssertionError("waiter must not fetch once the holder's value shows up"))
            result = cache.get_or_set(key, 30, fetch)
        finally:
            writer.join()

        assert result == {"from": "holder"}
        fetch.assert_not_called()

    def test_waiter_fails_open_to_its_own_fetch_when_the_lock_is_never_released(
        self, fake_redis: FakeRedis, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A crashed/stuck holder must never turn into a hang for everyone
        else -- the bounded wait times out and the waiter fetches directly."""
        monkeypatch.setattr(cache, "LOCK_POLL_INTERVAL_SECONDS", 0.01)
        monkeypatch.setattr(cache, "LOCK_WAIT_SECONDS", 0.05)
        key = cache.shipment_key("SHP1")
        fake_redis.set(cache._lock_key_for(key), "stuck-holder-token", nx=True)

        fetch = Mock(return_value={"from": "fetch"})
        result = cache.get_or_set(key, 30, fetch)

        assert result == {"from": "fetch"}
        fetch.assert_called_once()

    def test_lock_loss_recovery_never_writes_via_bare_set_json_and_refetches_at_most_once(
        self, fake_redis: FakeRedis, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Final Correction A: when the commit-and-release script reports the
        lock was lost mid-fetch, the code must never call set_json directly,
        must never attempt a second lock, and must issue at most one extra
        uncached fetch -- never a retry loop."""
        key = cache.shipment_key("SHP1")
        fetch_calls = {"n": 0}

        def _fetch() -> dict[str, str]:
            fetch_calls["n"] += 1
            if fetch_calls["n"] == 1:
                # Simulate a concurrent invalidation racing in mid-fetch:
                # someone else deletes our lock before we can commit.
                fake_redis.delete(cache._lock_key_for(key))
            return {"from": f"fetch-{fetch_calls['n']}"}

        set_json_spy = Mock(wraps=cache.set_json)
        monkeypatch.setattr(cache, "set_json", set_json_spy)

        result = cache.get_or_set(key, 30, _fetch)

        assert result == {"from": "fetch-2"}
        assert fetch_calls["n"] == 2  # exactly one bounded re-fetch, no loop
        set_json_spy.assert_not_called()  # the recovery path never writes bare
        # The uncached re-fetch result must never end up cached either.
        assert cache.get_json(key) is None

    def test_lock_loss_recovery_returns_a_concurrently_repopulated_value_without_a_second_fetch(
        self, fake_redis: FakeRedis, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If someone else correctly repopulates the key while our lock was
        lost, we must pick that up on the re-read rather than doing a second
        (redundant) uncached fetch."""
        key = cache.shipment_key("SHP1")
        fetch_calls = {"n": 0}

        def _fetch() -> dict[str, str]:
            fetch_calls["n"] += 1
            fake_redis.delete(cache._lock_key_for(key))
            # Simulate another process winning the race and correctly
            # repopulating the key through its own protected path.
            cache.set_json(key, {"from": "someone-else"}, 30)
            return {"from": "ours-should-be-discarded"}

        result = cache.get_or_set(key, 30, _fetch)

        assert result == {"from": "someone-else"}
        assert fetch_calls["n"] == 1


class TestHashSlotAffinity:
    """v2 item 2: every key builder's data key and its own stampede-lock
    companion must always hash to the same Redis Cluster slot, verified via
    redis-py's real key_slot() (the actual algorithm ElastiCache/Valkey
    Cluster uses), not a reimplementation of it."""

    @pytest.mark.parametrize(
        "key",
        [
            cache.shipment_key("SHP1"),
            cache.shipment_context_key("SHP1"),
            cache.shipments_list_key("FAC-JAI-01", "fp1"),
            cache.dock_board_key("SHP1"),
            cache.change_requests_key("PENDING"),
            cache.checkin_status_key("SHP1"),
            cache.driver_profile_key("DRV1"),
            cache.vehicle_key("VEH1"),
            cache.facility_key("FAC1"),
            cache.docks_key("FAC1"),
            cache.reference_key("carriers"),
        ],
    )
    def test_data_key_and_lock_key_share_a_slot(self, key: str) -> None:
        lock_key = cache._lock_key_for(key)
        assert redis_client._key_slot(key) == redis_client._key_slot(lock_key)

    def test_different_entities_are_not_forced_onto_the_same_slot(self) -> None:
        # Not a correctness requirement (nothing breaks if they collide), but
        # confirms the hash-tag design isn't accidentally collapsing every
        # key onto one slot, which would defeat cluster sharding entirely.
        slots = {redis_client._key_slot(cache.shipment_key(f"SHP{i}")) for i in range(1, 20)}
        assert len(slots) > 1


class TestCrossSlotSafety:
    """v3 Correction 3: SCAN-sweep and multi-key invalidation must group by
    cluster hash slot before issuing DEL, since different entities are
    deliberately on different slots. FakeRedisCluster raises the same
    CROSSSLOT error a real cluster would if that grouping is ever wrong."""

    @pytest.fixture()
    def fake_cluster(self, monkeypatch: pytest.MonkeyPatch) -> FakeRedisCluster:
        redis = FakeRedisCluster()
        monkeypatch.setattr(redis_client, "get_client", lambda: redis)
        return redis

    def test_the_fakes_own_guard_actually_raises_on_multi_slot_keys(self, fake_cluster: FakeRedisCluster) -> None:
        # Proves the fake's guard is real (not a no-op) before trusting the
        # negative result of the tests below.
        from redis.exceptions import ClusterCrossSlotError

        multi_slot_keys = [cache.shipment_key(f"SHP{i}") for i in range(1, 50)]
        assert len({redis_client._key_slot(k) for k in multi_slot_keys}) > 1
        with pytest.raises(ClusterCrossSlotError):
            fake_cluster.delete(*multi_slot_keys)

    def test_cache_delete_groups_multi_slot_keys_and_never_raises(self, fake_cluster: FakeRedisCluster) -> None:
        keys = [cache.shipment_key(f"SHP{i}") for i in range(1, 50)]
        assert len({redis_client._key_slot(k) for k in keys}) > 1  # confirm the test is meaningful
        for k in keys:
            fake_cluster._store[k] = '{"a": 1}'

        cache.delete(*keys)  # must not raise CROSSSLOT

        for k in keys:
            assert k not in fake_cluster._store

    def test_invalidate_shipment_is_crossslot_safe(self, fake_cluster: FakeRedisCluster) -> None:
        shipment_id = "SHP1"
        cache.set_json(cache.shipment_key(shipment_id), {"a": 1}, 30)
        cache.set_json(cache.shipment_context_key(shipment_id), {"b": 1}, 30)
        cache.set_json(cache.dock_board_key(shipment_id), {"c": 1}, 30)
        cache.set_json(cache.checkin_status_key(shipment_id), {"d": 1}, 30)

        cache.invalidate_shipment(shipment_id)  # must not raise CROSSSLOT

        assert fake_cluster.get(cache._generation_key("shipment", shipment_id)) == "1"

    def test_invalidate_dock_boards_sweep_is_crossslot_safe(self, fake_cluster: FakeRedisCluster) -> None:
        for i in range(1, 30):
            cache.set_json(cache.dock_board_key(f"SHP{i}"), [{"a": i}], 30)

        cache.invalidate_dock_boards()  # must not raise CROSSSLOT

        assert fake_cluster.get(cache._generation_key("dock-board", "global")) == "1"

    def test_invalidate_shipments_lists_sweep_is_crossslot_safe(self, fake_cluster: FakeRedisCluster) -> None:
        for i in range(1, 30):
            cache.set_json(cache.shipments_list_key(f"FAC{i}", "fp"), [{"a": i}], 30)

        cache.invalidate_shipments_lists()  # must not raise CROSSSLOT

        assert fake_cluster.get(cache._generation_key("shipments-list", "global")) == "1"

    def test_invalidate_facility_sweep_is_crossslot_safe(self, fake_cluster: FakeRedisCluster) -> None:
        for i in range(1, 30):
            cache.set_json(cache.reference_key(f"facilities:200:{i}"), [{"a": i}], 300)

        cache.invalidate_facility("FAC1")  # must not raise CROSSSLOT


class TestDeterministicTtlJitter:
    """v2 item 7: jitter must be testable with exact-value assertions via a
    mocked RNG, not statistical/range-based assertions."""

    def test_exact_jittered_ttl_from_a_mocked_positive_jitter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cache._jitter_rng, "uniform", lambda a, b: 0.10)
        assert cache._jittered_ttl(20) == 22

    def test_exact_jittered_ttl_from_a_mocked_negative_jitter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cache._jitter_rng, "uniform", lambda a, b: -0.10)
        assert cache._jittered_ttl(20) == 18

    def test_jitter_never_floors_below_one_second(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cache._jitter_rng, "uniform", lambda a, b: -0.99)
        assert cache._jittered_ttl(1) == 1

    def test_set_json_uses_jittered_ttl(self, fake_redis: FakeRedis, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, int] = {}
        real_set = fake_redis.set

        def _spy_set(key: str, value: str, ex: int | None = None, nx: bool = False):
            captured["ex"] = ex
            return real_set(key, value, ex=ex, nx=nx)

        monkeypatch.setattr(fake_redis, "set", _spy_set)
        monkeypatch.setattr(cache._jitter_rng, "uniform", lambda a, b: 0.10)

        cache.set_json(cache.shipment_key("SHP1"), {"a": 1}, 20)

        assert captured["ex"] == 22


def test_scopes_and_query_hashes_are_deterministic_and_non_identical(fake_redis: FakeRedis) -> None:
    assert cache.canonical_query_hash({"limit": 10, "status": "OPEN"}) == cache.canonical_query_hash(
        {"status": "OPEN", "limit": 10}
    )
    role_a = cache.CacheScope.role("ADMIN_1")
    role_b = cache.CacheScope.role("AGENT_READER")
    query_hash = cache.canonical_query_hash({"limit": 10})
    key_a = cache._data_key(role_a, "shipments-list", "global", "0", query_hash)
    key_b = cache._data_key(role_b, "shipments-list", "global", "0", query_hash)
    assert key_a != key_b
    assert "ADMIN_1" not in key_a
    assert "AGENT_READER" not in key_b


def test_none_results_are_not_negative_cached(fake_redis: FakeRedis) -> None:
    scope = cache.CacheScope.user("driver-1")
    assert cache.get_or_set_scoped(scope, "shipment", "missing", {}, 20, lambda: None) is None
    assert not any(":data:" in key for key in fake_redis.raw())


def test_generation_prevents_stale_reader_repopulation_after_invalidation(fake_redis: FakeRedis) -> None:
    scope = cache.CacheScope.role("ADMIN_1")
    started = threading.Event()
    release = threading.Event()
    result: list[dict[str, str]] = []

    def stale_fetch() -> dict[str, str]:
        started.set()
        assert release.wait(1)
        return {"version": "before-write"}

    thread = threading.Thread(
        target=lambda: result.append(cache.get_or_set_scoped(scope, "shipment", "SHP1", {}, 20, stale_fetch))
    )
    thread.start()
    assert started.wait(1)
    cache.invalidate_shipment("SHP1")
    release.set()
    thread.join(timeout=2)
    assert result == [{"version": "before-write"}]

    fresh_calls = {"count": 0}

    def fresh_fetch() -> dict[str, str]:
        fresh_calls["count"] += 1
        return {"version": "after-write"}

    assert cache.get_or_set_scoped(scope, "shipment", "SHP1", {}, 20, fresh_fetch) == {"version": "after-write"}
    assert fresh_calls["count"] == 1
