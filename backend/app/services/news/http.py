"""One HTTP path for the news sources.

The social providers' client (services/social/http.py) insists on JSON and
sends the app's own User-Agent; the news sources are XML feeds, and EDGAR
demands a User-Agent with a contact address. So this is its sibling rather
than a reuse: same timeout, same retry policy, same `SourceBlocked`, and the
same transport hook so no test ever touches the network.
"""

from __future__ import annotations

from typing import Any, Optional

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_fixed

from app.config import get_settings
from app.services.social.http import SourceBlocked
from app.tools.retry_decorator import _is_retryable

__all__ = ["SourceBlocked", "get_text", "get_json", "use_transport"]

_transport: Optional[httpx.AsyncBaseTransport] = None


def use_transport(transport: Optional[httpx.AsyncBaseTransport]) -> None:
    """Route every news request through `transport` (tests), or reset."""
    global _transport
    _transport = transport


def _retryable(exc: BaseException) -> bool:
    return not isinstance(exc, SourceBlocked) and _is_retryable(exc)


async def _get(url: str, *, params: Optional[dict], headers: dict[str, str]) -> httpx.Response:
    settings = get_settings()
    async with httpx.AsyncClient(
        timeout=settings.news_http_timeout_seconds,
        headers={"User-Agent": settings.social_user_agent, **headers},
        transport=_transport,
        follow_redirects=True,
    ) as client:
        response = await client.get(url, params=params)
    if response.status_code in (401, 403, 429):
        raise SourceBlocked(response.status_code, "refused by the source")
    response.raise_for_status()
    return response


@retry(retry=retry_if_exception(_retryable), stop=stop_after_attempt(2),
       wait=wait_fixed(0.5), reraise=True)
async def get_text(url: str, *, params: Optional[dict] = None,
                   headers: Optional[dict[str, str]] = None) -> str:
    return (await _get(url, params=params, headers=headers or {})).text


@retry(retry=retry_if_exception(_retryable), stop=stop_after_attempt(2),
       wait=wait_fixed(0.5), reraise=True)
async def get_json(url: str, *, params: Optional[dict] = None,
                   headers: Optional[dict[str, str]] = None) -> Any:
    return (await _get(url, params=params, headers=headers or {})).json()
