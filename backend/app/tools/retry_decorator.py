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


def _is_retryable(exc: BaseException) -> bool:
    """Return True if the exception should trigger a retry.

    Permanent failures (NOT retried):
    - Auth errors: Tavily InvalidAPIKeyError/ForbiddenError/MissingAPIKeyError
    - Bad request: Tavily BadRequestError (400)
    - NewsAPI 401/403 errors
    - ValueError from yfinance for unknown tickers

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
            msg = str(exc).lower()
            if "401" in msg or "403" in msg or "apikeydisabled" in msg:
                return False
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
