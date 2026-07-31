"""Two-tier TTL cache for the UI-facing market services.

L1 is a dict inside this process — sub-microsecond, but private to one replica
and lost on restart. L2 is Redis, shared by every replica and surviving
deploys. A lookup walks L1 → L2 → upstream, filling the tiers above on the way
back, so the cost of a fetch is paid once per cluster per TTL instead of once
per pod per restart.

Two things make the tiering honest:

* Values carry the timestamp they were produced at, not the time this pod read
  them. Re-stamping on an L2 hit would let a 10s value live 10s in Redis and
  another 10s in L1.
* A cold key is fetched once. Without single-flight, every pod and every open
  tab races to refill it simultaneously, which is what gets us rate limited.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Any, Callable

import structlog

from app.cache import redis_client

log = structlog.get_logger(__name__)

# Bump when a cached payload's shape changes — old entries then simply miss
# instead of deserialising into something the code no longer expects.
NAMESPACE = "mdata:v1:"

# Redis keys have no practical length limit but long ones waste memory and are
# unreadable in redis-cli; `events:` keys embed a whole ticker universe.
MAX_KEY_CHARS = 120

# How much longer than its TTL a value is retained purely as a fallback for
# when upstream fails. Serving a 10-minute-old quote beats serving an error.
STALE_MULTIPLIER = 20

# Cap on how long a caller waits for another replica's in-flight fetch before
# giving up and fetching itself. Never block a request indefinitely on a lock.
LOCK_WAIT_SECONDS = 2.0
LOCK_POLL_SECONDS = 0.1

_CACHE: dict[str, tuple[float, Any]] = {}

# One lock per key, kept for the process lifetime. Keys are bounded by the
# symbols users actually look at, and a Lock is ~100 bytes, so this is left to
# grow rather than reference-counted.
_locks: dict[str, asyncio.Lock] = {}


# ── keys ──────────────────────────────────────────────────────────────────────


def _redis_key(key: str) -> str:
    """Namespace a caller's short key, hashing the tail if it is unwieldy."""
    if len(key) <= MAX_KEY_CHARS:
        return f"{NAMESPACE}{key}"
    kind, _, rest = key.partition(":")
    digest = hashlib.sha1(rest.encode()).hexdigest()[:16]
    return f"{NAMESPACE}{kind}:{digest}"


def _stale_key(key: str) -> str:
    return f"{NAMESPACE}stale:{key}"


def _lock_key(key: str) -> str:
    return f"{NAMESPACE}lock:{key}"


def key_kind(key: str) -> str:
    """The metric label for a key — its prefix only, never the symbol."""
    return key.partition(":")[0] or "unknown"


# ── metrics ───────────────────────────────────────────────────────────────────


def _record(key: str, tier: str) -> None:
    try:
        from app.observability.metrics import record_cache_lookup

        record_cache_lookup(key_kind(key), tier)
    except Exception:
        pass  # metrics must never break a request


# ── envelope ──────────────────────────────────────────────────────────────────
#
# {"t": <epoch seconds the value was produced>, "v": <value>}


def _wrap(value: Any, produced_at: float) -> dict:
    return {"t": produced_at, "v": value}


def _unwrap(envelope: Any, ttl: float) -> tuple[Any, bool] | None:
    """(value, is_fresh) from a stored envelope, or None if unusable."""
    if not isinstance(envelope, dict) or "v" not in envelope:
        return None
    produced_at = envelope.get("t")
    if not isinstance(produced_at, (int, float)):
        return None
    return envelope["v"], (time.time() - produced_at) < ttl


def _l1_get(key: str, ttl: float) -> Any | None:
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    return None


def _l1_put(key: str, value: Any, produced_at: float) -> None:
    _CACHE[key] = (produced_at, value)


async def _l2_get(key: str, ttl: float) -> Any | None:
    envelope = await redis_client.cache_get(_redis_key(key))
    if envelope is None:
        return None
    unwrapped = _unwrap(envelope, ttl)
    if unwrapped is None:
        return None
    value, fresh = unwrapped
    if not fresh:
        return None
    # Seed L1 with the ORIGINAL production time so staleness stays bounded by
    # the TTL rather than compounding across tiers.
    _l1_put(key, value, envelope["t"])
    return value


async def _store(key: str, value: Any, ttl: float, produced_at: float) -> None:
    _l1_put(key, value, produced_at)
    envelope = _wrap(value, produced_at)
    await redis_client.cache_set(_redis_key(key), envelope, ttl)
    await redis_client.cache_set(_stale_key(key), envelope, ttl * STALE_MULTIPLIER)


async def _last_good(key: str) -> Any | None:
    """The most recent successfully-fetched value, however old."""
    envelope = await redis_client.cache_get(_stale_key(key))
    if isinstance(envelope, dict) and "v" in envelope:
        return envelope["v"]
    hit = _CACHE.get(key)
    return hit[1] if hit else None


# ── core ──────────────────────────────────────────────────────────────────────


