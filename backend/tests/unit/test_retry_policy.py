"""Which provider errors are worth retrying."""

from __future__ import annotations

import pytest
from newsapi.newsapi_exception import NewsAPIException

from app.tools.retry_decorator import _is_retryable


def _newsapi(code: str) -> NewsAPIException:
    return NewsAPIException({"status": "error", "code": code, "message": "x"})


@pytest.mark.parametrize("code", ["apiKeyInvalid", "apiKeyMissing", "apiKeyDisabled",
                                  "apiKeyExhausted", "parameterInvalid"])
def test_permanent_newsapi_errors_are_not_retried(code):
    """apiKeyInvalid carries no "401" in its text, which is how it slipped through."""
    assert _is_retryable(_newsapi(code)) is False


def test_a_newsapi_rate_limit_is_retried():
    assert _is_retryable(_newsapi("rateLimited")) is True


def test_network_errors_are_retried_and_value_errors_are_not():
    assert _is_retryable(ConnectionError("reset")) is True
    assert _is_retryable(ValueError("unknown ticker")) is False
