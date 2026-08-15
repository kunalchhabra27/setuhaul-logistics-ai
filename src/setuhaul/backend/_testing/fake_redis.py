"""A minimal in-memory stand-in for the subset of the ``redis`` client API
``setuhaul.infrastructure.cache`` actually uses: ``get``, ``set`` (with
``ex=``/``nx=``), ``delete``, ``scan_iter``, ``eval`` (the two stampede-lock
Lua scripts). Lets cache tests exercise real hit/miss/TTL/invalidation/
locking behavior without a real Redis server.

TTL is tracked but not actively expired by a background clock -- tests that
care about expiry call ``expire_all()`` to simulate time passing, rather than
sleeping in the test suite.
"""

from __future__ import annotations

import fnmatch
from typing import Any


class FakeRedis:
    def __init__(self):
        self._store: dict[str, str] = {}

    def ping(self) -> bool:
        return True

    def get(self, key: str) -> str | None:
        return self._store.get(key)

    def set(self, key: str, value: str, ex: int | None = None, nx: bool = False) -> bool:
        # ex (TTL seconds) is accepted for signature compatibility but not
        # enforced here -- see expire_all() for simulating expiry. nx=True
        # mirrors real Redis's SET ... NX: only set (and return True) if the
        # key doesn't already exist -- this is what cache.get_or_set()'s
        # stampede lock relies on.
        if nx and key in self._store:
            return False
        self._store[key] = value
        return True

    def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            if key in self._store:
                del self._store[key]
                removed += 1
        return removed

    def incr(self, key: str) -> int:
        value = int(self._store.get(key, "0")) + 1
        self._store[key] = str(value)
        return value

    def scan_iter(self, match: str, count: int = 200):
        for key in list(self._store.keys()):
            if fnmatch.fnmatch(key, match):
                yield key

    def eval(self, script: str, numkeys: int, *keys_and_args: Any) -> int:
        """Recognizes cache.py's two specific Lua scripts by content and
        emulates their exact atomic-ownership-check behavior in plain
        Python. This is a test double's job -- behavioral equivalence for
        *our* scripts, not a general Lua interpreter."""
        from setuhaul.infrastructure.cache import _COMMIT_AND_RELEASE_SCRIPT, _RELEASE_LOCK_SCRIPT

        keys = keys_and_args[:numkeys]
        args = keys_and_args[numkeys:]

        if script == _RELEASE_LOCK_SCRIPT:
            (lock_key,) = keys
            (token,) = args
            if self._store.get(lock_key) == token:
                del self._store[lock_key]
                return 1
            return 0

        if script == _COMMIT_AND_RELEASE_SCRIPT:
            data_key, lock_key = keys
            token, value, ttl = args
            if self._store.get(lock_key) == token:
                self._store[data_key] = value
                self._store.pop(lock_key, None)
                return 1
            return 0

        raise NotImplementedError(f"FakeRedis.eval: unrecognized script: {script!r}")

    def expire_all(self) -> None:
        """Simulate every key's TTL having elapsed."""
        self._store.clear()

    def __len__(self) -> int:
        return len(self._store)

    def raw(self) -> dict[str, Any]:
        return dict(self._store)


class FakeRedisCluster(FakeRedis):
    """Behavioral guard for CROSSSLOT-prevention tests. A real Redis Cluster
    rejects a multi-key command (e.g. ``DEL``) whose keys don't all hash to
    the same slot -- this fake raises the same way, so a test can actually
    prove ``cache.py``'s slot-grouping prevents that error, rather than
    trusting the grouping code without ever exercising the failure it
    exists to avoid. Everything else is identical to ``FakeRedis``.
    """

    def delete(self, *keys: str) -> int:
        from redis.exceptions import ClusterCrossSlotError

        from setuhaul.infrastructure.redis_client import _key_slot

        if len({_key_slot(k) for k in keys}) > 1:
            raise ClusterCrossSlotError(f"CROSSSLOT keys don't hash to the same slot: {keys}")
        return super().delete(*keys)
