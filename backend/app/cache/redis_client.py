"""Async Redis client with a simple decorator for caching tool results."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, Optional

import redis.asyncio as aioredis

from app.config import get_settings


@lru_cache
def _get_pool():
    settings = get_settings()
    return aioredis.ConnectionPool.from_url(settings.redis_url, decode_responses=True)


async def get_redis() -> aioredis.Redis:
    """Return an async Redis client from the connection pool."""
    return aioredis.Redis(connection_pool=_get_pool())


async def cache_get(key: str) -> Optional[Any]:
    try:
        r = await get_redis()
        val = await r.get(key)
        return json.loads(val) if val else None
    except Exception:
        return None


async def cache_set(key: str, value: Any, ttl: int = 300) -> None:
    try:
        r = await get_redis()
        await r.setex(key, ttl, json.dumps(value, default=str))
    except Exception:
        pass  # Cache failure should never break the agent pipeline


async def cache_delete(key: str) -> None:
    try:
        r = await get_redis()
        await r.delete(key)
    except Exception:
        pass
