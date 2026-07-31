"""Tiny TTL cache shared by the UI-facing services.

Dashboard endpoints get polled every few seconds by every open tab; this keeps
the upstream call count proportional to time rather than to traffic.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable

_CACHE: dict[str, tuple[float, Any]] = {}


async def cached(
    key: str,
    ttl: float,
    fn: Callable[[], Any],
    should_cache: Callable[[Any], bool] | None = None,
) -> Any:
    """Run the blocking `fn` off-thread unless a fresh cached value exists.

    `should_cache` guards against pinning a partial upstream response — yfinance
    occasionally drops symbols from a batch, and caching that would show gaps
    for the whole TTL instead of self-healing on the next poll.
    """
    hit = _CACHE.get(key)
    now = time.time()
    if hit and now - hit[0] < ttl:
        return hit[1]

    value = await asyncio.to_thread(fn)
    if should_cache is None or should_cache(value):
        _CACHE[key] = (now, value)
    return value


async def cached_async(
    key: str,
    ttl: float,
    fn: Callable[[], Any],
    should_cache: Callable[[Any], bool] | None = None,
) -> Any:
    """Same contract as `cached`, for a coroutine factory."""
    hit = _CACHE.get(key)
    now = time.time()
    if hit and now - hit[0] < ttl:
        return hit[1]

    value = await fn()
    if should_cache is None or should_cache(value):
        _CACHE[key] = (now, value)
    return value


def peek(key: str) -> Any | None:
    """Current value for `key` regardless of age — None if never cached."""
    hit = _CACHE.get(key)
    return hit[1] if hit else None


def get_fresh(key: str, ttl: float) -> Any | None:
    """Cached value if it is younger than `ttl`, else None.

    For callers that batch their own fetches (quotes pulls every stale symbol
    in one request) and so can't express the work as a single thunk.
    """
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    return None


def put(key: str, value: Any) -> None:
    _CACHE[key] = (time.time(), value)