async def _resolve(
    key: str,
    ttl: float,
    produce: Callable[[], Any],
    should_cache: Callable[[Any], bool] | None,
    lock: bool,
) -> Any:
    """L1 → L2 → single-flight → produce(). `produce` returns an awaitable."""
    hit = _l1_get(key, ttl)
    if hit is not None:
        _record(key, "l1")
        return hit

    from_l2 = await _l2_get(key, ttl)
    if from_l2 is not None:
        _record(key, "l2")
        return from_l2

    # One coroutine per key does the work; the rest wait and re-check.
    key_lock = _locks.setdefault(key, asyncio.Lock())
    async with key_lock:
        hit = _l1_get(key, ttl)
        if hit is not None:
            _record(key, "l1")
            return hit
        from_l2 = await _l2_get(key, ttl)
        if from_l2 is not None:
            _record(key, "l2")
            return from_l2

        # Expensive fan-outs additionally coordinate across replicas.
        if lock and not await redis_client.try_lock(_lock_key(key), ttl):
            waited = await _await_other_replica(key, ttl)
            if waited is not None:
                _record(key, "l2")
                return waited

        started = time.time()
        try:
            value = await produce()
        except Exception as e:
            fallback = await _last_good(key)
            if fallback is None:
                _record(key, "error")
                raise
            log.warning("cache_serving_stale", key=key, error=str(e))
            _record(key, "stale")
            return _mark_stale(fallback)
        finally:
            if lock:
                await redis_client.unlock(_lock_key(key))

        _record(key, "miss")
        _observe_fetch(key, time.time() - started)

        if should_cache is None or should_cache(value):
            await _store(key, value, ttl, started)
        return value


def _mark_stale(value: Any) -> Any:
    """Flag a fallback payload so callers can surface it honestly."""
    if isinstance(value, dict):
        return {**value, "stale": True}
    return value


def _observe_fetch(key: str, seconds: float) -> None:
    try:
        from app.observability.metrics import record_cache_fetch

        record_cache_fetch(key_kind(key), seconds)
    except Exception:
        pass


async def _await_other_replica(key: str, ttl: float) -> Any | None:
    """Poll L2 briefly for the value the lock holder is fetching."""
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while time.monotonic() < deadline:
        await asyncio.sleep(LOCK_POLL_SECONDS)
        value = await _l2_get(key, ttl)
        if value is not None:
            return value
    return None


# ── public API ────────────────────────────────────────────────────────────────


async def cached(
    key: str,
    ttl: float,
    fn: Callable[[], Any],
    should_cache: Callable[[Any], bool] | None = None,
    lock: bool = False,
) -> Any:
    """Run the blocking `fn` off-thread unless a fresh cached value exists.

    `should_cache` guards against pinning a partial upstream response — yfinance
    occasionally drops symbols from a batch, and caching that would show gaps
    for the whole TTL instead of self-healing on the next poll. It gates both
    tiers: pinning a bad value cluster-wide is worse than pinning it in one pod.

    `lock` adds cross-replica single-flight, worth it only for fan-outs
    expensive enough to justify the extra Redis round trip.
    """
    return await _resolve(key, ttl, lambda: asyncio.to_thread(fn), should_cache, lock)


async def cached_async(
    key: str,
    ttl: float,
    fn: Callable[[], Any],
    should_cache: Callable[[Any], bool] | None = None,
    lock: bool = False,
) -> Any:
    """Same contract as `cached`, for a coroutine factory."""
    return await _resolve(key, ttl, fn, should_cache, lock)


def peek(key: str) -> Any | None:
    """Current L1 value for `key` regardless of age — None if never cached."""
    hit = _CACHE.get(key)
    return hit[1] if hit else None


def get_fresh(key: str, ttl: float) -> Any | None:
    """L1-only lookup, for synchronous callers."""
    return _l1_get(key, ttl)


def put(key: str, value: Any) -> None:
    """L1-only write, for synchronous callers."""
    _l1_put(key, value, time.time())


async def get_fresh_many(keys: list[str], ttl: float) -> dict[str, Any]:
    """Fresh values for whichever keys have them, checking L1 then one MGET.

    For callers that batch their own fetches — quotes pulls every stale symbol
    in a single download, so it can't express the work as one thunk per key.
    """
    found: dict[str, Any] = {}
    misses: list[str] = []
    for key in keys:
        hit = _l1_get(key, ttl)
        if hit is not None:
            found[key] = hit
            _record(key, "l1")
        else:
            misses.append(key)

    if not misses:
        return found

    envelopes = await redis_client.cache_get_many([_redis_key(k) for k in misses])
    for key in misses:
        envelope = envelopes.get(_redis_key(key))
        if envelope is None:
            _record(key, "miss")
            continue
        unwrapped = _unwrap(envelope, ttl)
        if unwrapped is None or not unwrapped[1]:
            _record(key, "miss")
            continue
        value = unwrapped[0]
        _l1_put(key, value, envelope["t"])
        found[key] = value
        _record(key, "l2")

    return found


async def put_many(mapping: dict[str, Any], ttl: float) -> None:
    """Write a batch of values to both tiers."""
    if not mapping:
        return
    produced_at = time.time()
    fresh: dict[str, Any] = {}
    stale: dict[str, Any] = {}
    for key, value in mapping.items():
        _l1_put(key, value, produced_at)
        envelope = _wrap(value, produced_at)
        fresh[_redis_key(key)] = envelope
        stale[_stale_key(key)] = envelope

    await redis_client.cache_set_many(fresh, ttl)
    await redis_client.cache_set_many(stale, ttl * STALE_MULTIPLIER)
