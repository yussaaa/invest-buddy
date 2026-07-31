"""Unit tests for the two-tier market data cache.

L2 is monkeypatched to an in-memory dict, so these run with no Redis and no
network — `make test` must stay offline.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.services import cache


class FakeRedis:
    """Stand-in for app.cache.redis_client with the same coroutine surface."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.locks: set[str] = set()
        self.get_calls = 0
        self.fail = False

    async def cache_get(self, key):
        self.get_calls += 1
        if self.fail:
            return None
        raw = self.store.get(key)
        return json.loads(raw) if raw else None

    async def cache_get_many(self, keys):
        if self.fail:
            return {}
        return {k: json.loads(self.store[k]) for k in keys if k in self.store}

    async def cache_set(self, key, value, ttl=300):
        if self.fail:
            return
        self.store[key] = json.dumps(value, default=str)

    async def cache_set_many(self, mapping, ttl=300):
        if self.fail:
            return
        for key, value in mapping.items():
            self.store[key] = json.dumps(value, default=str)

    async def cache_delete(self, key):
        self.store.pop(key, None)

    async def try_lock(self, key, ttl):
        if key in self.locks:
            return False
        self.locks.add(key)
        return True

    async def unlock(self, key):
        self.locks.discard(key)


@pytest.fixture
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(cache, "redis_client", fake)
    cache._CACHE.clear()
    cache._locks.clear()
    yield fake
    cache._CACHE.clear()
    cache._locks.clear()


async def test_l1_hit_does_not_touch_l2(fake_redis):
    calls = []

    async def produce():
        calls.append(1)
        return {"v": 1}

    await cache.cached_async("quote:AAPL", 60, produce)
    baseline = fake_redis.get_calls

    await cache.cached_async("quote:AAPL", 60, produce)

    assert len(calls) == 1
    assert fake_redis.get_calls == baseline, "L1 hit should not read Redis"


async def test_l2_hit_preserves_original_timestamp(fake_redis, monkeypatch):
    """Staleness must stay bounded by the TTL across tiers.

    A pod reading from L2 has to keep the value's original production time; if
    it stamped `now`, a 10s value could live 10s in Redis then another 10s in
    L1.
    """
    clock = {"t": 1000.0}
    monkeypatch.setattr(cache.time, "time", lambda: clock["t"])

    async def produce():
        return {"price": 100}

    # Pod A produces at t=1000.
    await cache.cached_async("quote:AAPL", 10, produce)

    # Pod B (empty L1, shared L2) reads at t=1009 — still inside the TTL.
    cache._CACHE.clear()
    clock["t"] = 1009.0
    assert await cache.cached_async("quote:AAPL", 10, produce) == {"price": 100}

    # At t=1011 the value is 11s old. It must be gone from both tiers, not
    # revived by pod B's read.
    clock["t"] = 1011.0
    calls = []

    async def produce_again():
        calls.append(1)
        return {"price": 200}

    assert await cache.cached_async("quote:AAPL", 10, produce_again) == {"price": 200}
    assert len(calls) == 1, "expired value was served past its TTL"


async def test_should_cache_gates_both_tiers(fake_redis):
    async def produce():
        return {"partial": True}

    await cache.cached_async("breadth", 60, produce, should_cache=lambda v: False)

    assert cache.peek("breadth") is None
    assert fake_redis.store == {}, "rejected value must not reach Redis either"


async def test_redis_down_still_serves(fake_redis):
    fake_redis.fail = True
    calls = []

    async def produce():
        calls.append(1)
        return {"ok": True}

    assert await cache.cached_async("overview", 60, produce) == {"ok": True}
    # L1 still works, so a second call is served without refetching.
    assert await cache.cached_async("overview", 60, produce) == {"ok": True}
    assert len(calls) == 1


async def test_single_flight_collapses_concurrent_misses(fake_redis):
    calls = []

    async def produce():
        calls.append(1)
        await asyncio.sleep(0.05)  # long enough for the racers to pile up
        return {"n": len(calls)}

    results = await asyncio.gather(
        *[cache.cached_async("breadth", 60, produce) for _ in range(5)]
    )

    assert len(calls) == 1, "cold key should be fetched once, not once per caller"
    assert all(r == {"n": 1} for r in results)


async def test_stale_fallback_when_upstream_fails(fake_redis):
    async def good():
        return {"price": 100}

    await cache.cached_async("quote:AAPL", 0.01, good)
    await asyncio.sleep(0.02)  # let it expire

    async def broken():
        raise RuntimeError("YFRateLimitError")

    result = await cache.cached_async("quote:AAPL", 0.01, broken)

    assert result["price"] == 100
    assert result["stale"] is True


async def test_error_propagates_when_nothing_cached(fake_redis):
    async def broken():
        raise RuntimeError("upstream down")

    with pytest.raises(RuntimeError):
        await cache.cached_async("quote:NEW", 60, broken)


async def test_batch_helpers_round_trip(fake_redis):
    await cache.put_many({"quote:A": {"last": 1}, "quote:B": {"last": 2}}, 60)

    cache._CACHE.clear()  # force the L2 path
    found = await cache.get_fresh_many(["quote:A", "quote:B", "quote:C"], 60)

    assert found == {"quote:A": {"last": 1}, "quote:B": {"last": 2}}


async def test_long_keys_are_hashed(fake_redis):
    key = "events:" + ",".join(f"SYM{i}" for i in range(100))
    assert len(cache._redis_key(key)) < 80
    assert cache._redis_key(key).startswith(f"{cache.NAMESPACE}events:")


def test_key_kind_is_prefix_only():
    # Guards metric cardinality: never one series per symbol.
    assert cache.key_kind("quote:AAPL") == "quote"
    assert cache.key_kind("hist:AAPL:1Y") == "hist"
    assert cache.key_kind("breadth") == "breadth"


@pytest.mark.parametrize(
    "payload",
    [
        {"symbol": "AAPL", "last": 333.43, "volume": 55_000_000, "change": -1.2},
        {"indices": [{"label": "S&P 500", "sparkline": [1.0, 2.0]}]},
        {"candles": [{"time": "2026-07-30", "open": 1.0, "volume": 0}]},
        {"rsi": {"current_rsi": 61.66, "zone": "neutral", "historical": [60.2]}},
    ],
)
def test_payloads_survive_json_round_trip(payload):
    """L1 returns native objects, L2 returns whatever survived JSON.

    Any payload that changes type through serialisation would behave
    differently depending on which tier answered.
    """
    assert json.loads(json.dumps(payload, default=str)) == payload
