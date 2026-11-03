"""One HTTP path for every social provider.

Small on purpose: a timeout, a User-Agent that says who we are, one retry for
transient failures, and a typed exception for "this source refused us" so the
caller can trip a breaker instead of retrying into a wall.

Tests swap the transport with `use_transport(httpx.MockTransport(...))`, so no
provider test ever touches the network.
"""

from __future__ import annotations

from typing import Any, Optional

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_fixed

from app.config import get_settings
from app.tools.retry_decorator import _is_retryable

_transport: Optional[httpx.AsyncBaseTransport] = None


class SourceBlocked(Exception):
    """The source answered, but not with data: 403, 429, or a challenge page."""

    def __init__(self, status: int, detail: str = ""):
        super().__init__(f"HTTP {status}{': ' + detail if detail else ''}")
        self.status = status


def use_transport(transport: Optional[httpx.AsyncBaseTransport]) -> None:
    """Route every provider request through `transport` (tests), or reset."""
    global _transport
    _transport = transport


def _client() -> httpx.AsyncClient:
    settings = get_settings()
    return httpx.AsyncClient(
        timeout=settings.social_http_timeout_seconds,
        headers={"User-Agent": settings.social_user_agent, "Accept": "application/json"},
        transport=_transport,
        follow_redirects=True,
    )


def _retryable(exc: BaseException) -> bool:
    return not isinstance(exc, SourceBlocked) and _is_retryable(exc)


# One retry, quickly. A social panel is never worth the backoff the market
# data tools use; a stale cached value is served instead (services/cache.py).
@retry(retry=retry_if_exception(_retryable), stop=stop_after_attempt(2),
       wait=wait_fixed(0.5), reraise=True)
async def get_json(url: str, params: Optional[dict] = None) -> Any:
    async with _client() as client:
        response = await client.get(url, params=params)

    # Cloudflare answers a refused client with 403 (or 503) and an HTML
    # challenge page. Either way there is no data behind it this time.
    content_type = response.headers.get("content-type", "")
    if response.status_code in (401, 403, 429):
        raise SourceBlocked(response.status_code, "refused by the source")
    response.raise_for_status()
    if "json" not in content_type:
        raise SourceBlocked(response.status_code, "returned a page, not data")
    return response.json()
