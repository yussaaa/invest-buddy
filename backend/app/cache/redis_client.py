"""Async Redis client used as the shared cache tier across backend replicas.

Every helper here fails open: Redis being down degrades the app to its
process-local cache rather than breaking a request. Two details make that
promise real —

* short socket timeouts, so an unreachable Redis costs milliseconds rather than
  the OS connect timeout on every call, and
* a circuit breaker, so we stop paying even that during a sustained outage.
"""

from __future__ import annotations

import json
import time
from functools import lru_cache
from typing import Any, Optional

import redis.asyncio as aioredis
import structlog

from app.config import get_settings

log = structlog.get_logger(__name__)

# Redis is a cache, never a source of truth — waiting on it is always the wrong
# trade. A quarter second is generous for a same-cluster round trip.
SOCKET_TIMEOUT_SECONDS = 0.25
BREAKER_COOLDOWN_SECONDS = 5.0

_unhealthy_until: float = 0.0


@lru_cache
def _get_pool():
    settings = get_settings()
    return aioredis.ConnectionPool.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=SOCKET_TIMEOUT_SECONDS,
        socket_timeout=SOCKET_TIMEOUT_SECONDS,
        health_check_interval=30,
    )


async def get_redis() -> aioredis.Redis:
    """Return an async Redis client from the connection pool."""
    return aioredis.Redis(connection_pool=_get_pool())


def _breaker_open() -> bool:
    return time.monotonic() < _unhealthy_until


def _trip_breaker(operation: str, error: Exception) -> None:
    global _unhealthy_until
    first_failure = not _breaker_open()
    _unhealthy_until = time.monotonic() + BREAKER_COOLDOWN_SECONDS
    # Only log the transition, or an outage would log on every request.
    if first_failure:
        log.warning(
            "redis_unavailable",
            operation=operation,
            error=str(error),
            cooldown_seconds=BREAKER_COOLDOWN_SECONDS,
        )


def is_available() -> bool:
    """False while the breaker is open — for health reporting, not gating."""
    return not _breaker_open()


def _coerce_ttl(ttl: float) -> int:
    """Redis SETEX wants whole seconds.

    Callers pass float TTLs (`QUOTES_TTL = 10.0`); handing those to SETEX makes
    Redis reply "value is not an integer" and, because failures are swallowed,
    the write silently does nothing at all.
    """
    return max(1, int(ttl))


async def cache_get(key: str) -> Optional[Any]:
    if _breaker_open():
        return None
    try:
        r = await get_redis()
        val = await r.get(key)
        return json.loads(val) if val else None
    except Exception as e:
        _trip_breaker("get", e)
        return None


async def cache_get_many(keys: list[str]) -> dict[str, Any]:
    """One MGET instead of a round trip per key. Missing keys are omitted."""
    if not keys or _breaker_open():
        return {}
    try:
        r = await get_redis()
        values = await r.mget(keys)
    except Exception as e:
        _trip_breaker("mget", e)
        return {}

    out: dict[str, Any] = {}
    for key, raw in zip(keys, values):
        if not raw:
            continue
        try:
            out[key] = json.loads(raw)
        except (TypeError, ValueError):
            continue  # a poisoned entry shouldn't take the whole batch down
    return out


async def cache_set(key: str, value: Any, ttl: float = 300) -> None:
    if _breaker_open():
        return
    try:
        r = await get_redis()
        await r.setex(key, _coerce_ttl(ttl), json.dumps(value, default=str))
    except Exception as e:
        _trip_breaker("set", e)


async def cache_set_many(mapping: dict[str, Any], ttl: float = 300) -> None:
    """Pipelined SETEX for a batch of keys."""
    if not mapping or _breaker_open():
        return
    try:
        r = await get_redis()
        seconds = _coerce_ttl(ttl)
        async with r.pipeline(transaction=False) as pipe:
            for key, value in mapping.items():
                pipe.setex(key, seconds, json.dumps(value, default=str))
            await pipe.execute()
    except Exception as e:
        _trip_breaker("set_many", e)


async def cache_delete(key: str) -> None:
    if _breaker_open():
        return
    try:
        r = await get_redis()
        await r.delete(key)
    except Exception as e:
        _trip_breaker("delete", e)


async def try_lock(key: str, ttl: float) -> bool:
    """SET NX — True if this caller acquired the lock.

    Used to keep one replica doing an expensive fan-out while the others wait
    for its result. Never blocks: a False just means someone else is on it.
    """
    if _breaker_open():
        return False
    try:
        r = await get_redis()
        return bool(await r.set(key, "1", nx=True, ex=_coerce_ttl(ttl)))
    except Exception as e:
        _trip_breaker("lock", e)
        return False


async def unlock(key: str) -> None:
    await cache_delete(key)
