"""Redis-backed session memory for short-lived data.

Stores ephemeral session state (e.g. current analysis context) and
tracks a user's recently analysed tickers.  All keys expire automatically.
"""

from __future__ import annotations

import json
from typing import Any

from app.cache.redis_client import get_redis

SESSION_TTL = 7200  # 2 hours


async def save_session(session_id: str, data: dict, ttl: int = SESSION_TTL) -> None:
    """Persist session data as a JSON blob with an expiry."""
    try:
        r = await get_redis()
        await r.setex(f"session:{session_id}", ttl, json.dumps(data, default=str))
    except Exception:
        pass  # Redis unavailability must not break the request path


async def get_session(session_id: str) -> dict | None:
    """Retrieve session data, or None if expired / missing."""
    try:
        r = await get_redis()
        val = await r.get(f"session:{session_id}")
        return json.loads(val) if val else None
    except Exception:
        return None


async def add_recent_ticker(
    user_id: str, ticker: str, max_items: int = 20
) -> None:
    """Push a ticker to the front of the user's recent-tickers list.

    Duplicates are removed first so the list stays unique.
    Capped at *max_items* entries.  Expires after 30 days of inactivity.
    """
    key = f"recent_tickers:{user_id}"
    try:
        r = await get_redis()
        # Remove existing occurrence so it moves to the front
        await r.lrem(key, 0, ticker.upper())
        await r.lpush(key, ticker.upper())
        await r.ltrim(key, 0, max_items - 1)
        await r.expire(key, 30 * 86400)  # 30 days
    except Exception:
        pass


async def get_recent_tickers(user_id: str) -> list[str]:
    """Return the user's recently analysed tickers (most recent first)."""
    key = f"recent_tickers:{user_id}"
    try:
        r = await get_redis()
        return await r.lrange(key, 0, -1)
    except Exception:
        return []
