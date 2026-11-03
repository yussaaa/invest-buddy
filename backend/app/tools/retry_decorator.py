"""Centralized tenacity retry configuration for external API calls.

All retry policy lives here so it can be updated in one place.

Usage:
    @retry_network_errors
    def _fetch():
        ...  # sync function that makes HTTP calls — let exceptions propagate
"""
from __future__ import annotations

import logging

from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

logger = logging.getLogger(__name__)


# NewsAPI error codes worth retrying; every other code (apiKeyInvalid,
# apiKeyMissing, apiKeyDisabled, apiKeyExhausted, parameterInvalid, ...) is
# permanent for the request as made.
_NEWSAPI_RETRYABLE = {"rateLimited", "unexpectedError"}


def _newsapi_code(exc: BaseException) -> str | None:
    payload = getattr(exc, "exception", None)
    if isinstance(payload, dict):
        code = payload.get("code")
        return str(code) if code else None
    return None


def _is_retryable(exc: BaseException) -> bool:
    """Return True if the exception should trigger a retry.

    Permanent failures (NOT retried):
    - Auth errors: Tavily InvalidAPIKeyError/ForbiddenError/MissingAPIKeyError
    - Bad request: Tavily BadRequestError (400)
    - NewsAPI 401/403 errors
    - ValueError from yfinance for unknown tickers
    - httpx HTTPStatusError with a 4xx status other than 429

    Transient failures (ARE retried):
    - Network errors: ConnectionError, OSError, TimeoutError
    - Tavily UsageLimitExceededError (429)
    - Rate limits, service unavailable, transient HTTP failures
    """
    try:
        from tavily.errors import (
            BadRequestError,
            ForbiddenError,
            InvalidAPIKeyError,
            MissingAPIKeyError,
        )
        if isinstance(exc, (InvalidAPIKeyError, ForbiddenError,
                             MissingAPIKeyError, BadRequestError)):
            return False
    except ImportError:
        pass

    try:
        from newsapi.newsapi_exception import NewsAPIException
        if isinstance(exc, NewsAPIException):
            # NewsAPI reports errors by code, not HTTP status — an invalid key
            # arrives as {'code': 'apiKeyInvalid', ...} with no "401" anywhere,
            # so matching on the status text retried it three times and cost
            # every chat turn that asked for news about four seconds.
            code = _newsapi_code(exc)
            if code is not None:
                return code in _NEWSAPI_RETRYABLE
            msg = str(exc).lower()
            if "401" in msg or "403" in msg or "apikey" in msg:
                return False
    except ImportError:
        pass

    # An HTTP 4xx from httpx is the server's answer about the request as
    # made, so repeating it changes nothing — except 429, which says "later".
    # Without this, a Cloudflare 403 (StockTwits blocks server clients) was
    # retried three times with backoff before the caller saw it.
    try:
        import httpx
        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            return status == 429 or status >= 500
    except ImportError:
        pass

    # ValueError from yfinance for unknown tickers — permanent failure
    if isinstance(exc, ValueError):
        return False

    return True


retry_network_errors = retry(
    retry=retry_if_exception(_is_retryable),
    stop=stop_after_attempt(3),
    # Exponential backoff: ~1s, ~2-3s with jitter, capped at 30s
    wait=wait_exponential_jitter(initial=1, exp_base=2, max=30, jitter=1),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,  # re-raise after all attempts exhausted → outer try/except handles it
)
